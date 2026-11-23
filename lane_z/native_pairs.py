"""Paper-test mean-reverting price ratios among native Hyperliquid perps."""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .perp_basis import BAR, DAY, DATES, SCENARIOS, MarketData, epoch, replay_window, stamp

PAIRS = (("ETH", "BTC"), ("SOL", "BTC"))
FORMATION_DAYS = 90
ENTRY_Z = 2.5
STOP_Z = 4.0
HOLD_DAYS = 7
MINIMUM_CORRELATION = 0.60
MAXIMUM_PERSISTENCE = 0.90


@dataclass(frozen=True)
class Position:
    underlying_pair: tuple[str, str]
    long_short: tuple[str, str]
    mean: float
    spread_scale: float
    sign: float
    entered: int


def close_prices(data, pair, day):
    indices = range(day - FORMATION_DAYS * DAY, day + 1, DAY)
    return np.log(np.array([[data.candles[asset][t - BAR].close for t in indices] for asset in pair]))


def signal(log_prices):
    ratio = log_prices[0] - log_prices[1]
    prior = ratio[:-1]
    mean = float(prior.mean())
    spread_scale = float(prior.std(ddof=1))
    if spread_scale <= 0:
        return None
    centered = prior - mean
    persistence = float(centered[:-1] @ centered[1:] / (centered[:-1] @ centered[:-1]))
    correlation = float(np.corrcoef(np.diff(log_prices[:, :-1]))[0, 1])
    z = float((ratio[-1] - mean) / spread_scale)
    if not (0 <= persistence <= MAXIMUM_PERSISTENCE and correlation >= MINIMUM_CORRELATION and ENTRY_Z <= abs(z) < STOP_Z):
        return None
    return z, mean, spread_scale, correlation, persistence


def choose_pairs(data, start, end):
    positions = {}
    entries = []
    exits = []
    active = None
    for day in range(start, end, DAY):
        desired = ()
        if active:
            latest = close_prices(data, active.underlying_pair, day)[:, -1]
            signed_z = active.sign * (float(latest[0] - latest[1]) - active.mean) / active.spread_scale
            if signed_z <= 0 or signed_z >= STOP_Z or day - active.entered >= HOLD_DAYS * DAY:
                reason = "convergence" if signed_z <= 0 else "stop" if signed_z >= STOP_Z else "time"
                exits.append({"decision_utc": stamp(day), "signed_z": signed_z, "reason": reason})
                active = None
            else:
                desired = active.long_short
        else:
            candidates = [(stats, pair) for pair in PAIRS if (stats := signal(close_prices(data, pair, day))) is not None]
            if candidates:
                (z, mean, spread_scale, correlation, persistence), pair = max(candidates, key=lambda item: abs(item[0][0]))
                desired = pair[::-1] if z > 0 else pair
                active = Position(pair, desired, mean, spread_scale, float(np.sign(z)), day)
                entries.append({"decision_utc": stamp(day), "long_short": desired, "z": z,
                                "return_correlation": correlation, "ratio_persistence": persistence})
        positions[day] = desired
    if active:
        exits.append({"decision_utc": stamp(end), "signed_z": None, "reason": "vintage_end"})
    return positions, entries, exits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = MarketData(args.data)
    windows = []
    dates = [epoch(day) for day in DATES[1:]]
    for start, end in zip(dates, dates[1:]):
        positions, entries, exits = choose_pairs(data, start, end)
        windows.append({
            "start_utc": stamp(start), "end_utc": stamp(end), "entries": entries, "exits": exits,
            "scenarios": {name: replay_window(data, start, end, scenario, positions) for name, scenario in SCENARIOS.items()},
        })
    results = {
        "input_files": data.audit,
        "historical_screen_pass": all(window["scenarios"]["stress"]["excess_vs_cash_usdc"] > 0 for window in windows),
        "windows": windows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
