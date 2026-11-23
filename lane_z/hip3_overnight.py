"""Replay the fixed fully funded XYZ100 overnight schedule and daytime controls."""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

from .nasdaq_trend import load as load_qqq
from .spot_carry import DAY, HOUR, PublicArchive, stamp


MARKET = "xyz:XYZ100"
NY = ZoneInfo("America/New_York")
DATES = tuple(date.fromisoformat(value) for value in ("2026-05-04", "2026-07-06", "2026-09-08", "2026-11-20"))
CAPITAL = 10_000.0
ALLOCATION = 0.98
OPEN, FUNDING_CLOSE = "xyz100-hourly.json", "xyz100-funding-final.json"


def ny_hour(day, hour):
    return int(datetime.combine(day, time(hour), tzinfo=NY).timestamp() * 1000)


FIRST, LAST = ny_hour(DATES[0], 0), ny_hour(DATES[-1], 16)


@dataclass(frozen=True)
class Scenario:
    buy_hour: int
    sell_hour: int
    taker_bps: float
    slippage_bps: float
    funding: bool = True
    funding_stress: float = 0.0


SCENARIOS = {
    "gross": Scenario(18, 9, 0, 0, False),
    "only_grow_fee": Scenario(18, 9, 0.9, 0),
    "base": Scenario(18, 9, 0.9, 2),
    "stress": Scenario(19, 8, 0.9, 7.5, True, 0.10),
    "standard_fee": Scenario(18, 9, 9, 2),
    "daytime": Scenario(10, 16, 0.9, 2),
    "european_open_diagnostic": Scenario(2, 3, 0.9, 2),
}


def fetch(root):
    archive = PublicArchive(root)
    archive.get(OPEN, {"type": "candleSnapshot", "req": {
        "coin": MARKET, "interval": "1h", "startTime": FIRST, "endTime": LAST,
    }}, 101)
    archive.get(FUNDING_CLOSE, {"type": "fundingHistory", "coin": MARKET,
                               "startTime": LAST - 4 * HOUR, "endTime": LAST + HOUR - 1}, 21)


def load(root, funding_data):
    sources = {}

    def read(path):
        payload = path.read_bytes()
        sources[path.name] = sha256(payload).hexdigest()
        return json.loads(payload)

    opening, close, volume = {}, {}, {}
    for row in read(root / OPEN):
        hour = row["t"]
        start, end, high, low, size = [float(row[key]) for key in ("o", "c", "h", "l", "v")]
        if (row["s"] != MARKET or row["i"] != "1h" or hour in opening or row["T"] != hour + HOUR - 1
                or not all(math.isfinite(value) for value in (start, end, high, low, size))
                or not 0 < low <= min(start, end) <= max(start, end) <= high or size <= 0):
            raise ValueError(f"Invalid {MARKET} bar at {stamp(hour)}")
        opening[hour], close[hour], volume[hour] = start, end, size
    grid = set(range(FIRST, LAST + 1, HOUR))
    if opening.keys() != grid:
        raise ValueError("The 5,000-bar API cap or missing trades truncated the frozen hourly price sample")

    rates = {}
    funding_files = sorted(funding_data.glob("equity-funding-*.json"))
    for path in [*funding_files, root / FUNDING_CLOSE]:
        for row in read(path):
            hour = row["time"] // HOUR * HOUR
            if hour not in grid:
                continue
            rate = float(row["fundingRate"])
            if row["coin"] != MARKET or hour in rates or not math.isfinite(rate) or abs(rate) > 0.04:
                raise ValueError(f"Invalid {MARKET} funding at {stamp(hour)}")
            rates[hour] = rate
    if rates.keys() != grid:
        raise ValueError("Incomplete funding throughout the hourly price sample")
    asset = next(row for row in read(funding_data / "xyz-meta.json")["universe"] if row["name"] == MARKET)
    return opening, close, volume, rates, asset, sources


