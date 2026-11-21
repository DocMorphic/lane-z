"""Replay a fixed broad-market momentum confirmation rule on Hyperliquid."""

import argparse
import json
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from .data import US_PER_MS, read_quotes, sample_path, sample_url, timestamp
from .forward import utc_window
from .model import LOCAL_FEATURES
from .replay import EXIT_BUFFER_MS, SPREAD_CAP_BPS, Execution, replay
from .reversion import funding_guarded, make_session, quote_coverage, source_file, trailing_move_bps
from .study import TARGETS, breakdown


RETROSPECTIVE_DATES = ("2026-02-01", "2026-03-01", "2026-08-01", "2026-11-01")
INDEX = "XYZ100"
TREND_MS = 30_000
MIN_TREND_BPS = 4.0
MIN_CONFIRMATION_BPS = 1.0
BASE_EXECUTION = Execution(hold_ms=30_000, delay_ms=500, fee_bps=0.9, slippage_bps=0.5)
SCENARIOS = {f"{delay}ms": replace(BASE_EXECUTION, delay_ms=delay) for delay in (250, 500, 900)}


def momentum_direction(session, trend, peer_trend, index_trend, execution):
    confirmation = session.features.local[:, LOCAL_FEATURES.index("ret_5s")]
    spread = session.features.local[:, LOCAL_FEATURES.index("spread")]
    side = np.where(np.isfinite(trend), np.sign(trend), 0).astype(np.int8)
    friction = spread + 2 * (execution.fee_bps + execution.slippage_bps)
    support = funding_guarded(session, execution) & np.isfinite(trend) & np.isfinite(confirmation)
    support &= np.isfinite(peer_trend) & np.isfinite(index_trend)
    target_setup = support & (np.abs(trend) >= MIN_TREND_BPS) & (np.abs(trend) > friction)
    target_setup &= (side * confirmation >= MIN_CONFIRMATION_BPS) & (spread <= SPREAD_CAP_BPS)
    broad_setup = target_setup & (side * peer_trend >= MIN_TREND_BPS) & (side * index_trend >= MIN_TREND_BPS)
    directions = {}
    for rule, setup in (("broad_confirmed", broad_setup), ("target_only", target_setup)):
        stance = np.where(setup, side, 0)
        directions[rule] = np.where(stance != np.r_[0, stance[:-1]], stance, 0)
    return directions, int(support.sum())


def run(root, windows, retrospective):
    outputs = {rule: {name: {} for name in SCENARIOS} for rule in ("broad_confirmed", "target_only")}
    audits = []
    for day, start, end in windows:
        sessions = {}
        sources = {}
        for symbol in (*TARGETS, INDEX):
            path = sample_path(root, day, symbol)
            quotes = read_quotes(path, symbol)
            sessions[symbol] = make_session(day, symbol, quotes, start, end)
            url = sample_url(day, symbol) if retrospective else None
            sources[symbol] = {**source_file(path, url), **quote_coverage(quotes, start, end)}
        trends = {symbol: trailing_move_bps(session.features.mid, TREND_MS) for symbol, session in sessions.items()}
        signals = {}
        for symbol, peer in (TARGETS, TARGETS[::-1]):
            session = sessions[symbol]
            signals[symbol] = {}
            for name, execution in SCENARIOS.items():
                directions, support = momentum_direction(session, trends[symbol], trends[peer], trends[INDEX], execution)
                signals[symbol][name] = {"common_usable_rows": support}
                for rule, direction in directions.items():
                    outputs[rule][name][day, symbol] = replay(session, direction, execution)
                    signals[symbol][name][f"{rule}_onsets"] = int(np.count_nonzero(direction))
        audits.append({
            "date": day,
            "start_utc": datetime.fromtimestamp(start / 1_000_000, timezone.utc).isoformat(),
            "end_utc_with_exit_buffer": datetime.fromtimestamp(end / 1_000_000, timezone.utc).isoformat(),
            "inputs": sources,
            "signals": signals,
        })
    return {
        "strategy": "hyperliquid_weekend_momentum",
        "sample": "retrospective" if retrospective else "direct_capture",
        "execution": {name: asdict(execution) for name, execution in SCENARIOS.items()},
        "performance": {
            rule: {name: breakdown(results) for name, results in scenarios.items()}
            for rule, scenarios in outputs.items()
        },
        "session_audit": audits,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--retrospective", action="store_true")
    mode.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--utc-window", nargs=2, metavar=("START", "END_WITH_EXIT_BUFFER"))
    args = parser.parse_args()

    if args.retrospective:
        if args.utc_window:
            parser.error("--retrospective uses the frozen New York session windows")
        windows = [
            (day, timestamp(day, 9, 35), timestamp(day, 15, 55) + EXIT_BUFFER_MS * US_PER_MS)
            for day in RETROSPECTIVE_DATES
        ]
    else:
        if not args.utc_window or args.date.weekday() < 5:
            parser.error("--date requires a weekend date and --utc-window")
        day = args.date.isoformat()
        start, end = utc_window(day, args.utc_window)
        if end - start <= (TREND_MS + EXIT_BUFFER_MS) * US_PER_MS:
            parser.error("--utc-window must cover the 30-second warmup and two-minute exit buffer")
        windows = [(day, start, end)]
    result = run(args.data, windows, args.retrospective)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        json.dump(result, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
