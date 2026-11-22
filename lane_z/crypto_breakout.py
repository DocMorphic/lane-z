"""Reproduce a fixed, fully funded BTC and ETH spot breakout paper test."""

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DAY = timedelta(days=1)
SYMBOLS = ("BTC", "ETH")
ENTRY_DAYS = 100
EXIT_DAYS = 50
START_CAPITAL = 1_000
FEE_BPS = 60
BASE_RESERVE_BPS = 20
STRESS_RESERVE_BPS = 90
CASH_HURDLE = 0.04
FIRST_DAY = date(2017, 1, 1)
TERMINAL_DAY = date(2026, 11, 22)
KRAKEN_START = date(2025, 4, 1)
COINBASE_WINDOWS = {
    "development": ("2018-01-01", "2021-01-01"),
    "holdout_2021_2023": ("2021-01-01", "2024-01-01"),
    "holdout_2024_2026": ("2024-01-01", "2026-11-22"),
    "calendar_2024": ("2024-01-01", "2025-01-01"),
    "calendar_2025": ("2025-01-01", "2026-01-01"),
    "calendar_2026": ("2026-01-01", "2026-11-22"),
}


def days(first, end):
    return (first + timedelta(days=i) for i in range((end - first).days))


def utc_stamp(day):
    return datetime.combine(day, datetime.min.time(), UTC)


def request_json(url):
    request = Request(url, headers={"User-Agent": "Lane-Z-public-market-research/1"})
    with urlopen(request, timeout=40) as reply:
        return json.load(reply)


def fetch(root):
    root.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        end = TERMINAL_DAY + DAY
        for offset in range(0, (end - FIRST_DAY).days, 280):
            first = FIRST_DAY + offset * DAY
            stop = min(first + 280 * DAY, end)
            url = f"https://api.exchange.coinbase.com/products/{symbol}-USD/candles?" + urlencode({
                "granularity": 86400, "start": utc_stamp(first).isoformat(), "end": utc_stamp(stop).isoformat(),
            })
            save_source(root / f"coinbase-{symbol}-{first}.json", url, first, stop)
        pair = "XBT" if symbol == "BTC" else symbol
        start = TERMINAL_DAY - 719 * DAY
        url = "https://api.kraken.com/0/public/OHLC?" + urlencode({
            "pair": pair + "USD", "interval": 1440, "since": int(utc_stamp(start).timestamp()),
        })
        save_source(root / f"kraken-{symbol}.json", url, start, end)


def save_source(path, url, first, end):
    if path.exists():
        return
    payload = request_json(url)
    with path.open("x") as output:
        json.dump({"url": url, "start": first.isoformat(), "end": end.isoformat(), "payload": payload}, output)


@dataclass(frozen=True)
class Bar:
    open: float
    close: float


def load(root, venue, symbol):
    seen = {}
    sources = []
    for path in sorted(root.glob(f"{venue}-{symbol}*.json")):
        raw = path.read_bytes()
        data = json.loads(raw)
        block_start = date.fromisoformat(data["start"])
        block_end = date.fromisoformat(data["end"])
        payload = data["payload"]
        if venue == "kraken":
            if payload["error"]:
                raise ValueError(payload["error"])
            rows, = (r for k, r in payload["result"].items() if k != "last")
        else:
            rows = payload
        selected = 0
        for row in rows:
            stamp = datetime.fromtimestamp(row[0], UTC)
            day = stamp.date()
            if not block_start <= day < block_end:
                continue
            opening, close, volume = map(float, (row[1], row[4], row[6]) if venue == "kraken" else row[3:6])
            if stamp != utc_stamp(day) or min(opening, close, volume) <= 0 or not isfinite(opening + close + volume):
                raise ValueError(f"Bad {venue} {symbol} bar on {day}")
            bar = Bar(opening, close)
            if seen.setdefault(day, bar) != bar:
                raise ValueError(f"Conflicting {venue} {symbol} opening/close on {day}")
            selected += 1
        sources.append({"file": path.name, "url": data["url"], "rows": selected,
                        "sha256": hashlib.sha256(raw).hexdigest()})
    first_expected = FIRST_DAY if venue == "coinbase" else TERMINAL_DAY - 719 * DAY
    for day in days(first_expected, TERMINAL_DAY + DAY):
        if day not in seen:
            raise ValueError(f"Missing {venue} {symbol} bar on {day}")
    return seen, sources


