"""Test a pre-specified Binance-confirmed continuation on Lighter Standard."""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from .data import Book
from .forward import utc_window
from .model import local_features
from .replay import replay
from .reversion import (
    VENUES, funding_guarded, make_session, onset, quote_coverage, read_crypto_hour,
    trailing_move_bps,
)
from .study import TARGETS, breakdown


DATES = (
    "2026-11-05", "2026-11-09", "2026-11-10",
    "2026-11-12", "2026-11-16", "2026-11-17",
)


def continuation_direction(session, leader, leader_mid, execution):
    local = session.features.local
    trend = (trailing_move_bps(session.features.mid, 10_000) >= 8)
    trend &= trailing_move_bps(leader_mid, 10_000) >= 8
    rebound_setup = (leader[:, 1] >= 0) & (local[:, 1] >= -3) & (local[:, 1] <= -0.5)
    depth = (local[:, 3] > 0) & (local[:, 5] <= 4)
    eligible = funding_guarded(session, execution) & trend & rebound_setup & depth
    return onset(eligible)


def run(root: Path) -> dict:
    base = VENUES["lighter"]
    scenarios = {
        "base_500ms": base,
        "stress_900ms": replace(base, delay_ms=900),
        "slippage_1bp_per_side": replace(base, slippage_bps=1),
    }
    outputs = {name: {} for name in scenarios}
    audit = []
    for day in DATES:
        start, end = utc_window(day, ["15:02", "16:00"])
        for symbol in TARGETS:
            quotes, local_audit = read_crypto_hour(root, "lighter", day, symbol)
            source, source_audit = read_crypto_hour(root, "binance_futures", day, symbol + "USDT")
            session = make_session(day, symbol, quotes, start, end)
            leader, leader_mid, _ = local_features(Book.observed(source), session.grid)
            eligible = session.can_signal & np.isfinite(session.features.mid) & np.isfinite(leader_mid)
            counts = {}
            for name, settings in scenarios.items():
                if symbol == "TSLA":
                    settings = replace(settings, lot_size=0.0001)
                direction = continuation_direction(session, leader, leader_mid, settings)
                counts[name] = int(direction.sum())
                outputs[name][day, symbol] = replay(session, direction, settings)
            audit.append({
                "day": day,
                "symbol": symbol,
                "common_usable_decision_rows": int(eligible.sum()),
                "funding_guarded_opportunities": counts,
                "lighter": {**local_audit, **quote_coverage(quotes, start, end)},
                "binance": {**source_audit, **quote_coverage(source, start, end)},
            })
    return {
        "venue": "lighter",
        "signal_source": "binance-futures",
        "dates": DATES,
        "decision_window_utc": ["15:02", "15:58"],
        "lighter_tsla_lot_size": 0.0001,
        "execution": {name: asdict(settings) for name, settings in scenarios.items()},
        "performance": {name: breakdown(results) for name, results in outputs.items()},
        "input_audit": audit,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
