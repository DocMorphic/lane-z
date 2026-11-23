"""Paper-test 28/84-day equal-group-risk trend on four Hyperliquid perps."""

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, stdev

from .hip3_allocation import Archive as EquityGoldArchive
from .native_trend import Scenario, Tape as NativeArchive, funding_cash
from .selective_carry import cash_return
from .spot_carry import BAR, DAY, HOUR, MAINTENANCE_RATE, MIN_NOTIONAL_USD, PERP_FEE, Candle, epoch, stamp


GROUPS = {"crypto": ("BTC", "ETH"), "equity": ("XYZ100",), "gold": ("GOLD",)}
ASSETS = tuple(asset for group in GROUPS.values() for asset in group)
HIP3_NAMES = {"XYZ100": "equity", "GOLD": "gold"}
DATES = ("2026-05-15", "2026-07-17", "2026-09-18", "2026-11-20")
VINTAGES = tuple(epoch(date) + 16 * HOUR for date in DATES)
CAPITAL = 10_000.0
HORIZONS = (28, 84)
VOL_DAYS = 28
REBALANCE = 28 * DAY
ASSET_VOL_FLOOR = 0.10
TARGET_RISK = 0.10
GROUP_GROSS_CAP = 0.30
ISOLATED_BUFFER = 0.05
BAR_PARTICIPATION_LIMIT = 0.01
SHORT_SHOCK = 0.30
SCENARIOS = {"base": Scenario(1, 3), "combined_stress": Scenario(1, 15, True)}
LONG_EQUAL = "long_equal_group"
TREND_EQUAL = "trend_equal_group"
TREND_RISK = "trend_equal_risk"
POLICIES = (TREND_RISK, TREND_EQUAL, LONG_EQUAL)


@dataclass(frozen=True)
class Instrument:
    candles: dict
    funding: dict
    decimals: int
    taker_fee: float
    stress_fee: float


def load_markets(native_root, carry_root, hip3_root):
    native = NativeArchive(native_root, carry_root)
    sizes = native.sizes()
    markets = {a: Instrument(native.candles(a), native.funding(a), sizes[a], PERP_FEE, PERP_FEE)
               for a in GROUPS["crypto"]}
    hip3 = EquityGoldArchive(hip3_root)
    panels = hip3.load_markets()
    metadata = hip3.read("xyz-meta.json")
    xyz = next(row for row in metadata["universe"] if row["name"] == "xyz:XYZ100")
    gold = next(row for row in metadata["universe"] if row["name"] == "xyz:GOLD")
    if (metadata["collateralToken"] != 0 or xyz.get("growthMode") != "enabled"
            or xyz["lastFeeScaleChangeTime"][:10] != "2025-11-23" or gold.get("growthMode") == "enabled"):
        raise ValueError("The route's original USDC collateral or fee regime could not be reproduced")
    for asset, name in HIP3_NAMES.items():
        observed = panels[name]
        candles = {}
        for row in hip3.read(f"{name}-4h.json"):
            ts = row["t"]
            volume = float(row["v"])
            if not math.isfinite(volume) or volume <= 0:
                raise ValueError(f"{asset} missing four-hour volume at {stamp(ts)}")
            candles[ts] = Candle(observed.opening[ts], observed.close[ts], float(row["l"]), float(row["h"]), volume)
        regular_fee = 2 * PERP_FEE
        base_fee = regular_fee / 10 if asset == "XYZ100" else regular_fee
        markets[asset] = Instrument(candles, observed.funding, observed.precision, base_fee, regular_fee)
    return markets, {"native": native.audit, "hip3": hip3.audit}


def annualized(series):
    return stdev(series) * math.sqrt(365)


def weighted_volatility(weights, returns):
    return annualized([math.fsum(weights[asset] * returns[asset][i] for asset in weights)
                       for i in range(VOL_DAYS)])


