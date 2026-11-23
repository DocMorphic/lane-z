"""Paper-replay a same-share convergence screen in two Hyperliquid HIP-3 books."""

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, median

from .hip3_overnight import ny_hour
from .nasdaq_trend import load as load_qqq
from .spot_carry import DAY, HOUR, PublicArchive, epoch, stamp


DEXES = ("xyz", "io")
DATES = tuple(date.fromisoformat(value) for value in ("2026-08-24", "2026-09-21", "2026-10-19", "2026-11-19"))
FIRST, LAST = epoch(str(DATES[0])), epoch(str(DATES[-1]))
CAPITAL = 10_000.0
SIZE_WEIGHT = 0.4
MIN_TRADE_NOTIONAL = 10.0
MIN_SIGNAL_PRINTS = 30
MIN_BASIS_BPS = 50


@dataclass(frozen=True)
class Bar:
    opening: float
    close: float
    low: float
    high: float
    volume: float
    prints: int


@dataclass(frozen=True)
class Scenario:
    xyz_entry: int
    io_entry: int
    io_exit: int
    fee: float
    reserve: float


SCENARIOS = {
    "base": Scenario(12, 12, 15, 0.00009, 0.0004),
    "stress_costs": Scenario(12, 12, 15, 0.0009, 0.001),
    "stress_asynchronous": Scenario(12, 13, 16, 0.00009, 0.0004),
    "same_hour_diagnostic": Scenario(11, 11, 15, 0.00009, 0.0004),
}


def funding_pages(dex):
    for start in range(FIRST, LAST + 1, 20 * DAY):
        yield f"{dex}-funding-{stamp(start)[:10]}.json", start, min(start + 20 * DAY, LAST + HOUR) - 1


def fetch(root):
    archive = PublicArchive(root)
    for dex in DEXES:
        coin = f"{dex}:SNDK"
        archive.get(f"{dex}-meta.json", {"type": "meta", "dex": dex}, 20)
        archive.get(f"{dex}-1h.json", {"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "1h", "startTime": FIRST, "endTime": LAST,
        }}, 62)
        for name, start, stop in funding_pages(dex):
            archive.get(name, {"type": "fundingHistory", "coin": coin, "startTime": start, "endTime": stop}, 44)


def load(root):
    sources = {}

    def read(filename):
        payload = (root / filename).read_bytes()
        sources[filename] = sha256(payload).hexdigest()
        return json.loads(payload)

    bars, funding, metadata = {}, {}, {}
    exact = set(range(FIRST, LAST + 1, HOUR))
    for dex in DEXES:
        coin = f"{dex}:SNDK"
        market = read(f"{dex}-meta.json")
        metadata[dex] = next(row for row in market["universe"] if row["name"] == coin)
        if market["collateralToken"] != 0 or metadata[dex].get("isDelisted"):
            raise ValueError(f"{coin} no longer matches the USDC contract assumption")
        bars[dex], funding[dex] = {}, {}
        for row in read(f"{dex}-1h.json"):
            hour = row["t"]
            values = [float(row[key]) for key in ("o", "c", "l", "h", "v")]
            first, last, low, high, volume = values
            if (row["s"] != coin or row["i"] != "1h" or hour in bars[dex] or row["T"] != hour + HOUR - 1
                    or not all(math.isfinite(value) for value in values)
                    or not 0 < low <= min(first, last) <= max(first, last) <= high or volume <= 0):
                raise ValueError(f"Invalid {coin} candle at {stamp(hour)}")
            bars[dex][hour] = Bar(*values, row["n"])
        for filename, _, _ in funding_pages(dex):
            for row in read(filename):
                hour = row["time"] // HOUR * HOUR
                rate = float(row["fundingRate"])
                if row["coin"] != coin or hour in funding[dex] or not math.isfinite(rate) or abs(rate) > 0.04:
                    raise ValueError(f"Invalid {coin} funding at {stamp(hour)}")
                funding[dex][hour] = rate
        if bars[dex].keys() != exact or funding[dex].keys() != exact:
            raise ValueError(f"Incomplete {coin} price or funding data")
    return bars, funding, metadata, sources


