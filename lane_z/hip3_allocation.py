"""Replay a frozen equity and gold funding-cost gate on trade[XYZ] perps."""

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean
from urllib.request import Request, urlopen

from .nasdaq_trend import american_date, load as load_qqq
from .spot_carry import BAR, DAY, HOUR, PublicArchive, epoch, stamp


MARKETS = {"equity": "xyz:XYZ100", "gold": "xyz:GOLD"}
VINTAGE_DATES = ("2026-01-05", "2026-04-06", "2026-07-06", "2026-11-20")
VINTAGES = tuple(epoch(day) + 16 * HOUR for day in VINTAGE_DATES)
WARMUP = epoch("2025-12-28")
CAPITAL = 100_000.0
WEEK = 7 * DAY
FUNDING_BUDGET = 0.04
POSITION_WEIGHT = 0.35
TAKER_FEE = 0.0009
MIN_ORDER_USD = 10.0
GLD = "nasdaq-gld-historical.json"
GLD_URL = ("https://api.nasdaq.com/api/quote/GLD/historical?assetclass=etf"
           "&fromdate=2026-01-05&todate=2026-11-20&limit=500")


def funding_files(asset):
    for start in range(WARMUP, VINTAGES[-1] + HOUR, 20 * DAY):
        stop = min(start + 20 * DAY, VINTAGES[-1] + HOUR)
        key = f"{asset}-funding-{stamp(start)[:10]}.json"
        yield key, start, stop


def fetch(root):
    archive = PublicArchive(root)
    archive.get("xyz-meta.json", {"type": "meta", "dex": "xyz"}, 20)
    for name, coin in MARKETS.items():
        archive.get(f"{name}-4h.json", {"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "4h", "startTime": WARMUP, "endTime": VINTAGES[-1],
        }}, 20 + math.ceil((VINTAGES[-1] - WARMUP) / BAR / 60))
        for key, start, stop in funding_files(name):
            archive.get(key, {"type": "fundingHistory", "coin": coin,
                              "startTime": start, "endTime": stop - 1},
                        20 + math.ceil((stop - start) / HOUR / 20))
    gld = root / GLD
    if not gld.exists():
        request = Request(GLD_URL, headers={
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
            "Accept": "application/json", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/",
        })
        with urlopen(request, timeout=30) as response:
            payload = response.read()
        json.loads(payload)
        gld.write_bytes(payload)


@dataclass(frozen=True)
class Market:
    precision: int
    opening: dict
    close: dict
    funding: dict


class Archive:
    def __init__(self, root):
        self.root = root
        self.audit = {}

    def read(self, name):
        payload = (self.root / name).read_bytes()
        self.audit[name] = sha256(payload).hexdigest()
        return json.loads(payload)

    def load_markets(self):
        metadata = self.read("xyz-meta.json")
        precision = {row["name"]: row["szDecimals"] for row in metadata["universe"]}
        markets = {}
        first_bar, last_bar = WARMUP, VINTAGES[-1]
        for name, coin in MARKETS.items():
            opening, close, funding = {}, {}, {}
            for row in self.read(f"{name}-4h.json"):
                ts = row["t"]
                values = [float(row[field]) for field in ("o", "c", "l", "h")]
                first, last, low, high = values
                if (row["s"] != coin or row["i"] != "4h" or ts in opening or row["T"] != ts + BAR - 1
                        or not all(math.isfinite(value) for value in values)
                        or not 0 < low <= min(first, last) <= max(first, last) <= high or row["n"] < 1):
                    raise ValueError(f"Invalid {coin} price candle at {stamp(ts)}")
                opening[ts], close[ts] = first, last
            if opening.keys() != set(range(first_bar, last_bar + 1, BAR)):
                raise ValueError(f"Missing {coin} four-hour candles")
            for key, first, stop in funding_files(name):
                for row in self.read(key):
                    ts = row["time"] // HOUR * HOUR
                    rate = float(row["fundingRate"])
                    if (row["coin"] != coin or ts in funding or not first <= ts < stop
                            or row["time"] - ts > 60_000 or not math.isfinite(rate) or abs(rate) > 0.04):
                        raise ValueError(f"Invalid {coin} funding event at {stamp(ts)}")
                    funding[ts] = rate
            if funding.keys() != set(range(first_bar, last_bar + 1, HOUR)):
                raise ValueError(f"Incomplete {coin} hourly funding history")
            markets[name] = Market(precision[coin], opening, close, funding)
        return markets


def funding_gate(market, hour):
    history = range(hour - WEEK, hour, HOUR)
    rate = math.fsum(market.funding[past] for past in history) * (365.25 / 7)
    return rate, rate <= FUNDING_BUDGET


def max_drawdown(equity):
    peak = CAPITAL
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak)
    return worst


