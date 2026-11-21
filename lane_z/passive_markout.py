"""Measure public Hyperliquid maker-side prints without assigning passive fills."""

import argparse
import json
from pathlib import Path

import numpy as np

from .absorption import BASE, MAX_TRADE_AGE_MS, read_public_trades
from .data import BPS, US_PER_HOUR, US_PER_MS, Book, read_quotes, sample_path
from .forward import utc_window
from .reversion import source_file
from .study import TARGETS


HORIZONS_MS = (500, 5_000, 15_500, 60_500)
MAKER_FEE_BPS = 0.3


def describe(quotes, trades, start, end):
    trades = trades[(trades["venue"] >= start) & (trades["venue"] < end)]
    stamps = trades["venue"]
    size, price = trades["amount"], trades["price"]
    side = np.where(trades["taker_sold"], 1, -1)
    book = Book.executed(quotes)
    max_age_us = BASE.max_venue_age_ms * US_PER_MS
    earlier, valid = book.asof(stamps - 1, max_age_us)
    touch = np.where(side > 0, earlier["bid"], earlier["ask"])
    selected = valid & np.isclose(touch, price, rtol=0, atol=1e-9)
    with np.errstate(invalid="ignore", divide="ignore"):
        earlier_spread = 2 * BPS * (earlier["ask"] - earlier["bid"]) / (earlier["ask"] + earlier["bid"])
    horizons = {}
    for horizon in HORIZONS_MS:
        offset = horizon * US_PER_MS
        later, usable = book.asof(stamps + offset, max_age_us)
        sample = selected & usable & ((stamps % US_PER_HOUR) + offset < US_PER_HOUR)
        notional = size[sample] * price[sample]
        if not len(notional):
            horizons[str(horizon)] = {"prints": 0}
            continue
        midpoint = (later["bid"] + later["ask"]) / 2
        exit_touch = np.where(side > 0, later["bid"], later["ask"])
        midpoint_bps = BPS * side * (midpoint - price) / price
        exit_bps = BPS * side * (exit_touch - price) / price
        exit_bps -= MAKER_FEE_BPS + (BASE.fee_bps + BASE.slippage_bps) * exit_touch / price
        horizons[str(horizon)] = {
            "prints": len(notional), "entry_notional_usd": float(notional.sum()),
            "prior_spread_median_bps": float(np.median(earlier_spread[sample])),
            "midpoint_markout_weighted_bps": float(np.average(midpoint_bps[sample], weights=notional)),
            "ioc_exit_proxy_weighted_bps": float(np.average(exit_bps[sample], weights=notional)),
        }
    return {
        "distinct_timely_prints": len(trades), "prints_at_prior_maker_touch": int(selected.sum()),
        "distinct_times_at_prior_maker_touch": int(len(np.unique(stamps[selected]))), "horizons_ms": horizons,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date")
    parser.add_argument("--quotes", type=Path, required=True)
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--utc-window", nargs=2, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start, end = utc_window(args.date, args.utc_window)
    result = {
        "date": args.date, "utc_window": args.utc_window, "maker_entry_fee_bps": MAKER_FEE_BPS,
        "ioc_exit_fee_bps": BASE.fee_bps, "ioc_exit_reserve_bps": BASE.slippage_bps,
        "max_trade_age_ms": MAX_TRADE_AGE_MS, "max_quote_age_ms": BASE.max_venue_age_ms, "symbols": {},
    }
    for symbol in TARGETS:
        quote_path = sample_path(args.quotes, args.date, symbol)
        trade_path = sample_path(args.trades, args.date, symbol)
        quotes = read_quotes(quote_path, symbol)
        trades, trade_audit = read_public_trades(trade_path, symbol)
        if not len(trades) or min(int(trades["venue"].max()), int(quotes["venue"].max())) < end:
            raise ValueError(f"trade and quote files for {symbol} must extend beyond the requested window")
        result["symbols"][symbol] = {
            "quote_file": source_file(quote_path), "trade_file": trade_audit, **describe(quotes, trades, start, end),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        json.dump(result, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
