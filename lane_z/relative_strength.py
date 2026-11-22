"""Paper-test relative strength on hourly Trade[XYZ] equity perpetuals."""

import argparse
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


INFO_URL = "https://api.hyperliquid.xyz/info"
MARKETS = tuple(f"xyz:{name}" for name in (
    "AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "TSLA",
))
HOUR_MS = 3_600_000
HOLD_HOURS = 24
LOOKBACK_HOURS = 7 * 24
DATA_FIRST = date(2026, 5, 11)
DATA_STOP = date(2026, 11, 21)
PERIODS = {
    "development": (date(2026, 5, 18), date(2026, 8, 28)),
    "test": (date(2026, 8, 31), date(2026, 10, 9)),
    "confirmation": (date(2026, 10, 12), date(2026, 11, 20)),
}
TARGET_PER_LEG = 200
CAPITAL = 1_500
CASH_RATE = 0.04
GROWTH_TAKER_BPS = 0.9
STANDARD_TAKER_BPS = 9
FUNDING_PAGE_HOURS = 480
RATE_LIMIT_WEIGHT_PER_MIN = 420


def timestamp(day, hour=0):
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp()) * 1000


def market_path(root, coin, kind):
    return root / f"{coin.replace(':', '-')}-{kind}.json"


class InfoCache:
    def __init__(self):
        self.ready = time.monotonic()

    def request(self, path, payload, weight):
        if path.exists():
            return
        request = Request(INFO_URL, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        for attempt in range(4):
            time.sleep(max(0, self.ready - time.monotonic()))
            self.ready = time.monotonic() + 60 * weight / RATE_LIMIT_WEIGHT_PER_MIN
            try:
                with urlopen(request, timeout=45) as response:
                    data = json.load(response)
                break
            except (HTTPError, URLError) as exc:
                if attempt == 3 or (isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504)):
                    raise
                if isinstance(exc, HTTPError) and exc.code == 429:
                    self.ready += 60
                else:
                    self.ready += 5 * (attempt + 1)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"request": payload, "data": data}, separators=(",", ":")) + "\n")
        temporary.replace(path)


def funding_requests(coin):
    start = timestamp(PERIODS["development"][0])
    stop = timestamp(DATA_STOP)
    page = FUNDING_PAGE_HOURS * HOUR_MS
    for index, chunk in enumerate(range(start, stop, page)):
        yield index, {
            "type": "fundingHistory", "coin": coin, "startTime": chunk,
            "endTime": min(chunk + page, stop) - 1,
        }


def fetch(root):
    root.mkdir(parents=True, exist_ok=True)
    api = InfoCache()
    api.request(root / "xyz-meta.json", {"type": "meta", "dex": "xyz"}, 20)
    first, stop = timestamp(DATA_FIRST), timestamp(DATA_STOP)
    count = (stop - first) // HOUR_MS
    for coin in MARKETS:
        api.request(market_path(root, coin, "candles"), {"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "1h", "startTime": first, "endTime": stop - 1,
        }}, 20 + math.ceil(count / 60))
        print(f"Cached {coin} candles", flush=True)
    for coin in MARKETS:
        for index, request in funding_requests(coin):
            api.request(market_path(root, coin, f"funding-{index:02}"), request,
                        20 + math.ceil(FUNDING_PAGE_HOURS / 20))
        print(f"Cached {coin} funding", flush=True)


def source(path, expected):
    raw = path.read_bytes()
    item = json.loads(raw)
    if item["request"] != expected:
        raise ValueError(f"{path} has an unexpected request")
    return item["data"], {"file": path.name, "request": expected, "sha256": hashlib.sha256(raw).hexdigest()}


@dataclass(frozen=True)
class Candle:
    opening: float
    close: float
    volume: float


def candles(root, coin):
    request = {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1h", "startTime": timestamp(DATA_FIRST),
        "endTime": timestamp(DATA_STOP) - 1,
    }}
    records, audit = source(market_path(root, coin, "candles"), request)
    result = {}
    for record in records:
        clock = record["t"]
        if (record["s"] != coin or record["i"] != "1h" or clock % HOUR_MS or clock in result
                or record["T"] != clock + HOUR_MS - 1):
            raise ValueError(f"unexpected candle for {coin} at {clock}")
        opening, close, volume = (float(record[key]) for key in ("o", "c", "v"))
        if not all(math.isfinite(value) for value in (opening, close, volume)) or min(opening, close) <= 0 or volume < 0:
            raise ValueError(f"invalid candle values for {coin} at {clock}")
        if (record["n"] > 0) != (volume > 0):
            raise ValueError(f"inconsistent candle liquidity for {coin} at {clock}")
        if clock + HOUR_MS <= timestamp(DATA_STOP):
            result[clock] = Candle(opening, close, volume)
    return result, {**audit, "candles": len(result), "zero_volume_bars": sum(not bar.volume for bar in result.values())}