def allocation(markets, decision, policy):
    scores, returns = {}, {}
    for asset, info in markets.items():
        last_bar = decision - BAR
        history = [info.candles[last_bar - i * DAY].close for i in range(max(HORIZONS) + 1)]
        returns[asset] = [math.log(history[i] / history[i + 1]) for i in range(VOL_DAYS)]
        scores[asset] = mean((history[0] > history[h]) - (history[0] < history[h]) for h in HORIZONS)
    target = dict.fromkeys(ASSETS, 0.0)
    asset_vol = {asset: annualized(returns[asset]) for asset in ASSETS}
    group_vol = {}
    for name, assets in GROUPS.items():
        if policy == LONG_EQUAL:
            raw = dict.fromkeys(assets, 1.0)
        elif policy == TREND_EQUAL:
            raw = {a: scores[a] for a in assets}
        else:
            raw = {a: scores[a] / max(asset_vol[a], ASSET_VOL_FLOOR) for a in assets}
        total = math.fsum(abs(value) for value in raw.values())
        if not total:
            group_vol[name] = 0.0
            continue
        units = {a: value / total for a, value in raw.items()}
        vol = weighted_volatility(units, returns)
        budget = GROUP_GROSS_CAP
        if policy == TREND_RISK and vol > 0:
            budget = min(budget, TARGET_RISK / math.sqrt(len(GROUPS)) / vol)
        target.update({a: ratio * budget for a, ratio in units.items()})
        group_vol[name] = budget * vol
    forecast_vol = weighted_volatility(target, returns)
    if policy == TREND_RISK and forecast_vol > TARGET_RISK:
        factor = TARGET_RISK / forecast_vol
        target = {asset: value * factor for asset, value in target.items()}
        group_vol = {group: vol * factor for group, vol in group_vol.items()}
        forecast_vol = TARGET_RISK
    return {
        "directional_score": scores, "asset_vol_forecast": asset_vol, "group_risk_forecast": group_vol,
        "portfolio_risk_forecast": forecast_vol, "weights": target,
    }


def rebalance(markets, pos, prices, equity, signal, scenario):
    weights = signal["weights"]
    target, orders = dict(pos), {}
    for asset, market in markets.items():
        bar = prices[asset]
        step = 10 ** market.decimals
        desired = int(weights[asset] * equity / bar.opening * step) / step
        change = desired - pos[asset]
        if not change or (desired != 0 and abs(change) * bar.opening < MIN_NOTIONAL_USD):
            continue
        fill = bar.opening * (1 + math.copysign(scenario.execution_bps / 10_000, change))
        fee = market.stress_fee if scenario.adverse_funding else market.taker_fee
        target[asset] = desired
        orders[asset] = {
            "previous_units": pos[asset], "next_units": desired, "fill_proxy": fill,
            "fee_usdc": abs(change) * fill * fee, "execution_usdc": (fill - bar.opening) * change,
            "fraction_of_4h_volume": abs(change) / bar.volume,
        }
    return target, orders


