"""Paper-test reversal after a large Hyperliquid move away from its oracle premium."""

import argparse
import csv
import gzip
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .closed_session import BAR_MS, FORWARD_DATE, FORWARD_WINDOW, MOVE_MS, RETROSPECTIVE, SCENARIOS
from .data import US_PER_MS, Book, read_quotes, sample_path, sample_url, timestamp
from .forward import utc_window
from .model import GRID_MS, LOCAL_FEATURES, delta_bps
from .replay import SPREAD_CAP_BPS, exit_delay_steps, replay
from .reversion import funding_guarded, make_session, quote_coverage, source_file
from .study import TARGETS, breakdown


HISTORY_BARS = 12
MIN_HISTORY = 8
MAX_HISTORICAL_QUOTE_AGE_MS = 30_000
MAX_ORACLE_RECEIPT_AGE_MS = 5_000
MIN_MOVE_BPS = 50
MIN_PREMIUM_DEVIATION_BPS = 30
MIN_IMBALANCE = 0.20


def read_oracle(path, symbol):
    values = []
    previous = -1
    with gzip.open(path, "rt", newline="") as source:
        for row in csv.DictReader(source):
            if row["exchange"] != "hyperliquid" or row["symbol"] != "XYZ:" + symbol:
                raise ValueError(f"oracle data for a different market in {path}")
            receipt = int(row["local_timestamp"])
            if receipt < previous:
                raise ValueError(f"oracle observations out of receipt order in {path}")
            previous = receipt
            if row["index_price"]:
                price = float(row["index_price"])
                if np.isfinite(price) and price > 0:
                    values.append((receipt, price))
    if not values:
        raise ValueError(f"no oracle observations in {path}")
    return np.array(values, dtype=[("receipt", "i8"), ("price", "f8")])


def bar_features(session, quotes, oracle):
    bar_rows = np.flatnonzero(session.grid % (BAR_MS * US_PER_MS) == 0)
    bars = session.grid[bar_rows]
    quote, usable = Book.observed(quotes).asof(bars, MAX_HISTORICAL_QUOTE_AGE_MS * US_PER_MS)
    midpoint = np.where(usable, (quote["bid"] + quote["ask"]) / 2, np.nan)
    latest = np.searchsorted(oracle["receipt"], bars, side="right") - 1
    seen = oracle[np.maximum(latest, 0)]
    valid = (latest >= 0) & (bars - seen["receipt"] <= MAX_ORACLE_RECEIPT_AGE_MS * US_PER_MS)
    reference = np.where(valid, seen["price"], np.nan)
    recorded_premium = delta_bps(midpoint, reference)
    current_mid = session.features.mid[bar_rows]
    current_premium = delta_bps(current_mid, reference)

    steps = MOVE_MS // BAR_MS
    move = np.full(len(bars), np.nan)
    move[steps:] = delta_bps(current_mid[steps:], midpoint[:-steps])
    deviation = np.full(len(bars), np.nan)
    for row in range(HISTORY_BARS, len(bars)):
        past = recorded_premium[row - HISTORY_BARS:row]
        past = past[np.isfinite(past)]
        if len(past) >= MIN_HISTORY:
            deviation[row] = current_premium[row] - np.median(past)
    return bar_rows, move, deviation


def directions(session, features, execution):
    rows, move, deviation = features
    local = session.features.local[rows]
    imbalance = local[:, LOCAL_FEATURES.index("imbalance")]
    spread = local[:, LOCAL_FEATURES.index("spread")]
    scheduled_exit = (exit_delay_steps(execution) * GRID_MS + execution.delay_ms) * US_PER_MS
    allowed = funding_guarded(session, execution)[rows] & (session.grid[rows] + scheduled_exit < session.signal_end)
    supported = allowed & np.isfinite(move) & np.isfinite(deviation)
    direction = np.where(np.isfinite(move), -np.sign(move), 0)
    shock = supported & (np.abs(move) >= MIN_MOVE_BPS)
    dislocation = shock & (-direction * deviation >= MIN_PREMIUM_DEVIATION_BPS)
    eligible = dislocation & (direction * imbalance >= MIN_IMBALANCE) & (spread <= SPREAD_CAP_BPS)
    result = np.zeros(len(session.grid), dtype=np.int8)
    result[rows[eligible]] = direction[eligible]
    return result, {
        "funding_and_exit_guarded_bars": int(allowed.sum()), "bar_and_oracle_support": int(supported.sum()),
        "large_target_moves": int(shock.sum()), "large_oracle_dislocations": int(dislocation.sum()),
        "signals": int(eligible.sum()),
    }


def run(quote_root, oracle_root, retrospective):
    windows = [(day, timestamp(day, 9, 35), timestamp(day, 15, 57)) for day in RETROSPECTIVE]
    if not retrospective:
        windows = [(FORWARD_DATE, *utc_window(FORWARD_DATE, FORWARD_WINDOW))]
    outcomes = {name: {} for name in SCENARIOS}
    audit = []
    for day, start, end in windows:
        for symbol in TARGETS:
            quote_path = sample_path(quote_root, day, symbol)
            oracle_path = sample_path(oracle_root, day, symbol)
            quotes = read_quotes(quote_path, symbol)
            oracle = read_oracle(oracle_path, symbol)
            session = make_session(day, symbol, quotes, start, end)
            features = bar_features(session, quotes, oracle)
            diagnostics = {}
            for name, execution in SCENARIOS.items():
                direction, diagnostics[name] = directions(session, features, execution)
                outcomes[name][day, symbol] = replay(session, direction, execution)
            quote_url = sample_url(day, symbol) if retrospective else None
            oracle_url = quote_url.replace("/book_ticker/", "/derivative_ticker/") if retrospective else None
            audit.append({
                "day": day, "symbol": symbol,
                "start_utc": datetime.fromtimestamp(start / 1_000_000, timezone.utc).isoformat(),
                "end_utc_with_exit_buffer": datetime.fromtimestamp(end / 1_000_000, timezone.utc).isoformat(),
                "quotes": {**source_file(quote_path, quote_url), **quote_coverage(quotes, start, end)},
                "oracle": {**source_file(oracle_path, oracle_url), "valid_receipt_rows": len(oracle)},
                "entry_diagnostics": diagnostics,
            })
    return {
        "sample": "retrospective" if retrospective else "forward", "oracle_clock": "collector_receipt",
        "execution": {name: asdict(execution) for name, execution in SCENARIOS.items()},
        "performance": {name: breakdown(results) for name, results in outcomes.items()}, "input_audit": audit,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--retrospective", action="store_true")
    mode.add_argument("--forward", action="store_true")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--oracle-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.data, args.oracle_data, args.retrospective)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        json.dump(report, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
