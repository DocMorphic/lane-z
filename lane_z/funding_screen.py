"""Screen whether lagged funding spreads alone could pay two-venue trading costs."""

import argparse
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


LIGHTER_MARKETS = {"NVDA": 110, "TSLA": 112}
FIRST_INPUT = date(2026, 10, 1)
LAST_INPUT = date(2026, 11, 21)
PERIODS = {"validation": (date(2026, 10, 2), date(2026, 10, 31)),
           "test": (date(2026, 11, 1), date(2026, 11, 20))}
HURDLE_BPS = Decimal(12)
QUOTED_COSTS = (Decimal(10), HURDLE_BPS, Decimal(15))
DECISION_TIME = time(15, 30, tzinfo=timezone.utc)
DAY_SECONDS = 86_400


def dates(start, stop):
    for offset in range((stop - start).days):
        yield start + timedelta(days=offset)


def sample_inputs():
    snapshots = []
    for chunk_start in list(dates(FIRST_INPUT, LAST_INPUT))[::7]:
        chunk_end = min(chunk_start + timedelta(days=7), LAST_INPUT)
        start, end = (int(datetime.combine(day, time(), timezone.utc).timestamp()) for day in (chunk_start, chunk_end))
        for symbol, market_id in LIGHTER_MARKETS.items():
            sources = {
                "lighter": ("https://mainnet.zklighter.elliot.ai/api/v1/fundings", {
                    "market_id": market_id, "resolution": "1h", "start_timestamp": start - 1,
                    "end_timestamp": end + 1, "count_back": 0,
                }),
                "binance": ("https://fapi.binance.com/fapi/v1/fundingRate", {
                    "symbol": symbol + "USDT", "startTime": start * 1_000,
                    "endTime": end * 1_000, "limit": 1_000,
                }),
            }
            for venue, (endpoint, params) in sources.items():
                url = endpoint + "?" + urlencode(params)
                with urlopen(url, timeout=30) as response:
                    payload = json.load(response)
                snapshots.append({"venue": venue, "symbol": symbol, "url": url, "payload": payload})
    return snapshots


def rates(snapshots):
    by_market = {}
    for snapshot in snapshots:
        symbol, venue = snapshot["symbol"], snapshot["venue"]
        payload = snapshot["payload"]
        market = by_market.setdefault((symbol, venue), {})
        if venue == "lighter":
            if payload["code"] != 200 or payload["resolution"] != "1h":
                raise ValueError(f"invalid Lighter hourly history for {symbol}")
            for row in payload["fundings"]:
                magnitude_bps = Decimal(row["rate"]) * 100
                sign = {"long": 1, "short": -1}.get(row["direction"], 0)
                if magnitude_bps < 0 or (magnitude_bps and not sign):
                    raise ValueError(f"invalid Lighter funding row for {symbol}")
                value = magnitude_bps * sign
                timestamp = row["timestamp"]
                if market.setdefault(timestamp, value) != value:
                    raise ValueError(f"conflicting hourly funding for {symbol}")
        else:
            for row in payload:
                if row["symbol"] != symbol + "USDT":
                    raise ValueError(f"wrong Binance contract for {symbol}")
                timestamp = row["fundingTime"] // 1_000
                value = Decimal(row["fundingRate"]) * 10_000
                if market.setdefault(timestamp, value) != value:
                    raise ValueError(f"conflicting Binance funding for {symbol}")
    for (symbol, venue), market in by_market.items():
        timestamps = sorted(market)
        maximum_spacing = 3_600 if venue == "lighter" else 8 * 3_600 + 5
        if any(b - a > maximum_spacing for a, b in zip(timestamps, timestamps[1:])):
            raise ValueError(f"gap in {venue} funding history for {symbol}")
    return by_market


def spread(history, symbol, start, end):
    lighter, binance = ([rate for stamp, rate in history[symbol, venue].items() if start < stamp < end]
                        for venue in ("lighter", "binance"))
    if len(lighter) != 24 or len(binance) < 3:
        raise ValueError(f"incomplete funding records for {symbol} at {start}")
    return sum(binance) - sum(lighter)


def screen(snapshots):
    history = rates(snapshots)
    result = {}
    for period, bounds in PERIODS.items():
        selected = []
        total_days = 0
        for day in dates(*bounds):
            total_days += 1
            stamp = int(datetime.combine(day, DECISION_TIME).timestamp())
            for symbol in LIGHTER_MARKETS:
                prior = spread(history, symbol, stamp - DAY_SECONDS, stamp)
                if abs(prior) <= HURDLE_BPS:
                    continue
                following = spread(history, symbol, stamp, stamp + DAY_SECONDS)
                selected.append({"day_utc": day.isoformat(), "symbol": symbol,
                                 "long_venue": "lighter" if prior > 0 else "binance",
                                 "prior_spread_bps": float(prior),
                                 "received_funding_bps": float(following if prior > 0 else -following)})
        received = sum(row["received_funding_bps"] for row in selected)
        result[period] = {
            "symbol_days": total_days * len(LIGHTER_MARKETS), "selected_symbol_days": len(selected),
            "total_funding_bps_at_equal_notional": received,
            "after_cost_bps_at_equal_notional": {str(cost): received - len(selected) * float(cost) for cost in QUOTED_COSTS},
            "selected": selected,
        }
    return {"decision_time_utc": DECISION_TIME.isoformat(), "hurdle_bps": int(HURDLE_BPS),
            "paper_fill_simulation": False, "sources": [item["url"] for item in snapshots], **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    if args.capture:
        snapshots = sample_inputs()
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        with args.snapshot.open("x") as destination:
            json.dump(snapshots, destination)
    raw = args.snapshot.read_bytes()
    result = screen(json.loads(raw))
    result["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        json.dump(result, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