def run_vintage(markets, start, end, policy, scenario):
    signals = {t: allocation(markets, t - BAR, policy) for t in range(start, end, REBALANCE)}
    signals[end] = {"weights": dict.fromkeys(ASSETS, 0.0)}
    equity = peak = CAPITAL
    pos = dict.fromkeys(ASSETS, 0.0)
    pocket = pos.copy()
    component = {a: dict.fromkeys(("price_usdc", "funding_usdc", "fee_usdc", "execution_usdc"), 0.0)
                 for a in ASSETS}
    metrics = {"max_gross_fraction": 0.0, "max_short_gross_fraction": 0.0, "max_drawdown": 0.0,
               "max_bar_participation": 0.0}
    min_headroom = math.inf
    min_short_shock = math.inf
    failures = {}
    events = {}
    for t in range(start, end + BAR, BAR):
        prices = {a: m.candles[t] for a, m in markets.items()}
        for asset, units in pos.items():
            mark = prices[asset].opening
            prior = markets[asset].candles[t - BAR].opening
            price_move = units * (mark - prior)
            equity += price_move
            pocket[asset] += price_move
            component[asset]["price_usdc"] += price_move
            if units and pocket[asset] < abs(units) * mark * MAINTENANCE_RATE:
                failures.setdefault("margin", (stamp(t), asset))
        if t in signals:
            next_pos, orders = rebalance(markets, pos, prices, equity, signals[t], scenario)
            events[stamp(t)] = {"decision_utc": stamp(t - BAR) if t < end else None,
                                "signal": signals[t], "orders": orders}
        else:
            next_pos, orders = pos, {}
        for asset, market in markets.items():
            book = component[asset]
            rate = market.funding[t]
            bar = prices[asset]
            boundary = min(funding_cash(pos[asset], rate, bar, scenario, t == end),
                           funding_cash(next_pos[asset], rate, bar, scenario, t == end))
            book["funding_usdc"] += boundary
            equity += boundary
            pocket[asset] += boundary
            if asset in orders:
                order = orders[asset]
                for key in ("fee_usdc", "execution_usdc"):
                    book[key] += order[key]
                    equity -= order[key]
                capacity = order["fraction_of_4h_volume"]
                metrics["max_bar_participation"] = max(metrics["max_bar_participation"], capacity)
                if capacity > BAR_PARTICIPATION_LIMIT:
                    failures.setdefault("fill", (stamp(t), asset))
        pos = next_pos
        if t in signals:
            pocket = {a: abs(pos[a]) * prices[a].opening * (1 + ISOLATED_BUFFER) for a in ASSETS}
            if math.fsum(pocket.values()) > equity or equity <= 0:
                failures.setdefault("initial_collateral", (stamp(t), None))
        if equity <= 0:
            failures.setdefault("nonpositive_capital", (stamp(t), None))
            break
        gross = math.fsum(abs(pos[a]) * prices[a].opening for a in ASSETS)
        short_gross = math.fsum(-pos[a] * prices[a].opening for a in ASSETS if pos[a] < 0)
        metrics["max_gross_fraction"] = max(metrics["max_gross_fraction"], gross / equity)
        metrics["max_short_gross_fraction"] = max(metrics["max_short_gross_fraction"], short_gross / equity)
        peak = max(peak, equity)
        metrics["max_drawdown"] = max(metrics["max_drawdown"], 1 - equity / peak)
        if t == end:
            break
        for asset, units in pos.items():
            if not units:
                continue
            market, bar = markets[asset], prices[asset]
            flows = [funding_cash(units, market.funding[h], bar, scenario)
                     for h in range(t + HOUR, t + BAR, HOUR)]
            adverse_debits = math.fsum(min(0, flow) for flow in flows)
            worst_mark = bar.low if units > 0 else bar.high
            worst_equity = pocket[asset] + units * (worst_mark - bar.opening) + adverse_debits
            remaining = worst_equity - abs(units) * worst_mark * MAINTENANCE_RATE
            min_headroom = min(min_headroom, remaining)
            if remaining < 0:
                failures.setdefault("margin", (stamp(t), asset))
            if units < 0:
                shock = -units * worst_mark * SHORT_SHOCK * (1 + MAINTENANCE_RATE)
                min_short_shock = min(min_short_shock, remaining - shock)
            settled = math.fsum(flows)
            pocket[asset] += settled
            equity += settled
            component[asset]["funding_usdc"] += settled
    totals = {key: math.fsum(row[key] for row in component.values()) for key in component[ASSETS[0]]}
    net = equity - CAPITAL
    if not math.isclose(net, totals["price_usdc"] + totals["funding_usdc"] - totals["fee_usdc"]
                        - totals["execution_usdc"], abs_tol=1e-8):
        raise ValueError("Managed futures book does not reconcile")
    cash = CAPITAL * cash_return(start, end)
    return {
        **totals, **metrics, "complete": not failures and t == end and not any(pos.values()), "failure": failures,
        "net_usdc": net, "cash_4pct_usdc": cash, "excess_cash_usdc": net - cash,
        "minimum_isolated_headroom_usdc": None if math.isinf(min_headroom) else min_headroom,
        "minimum_short_30pct_jump_headroom_usdc": None if math.isinf(min_short_shock) else min_short_shock,
        "asset_pnl": component, "events": events,
    }


def report(markets, audit):
    periods = []
    for first, final in zip(VINTAGES, VINTAGES[1:]):
        results = {policy: {name: run_vintage(markets, first, final, policy, scenario)
                            for name, scenario in SCENARIOS.items()} for policy in POLICIES}
        periods.append({"start_utc": stamp(first), "end_utc": stamp(final), "results": results})
    primary = [row["results"][TREND_RISK]["combined_stress"] for row in periods]
    return {
        "initial_usdc_per_vintage": CAPITAL, "vintages": periods, "source_hashes": audit,
        "scenarios": {name: asdict(scenario) for name, scenario in SCENARIOS.items()},
        "qualified_all_three": len(primary) == 3 and all(x["complete"] and x["excess_cash_usdc"] > 0 for x in primary),
        "coverage": {a: {"first_candle_utc": stamp(min(m.candles)), "last_candle_utc": stamp(max(m.candles)),
                         "first_funding_utc": stamp(min(m.funding)), "last_funding_utc": stamp(max(m.funding)),
                         "base_taker_fee": m.taker_fee, "stress_taker_fee": m.stress_fee}
                     for a, m in markets.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, default=Path("data/agent-native-trend"))
    parser.add_argument("--carry", type=Path, default=Path("data/agent-spot-carry"))
    parser.add_argument("--hip3", type=Path, default=Path("data/agent-hip3-allocation"))
    parser.add_argument("--output", type=Path, default=Path("data/agent-managed-futures/report.json"))
    args = parser.parse_args()
    result = report(*load_markets(args.native, args.carry, args.hip3))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")
    print(f"Qualified all three: {result['qualified_all_three']}. Report written to {args.output}")


if __name__ == "__main__":
    main()
