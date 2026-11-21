"""Replay a fixed 15-minute Hyperliquid weekend dislocation reversal in paper."""

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .data import US_PER_MS, read_quotes, sample_path, sample_url, timestamp
from .forward import utc_window
from .model import GRID_MS, LOCAL_FEATURES
from .replay import SPREAD_CAP_BPS, Execution, exit_delay_steps, replay
from .reversion import funding_guarded, make_session, quote_coverage, source_file, trailing_move_bps
from .study import TARGETS, breakdown


INDEX = "XYZ100"
RETROSPECTIVE = ("2026-02-01", "2026-03-01", "2026-08-01", "2026-11-01")
FORWARD_DATE = "2026-11-22"
FORWARD_WINDOW = ("00:02", "04:57")
BAR_MS = 300_000
MOVE_MS = 900_000
MIN_MOVE_BPS = 50.0
MIN_RELATIVE_MOVE_BPS = 25.0
MIN_IMBALANCE = 0.20
BASE = Execution(hold_ms=MOVE_MS, delay_ms=500, fee_bps=0.9, slippage_bps=0.5)
SCENARIOS = {f"{delay}ms": replace(BASE, delay_ms=delay) for delay in (250, 500, 900)}


def directions(session, target_move, index_move, execution):
    local = session.features.local
    imbalance = local[:, LOCAL_FEATURES.index("imbalance")]
    spread = local[:, LOCAL_FEATURES.index("spread")]
    scheduled_exit_us = (exit_delay_steps(execution) * GRID_MS + execution.delay_ms) * US_PER_MS
    scheduled = (session.grid % (BAR_MS * US_PER_MS) == 0) & funding_guarded(session, execution)
    scheduled &= session.grid + scheduled_exit_us < session.signal_end
    supported = scheduled & np.isfinite(target_move) & np.isfinite(index_move)
    large_move = supported & (np.abs(target_move) >= MIN_MOVE_BPS)
    side = np.where(np.isfinite(target_move), -np.sign(target_move), 0)
    relative_move = large_move & (-side * (target_move - index_move) >= MIN_RELATIVE_MOVE_BPS)
    liquid = relative_move & (side * imbalance >= MIN_IMBALANCE) & (spread <= SPREAD_CAP_BPS)
    audit = {
        "funding_and_exit_guarded_bars": int(scheduled.sum()),
        "quote_supported_bars": int(supported.sum()),
        "large_target_shocks": int(large_move.sum()),
        "index_confirmed_shocks": int(relative_move.sum()),
        "eligible_signals": int(liquid.sum()),
    }
    return np.where(liquid, side, 0).astype(np.int8), audit


def run(root, retrospective):
    windows = [(day, timestamp(day, 9, 35), timestamp(day, 15, 57)) for day in RETROSPECTIVE]
    if not retrospective:
        windows = [(FORWARD_DATE, *utc_window(FORWARD_DATE, FORWARD_WINDOW))]
    outputs = {name: {} for name in SCENARIOS}
    audits = []
    for day, start, end in windows:
        source = {}
        sessions = {}
        for symbol in (*TARGETS, INDEX):
            path = sample_path(root, day, symbol)
            quotes = read_quotes(path, symbol)
            sessions[symbol] = make_session(day, symbol, quotes, start, end)
            url = sample_url(day, symbol) if retrospective else None
            source[symbol] = {**source_file(path, url), **quote_coverage(quotes, start, end)}
        index_move = trailing_move_bps(sessions[INDEX].features.mid, MOVE_MS)
        signals = {}
        for symbol in TARGETS:
            session = sessions[symbol]
            target_move = trailing_move_bps(session.features.mid, MOVE_MS)
            signals[symbol] = {}
            for name, execution in SCENARIOS.items():
                side, signals[symbol][name] = directions(session, target_move, index_move, execution)
                outputs[name][day, symbol] = replay(session, side, execution)
        audits.append({
            "day": day,
            "start_utc": datetime.fromtimestamp(start / 1_000_000, timezone.utc).isoformat(),
            "end_utc_with_exit_buffer": datetime.fromtimestamp(end / 1_000_000, timezone.utc).isoformat(),
            "inputs": source,
            "signals": signals,
        })
    return {
        "strategy": "hyperliquid_closed_session_dislocation",
        "sample": "retrospective" if retrospective else "forward",
        "decision_interval_ms": BAR_MS,
        "signal": {"lookback_ms": MOVE_MS, "min_move_bps": MIN_MOVE_BPS,
                   "min_relative_move_bps": MIN_RELATIVE_MOVE_BPS, "min_imbalance": MIN_IMBALANCE,
                   "max_spread_bps": SPREAD_CAP_BPS},
        "execution": {name: asdict(execution) for name, execution in SCENARIOS.items()},
        "performance": {name: breakdown(results) for name, results in outputs.items()},
        "session_audit": audits,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--retrospective", action="store_true")
    mode.add_argument("--forward", action="store_true")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = run(args.data, args.retrospective)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        json.dump(report, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