def historic_funding(root, coin):
    result = {}
    audit = []
    for index, request in funding_requests(coin):
        records, entry = source(market_path(root, coin, f"funding-{index:02}"), request)
        audit.append({**entry, "rows": len(records)})
        for record in records:
            clock = record["time"]
            hour = clock - clock % HOUR_MS
            value = float(record["fundingRate"])
            if (record["coin"] != coin or not request["startTime"] <= clock <= request["endTime"]
                    or hour in result or not math.isfinite(value)):
                raise ValueError(f"unexpected funding for {coin} at {clock}")
            result[hour] = value
    return result, audit


def fee_for_market(metadata):
    if metadata.get("growthMode") != "enabled" or not metadata.get("lastGrowthModeChangeTime"):
        return STANDARD_TAKER_BPS
    changed = datetime.fromisoformat(metadata["lastGrowthModeChangeTime"])
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    changed_before_study = changed.timestamp() * 1000 <= timestamp(PERIODS["development"][0])
    return GROWTH_TAKER_BPS if changed_before_study else STANDARD_TAKER_BPS


def decision_times(first, last):
    day = first
    while day <= last:
        if day.weekday() < 4:
            yield day, timestamp(day, hour=16)
        day += timedelta(days=1)


def ranks(by_coin, decision):
    output = []
    current = decision - HOUR_MS
    past = current - LOOKBACK_HOURS * HOUR_MS
    for coin, bars in by_coin.items():
        recent = [bars.get(t) for t in range(decision - 24 * HOUR_MS, decision, HOUR_MS)]
        if past not in bars or not bars[past].volume or any(bar is None or not bar.volume for bar in recent):
            continue
        volumes = sorted(bar.close * bar.volume for bar in recent)
        median = (volumes[11] + volumes[12]) / 2
        if median >= 20_000:
            output.append((bars[current].close / bars[past].close - 1, coin))
    return sorted(output)


@dataclass(frozen=True)
class Scenario:
    delay_hours: int
    slip_bps: int
    standard_fee: bool = False
    leg_usd: int = TARGET_PER_LEG


SCENARIOS = {
    "base": Scenario(1, 5),
    "cost_stress": Scenario(1, 15, standard_fee=True),
    "timing_stress": Scenario(2, 5),
    "small_cost_stress": Scenario(1, 15, standard_fee=True, leg_usd=25),
}


def leg(bars, rates, start, stop, qty, fee_bps, slip_bps):
    try:
        if not bars[start].volume or not bars[stop].volume:
            raise ValueError("entry or exit bar has no trade")
        raw_entry, raw_exit = bars[start].opening, bars[stop].opening
        hours = range(start, stop + HOUR_MS, HOUR_MS)
        debits = {t: -qty * bars[t - HOUR_MS].close * rates[t] for t in hours}
        closes = [bars[t].close for t in range(start, stop, HOUR_MS)]
    except KeyError as exc:
        raise ValueError(f"missing executable proxy or funding at {exc.args[0]}") from exc
    entry_slip = abs(qty) * raw_entry * slip_bps * 1e-4
    exit_slip = abs(qty) * raw_exit * slip_bps * 1e-4
    entry_fee = (abs(qty) * raw_entry + math.copysign(entry_slip, qty)) * fee_bps * 1e-4
    exit_fee = (abs(qty) * raw_exit - math.copysign(exit_slip, qty)) * fee_bps * 1e-4
    debits[start] = min(debits[start], 0)
    debits[stop] = min(debits[stop], 0)
    funding = sum(debits.values())
    gross = qty * (raw_exit - raw_entry)
    fees = entry_fee + exit_fee
    slip = entry_slip + exit_slip
    net = gross + funding - fees - slip
    path = [-entry_fee - entry_slip + debits[start]]
    accrued = 0
    for hour, close in enumerate(closes):
        accrued += debits[start + hour * HOUR_MS]
        path.append(qty * (close - raw_entry) + accrued - entry_fee - entry_slip)
    path.append(net)
    return {"quantity": qty, "gross_usd": gross, "funding_usd": funding, "fees_usd": fees,
            "allowance_usd": slip, "net_usd": net, "entry_notional": abs(qty) * raw_entry}, path


