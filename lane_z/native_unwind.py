"""Paper-test a fixed funding-conditioned price reversal on native Hyperliquid perps."""

import argparse
import json
import math
import statistics
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from .native_trend import (
    CAPITAL, CASH_EFFECTIVE_RATE, MAINTENANCE, MARKETS, SCENARIOS,
    Tape as FundingTape, funding_cash, max_drawdown, transact,
)
from .spot_carry import DAY, ENDPOINT, HOUR, MIN_NOTIONAL_USD, Candle, PublicArchive, epoch, stamp


WARMUP = "2026-05-04"
END = "2026-11-21"
WINDOWS = {
    "development": ("2026-05-08", "2026-07-13"),
    "holdout": ("2026-07-13", "2026-09-14"),
    "confirmation": ("2026-09-14", END),
}
POLICIES = ("funding_conditioned", "shock_only", "equal_long")
GROSS_SHARE = 0.5
HOLD = 12 * HOUR
VOL_HOURS = 72
CROWD_HOURS = 8
SHOCK_FLOOR = 0.01
SHOCK_SIGMAS = 2.5
CROWD_FLOOR = 0.0003


def capture(root):
    archive = PublicArchive(root)
    first, last = epoch(WARMUP), epoch(END)
    weight = 20 + math.ceil(((last - first) // HOUR + 1) / 60)
    for asset in MARKETS:
        archive.get(f"{asset}-perp-candles.json", {
            "type": "candleSnapshot", "req": {
                "coin": asset, "interval": "1h", "startTime": first, "endTime": last,
            },
        }, weight)


def candle_tape(root, funding_tape, asset):
    name = f"{asset}-perp-candles.json"
    result = {}
    for row in funding_tape.read(root / name, f"1h/{name}"):
        ts = row["t"]
        first, last, low, high, volume = (float(row[field]) for field in ("o", "c", "l", "h", "v"))
        if (row["s"] != asset or row["i"] != "1h" or ts % HOUR or row["T"] != ts + HOUR - 1
                or not all(math.isfinite(v) for v in (first, last, low, high, volume))
                or not 0 < low <= min(first, last) <= max(first, last) <= high
                or volume <= 0 or row["n"] <= 0 or ts in result):
            raise ValueError(f"Invalid {asset} one-hour candle at {stamp(ts)}")
        result[ts] = Candle(first, last, low, high, volume)
    if result.keys() != set(range(epoch(WARMUP), epoch(END) + 1, HOUR)):
        raise ValueError(f"{asset} lacks a complete one-hour candle grid")
    return result


@dataclass(frozen=True)
class Signal:
    asset: str
    decision_utc: str
    shock: float
    trailing_vol: float
    trailing_funding: float
    direction: int

    @property
    def crowded(self):
        return abs(self.trailing_funding) >= CROWD_FLOOR and self.trailing_funding * self.shock < 0


def build_signals(candles, rates, first, stop):
    shocks = {}
    for asset, panel in candles.items():
        log_returns = {t: math.log(panel[t].close / panel[t - HOUR].close)
                       for t in range(epoch(WARMUP) + HOUR, stop, HOUR)}
        shocks[asset] = {}
        for t in range(first, stop, HOUR):
            shock = log_returns[t - HOUR]
            if abs(shock) < SHOCK_FLOOR:
                continue
            vol = statistics.stdev(log_returns[hour] for hour in range(t - (VOL_HOURS + 1) * HOUR, t - HOUR, HOUR))
            if abs(shock) >= SHOCK_SIGMAS * vol:
                crowd = math.fsum(rates[asset][hour] for hour in range(t - (CROWD_HOURS + 1) * HOUR, t - HOUR, HOUR))
                shocks[asset][t] = Signal(asset, stamp(t), shock, vol, crowd, -1 if shock > 0 else 1)
    by_hour = {}
    for t in range(first, stop, HOUR):
        candidates = [shocks[asset][t] for asset in MARKETS if t in shocks[asset]]
        if candidates:
            by_hour[t] = sorted(candidates, key=lambda s: (-abs(s.shock) / (s.trailing_vol or 1e-100), s.asset))
    return by_hour


def make_targets(weights, last, decimals, equity):
    targets = dict.fromkeys(MARKETS, 0.0)
    for asset, weight in weights.items():
        scale = 10 ** decimals[asset]
        raw_lots = Decimal(str(weight)) * Decimal(str(equity)) * scale / Decimal(str(last[asset].close))
        targets[asset] = int(raw_lots) / scale
    return targets


@dataclass(frozen=True)
class PendingEntry:
    time: int
    end: int
    targets: dict
    signal: Signal | None


def play(candles, rates, decimals, signals, first, stop, scenario, policy):
    flat = dict.fromkeys(MARKETS, 0.0)
    positions, active = flat.copy(), {}
    pending, release = None, None
    counts = {"scheduled_entries": 0, "under_minimum_entries": 0, "under_minimum_exits": 0}
    totals = dict.fromkeys(("price_usdc", "funding_usdc", "fee_usdc", "allowance_usdc", "turnover_usdc"), 0.0)
    equity, peak, equity_marks = CAPITAL, CAPITAL, [CAPITAL]
    adverse_decline, minimum_headroom, maximum_gross = 0.0, CAPITAL, 0.0
    entries, exits, failure = [], [], None
    lag = scenario.lag_bars * HOUR

    for t in range(first, stop + HOUR, HOUR):
        panel = {asset: candles[asset][t] for asset in MARKETS}
        last = {asset: candles[asset][t - HOUR] for asset in MARKETS}
        individual_pnl = {a: positions[a] * (panel[a].opening - last[a].opening) for a in MARKETS}
        equity += sum(individual_pnl.values())
        totals["price_usdc"] += sum(individual_pnl.values())
        opening_gross = sum(abs(positions[a]) * panel[a].opening for a in MARKETS)
        if equity <= MAINTENANCE * opening_gross:
            failure = {"utc": stamp(t), "reason": "opening price proxy crosses maintenance before trades"}
            equity_marks.append(equity)
            break

        entering = pending if pending and pending.time == t else None
        exit_now = release == t
        targets = entering.targets if entering else flat if exit_now else positions
        next_positions, fills = transact(positions, targets, panel, scenario, terminal=exit_now)
        if entering:
            counts["under_minimum_entries"] += sum(
                a not in fills and amount != 0 for a, amount in targets.items()
            )
        if exit_now:
            counts["under_minimum_exits"] += sum(
                abs(positions[a]) * panel[a].opening < MIN_NOTIONAL_USD for a in fills
            )
        flows = {}
        for asset in MARKETS:
            bar, rate = panel[asset], rates[asset][t]
            before = funding_cash(positions[asset], rate, bar, scenario, final=exit_now)
            after = funding_cash(next_positions[asset], rate, bar, scenario, final=exit_now)
            flows[asset] = min(before, after)
        fee = sum(fill["taker_fee"] for fill in fills.values())
        allowance = sum(fill["execution_allowance"] for fill in fills.values())
        totals["funding_usdc"] += sum(flows.values())
        totals["fee_usdc"] += fee
        totals["allowance_usdc"] += allowance
        totals["turnover_usdc"] += sum(fill["notional"] for fill in fills.values())
        equity += sum(flows.values()) - fee - allowance
        equity_marks.append(equity)
        peak = max(peak, equity)

        for asset, episode in active.items():
            episode["price_usdc"] += individual_pnl[asset]
            episode["funding_usdc"] += flows[asset]
        if entering:
            pending = None
            if fills:
                release = entering.end
                active = {
                    a: {"asset": a, "price_usdc": 0.0, "funding_usdc": flows[a],
                        "entry_fee_usdc": fill["taker_fee"], "entry_allowance_usdc": fill["execution_allowance"],
                        "entry_notional_usdc": fill["notional"], "qty": fill["after"], "entry_utc": stamp(t),
                        "entry_open_proxy": panel[a].opening,
                        "signal": asdict(entering.signal) if entering.signal else None}
                    for a, fill in fills.items()
                }
                entries.append({"utc": stamp(t), "equity_after_costs": equity,
                                "release_utc": stamp(release), "assets": fills})
        if exit_now:
            release = None
            for a, fill in fills.items():
                episode = active[a]
                episode.update(exit_utc=stamp(t), exit_open_proxy=panel[a].opening,
                               exit_fee_usdc=fill["taker_fee"], exit_allowance_usdc=fill["execution_allowance"],
                               exit_notional_usdc=fill["notional"])
                episode["net_usdc"] = (episode["price_usdc"] + episode["funding_usdc"]
                                       - episode["entry_fee_usdc"] - episode["exit_fee_usdc"]
                                       - episode["entry_allowance_usdc"] - episode["exit_allowance_usdc"])
                exits.append(episode)
            active = {}
        positions = next_positions
        if equity <= 0 or (entering and sum(fill["notional"] for fill in fills.values()) > equity):
            failure = {"utc": stamp(t), "reason": "insufficient capital to fully back modeled entry"}
            break

        if t < stop:
            gross = sum(abs(positions[a]) * panel[a].opening for a in MARKETS)
            maximum_gross = max(maximum_gross, gross / equity)
            worst = {a: panel[a].low if positions[a] >= 0 else panel[a].high for a in MARKETS}
            adverse = equity + sum(positions[a] * (worst[a] - panel[a].opening) for a in MARKETS)
            maintenance = MAINTENANCE * sum(abs(positions[a]) * worst[a] for a in MARKETS)
            minimum_headroom = min(minimum_headroom, adverse - maintenance)
            adverse_decline = max(adverse_decline, peak - adverse)
            if adverse <= maintenance:
                failure = {"utc": stamp(t), "reason": "adverse intrahour proxy crosses maintenance"}
                break

        if pending or release is not None or t >= stop:
            continue
        if policy == "equal_long":
            if t == first and t + lag < stop:
                weights = dict.fromkeys(MARKETS, GROSS_SHARE / len(MARKETS))
                pending = PendingEntry(t + lag, stop, make_targets(weights, last, decimals, equity), None)
        elif t + lag + HOLD <= stop:
            candidates = signals.get(t, ())
            signal = next((s for s in candidates if policy == "shock_only" or s.crowded), None)
            if signal:
                weights = {signal.asset: signal.direction * GROSS_SHARE}
                pending = PendingEntry(t + lag, t + lag + HOLD, make_targets(weights, last, decimals, equity), signal)
        if pending:
            counts["scheduled_entries"] += 1

    complete = failure is None and positions == flat and pending is None and not active
    net = equity - CAPITAL
    hurdle = CAPITAL * ((1 + CASH_EFFECTIVE_RATE) ** ((stop - first) / (365 * DAY)) - 1)
    reconciled = totals["price_usdc"] + totals["funding_usdc"] - totals["fee_usdc"] - totals["allowance_usdc"]
    if abs(net - reconciled) > 1e-8 or (complete and abs(net - sum(row["net_usdc"] for row in exits)) > 1e-8):
        raise ValueError(f"Paper equity failed to reconcile at {stamp(t)}")
    return {"complete": complete, "failure": failure, "cash_hurdle_usdc": hurdle,
            "net_usdc": net if complete else None, "excess_over_cash_usdc": net - hurdle if complete else None,
            "return_pct": 100 * net / CAPITAL if complete else None,
            "mark_drawdown_usdc": max_drawdown(equity_marks), "adverse_envelope_drawdown_usdc": adverse_decline,
            "minimum_adverse_maintenance_headroom_usdc": minimum_headroom, "maximum_gross_over_equity": maximum_gross,
            "asset_round_trips": len(exits), "entry_events": len(entries),
            "positive_net_trips": sum(episode["net_usdc"] > 0 for episode in exits),
            **counts, **totals, "entries": entries, "episodes": exits}


def run(root, native, carry):
    tape = FundingTape(native, carry)
    candles = {a: candle_tape(root, tape, a) for a in MARKETS}
    rates = {a: tape.funding(a) for a in MARKETS}
    decimals = tape.sizes()
    periods = {}
    for name, (first, stop) in WINDOWS.items():
        start, end = epoch(first), epoch(stop)
        signals = build_signals(candles, rates, start, end)
        periods[name] = {
            "start_utc": first, "close_utc": stop,
            "shock_timestamps": len(signals),
            "conditioned_timestamps": sum(any(s.crowded for s in row) for row in signals.values()),
            "asset_shocks": sum(len(row) for row in signals.values()),
            "asset_conditioned_shocks": sum(s.crowded for row in signals.values() for s in row),
            "policies": {
                policy: {label: play(candles, rates, decimals, signals, start, end, scenario, policy)
                         for label, scenario in SCENARIOS.items()} for policy in POLICIES
            },
        }
    return {"endpoint": ENDPOINT, "capital_usdc": CAPITAL, "universe": list(MARKETS),
            "candles_per_asset": len(candles["BTC"]), "funding_hours_per_asset": (epoch(END) - epoch(WARMUP)) // HOUR + 1,
            "scenarios": {name: asdict(s) for name, s in SCENARIOS.items()}, "source_audit": tape.audit,
            "periods": periods}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--native-funding", type=Path, required=True, help="native trend SOL funding archive")
    parser.add_argument("--carry-funding", type=Path, required=True, help="spot carry BTC and ETH funding archives")
    parser.add_argument("--capture", action="store_true", help="cache public one-hour candles")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.capture:
        capture(args.data)
    report = run(args.data, args.native_funding, args.carry_funding)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as target:
        json.dump(report, target, indent=2, allow_nan=False)
        target.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
