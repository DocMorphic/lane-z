"""Replay the frozen Hyperliquid equity-perp paper tests on a captured UTC date."""

import argparse
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
import json

import numpy as np

from lane_z.data import BINANCE, BPS, Book, read_quotes, sample_path, SYMBOLS, US_PER_HOUR, US_PER_MS, quote_audit
from lane_z.forward import frozen_models
from lane_z.model import Features, GRID_MS, delta_bps
from lane_z.replay import Execution, Session, replay, sides
from lane_z.study import breakdown, load_sessions, predictions

DEFAULT_DAY = "2026-11-24"
TARGETS = ("NVDA", "TSLA")
PASSES = ((250, 0), (500, 0), (900, 0), (500, 125))
STEP = GRID_MS * US_PER_MS


def fmt(ts):
    return datetime.fromtimestamp(ts / 10**6, UTC).isoformat()


def observed(rows, grid):
    values, valid = Book.observed(rows).asof(grid, 1000 * US_PER_MS)
    return np.where(valid, (values["bid"] + values["ask"]) / 2, np.nan), values


def spread(values, mid):
    with np.errstate(invalid="ignore", divide="ignore"):
        return BPS * (values["ask"] - values["bid"]) / mid


def past_mean(values, window):
    previous = np.full(len(values), np.nan)
    finite = np.isfinite(values)
    count = np.r_[0, np.cumsum(finite)]
    sums = np.r_[0.0, np.cumsum(np.where(finite, values, 0))]
    n = count[window:-1] - count[:-(window + 1)]
    previous[window:] = np.where(n >= .8 * window, (sums[window:-1] - sums[:-(window + 1)]) / np.maximum(n, 1), np.nan)
    return previous


def price_change(series, seconds):
    steps = seconds * 1000 // GRID_MS
    return np.r_[np.full(steps, np.nan), delta_bps(series[steps:], series[:-steps])]


def handwritten(quotes, start, signal_end, last, delay, source_delay, day):
    output, frequencies = {}, {}
    for target in TARGETS:
        grid = np.arange(start, last, STEP)
        hl_rows, bn_rows = quotes[(target, "hyperliquid")], quotes[(target, BINANCE)].copy()
        bn_rows["receive"] += source_delay * US_PER_MS
        relative_mid, values = observed(hl_rows, grid)
        leader_mid, _ = observed(bn_rows, grid)
        h5, b5 = price_change(relative_mid, 5), price_change(leader_mid, 5)
        basis = delta_bps(leader_mid, relative_mid)
        delta = basis - past_mean(basis, 120 * 1000 // GRID_MS)
        widths = spread(values, relative_mid)
        direction = np.sign(b5)
        threshold = np.maximum(5, widths + 2 * (0.9 + 0.5) + 1)
        session = Session(day, target, grid, signal_end, Features(None, None, relative_mid, values), Book.executed(hl_rows))
        allowed = session.can_signal & (grid % US_PER_HOUR < US_PER_HOUR - 40 * 10**6)
        finite = np.isfinite(h5 + b5 + delta + widths) & allowed
        passes = finite & (np.abs(b5) >= 4) & (direction * h5 >= 0) & (direction * (b5 - h5) >= 4) & (
            direction * delta > threshold) & (widths <= 2)
        levels = np.where(passes, direction, 0).astype(np.int8)
        transitions = np.where(levels != np.r_[0, levels[:-1]], levels, 0).astype(np.int8)
        result = replay(session, transitions, Execution(hold_ms=30_000, delay_ms=delay, fee_bps=.9, slippage_bps=.5))
        output[(day, target)] = result
        frequencies[target] = {"rows": int(allowed.sum()), "usable": int(finite.sum()), "eligible_rows": int(passes.sum()),
                               "transitions": int(np.count_nonzero(transitions))}
    return {"coverage": frequencies, "performance": breakdown(output)}


def published(root, start, end, report, delay, source_delay, day):
    sessions, _ = load_sessions(root, (day,), "binance", source_delay_ms=source_delay, window=(start, end))
    execution = replace(Execution(**report["execution_assumptions"]["cross_base"]), delay_ms=delay, fee_bps=.9, slippage_bps=.5)
    scores = predictions(sessions, (day,), execution.hold_ms, frozen_models(report, execution.hold_ms))
    output, coverage = {}, {}
    for key, session in sessions.items():
        predicted = scores[key]["cross"]
        signal = sides(session, predicted, report["reference_policy"]["margin_bps"])
        result = replay(session, signal, execution)
        output[key] = result
        coverage[key[1]] = {
            "rows": int(session.can_signal.sum()), "usable": int(np.count_nonzero(session.can_signal & np.isfinite(predicted))),
            "eligible_rows": int(np.count_nonzero(signal)),
            "entries_clearing_actual_cost_gate": int(np.count_nonzero(sides(
                session, predicted, report["reference_policy"]["margin_bps"], fee_bps=.9, slippage_bps=.5))),
        }
    return {"coverage": coverage, "performance": breakdown(output)}


def run(root, evaluation_start=None, evaluation_end=None, *, day=DEFAULT_DAY):
    market_pairs = [(a, "hyperliquid") for a in SYMBOLS] + [(a, BINANCE) for a in TARGETS]
    paths = {key: sample_path(root, day, *key) for key in market_pairs}
    quotes = {key: read_quotes(path, *key) for key, path in paths.items()}
    all_start = max(rows[0]["receive"] for rows in quotes.values())
    start = (int(all_start) // STEP + 1) * STEP
    last = min(int(quotes[t, "hyperliquid"][-1]["receive"]) for t in TARGETS)
    if evaluation_start:
        t = int(datetime.fromisoformat(evaluation_start).timestamp() * 1e6)
        if t > start:
            start = t
    horizon_end = last - 35 * 10**6
    if evaluation_end:
        horizon_end = min(horizon_end, int(datetime.fromisoformat(evaluation_end).timestamp() * 1e6))
    end = horizon_end // STEP * STEP
    if end - start <= 30 * 10**6:
        raise ValueError("insufficient common recorded tape with a 35-second exit buffer")
    report = json.loads(Path("results/binance.json").read_bytes())
    results = {"signal_start_utc": fmt(start), "signal_end_utc": fmt(end), "last_target_receipt_utc": fmt(last),
               "quote_audit": [quote_audit(path, quotes[key], day, *key, window=(start, end))
                               for key, path in paths.items()], "tests": {}}
    for label in results["quote_audit"]:
        del label["url"]
    for delay, source_delay in PASSES:
        results["tests"][f"{delay}ms-plus-{source_delay}ms-source"] = {
            "published_5s": published(root, start, end, report, delay, source_delay, day),
            "binance_divergence_30s": handwritten(quotes, start, end, last, delay, source_delay, day),
        }
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("--day", type=date.fromisoformat, default=DEFAULT_DAY, help="UTC date of the quote files")
    parser.add_argument("--start", help="Earliest decision time as an ISO 8601 timestamp")
    parser.add_argument("--end", help="Last decision boundary as an ISO 8601 timestamp")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.data, args.start, args.end, day=args.day.isoformat())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(report, output, indent=2, default=lambda value: value.item(), allow_nan=False)
        output.write("\n")
    print("Wrote", args.output)


if __name__ == "__main__":
    main()
