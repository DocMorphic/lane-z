"""Paper-test trade-flow absorption on Hyperliquid with public trades and quotes."""

import argparse
import csv
import gzip
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from .data import US_PER_MS, read_quotes, sample_path
from .forward import utc_window
from .makers import aggressor_flow, quote_liquidity
from .model import LOCAL_FEATURES
from .replay import SPREAD_CAP_BPS, Execution, replay
from .reversion import funding_guarded, make_session, quote_coverage, source_file
from .study import TARGETS, breakdown


BASE = Execution(15_000, delay_ms=500, slippage_bps=0.5)
SCENARIOS = {f"{delay}ms": replace(BASE, delay_ms=delay) for delay in (250, 500, 900)}
MAX_TRADE_AGE_MS = 2_000
MAX_QUOTE_AGE_MS = 1_000
MIN_TRADE_SHARE = 0.10
MIN_AGGRESSOR_IMBALANCE = 0.60
MIN_REFILL_SHARE = 0.10
MIN_REFILL_DIRECTION_MOVE_BPS = 0.35
TRADE_DTYPE = np.dtype([
    ("venue", "i8"), ("receive", "i8"), ("price", "f8"), ("amount", "f8"), ("taker_sold", "?"),
])


def read_public_trades(path, symbol):
    seen = set()
    valid = []
    rows = 0
    discarded_by_age = 0
    previous = -1
    with gzip.open(path, "rt", newline="") as source:
        for row in csv.DictReader(source):
            rows += 1
            if row["exchange"] != "hyperliquid" or row["symbol"] != "XYZ:" + symbol:
                raise ValueError(f"unexpected trade market in {path}")
            timestamp = int(row["timestamp"])
            receipt = int(row["local_timestamp"])
            key = timestamp, row["trade_id"]
            price = float(row["price"])
            size = float(row["amount"])
            side = row["side"]
            if previous > receipt or side not in ("A", "B") or not np.isfinite(size + price) or min(price, size) <= 0:
                raise ValueError(f"invalid public trade in {path}")
            previous = receipt
            if not 0 <= receipt - timestamp <= MAX_TRADE_AGE_MS * US_PER_MS:
                discarded_by_age += 1
                continue
            if key in seen:
                continue
            seen.add(key)
            valid.append((timestamp, receipt, price, size, side == "A"))
    return np.array(valid, dtype=TRADE_DTYPE), {
        **source_file(path), "trade_rows": rows, "usable_trade_rows": len(valid),
        "discarded_by_age": discarded_by_age, "duplicates": rows - len(valid) - discarded_by_age,
    }


def direction(session, signed_flow, volume):
    depth, changes, _, withdrawal = quote_liquidity(session)
    refill = changes[:, 0]
    refill_side = np.where(np.isfinite(refill), np.sign(refill), 0).astype(np.int8)
    spread = session.features.local[:, LOCAL_FEATURES.index("spread")]
    recent_move = session.features.local[:, LOCAL_FEATURES.index("ret_250ms")]
    age = (session.grid - session.features.quote["venue"]) / US_PER_MS
    eligible = funding_guarded(session, BASE) & (age <= MAX_QUOTE_AGE_MS) & (spread <= SPREAD_CAP_BPS)
    eligible &= (volume >= MIN_TRADE_SHARE * depth) & (np.abs(refill) >= MIN_REFILL_SHARE) & ~withdrawal
    eligible &= (-refill_side * signed_flow >= MIN_AGGRESSOR_IMBALANCE * volume)
    eligible &= (refill_side * recent_move >= MIN_REFILL_DIRECTION_MOVE_BPS)
    signal = np.where(eligible, refill_side, 0)
    return np.where(signal != np.r_[0, signal[:-1]], signal, 0)


def run(day, window, quote_root, trade_root):
    start, end = utc_window(day, window)
    outputs = {name: {} for name in SCENARIOS}
    inputs = []
    for symbol in TARGETS:
        quote_path = sample_path(quote_root, day, symbol)
        trades_path = sample_path(trade_root, day, symbol)
        quotes = read_quotes(quote_path, symbol)
        session = make_session(day, symbol, quotes, start, end)
        trades, trade_audit = read_public_trades(trades_path, symbol)
        flow = aggressor_flow(trades["receive"] * 1_000, trades["amount"], trades["taker_sold"], session.grid)
        signals = direction(session, *flow)
        for name, execution in SCENARIOS.items():
            signals_allowed = np.where(funding_guarded(session, execution), signals, 0)
            outputs[name][day, symbol] = replay(session, signals_allowed, execution)
        inputs.append({
            "symbol": symbol, "quote": {**source_file(quote_path), **quote_coverage(quotes, start, end)},
            "trades": trade_audit, "eligible_grid_rows": int(session.can_signal.sum()),
            "decision_rows_with_aggressor_trades": int((session.can_signal & (flow[1] > 0)).sum()),
            "signal_onsets": int(np.count_nonzero(signals)),
        })
    return {"day": day, "utc_window_through_exit_buffer": window, "input_audit": inputs,
            "execution": {name: asdict(execution) for name, execution in SCENARIOS.items()},
            "performance": {name: breakdown(results) for name, results in outputs.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day")
    parser.add_argument("--quote-data", type=Path, required=True)
    parser.add_argument("--trade-data", type=Path, required=True)
    parser.add_argument("--utc-window", nargs=2, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.day, args.utc_window, args.quote_data, args.trade_data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        json.dump(result, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