def signals(bars):
    dates = sorted(bars)
    closes = [bars[day].close for day in dates]
    state = False
    result = {}
    for index in range(ENTRY_DAYS, len(dates)):
        day = dates[index]
        if day >= TERMINAL_DAY:
            break  # The terminal bar's opening print is the only field used for execution.
        close = closes[index]
        if close > max(closes[index - ENTRY_DAYS:index]):
            state = True
        elif close < min(closes[index - EXIT_DAYS:index]):
            state = False
        # The D close is visible at D+1 open. Wait a further full UTC day.
        result[day + 2 * DAY] = state
    return result


@dataclass
class Sleeve:
    cash: float = START_CAPITAL / len(SYMBOLS)
    quantity: float = 0
    fees: float = 0
    reserve: float = 0
    fills: int = 0

    def rebalance(self, target_long, opening, reserve_bps):
        if target_long == bool(self.quantity):
            return
        cost = (FEE_BPS + reserve_bps) / 10_000
        if target_long:
            executed = self.cash / (1 + cost)
            self.quantity = executed / opening
            self.cash = 0
        else:
            executed = self.quantity * opening
            self.cash = executed * (1 - cost)
            self.quantity = 0
        self.fees += executed * FEE_BPS / 10_000
        self.reserve += executed * reserve_bps / 10_000
        self.fills += 1


def replay(bars, orders, start, end, reserve_bps, buy_hold=False):
    if not all(start in orders[symbol] for symbol in SYMBOLS):
        raise ValueError(f"Insufficient strategy history on {start}")
    sleeves = {symbol: Sleeve() for symbol in SYMBOLS}
    exposure_count = 0
    high_water = START_CAPITAL
    maximum_drawdown = 0.0
    equity = START_CAPITAL
    for day in days(start, end + DAY):
        equity = 0.0
        for symbol, sleeve in sleeves.items():
            opening = bars[symbol][day].open
            target = day < end and (buy_hold or orders[symbol][day])
            sleeve.rebalance(target, opening, reserve_bps)
            exposure_count += int(bool(sleeve.quantity))
            equity += sleeve.cash + sleeve.quantity * opening
        high_water = max(high_water, equity)
        maximum_drawdown = max(maximum_drawdown, 1 - equity / high_water)
    return {
        "end_usd": equity, "net_usd": equity - START_CAPITAL, "return_fraction": equity / START_CAPITAL - 1,
        "maximum_drawdown": maximum_drawdown, "funded_sleeved_days": exposure_count,
        "all_sleeve_days": len(SYMBOLS) * (end - start).days,
        "sleeves": {symbol: {"end_usd": sleeve.cash, "fees_usd": sleeve.fees,
                              "spread_impact_reserve_usd": sleeve.reserve, "fills": sleeve.fills}
                    for symbol, sleeve in sleeves.items()},
    }


def run(root):
    report = {"initial_usd": START_CAPITAL, "fee_bps_per_fill": FEE_BPS, "venues": {}}
    for venue in ("coinbase", "kraken"):
        bars = {}
        sources = {}
        for symbol in SYMBOLS:
            bars[symbol], sources[symbol] = load(root, venue, symbol)
        orders = {symbol: signals(bars[symbol]) for symbol in SYMBOLS}
        windows = COINBASE_WINDOWS if venue == "coinbase" else {
            "cross_venue_check": (str(KRAKEN_START), str(TERMINAL_DAY)),
        }
        results = {}
        for name, bounds in windows.items():
            start, end = map(date.fromisoformat, bounds)
            results[name] = {"start": str(start), "end_open": str(end), "cash_usd": START_CAPITAL,
                             "cash_hurdle_usd": START_CAPITAL * (1 + CASH_HURDLE) ** ((end - start).days / 365)}
            for reserve in (BASE_RESERVE_BPS, STRESS_RESERVE_BPS):
                results[name][str(reserve)] = {
                    "breakout": replay(bars, orders, start, end, reserve),
                    "buy_hold": replay(bars, orders, start, end, reserve, buy_hold=True),
                }
        report["venues"][venue] = {"sources": sources, "windows": results}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.data)
    else:
        if not args.output:
            parser.error("run requires --output")
        result = run(args.data)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as output:
            json.dump(result, output, indent=2, allow_nan=False)
            output.write("\n")


if __name__ == "__main__":
    main()
