"""Replay a fixed fully funded spot long and perpetual short from public data."""

import argparse
import hashlib
import json
import math
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINT = "https://api.hyperliquid.xyz/info"
HOUR = 3_600_000
BAR = 4 * HOUR
DAY = 24 * HOUR
LOOKBACK = 28 * DAY
START = "2025-12-01"
WINDOWS = {
    "development": ("2026-01-01", "2026-04-01"),
    "holdout": ("2026-04-01", "2026-08-01"),
    "confirmation": ("2026-08-01", "2026-11-22"),
}
OLDER_WARMUP = "2025-08-01"
OLDER_WINDOWS = {"older_confirmation": ("2025-09-01", "2025-12-01")}
INCIDENT_EXIT = "2025-10-11"
MARKETS = {"BTC": ("UBTC", "@142", 5), "ETH": ("UETH", "@151", 4)}
INITIAL_SLEEVE_CAPITAL = 1_000.0
SLEEVE_DEPLOYMENT = 0.45
MAINTENANCE_RATE = 0.05
SPOT_FEE = 0.0007
PERP_FEE = 0.00045
MIN_NOTIONAL_USD = 10
CASH_HURDLE_ANNUAL_RATE = 0.04


def epoch(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def funding_pages(warmup, final):
    end = epoch(final) + 1
    for start in range(epoch(warmup), end, 20 * DAY):
        label = stamp(start)[:10]
        yield label, start, min(start + 20 * DAY, end) - 1


class PublicArchive:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.requests = deque()

    def throttle(self, weight):
        while True:
            now = time.monotonic()
            while self.requests and self.requests[0][0] <= now - 60:
                self.requests.popleft()
            if sum(w for _, w in self.requests) + weight <= 340:
                self.requests.append((now, weight))
                return
            time.sleep(max(0.1, 60.1 - (now - self.requests[0][0])))

    def get(self, name, payload, weight):
        path = self.root / name
        if path.is_file():
            return
        for attempt in range(4):
            self.throttle(weight)
            req = Request(ENDPOINT, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            try:
                with urlopen(req, timeout=30) as response:
                    raw = response.read()
                parsed = json.loads(raw)
                if not isinstance(parsed, (dict, list)) or not parsed:
                    raise ValueError(f"Empty or invalid response for {name}")
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(raw)
                temporary.replace(path)
                print(f"Captured {name} ({len(parsed)} rows or fields)", flush=True)
                return
            except (HTTPError, URLError, TimeoutError) as error:
                if attempt == 3 or isinstance(error, HTTPError) and error.code not in {429, 500, 502, 503, 504}:
                    raise
                time.sleep(8 * (attempt + 1))


def capture(root, warmup, final):
    archive = PublicArchive(root)
    archive.get("spot-meta.json", {"type": "spotMeta"}, 20)
    archive.get("perp-meta.json", {"type": "meta"}, 20)
    first, last = epoch(warmup), epoch(final)
    for asset, (_, spot, _) in MARKETS.items():
        for kind, symbol in (("spot", spot), ("perp", asset)):
            archive.get(f"{asset}-{kind}-candles.json", {"type": "candleSnapshot", "req": {
                "coin": symbol, "interval": "4h", "startTime": first, "endTime": last,
            }}, 20 + math.ceil(((last - first) // BAR + 1) / 60))
        for label, start, end in funding_pages(warmup, final):
            archive.get(f"{asset}-funding-{label}.json", {
                "type": "fundingHistory", "coin": asset, "startTime": start, "endTime": end,
            }, 20 + math.ceil(480 / 20))
        archive.get(f"{asset}-funding-close.json", {
            "type": "fundingHistory", "coin": asset, "startTime": last,
            "endTime": last + HOUR - 1,
        }, 21)


@dataclass(frozen=True)
class Candle:
    opening: float
    close: float
    low: float
    high: float
    volume: float


@dataclass(frozen=True)
class Scenario:
    execution_bps: float
    adverse_oracle: bool = False
    positive_funding_credit: float = 1


SCENARIOS = {
    "base": Scenario(2),
    "high_cost": Scenario(10, adverse_oracle=True),
    "funding_stress": Scenario(10, adverse_oracle=True, positive_funding_credit=0.75),
}


class Tape:
    def __init__(self, root, warmup, final):
        self.root = root
        self.warmup = warmup
        self.final = final
        self.audit = {}
        self.spot_meta = self.read("spot-meta.json")
        self.perp_meta = self.read("perp-meta.json")

    def read(self, name):
        raw = (self.root / name).read_bytes()
        payload = json.loads(raw)
        self.audit[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "rows_or_fields": len(payload)}
        return payload

    def verify_market(self, asset):
        name, symbol, decimals = MARKETS[asset]
        market = next(p for p in self.spot_meta["universe"] if p["name"] == symbol)
        by_index = {t["index"]: t for t in self.spot_meta["tokens"]}
        base, quote = (by_index[index] for index in market["tokens"])
        perp = next(p for p in self.perp_meta["universe"] if p["name"] == asset)
        if (base["name"], quote["name"], base["szDecimals"], perp["szDecimals"]) != (name, "USDC", decimals, decimals):
            raise ValueError(f"Unexpected spot pairing or lot sizes for {asset}")

    def candles(self, asset, kind, symbol):
        series = {}
        for row in self.read(f"{asset}-{kind}-candles.json"):
            ts = row["t"]
            opening, close, low, high, volume = (float(row[field]) for field in ("o", "c", "l", "h", "v"))
            if (row["s"] != symbol or row["i"] != "4h" or ts % BAR or row["T"] != ts + BAR - 1
                    or not all(math.isfinite(p) for p in (opening, close, low, high, volume))
                    or not 0 < low <= min(opening, close) <= max(opening, close) <= high
                    or volume <= 0 or row["n"] <= 0 or ts in series):
                raise ValueError(f"Invalid {symbol} candle at {stamp(ts)}")
            series[ts] = Candle(opening, close, low, high, volume)
        expected = set(range(epoch(self.warmup), epoch(self.final) + 1, BAR))
        if set(series) != expected:
            raise ValueError(f"{symbol} candles do not cover the required four-hour grid")
        return series

    def funding(self, asset):
        series = {}
        sources = [f"{asset}-funding-{label}.json" for label, _, _ in funding_pages(self.warmup, self.final)]
        sources.append(f"{asset}-funding-close.json")
        for filename in sources:
            for row in self.read(filename):
                hour, offset = divmod(row["time"], HOUR)
                ts = hour * HOUR
                rate = float(row["fundingRate"])
                if row["coin"] != asset or offset >= 60_000 or not math.isfinite(rate) or abs(rate) > 0.00501:
                    raise ValueError(f"Unexpected {asset} funding event in {filename}")
                if series.setdefault(ts, (row["time"], rate)) != (row["time"], rate):
                    raise ValueError(f"Conflicting {asset} funding rows for {stamp(ts)}")
        expected = set(range(epoch(self.warmup), epoch(self.final) + 1, HOUR))
        if series.keys() != expected:
            missing = sorted(expected - series.keys())
            examples = [stamp(t) for t in missing[:3]]
            raise ValueError(f"{asset} funding lacks a complete hourly grid, missing {len(missing)}. Examples {examples}")
        return {ts: rate for ts, (_, rate) in series.items()}


@dataclass(frozen=True)
class Holding:
    decision: int
    enter: int
    exit: int
    entry_trailing_funding: float | None


def holdings(funding, start, end, always_on):
    if always_on:
        return [Holding(start, start + BAR, end, None)]
    active = None
    selected = []
    for decision in range(start, end - BAR, DAY):
        if datetime.fromtimestamp(decision / 1000, timezone.utc).weekday() != 0:
            continue
        past = math.fsum(funding[t] for t in range(decision - LOOKBACK, decision, HOUR))
        if active is None and past >= 0.005:
            active = decision, past
        elif active is not None and past <= 0:
            entry, rate = active
            selected.append(Holding(entry, entry + BAR, decision + BAR, rate))
            active = None
    if active is not None:
        entry, rate = active
        selected.append(Holding(entry, entry + BAR, end, rate))
    return selected


def convert_funding(rate, spot_bar, scenario):
    if scenario.adverse_oracle:
        price = spot_bar.low * 0.995 if rate >= 0 else spot_bar.high * 1.005
    else:
        price = spot_bar.opening
    multiplier = scenario.positive_funding_credit if rate > 0 else 1
    return multiplier * rate * price


def replay_holding(spot, perp, funding, initial, holding, decimals, scenario):
    enter, exit_ = holding.enter, holding.exit
    prior = holding.decision - BAR
    quantity_scale = 10 ** decimals
    quantity = math.floor(initial * SLEEVE_DEPLOYMENT * quantity_scale / max(spot[prior].close, perp[prior].close)) / quantity_scale
    slip = scenario.execution_bps / 10_000
    spot_open = spot[enter].opening * (1 + slip)
    perp_open = perp[enter].opening * (1 - slip)
    spot_close = spot[exit_].opening * (1 - slip)
    perp_close = perp[exit_].opening * (1 + slip)
    if min(spot_open, perp_open, spot_close, perp_close) * quantity < MIN_NOTIONAL_USD:
        raise ValueError(f"Holding violates ${MIN_NOTIONAL_USD} minimum on at least one leg")
    spot_entry_fee = spot_open * quantity * (SPOT_FEE / (1 - SPOT_FEE))
    perp_entry_fee = perp_open * quantity * PERP_FEE
    fees = spot_entry_fee + perp_entry_fee + quantity * (SPOT_FEE * spot_close + PERP_FEE * perp_close)
    short_collateral = initial - quantity * spot_open - spot_entry_fee - perp_entry_fee
    received = 0.0
    minimum_headroom = math.inf
    breach = None
    equity = {}
    retained_events = 0
    for bar in range(enter, exit_, BAR):
        equity[bar] = short_collateral + received + quantity * (spot[bar].opening + perp_open - perp[bar].opening)
        cash_flows = [quantity * convert_funding(funding[t], spot[bar], scenario)
                      for t in range(bar, bar + BAR, HOUR) if t > enter or funding[t] < 0]
        worst_available = short_collateral + received + sum(min(flow, 0) for flow in cash_flows)
        headroom = worst_available + quantity * (perp_open - perp[bar].high * (1 + MAINTENANCE_RATE))
        if headroom < 0 and breach is None:
            breach = stamp(bar)
        minimum_headroom = min(minimum_headroom, headroom)
        retained_events += len(cash_flows)
        received += math.fsum(cash_flows)
    if funding[exit_] < 0:
        negative_at_exit = quantity * convert_funding(funding[exit_], spot[exit_], scenario)
        received += negative_at_exit
        retained_events += 1
    exit_headroom = short_collateral + received + quantity * (perp_open - perp_close * (1 + MAINTENANCE_RATE))
    if exit_headroom < 0 and breach is None:
        breach = stamp(exit_)
    minimum_headroom = min(minimum_headroom, exit_headroom)
    pnl_after_slip = quantity * (spot_close - spot_open + perp_open - perp_close)
    gross_price_pnl = quantity * (spot[exit_].opening - spot[enter].opening + perp[enter].opening - perp[exit_].opening)
    slippage = gross_price_pnl - pnl_after_slip
    total = pnl_after_slip + received - fees
    equity[exit_] = initial + total
    return equity, {
        "signal_utc": stamp(holding.decision), "entry_utc": stamp(enter), "exit_utc": stamp(exit_),
        "entry_trailing_funding_bps": (10_000 * holding.entry_trailing_funding
                                       if holding.entry_trailing_funding is not None else None),
        "holding_days": (exit_ - enter) / DAY, "quantity": quantity,
        "entry_spot_notional_usdc": quantity * spot_open, "entry_perp_notional_usdc": quantity * perp_open,
        "initial_short_collateral_usdc": short_collateral,
        "initial_full_funding_satisfied": short_collateral >= quantity * perp_open,
        "maximum_entry_proportion_of_bar_volume": (
            max(quantity / spot[enter].volume, quantity / perp[enter].volume)),
        "maximum_exit_proportion_of_bar_volume": (max(quantity / spot[exit_].volume, quantity / perp[exit_].volume)),
        "basis_pnl_before_costs_usdc": gross_price_pnl, "net_funding_usdc": received,
        "funding_events": retained_events, "fees_usdc": fees, "execution_reserve_usdc": slippage,
        "net_usdc": total, "short_minimum_margin_headroom_usdc": minimum_headroom, "margin_breach_utc": breach,
    }


def replay_sleeve(spot, perp, funding, decimals, start, end, scenario, always_on):
    planned = holdings(funding, start, end, always_on)
    curve = {}
    current = INITIAL_SLEEVE_CAPITAL
    last = start
    ledger = []
    for holding in planned:
        for bar in range(last, holding.enter, BAR):
            curve[bar] = current
        marked, trade = replay_holding(spot, perp, funding, current, holding, decimals, scenario)
        curve.update(marked)
        ledger.append(trade)
        last = holding.exit
        current = marked[last]
    for bar in range(last, end + 1, BAR):
        curve[bar] = current
    return curve, ledger


def max_drawdown(curve):
    drawdown = 0.0
    high = curve[0]
    for equity in curve:
        high = max(high, equity)
        drawdown = max(drawdown, (high - equity) / high)
    return drawdown


def summarize(balances, ledger):
    net = balances[-1] - balances[0]
    return {
        "starting_capital_usdc": balances[0], "ending_capital_usdc": balances[-1], "net_usdc": net,
        "return_pct": 100 * net / balances[0], "max_drawdown_pct": 100 * max_drawdown(balances),
        "round_trips": len(ledger), "profitable_round_trips": sum(t["net_usdc"] > 0 for t in ledger),
        "active_sleeve_days": math.fsum(t["holding_days"] for t in ledger),
        "net_funding_usdc": math.fsum(t["net_funding_usdc"] for t in ledger),
        "basis_pnl_before_costs_usdc": math.fsum(t["basis_pnl_before_costs_usdc"] for t in ledger),
        "fees_usdc": math.fsum(t["fees_usdc"] for t in ledger),
        "execution_reserve_usdc": math.fsum(t["execution_reserve_usdc"] for t in ledger),
        "minimum_short_margin_headroom_usdc": min((t["short_minimum_margin_headroom_usdc"] for t in ledger), default=None),
        "complete": all(t["margin_breach_utc"] is None and t["initial_full_funding_satisfied"] for t in ledger),
    }


def incident_exit_counterfactual(market_data):
    """Close both fully funded sleeves after the historical incident and stay flat."""
    begin = epoch(OLDER_WINDOWS["older_confirmation"][0])
    exit_time = epoch(INCIDENT_EXIT)
    scenario = SCENARIOS["funding_stress"]
    trades = []
    for asset, (_, _, decimals) in MARKETS.items():
        spot, perp, funding = market_data[asset]
        _, ledger = replay_sleeve(spot, perp, funding, decimals, begin, exit_time, scenario, always_on=True)
        close = ledger[0]
        notional = close["quantity"] * perp[exit_time].opening * (1 + scenario.execution_bps / 10_000)
        trades.append({"asset": asset, "exit_perp_notional_usdc": notional, **close})
    without_extra = math.fsum(t["net_usdc"] for t in trades)
    perp_buyback = math.fsum(t["exit_perp_notional_usdc"] for t in trades)
    return {
        "policy": "always_on", "scenario": "funding_stress", "hypothetical_exit_utc": stamp(exit_time),
        "net_before_additional_exit_charge_usdc": without_extra,
        "extra_charge_scope": "perpetual_buyback_leg_only",
        "exit_charge_sensitivity": {str(bps): {
            "extra_cost_usdc": perp_buyback * bps / 10_000,
            "net_usdc": without_extra - perp_buyback * bps / 10_000,
        } for bps in (50, 100)},
        "round_trip_ledger": trades,
    }


def run_replay(root, warmup=START, windows=WINDOWS):
    final = max(finish for _, finish in windows.values())
    tape = Tape(root, warmup, final)
    market_data = {}
    for asset, (_, symbol, _) in MARKETS.items():
        tape.verify_market(asset)
        market_data[asset] = tape.candles(asset, "spot", symbol), tape.candles(asset, "perp", asset), tape.funding(asset)
    periods = {}
    for label, (begin, finish) in windows.items():
        start, end = epoch(begin), epoch(finish)
        days = (end - start) / DAY
        periods[label] = {"start_utc": begin, "end_utc": finish, "calendar_days": days,
                          "idle_cash_usdc": 0, "illustrative_4pct_cash_hurdle_usdc": (
                              len(MARKETS) * INITIAL_SLEEVE_CAPITAL * ((1 + CASH_HURDLE_ANNUAL_RATE) ** (days / 365) - 1))}
        for scenario_name, scenario in SCENARIOS.items():
            variants = {}
            for policy, always_on in (("funding_filter", False), ("always_on", True)):
                curves = []
                ledger = []
                sleeves = {}
                for asset, (_, _, decimals) in MARKETS.items():
                    marked, trades = replay_sleeve(*market_data[asset], decimals, start, end, scenario, always_on)
                    curves.append(marked)
                    ledger.extend(trades)
                    sleeve = summarize([marked[bar] for bar in range(start, end + 1, BAR)], trades)
                    sleeves[asset] = {**sleeve, "round_trip_ledger": trades}
                combined = [math.fsum(curve[bar] for curve in curves) for bar in range(start, end + 1, BAR)]
                variants[policy] = {**summarize(combined, ledger), "sleeves": sleeves}
            periods[label][scenario_name] = variants
    result = {"endpoint": ENDPOINT, "input_audit": tape.audit, "assumptions": {
        "initial_sleeve_capital_usdc": INITIAL_SLEEVE_CAPITAL, "spot_deployment_share": SLEEVE_DEPLOYMENT,
        "funding_lookback_days": LOOKBACK // DAY, "entry_trailing_funding_bps": 50,
        "exit_trailing_funding_bps": 0, "spot_taker_fee_bps": 10_000 * SPOT_FEE,
        "perp_taker_fee_bps": 10_000 * PERP_FEE, "paper_price_reference": "next_four_hour_bar_open",
        "short_maintenance_rate": MAINTENANCE_RATE, "scenarios": {
            name: asdict(scenario) for name, scenario in SCENARIOS.items()},
    }, "periods": periods}
    if "older_confirmation" in windows:
        result["october_2025_incident_counterfactual"] = incident_exit_counterfactual(market_data)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--older-confirmation", action="store_true",
                        help="use the independent September–December 2025 replay and its own input directory")
    args = parser.parse_args()
    warmup, windows = (OLDER_WARMUP, OLDER_WINDOWS) if args.older_confirmation else (START, WINDOWS)
    if args.capture:
        capture(args.data, warmup, max(finish for _, finish in windows.values()))
    report = run_replay(args.data, warmup, windows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        json.dump(report, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
