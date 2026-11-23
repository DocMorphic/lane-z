"""Paper-test a predeclared volatility-scaled trend on native Hyperliquid perps."""

import argparse
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .spot_carry import (
    BAR, DAY, ENDPOINT, HOUR, MIN_NOTIONAL_USD, PERP_FEE, Candle, PublicArchive,
    epoch, funding_pages, stamp,
)


MARKETS = ("BTC", "ETH", "SOL")
WARMUP = "2025-05-01"
FUNDING_START = "2025-08-01"
END = "2026-11-21"
WINDOWS = {
    "development": ("2025-08-01", "2025-12-01"),
    "holdout_1": ("2025-12-01", "2026-04-01"),
    "holdout_2": ("2026-04-01", "2026-08-01"),
    "holdout_3": ("2026-08-01", END),
}
CAPITAL = 3_000.0
CASH_EFFECTIVE_RATE = 0.04
HORIZONS_DAYS = (14, 42, 84)
VOL_DAYS = 28
VOL_FLOOR = 0.25
VOL_TARGET = 0.40
MAX_ASSET_GROSS = 0.30
MAX_PORTFOLIO_GROSS = 0.75
MAINTENANCE = 0.05


def capture(root, reuse_carry=None):
    archive = PublicArchive(root)
    if reuse_carry is None:
        archive.get("perp-meta.json", {"type": "meta"}, 20)
    first, end = epoch(WARMUP), epoch(END)
    weight = 20 + math.ceil(((end - first) // BAR + 1) / 60)
    for asset in MARKETS:
        archive.get(f"{asset}-perp-candles.json", {"type": "candleSnapshot", "req": {
            "coin": asset, "interval": "4h", "startTime": first, "endTime": end,
        }}, weight)
    for asset in (MARKETS if reuse_carry is None else ("SOL",)):
        for label, start, stop in funding_pages(FUNDING_START, END):
            archive.get(f"{asset}-funding-{label}.json", {
                "type": "fundingHistory", "coin": asset, "startTime": start, "endTime": stop,
            }, 44)
        archive.get(f"{asset}-funding-close.json", {
            "type": "fundingHistory", "coin": asset, "startTime": end, "endTime": end + HOUR - 1,
        }, 21)


class Tape:
    def __init__(self, root, reuse_carry=None):
        self.root = root
        self.carry = reuse_carry
        self.audit = {}

    def read(self, path, label):
        contents = path.read_bytes()
        payload = json.loads(contents)
        self.audit[label] = {"sha256": hashlib.sha256(contents).hexdigest(),
                             "rows_or_fields": len(payload), "bytes": len(contents)}
        return payload

    def sizes(self):
        root = self.root if self.carry is None else self.carry
        meta = self.read(root / "perp-meta.json", "perp-meta.json")
        sizes = {row["name"]: row["szDecimals"] for row in meta["universe"] if row["name"] in MARKETS}
        if sizes.keys() != set(MARKETS) or any(not isinstance(size, int) or not 0 <= size <= 6 for size in sizes.values()):
            raise ValueError("Native perpetual metadata missing or invalid")
        return sizes

    def candles(self, asset):
        label = f"{asset}-perp-candles.json"
        series = {}
        for row in self.read(self.root / label, label):
            ts = row["t"]
            opening, close, low, high, volume = (float(row[f]) for f in ("o", "c", "l", "h", "v"))
            if (row["s"] != asset or row["i"] != "4h" or ts % BAR or row["T"] != ts + BAR - 1
                    or not all(math.isfinite(v) for v in (opening, close, low, high, volume))
                    or not 0 < low <= min(opening, close) <= max(opening, close) <= high
                    or volume <= 0 or row["n"] <= 0 or ts in series):
                raise ValueError(f"Invalid {asset} candle at {stamp(ts)}")
            series[ts] = Candle(opening, close, low, high, volume)
        if series.keys() != set(range(epoch(WARMUP), epoch(END) + 1, BAR)):
            raise ValueError(f"{asset} lacks a complete four-hour candle grid")
        return series

    def funding(self, asset):
        if self.carry is not None and asset in ("BTC", "ETH"):
            paths = [
                (prefix, path)
                for prefix, folder in (("carry-older", self.carry / "older"), ("carry-recent", self.carry))
                for path in sorted(folder.glob(f"{asset}-funding-*.json"))
            ]
        else:
            names = [f"{asset}-funding-{label}.json" for label, _, _ in funding_pages(FUNDING_START, END)]
            names.append(f"{asset}-funding-close.json")
            paths = [("native", self.root / name) for name in names]
        series = {}
        for prefix, path in paths:
            for row in self.read(path, f"{prefix}/{path.name}"):
                observed = row["time"]
                hour = observed - observed % HOUR
                value = float(row["fundingRate"])
                if row["coin"] != asset or observed - hour >= 60_000 or not math.isfinite(value) or abs(value) > 0.00501:
                    raise ValueError(f"Invalid {asset} funding observation at {stamp(observed)}")
                if epoch(FUNDING_START) <= hour <= epoch(END):
                    if series.setdefault(hour, (observed, value)) != (observed, value):
                        raise ValueError(f"Conflicting funding observations for {asset} at {stamp(hour)}")
        expected = set(range(epoch(FUNDING_START), epoch(END) + 1, HOUR))
        if series.keys() != expected:
            missing = sorted(expected - series.keys())
            raise ValueError(f"{asset} lacks {len(missing)} hourly funding rows, first gap {stamp(missing[0])}")
        return {hour: value for hour, (_, value) in series.items()}


@dataclass(frozen=True)
class Scenario:
    lag_bars: int
    execution_bps: int
    adverse_funding: bool = False


SCENARIOS = {"baseline": Scenario(1, 5), "timing_stress": Scenario(2, 5), "combined_stress": Scenario(2, 15, True)}


def weights(candles, at):
    result = {}
    for asset, series in candles.items():
        history = [series[at - BAR - i * DAY].close for i in range(max(HORIZONS_DAYS) + 1)]
        log_returns = [math.log(history[i] / history[i + 1]) for i in range(VOL_DAYS)]
        annual = statistics.stdev(log_returns) * math.sqrt(365)
        votes = [(history[0] > history[h]) - (history[0] < history[h]) for h in HORIZONS_DAYS]
        result[asset] = statistics.mean(votes) * min(MAX_ASSET_GROSS, VOL_TARGET / (len(MARKETS) * max(annual, VOL_FLOOR)))
    gross = sum(abs(weight) for weight in result.values())
    factor = min(1, MAX_PORTFOLIO_GROSS / gross) if gross else 1
    return {asset: value * factor for asset, value in result.items()}


def make_targets(pos, prices, decimals, target_weights, equity):
    target = {}
    for asset, weight in target_weights.items():
        scale = 10 ** decimals[asset]
        raw = Decimal(str(weight * equity * scale)) / Decimal(str(prices[asset]))
        qty = int(raw) / scale
        target[asset] = qty if abs(qty - pos[asset]) * prices[asset] >= MIN_NOTIONAL_USD else pos[asset]
    return target


def funding_cash(qty, rate, bar, scenario, final=False):
    units = -qty * rate
    if not scenario.adverse_funding:
        return units * bar.opening
    if units > 0:
        return units * (bar.opening if final else bar.low) * 0.995 * 0.75
    return units * (bar.opening if final else bar.high) * 1.005


def transact(pos, target, bars, scenario, terminal):
    filled = pos.copy()
    trades = {}
    for asset, old in pos.items():
        delta = target[asset] - old
        price = bars[asset].opening
        if not delta or (not terminal and abs(delta) * price < MIN_NOTIONAL_USD):
            continue
        fill = price * (1 + math.copysign(scenario.execution_bps / 10_000, delta))
        expense = abs(delta) * fill * PERP_FEE
        slippage = delta * (fill - price)
        trades[asset] = {"before": old, "after": target[asset], "fill_proxy": fill,
                         "taker_fee": expense, "execution_allowance": slippage, "notional": abs(delta) * fill}
        filled[asset] = target[asset]
    return filled, trades


def max_drawdown(equities):
    peak, decline = CAPITAL, 0.0
    for value in equities:
        peak = max(peak, value)
        decline = max(decline, peak - value)
    return decline


def run_window(candles, rates, decimals, first, stop, scenario, always_long):
    equity = CAPITAL
    marks = [CAPITAL]
    zero = dict.fromkeys(MARKETS, 0.0)
    positions = zero.copy()
    pnl = dict.fromkeys(("prices_usdc", "funding_usdc", "fees_usdc", "execution_allowance_usdc", "turnover_usdc"), 0.0)
    pending, orders = {}, []
    clock = None
    margin_headroom, max_adverse_decline, max_gross = CAPITAL, 0.0, 0.0
    years = (stop - first) / (365 * DAY)
    cash = CAPITAL * ((1 + CASH_EFFECTIVE_RATE) ** years - 1)
    peak = CAPITAL
    failure = None
    lag = scenario.lag_bars * BAR

    for t in range(first, stop + BAR, BAR):
        clock = t
        final = t == stop
        panel = {asset: candles[asset][t] for asset in MARKETS}
        last = {asset: candles[asset][t - BAR] for asset in MARKETS}
        weekday = datetime.fromtimestamp(t / 1000, timezone.utc).weekday()
        weekly = t % DAY == 0 and weekday == 0
        decide = t == first if always_long else weekly
        if decide and t + lag < stop:
            last_close = {asset: bar.close for asset, bar in last.items()}
            known_equity = equity + sum(positions[asset] * (bar.close - bar.opening) for asset, bar in last.items())
            desired_weights = dict.fromkeys(MARKETS, MAX_PORTFOLIO_GROSS / len(MARKETS)) if always_long else weights(candles, t)
            pending[t + lag] = make_targets(positions, last_close, decimals, desired_weights, known_equity)

        price_pnl = sum(positions[asset] * (bar.opening - last[asset].opening) for asset, bar in panel.items())
        equity += price_pnl
        pnl["prices_usdc"] += price_pnl
        prior_gross = sum(abs(positions[a]) * panel[a].opening for a in MARKETS)
        if equity <= MAINTENANCE * prior_gross:
            marks.append(equity)
            failure = {"utc": stamp(t), "reason": "opening proxy crosses maintenance before trades"}
            break
        target = zero if final else pending.pop(t, positions)
        next_pos, fills = transact(positions, target, panel, scenario, final)
        fees = sum(fill["taker_fee"] for fill in fills.values())
        allowance = sum(fill["execution_allowance"] for fill in fills.values())
        turnover = sum(fill["notional"] for fill in fills.values())
        cash_boundary = sum(min(funding_cash(positions[a], rates[a][t], panel[a], scenario, final),
                                funding_cash(next_pos[a], rates[a][t], panel[a], scenario, final)) for a in MARKETS)
        equity += cash_boundary - fees - allowance
        pnl["funding_usdc"] += cash_boundary
        pnl["fees_usdc"] += fees
        pnl["execution_allowance_usdc"] += allowance
        pnl["turnover_usdc"] += turnover
        marks.append(equity)
        peak = max(peak, equity)
        positions = next_pos
        if fills:
            orders.append({"utc": stamp(t), "trades": fills})
        gross = sum(abs(positions[a]) * panel[a].opening for a in MARKETS)
        if equity <= 0 or (fills and gross > equity):
            failure = {"utc": stamp(t), "reason": "insufficient full backing at fill, or nonpositive equity"}
            break
        if final:
            continue
        max_gross = max(max_gross, gross / equity)
        inside = [funding_cash(positions[a], rates[a][hour], panel[a], scenario)
                  for hour in range(t + HOUR, t + BAR, HOUR) for a in MARKETS]
        # A joint adverse traded-price envelope avoids claiming a known ordering of intrabar extremes.
        worst = {a: panel[a].low if positions[a] >= 0 else panel[a].high for a in MARKETS}
        adverse = equity + sum(positions[a] * (worst[a] - panel[a].opening) for a in MARKETS)
        adverse += sum(min(0, flow) for flow in inside)
        maintenance = MAINTENANCE * sum(abs(positions[a]) * worst[a] for a in MARKETS)
        margin_headroom = min(margin_headroom, adverse - maintenance)
        max_adverse_decline = max(max_adverse_decline, peak - adverse)
        if adverse <= maintenance:
            failure = {"utc": stamp(t), "reason": "adverse four-hour envelope crosses maintenance"}
            break
        pnl["funding_usdc"] += sum(inside)
        equity += sum(inside)

    complete = failure is None and clock == stop and positions == zero
    net = equity - CAPITAL
    reconciled = pnl["prices_usdc"] + pnl["funding_usdc"] - pnl["fees_usdc"] - pnl["execution_allowance_usdc"]
    if abs(reconciled - net) > 1e-8:
        raise ValueError(f"Capital accounting failed at {stamp(clock)}")
    return {
        "complete": complete, "failure": failure, "cash_hurdle_usdc": cash,
        "net_usdc": net if complete else None, "excess_over_cash_usdc": net - cash if complete else None,
        "return_pct": 100 * net / CAPITAL if complete else None, "net_to_last_checked_bar_usdc": net,
        "mark_drawdown_usdc": max_drawdown(marks), "adverse_envelope_drawdown_usdc": max_adverse_decline,
        "minimum_adverse_maintenance_headroom_usdc": margin_headroom, "max_gross_to_equity_on_marks": max_gross,
        "filled_asset_deltas": sum(len(order["trades"]) for order in orders),
        "trading_events": len(orders), **pnl, "orders": orders,
    }


def run(root, carry=None):
    tape = Tape(root, carry)
    candles = {asset: tape.candles(asset) for asset in MARKETS}
    funding = {asset: tape.funding(asset) for asset in MARKETS}
    decimals = tape.sizes()
    periods = {}
    for name, (start, end) in WINDOWS.items():
        first, stop = epoch(start), epoch(end)
        periods[name] = {"start_utc": start, "close_utc": end, "policies": {
            policy: {label: run_window(candles, funding, decimals, first, stop, scenario, always_long)
                     for label, scenario in SCENARIOS.items()}
            for policy, always_long in (("trend", False), ("equal_long", True))
        }}
    return {"endpoint": ENDPOINT, "capital_usdc": CAPITAL, "universe": list(MARKETS),
            "scenarios": {name: asdict(s) for name, s in SCENARIOS.items()}, "funding_hours_per_asset": len(funding["BTC"]),
            "source_audit": tape.audit, "periods": periods}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--reuse-carry", type=Path, help="reuse published spot carry BTC and ETH funding archives")
    parser.add_argument("--capture", action="store_true", help="cache public market-data responses")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.capture:
        capture(args.data, args.reuse_carry)
    report = run(args.data, args.reuse_carry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as target:
        json.dump(report, target, indent=2, allow_nan=False)
        target.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