def portfolio(round_trips, opening, close, volume, rates, size_precision, scenario):
    cash, peak, worst = CAPITAL, CAPITAL, 0.0
    lot_precision = 10 ** size_precision
    spread = scenario.slippage_bps / 10_000
    fee = scenario.taker_bps / 10_000
    trades = []
    for buy_hour, sell_hour in round_trips:
        start_cash = cash
        entry_reference, exit_reference = opening[buy_hour], opening[sell_hour]
        entry, exit = entry_reference * (1 + spread), exit_reference * (1 - spread)
        quantity = math.floor(cash * ALLOCATION * lot_precision / (entry * (1 + fee))) / lot_precision
        cash -= quantity * entry * (1 + fee)
        paid = 0.0
        for hour in range(buy_hour + HOUR, sell_hour + 1, HOUR):
            if scenario.funding:
                debit = quantity * close[hour - HOUR] * rates[hour]
                debit += abs(debit) * scenario.funding_stress
                cash -= debit
                paid += debit
            equity = cash + quantity * close[hour - HOUR]
            peak = max(peak, equity)
            worst = max(worst, 1 - equity / peak)
        cash += quantity * exit * (1 - fee)
        peak = max(peak, cash)
        worst = max(worst, 1 - cash / peak)
        net = cash - start_cash
        price_pnl = quantity * (exit_reference - entry_reference)
        trades.append({"entry": stamp(buy_hour), "exit": stamp(sell_hour), "quantity": quantity,
                       "gross_price_bps": (exit_reference / entry_reference - 1) * 10_000,
                       "net_equity_bps": net / start_cash * 10_000,
                       "modeled_cost_bps_on_equity": (price_pnl - net) / start_cash * 10_000,
                       "gross_price_pnl_usd": price_pnl, "net_pnl_usd": net, "funding_paid_usd": paid,
                       "fees_usd": quantity * (entry + exit) * fee,
                       "slippage_usd": quantity * (entry_reference + exit_reference) * spread,
                       "execution_hour_volume_fraction": max(quantity / volume[buy_hour], quantity / volume[sell_hour])})

    return {"pnl_usd": cash - CAPITAL, "maximum_drawdown": worst, "round_trips": len(trades),
            "wins": sum(trade["net_pnl_usd"] > 0 for trade in trades),
            "mean_gross_price_bps": mean(trade["gross_price_bps"] for trade in trades),
            "mean_net_equity_bps": mean(trade["net_equity_bps"] for trade in trades),
            "median_net_equity_bps": median(trade["net_equity_bps"] for trade in trades),
            "mean_modeled_cost_bps_on_equity": mean(trade["modeled_cost_bps_on_equity"] for trade in trades),
            "median_hour_volume_fraction": median(trade["execution_hour_volume_fraction"] for trade in trades),
            "max_hour_volume_fraction": max(trade["execution_hour_volume_fraction"] for trade in trades),
            "trades": trades}


def calendar_pairs(trading_days, start, end):
    return [(day, day + timedelta(days=1)) for day in sorted(trading_days)
            if start <= day < end and day.weekday() < 4 and day + timedelta(days=1) in trading_days]


def run(root, qqq_data, funding_data):
    opening, close, volume, rates, asset, sources = load(root, funding_data)
    qqq, _, qqq_audit = load_qqq(qqq_data)
    trading_days = {row.day for row in qqq}
    result = {"data": sources, "calendar_data": qqq_audit["sources"], "hours": len(rates),
              "asset_fee_metadata": asset, "vintages": []}
    for start, end in zip(DATES, DATES[1:]):
        days = calendar_pairs(trading_days, start, end)
        boundary = (ny_hour(start, 16), ny_hour(end, 16))
        cash = CAPITAL * (1.04 ** ((boundary[1] - boundary[0]) / DAY / 365.25) - 1)
        out = {"start": start.isoformat(), "end": end.isoformat(), "cash_4_percent_pnl_usd": cash}
        for policy, scenario in SCENARIOS.items():
            intraday = policy in {"daytime", "european_open_diagnostic"}
            trips = [(ny_hour(exit if intraday else entry, scenario.buy_hour), ny_hour(exit, scenario.sell_hour))
                     for entry, exit in days]
            out[policy] = portfolio(trips, opening, close, volume, rates, asset["szDecimals"], scenario)
        out["continuous"] = portfolio([boundary], opening, close, volume, rates, asset["szDecimals"], SCENARIOS["base"])
        result["vintages"].append(out)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/agent-hip3-overnight"))
    parser.add_argument("--qqq-data", type=Path, default=Path("data/agent-nasdaq"))
    parser.add_argument("--funding-data", type=Path, default=Path("data/agent-hip3-allocation"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "fetch":
        fetch(args.data)
    else:
        report = json.dumps(run(args.data, args.qqq_data, args.funding_data), indent=2) + "\n"
        if args.output:
            args.output.write_text(report)
        else:
            print(report, end="")


if __name__ == "__main__":
    main()