def replay(markets, start, end, gated, reserve_bps, delay_hours, funding_error):
    positions = {name: 0.0 for name in markets}
    funding_paid = {name: 0.0 for name in markets}
    cash, fee_paid, spread_paid = CAPITAL, 0.0, 0.0
    equity, gross_weights, orders, decisions = [], [], [], {}

    def trade(hour, name, target, price):
        nonlocal cash, fee_paid, spread_paid
        change = target - positions[name]
        if not change or (target > 0 and abs(change) * price < MIN_ORDER_USD):
            return
        cost = abs(change) * price * reserve_bps / 10_000
        fee = (abs(change) * price + math.copysign(cost, change)) * TAKER_FEE
        cash -= change * price + cost + fee
        fee_paid += fee
        spread_paid += cost
        positions[name] = target
        orders.append({"hour": stamp(hour), "market": name, "change": change, "price": price})

    for hour in range(start, end + 1, HOUR):
        prior_candle = hour // BAR * BAR - BAR
        mark = {name: market.close[prior_candle] for name, market in markets.items()}
        for name, market in markets.items():
            debit = positions[name] * mark[name] * market.funding[hour]
            debit += abs(debit) * funding_error
            cash -= debit
            funding_paid[name] += debit

        decision = hour - delay_hours * HOUR
        if hour == end:
            for name, market in markets.items():
                trade(hour, name, 0.0, market.opening[hour])
        elif decision >= start and (decision - start) % WEEK == 0:
            prices = {name: market.opening[hour] for name, market in markets.items()}
            balance = cash + sum(positions[name] * prices[name] for name in markets)
            signals = {name: funding_gate(market, decision) for name, market in markets.items()}
            decisions[stamp(decision)] = {name: {"funding_annualized": rate, "active": active}
                                          for name, (rate, active) in signals.items()}
            target = {}
            for name, market in markets.items():
                precision = 10 ** market.precision
                exposure = POSITION_WEIGHT * balance if not gated or signals[name][1] else 0
                target[name] = math.floor(precision * exposure / prices[name]) / precision
            for name in sorted(markets, key=lambda key: target[key] - positions[key]):
                trade(hour, name, target[name], prices[name])

        value = sum(mark[name] * amount for name, amount in positions.items())
        equity.append(cash + value)
        if cash < 0 or equity[-1] <= 0:
            raise ValueError(f"Fully funded allocation violated at {stamp(hour)}")
        gross_weights.append(value / equity[-1])

    return {"pnl_usd": cash - CAPITAL, "maximum_drawdown": max_drawdown(equity),
            "mean_gross_exposure": mean(gross_weights), "peak_gross_exposure": max(gross_weights),
            "funding_paid_usd": funding_paid, "fees_usd": fee_paid, "slippage_usd": spread_paid,
            "approved_sleeve_weeks": {name: sum(1 for signals in decisions.values() if signals[name]["active"])
                                      for name in markets},
            "scheduled_weeks": len(decisions), "orders": orders, "decisions": decisions}


def passive_benchmark(qqq, distributions, gld, start, end):
    first, last = stamp(start)[:10], stamp(end)[:10]
    qqq = [bar for bar in qqq if first <= bar.day.isoformat() <= last]
    if (not qqq or qqq[0].day.isoformat() != first or qqq[-1].day.isoformat() != last
            or any(bar.day not in gld for bar in qqq)):
        raise ValueError("ETF benchmark does not have the same start/end trading dates")

    initial = {"QQQ": qqq[0].opening, "GLD": gld[qqq[0].day][0]}
    shares = {name: math.floor(CAPITAL * POSITION_WEIGHT / (price * 1.0001)) for name, price in initial.items()}
    reserve = CAPITAL - sum(shares[name] * price * 1.0001 for name, price in initial.items())
    equity, paid = [], 0.0
    for bar in qqq:
        if bar.day in distributions and bar.day > qqq[0].day:
            paid += shares["QQQ"] * float(distributions[bar.day].amount)
        mark = shares["QQQ"] * bar.close + shares["GLD"] * gld[bar.day][1]
        equity.append(reserve + paid + mark)
    exit_cost = mark * (1 - 0.9999 * 0.99997)
    equity[-1] -= exit_cost
    return {"pnl_usd": equity[-1] - CAPITAL, "maximum_drawdown": max_drawdown(equity), "qqq_distributions_usd": paid}


def run(root, qqq_root):
    archive = Archive(root)
    markets = archive.load_markets()
    qqq, distributions, qqq_audit = load_qqq(qqq_root)
    gld_data = archive.read(GLD)["data"]
    gld = {american_date(row["date"]): (float(row["open"]), float(row["close"]))
           for row in gld_data["tradesTable"]["rows"]}
    if (gld_data["symbol"] != "GLD" or gld_data["totalRecords"] != len(gld)
            or any(not (opening > 0 and close > 0) for opening, close in gld.values())):
        raise ValueError("Unexpected Nasdaq GLD history")
    result = {"vintages": [], "data": archive.audit, "qqq_data": qqq_audit,
              "coverage": {name: {"candles": len(market.opening), "funding_hours": len(market.funding)}
                           for name, market in markets.items()}}
    for start, end in zip(VINTAGES, VINTAGES[1:]):
        case = {"start": stamp(start), "end": stamp(end),
                "cash_pnl_usd": CAPITAL * ((1 + FUNDING_BUDGET) ** ((end - start) / DAY / 365.25) - 1),
                "passive_etfs": passive_benchmark(qqq, distributions, gld, start, end)}
        for label, reserve_bps, delay_hours, funding_error in (("base", 3, 0, 0), ("stress", 15, 4, 0.10)):
            case[label] = {"gated": replay(markets, start, end, True, reserve_bps, delay_hours, funding_error),
                           "always_on": replay(markets, start, end, False, reserve_bps, delay_hours, funding_error)}
        result["vintages"].append(case)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/agent-hip3-allocation"))
    parser.add_argument("--qqq-data", type=Path, default=Path("data/agent-nasdaq"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "fetch":
        fetch(args.data)
    else:
        payload = json.dumps(run(args.data, args.qqq_data), indent=2, default=str) + "\n"
        if args.output:
            args.output.write_text(payload)
        else:
            print(payload, end="")


if __name__ == "__main__":
    main()