def portfolio(day, decision, ranking, candles_by_market, rates_by_market, metadata, strategy, scenario):
    leaders = [coin for _, coin in ranking[-3:]]
    laggards = [coin for _, coin in ranking[:3]]
    directions = {"relative_strength": (1, -1), "inverted": (-1, 1), "selected_equal_long": (1, 1)}
    start = decision + scenario.delay_hours * HOUR_MS
    stop = start + HOLD_HOURS * HOUR_MS
    legs, paths = {}, []
    for group, side in zip((leaders, laggards), directions[strategy], strict=True):
        for coin in group:
            bars, market = candles_by_market[coin], metadata[coin]
            precision = 10 ** market["szDecimals"]
            reference = Decimal(str(bars[decision - HOUR_MS].close))
            lot_count = int(Decimal(scenario.leg_usd * precision) / reference)
            qty = side * lot_count / precision
            if qty == 0:
                raise ValueError(f"{coin} rounds to zero at {day}")
            rate = STANDARD_TAKER_BPS if scenario.standard_fee else fee_for_market(market)
            try:
                legs[coin], path = leg(bars, rates_by_market[coin], start, stop, qty, rate, scenario.slip_bps)
            except ValueError as exc:
                raise ValueError(f"{strategy} {day} {coin}: {exc}") from exc
            paths.append(path)
    fields = ("gross_usd", "funding_usd", "fees_usd", "allowance_usd", "net_usd", "entry_notional")
    return {
        "decision_date": day.isoformat(), "entry_ms": start, "exit_ms": stop, "positions": legs,
        "eligible_markets": len(ranking),
        "long_entry_notional": sum(value["entry_notional"] for value in legs.values() if value["quantity"] > 0),
        "short_entry_notional": sum(value["entry_notional"] for value in legs.values() if value["quantity"] < 0),
        **{key: sum(value[key] for value in legs.values()) for key in fields},
    }, [sum(values) for values in zip(*paths, strict=True)]


def largest_drawdown(values):
    peak = 0.0
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        maximum = max(maximum, peak - value)
    return maximum


def evaluate(schedule, markets, rates, metadata, strategy, scenario, days):
    ledger, marks = [], [0.0]
    weekly = {}
    for date_, decision, eligible in schedule:
        row, path = portfolio(date_, decision, eligible, markets, rates, metadata, strategy, scenario)
        balance = marks[-1]
        marks.extend(balance + relative for relative in path)
        ledger.append(row)
        year, week, _ = date_.isocalendar()
        key = f"{year}-W{week:02}"
        weekly[key] = weekly.get(key, 0.0) + row["net_usd"]
    net = marks[-1]
    capital = CAPITAL * scenario.leg_usd / TARGET_PER_LEG
    return {
        "cycles": len(ledger), "positive_cycles": sum(row["net_usd"] > 0 for row in ledger),
        "starting_collateral_usd": capital,
        "tallied_net_usd": net, "return_pct_on_starting_collateral": 100 * net / capital,
        "marked_max_drawdown_usd": largest_drawdown(marks),
        "minimum_hourly_collateral_equity_usd": capital + min(marks),
        "gross_usd": sum(row["gross_usd"] for row in ledger),
        "funding_usd": sum(row["funding_usd"] for row in ledger),
        "fees_usd": sum(row["fees_usd"] for row in ledger),
        "allowance_usd": sum(row["allowance_usd"] for row in ledger),
        "idle_cash_usd": 0, "cash_hurdle_4pct_usd": capital * CASH_RATE * days / 365,
        "weekly_net_usd": weekly, "cycles_detail": ledger,
    }


def run(root):
    meta, meta_audit = source(root / "xyz-meta.json", {"type": "meta", "dex": "xyz"})
    metadata = {item["name"]: item for item in meta["universe"] if item["name"] in MARKETS}
    if set(metadata) != set(MARKETS):
        raise ValueError(f"missing metadata: {set(MARKETS) - set(metadata)}")
    markets, funding, sources = {}, {}, {}
    for coin in MARKETS:
        markets[coin], candle_audit = candles(root, coin)
        funding[coin], funding_audit = historic_funding(root, coin)
        sources[coin] = {
            "candles": candle_audit, "funding": funding_audit,
            "growth_mode_now": metadata[coin].get("growthMode"),
            "growth_mode_last_change": metadata[coin].get("lastGrowthModeChangeTime"),
            "base_taker_fee_bps": fee_for_market(metadata[coin]),
        }
    periods = {}
    for name, (first, end) in PERIODS.items():
        decisions, skipped = [], []
        for day, decision in decision_times(first, end):
            eligible = ranks(markets, decision)
            if len(eligible) >= 8:
                decisions.append((day, decision, eligible))
            else:
                skipped.append({"date": day.isoformat(), "eligible": len(eligible)})
        result = {}
        for scenario, parameters in SCENARIOS.items():
            result[scenario] = {
                strategy: evaluate(decisions, markets, funding, metadata, strategy, parameters, (end - first).days + 1)
                for strategy in ("relative_strength", "inverted", "selected_equal_long")
            }
        periods[name] = {"dates": [first.isoformat(), end.isoformat()], "skipped_decisions": skipped, "results": result}
    return {"scenario_parameters": {name: asdict(params) for name, params in SCENARIOS.items()}, "source": INFO_URL,
            "universe_metadata": meta_audit, "source_audit": sources, "periods": periods}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="cache public market data")
    parser.add_argument("--output", type=Path, help="write a paper replay report")
    args = parser.parse_args()
    if not (args.fetch or args.output):
        parser.error("specify --fetch or --output")
    if args.fetch:
        fetch(args.data)
    if args.output:
        report = run(args.data)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")


if __name__ == "__main__":
    main()