def replay(bars, funding, signals, scenario, precision):
    capital, peak, drawdown = CAPITAL, CAPITAL, 0.0
    trades = []
    for day, signal in signals.items():
        times = {"xyz": (ny_hour(day, scenario.xyz_entry), ny_hour(day, 15)),
                 "io": (ny_hour(day, scenario.io_entry), ny_hour(day, scenario.io_exit))}
        first = min(entry for entry, _ in times.values())
        latest = max(exit for _, exit in times.values())
        available_reference = max(bars[dex][first].opening for dex in DEXES)
        factor = 10 ** precision
        size = math.floor(SIZE_WEIGHT * capital * factor / available_reference) / factor
        if any(size * bars[dex][first].opening < MIN_TRADE_NOTIONAL for dex in DEXES):
            continue

        sign = math.copysign(1, signal)
        directions = {"xyz": sign, "io": -sign}
        fee_total, spread_total, gross, funding_total = 0.0, 0.0, 0.0, 0.0
        position_value = {dex: 0.0 for dex in DEXES}
        isolated_costs = {dex: 0.0 for dex in DEXES}
        last_capital = capital
        participation = []
        for hour in range(first, latest + 1, HOUR):
            for dex, (start, end) in times.items():
                amount = size * directions[dex]
                if start < hour <= end:
                    paid = amount * bars[dex][hour - HOUR].close * funding[dex][hour]
                    funding_total += paid
                    capital -= paid
                    isolated_costs[dex] += paid
                if start <= hour <= end:
                    anchor = bars[dex][start].opening
                    mark = bars[dex][hour].opening
                    position_value[dex] = amount * (mark - anchor)
                if hour in (start, end):
                    change = amount if hour == start else -amount
                    reference = bars[dex][hour].opening
                    adverse = reference * math.copysign(scenario.reserve, change)
                    cost = abs(change) * abs(adverse)
                    fee = abs(change) * (reference + adverse) * scenario.fee
                    participation.append(size / bars[dex][hour].volume)
                    spread_total += cost
                    fee_total += fee
                    capital -= cost + fee
                    isolated_costs[dex] += cost + fee
                if start <= hour < end:
                    bar = bars[dex][hour]
                    anchor = bars[dex][start].opening
                    adverse_mark = bar.low if amount > 0 else bar.high
                    balance = size * anchor + amount * (adverse_mark - anchor) - isolated_costs[dex]
                    if balance < 0.05 * size * adverse_mark:
                        raise ValueError(f"Modeled maintenance breach in {dex} at {stamp(hour)}")
                if hour == end:
                    gross += position_value[dex]
                    capital += position_value[dex]
                    position_value[dex] = 0.0
            equity = capital + sum(position_value.values())
            peak = max(peak, equity)
            drawdown = max(drawdown, 1 - equity / peak)
        if capital <= 0:
            raise ValueError(f"Cross-DEX portfolio lost all equity on {day}")
        trades.append({"day": str(day), "signal_bps": signal, "shares_each": size,
                       "price_pnl_usd": gross, "funding_paid_usd": funding_total,
                       "execution_reserve_usd": spread_total, "fees_usd": fee_total, "pnl_usd": capital - last_capital,
                       "max_hour_volume_fraction": max(participation)})
    return {"pnl_usd": capital - CAPITAL, "trades": len(trades), "maximum_drawdown": drawdown,
            "winners": sum(row["pnl_usd"] > 0 for row in trades),
            "gross_price_pnl_usd": sum(row["price_pnl_usd"] for row in trades),
            "mean_signal_bps": mean(abs(row["signal_bps"]) for row in trades) if trades else None,
            "round_trips": trades}


def run(root, qqq_root):
    bars, funding, metadata, files = load(root)
    qqq, _, qqq_audit = load_qqq(qqq_root)
    calendar = {row.day for row in qqq}
    precision = min(metadata[dex]["szDecimals"] for dex in DEXES)
    result = {"sources": files, "calendar": qqq_audit["sources"], "markets": metadata,
              "hours": len(bars["xyz"]), "vintages": []}
    for start, stop in zip(DATES, DATES[1:]):
        days = [day for day in sorted(calendar) if start <= day < stop]
        signals = {}
        qualified = []
        for day in days:
            # A multi-fold unit mismatch is not an intraday basis signal.
            when = ny_hour(day, 10)
            prior = {dex: bars[dex][when] for dex in DEXES}
            scale = prior["io"].close / prior["xyz"].close
            if not 0.5 < scale < 2:
                raise ValueError(f"Share-price units differ across books on {day}")
            if min(bar.prints for bar in prior.values()) < MIN_SIGNAL_PRINTS:
                continue
            basis = math.log(scale) * 10_000
            qualified.append(abs(basis))
            if abs(basis) >= MIN_BASIS_BPS:
                signals[day] = basis
        case = {"start": str(start), "end_inclusive": str(stop - timedelta(days=1)), "regular_sessions": len(days),
                "qualified_sessions": len(qualified),
                "median_absolute_basis_bps": median(qualified) if qualified else None,
                "maximum_absolute_basis_bps": max(qualified, default=None),
                "cash_pnl_usd": CAPITAL * (1.04 ** ((stop - start).days / 365.25) - 1)}
        for key, scenario in SCENARIOS.items():
            case[key] = replay(bars, funding, signals, scenario, precision)
        result["vintages"].append(case)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/agent-hip3-cross-dex"))
    parser.add_argument("--qqq-data", type=Path, default=Path("data/agent-nasdaq"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "fetch":
        fetch(args.data)
    else:
        report = json.dumps(run(args.data, args.qqq_data), indent=2, default=str) + "\n"
        if args.output:
            args.output.write_text(report)
        else:
            print(report, end="")


if __name__ == "__main__":
    main()
