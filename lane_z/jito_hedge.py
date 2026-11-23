"""Replay direct JitoSOL mint and redemption with a separately funded SOL hedge."""

import argparse
import bisect
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .native_trend import CAPITAL, CASH_EFFECTIVE_RATE, MAINTENANCE, Tape, funding_cash, max_drawdown
from .selective_carry import SelectiveTape
from .spot_carry import BAR, DAY, HOUR, MIN_NOTIONAL_USD, PERP_FEE, SPOT_FEE, epoch, stamp


END = "2026-11-21"
WINDOWS = {
    "development": ("2025-08-01", "2025-12-01"),
    "holdout_1": ("2025-12-01", "2026-04-01"),
    "holdout_2": ("2026-04-01", "2026-08-01"),
    "holdout_3": ("2026-08-01", END),
}
MAX_RATIO_AGE = 96 * HOUR
MAX_LAST_BAR_VOLUME_SHARE = 0.01
SPOT_ALLOCATION = 0.45
MIN_BRIDGE_SOL = 0.12
REDEMPTION_FEE = 0.001
RATIO_API = "https://kobe.mainnet.jito.network/api/v1/jitosol_sol_ratio"


@dataclass(frozen=True)
class Scenario:
    lag_bars: int
    delay_days: int
    execution_bps: int
    reserve_out_sol: float
    reserve_return_sol: float
    adverse_funding: bool = False
    route_haircut_bps: int = 0


SCENARIOS = {
    "base": Scenario(1, 3, 5, 0.01, 0.03),
    "combined": Scenario(2, 5, 20, 0.02, 0.06, True, 50),
}


class ConversionRates:
    def __init__(self, source):
        observed = {}
        for row in source["ratios"]:
            t = int(datetime.fromisoformat(row["date"].replace("Z", "+00:00")).timestamp() * 1000)
            value = float(row["data"])
            if not 0 < value < math.inf or observed.setdefault(t, value) != value:
                raise ValueError(f"Invalid Jito ratio observation at {stamp(t)}")
        self.times = sorted(observed)
        self.values = [observed[t] for t in self.times]

    def prior(self, time):
        index = bisect.bisect_left(self.times, time) - 1
        if index < 0 or time - self.times[index] > MAX_RATIO_AGE:
            raise ValueError(f"JitoSOL/SOL ratio missing or stale at {stamp(time)}")
        return self.values[index], self.times[index]


def lots(quantity, scale):
    return int(Decimal(str(quantity)) * scale) / scale


def execution(quantity, bar, previous, allowance, fee):
    open_proxy = bar.opening
    adjusted = open_proxy * (1 + math.copysign(allowance, quantity))
    notional = abs(quantity) * open_proxy
    if notional < MIN_NOTIONAL_USD:
        raise ValueError("execution would violate Hyperliquid's minimum notional")
    turnover = abs(quantity) * adjusted
    share = abs(quantity) / previous.volume if previous.volume > 0 else math.inf
    if share > MAX_LAST_BAR_VOLUME_SHARE:
        raise ValueError(
            f"execution needs {abs(quantity):.8f} SOL against {previous.volume:.8f} SOL of prior-bar volume "
            f"({share:.4%} versus the {MAX_LAST_BAR_VOLUME_SHARE:.2%} cap)"
        )
    return {"qty": quantity, "open_proxy": open_proxy, "adjusted_price": adjusted,
            "fee": turnover * fee, "allowance": abs(quantity) * abs(adjusted - open_proxy),
            "volume_share": share, "mark_notional": notional, "fill_notional": turnover}


def replay(spot, perp, funding, rates, market, start, stop, scenario, stake):
    lag = scenario.lag_bars * BAR
    allowance = scenario.execution_bps / 10_000
    spot_scale = 10 ** market["spot_lot_decimals"]
    perp_scale = 10 ** market["perp_lot_decimals"]
    common_scale = 10 ** market["common_lot_decimals"]
    buy_at = start + lag
    mint_at = buy_at + lag
    bridge_at = stop - lag
    burn_at = bridge_at - scenario.delay_days * DAY
    if not start < buy_at < mint_at < burn_at < bridge_at < stop:
        raise ValueError("Stake and collateral life cycle falls outside of the test window")
    hedge_usdc, idle_usdc = CAPITAL * (1 - SPOT_ALLOCATION), CAPITAL * SPOT_ALLOCATION
    spot_qty, short_qty, jito_shares = 0.0, 0.0, 0.0
    max_residual = 0.0
    pending = {}
    complete, failure, initial_funding = False, None, None
    mint, burn, arrival = None, None, None
    marks, adverse_drawdown, peak = [CAPITAL], 0.0, CAPITAL
    min_margin = hedge_usdc
    totals = dict.fromkeys(("basis_usdc", "stake_rewards_usdc", "funding_usdc", "fees_usdc", "allowance_usdc",
                            "bridge_reserve_usdc", "redeem_fee_usdc", "spot_dust_usdc", "route_haircut_usdc"), 0.0)
    state = "flat"
    trades, adjustments = [], []
    t = start

    def record(market_name, fill):
        trades.append({"market": market_name, "utc": stamp(t), **fill})
        totals["fees_usdc"] += fill["fee"]
        totals["allowance_usdc"] += fill["allowance"]

    for t in range(buy_at, stop + BAR, BAR):
        s, p = spot[t], perp[t]
        sp, pp = spot[t - BAR], perp[t - BAR]
        final = t == stop
        spot_price_change = spot_qty * (s.opening - sp.opening)
        hedge_price_change = short_qty * (p.opening - pp.opening)
        hedge_usdc += hedge_price_change
        totals["basis_usdc"] += spot_price_change + hedge_price_change
        prior_short = short_qty
        target = pending.pop(t, short_qty)
        if short_qty and hedge_usdc <= MAINTENANCE * abs(short_qty) * p.opening:
            failure = "opening adverse mark exhausts the separate hedge margin"
            break

        if t == buy_at:
            unit_spend = (1 + allowance) * (1 + SPOT_FEE)
            desired = lots(idle_usdc / (max(spot[start - BAR].close, perp[start - BAR].close) * unit_spend), common_scale)
            offered = lots(idle_usdc / (s.opening * unit_spend), common_scale)
            spot_qty = min(desired, offered)
            initial_funding = {"desired_SOL": desired, "bought_SOL": spot_qty, "spot_spending_cap_usdc": idle_usdc,
                               "initial_hedge_collateral_usdc": hedge_usdc, "capped_at_open": spot_qty < desired}
            try:
                spot_entry = execution(spot_qty, s, sp, allowance, SPOT_FEE)
                perp_entry = execution(-spot_qty, p, pp, allowance, PERP_FEE)
            except ValueError as error:
                failure = str(error)
                break
            outlay = spot_entry["fill_notional"] + spot_entry["fee"]
            hedge_debit = perp_entry["fee"] + perp_entry["allowance"]
            if idle_usdc + 1e-8 < outlay or hedge_usdc - hedge_debit < perp_entry["fill_notional"]:
                failure = "opening fill exceeds free spot cash or independently funded hedge capital"
                break
            record("USOL/USDC", spot_entry)
            idle_usdc -= outlay
            target = -spot_qty
            state = "in_transit" if stake else "unstaked"

        if stake and t == mint_at:
            if spot_qty - scenario.reserve_out_sol < MIN_BRIDGE_SOL:
                failure = "outbound model bridge transfer below the SOL minimum"
                break
            ratio, seen = rates.prior(t)
            spot_qty -= scenario.reserve_out_sol
            totals["bridge_reserve_usdc"] += scenario.reserve_out_sol * s.opening
            jito_shares = math.floor((spot_qty / ratio) * 1e9) / 1e9
            rounded_sol = jito_shares * ratio
            totals["spot_dust_usdc"] += (spot_qty - rounded_sol) * s.opening
            spot_qty = rounded_sol
            mint = {"utc": stamp(t), "snapshot_utc": stamp(seen), "ratio": ratio, "shares": jito_shares,
                    "redeemable_SOL": spot_qty, "bridge_reserve_SOL": scenario.reserve_out_sol}
            state = "staked"
        if state == "staked":
            ratio, seen = rates.prior(t)
            live_qty = jito_shares * ratio
            totals["stake_rewards_usdc"] += (live_qty - spot_qty) * s.opening
            spot_qty = live_qty
            if t == burn_at:
                debit = spot_qty * REDEMPTION_FEE
                spot_qty -= debit
                totals["redeem_fee_usdc"] += debit * s.opening
                burn = {"request_utc": stamp(t), "snapshot_utc": stamp(seen), "ratio": ratio,
                        "withdrawal_fee_SOL": debit, "claim_SOL": spot_qty, "delay_days": scenario.delay_days}
                state = "redeeming"
            monday = t % DAY == 0 and datetime.fromtimestamp(t / 1000, timezone.utc).weekday() == 0
            if t + lag < stop and (monday or t == burn_at):
                pending[t + lag] = -lots(spot_qty, perp_scale)
                adjustments.append({"decision_utc": stamp(t), "target_utc": stamp(t + lag),
                                    "ratio_as_of_utc": stamp(seen), "ratio": ratio,
                                    "known_redeemable_SOL": spot_qty, "desired_short_SOL": pending[t + lag]})
        if stake and t == bridge_at:
            if spot_qty - scenario.reserve_return_sol < MIN_BRIDGE_SOL:
                failure = "returning model bridge transfer below the SOL minimum"
                break
            spot_qty -= scenario.reserve_return_sol
            totals["bridge_reserve_usdc"] += scenario.reserve_return_sol * s.opening
            arrival = {"stake_release_utc": stamp(t), "unit_available_utc": stamp(stop),
                       "after_bridge_SOL": spot_qty, "bridge_reserve_SOL": scenario.reserve_return_sol}
            state = "returning"
        if final:
            if stake and state != "returning":
                failure = "stake-side redemption did not reach the sale window"
                break
            executable_qty = lots(spot_qty, spot_scale)
            try:
                sell = execution(-executable_qty, s, sp, allowance, SPOT_FEE)
            except ValueError as error:
                failure = str(error)
                break
            target = 0.0

        delta = float(Decimal(str(target)) - Decimal(str(short_qty)))
        if delta and (abs(delta) * p.opening >= MIN_NOTIONAL_USD or t in (buy_at, stop)):
            try:
                fill = execution(delta, p, pp, allowance, PERP_FEE)
            except ValueError as error:
                failure = str(error)
                break
            record("SOL-PERP", fill)
            hedge_usdc -= fill["fee"] + fill["allowance"]
            short_qty = target

        boundary = min(funding_cash(prior_short, funding[t], s, scenario, final),
                       funding_cash(short_qty, funding[t], s, scenario, final))
        inside = [funding_cash(short_qty, funding[hour], s, scenario)
                  for hour in range(t + HOUR, min(stop, t + BAR), HOUR)]
        adverse_short = p.high if not final else p.opening
        worst_funding = min(0, boundary) + sum(min(0, value) for value in inside)
        adverse_hedge = hedge_usdc + short_qty * (adverse_short - p.opening) + worst_funding
        margin = MAINTENANCE * abs(short_qty) * adverse_short
        min_margin = min(min_margin, adverse_hedge - margin)
        if adverse_hedge <= margin:
            failure = "joint adverse mark and funding exhaust the separate hedge margin"
            break
        funding_cashflow = boundary + math.fsum(inside)
        hedge_usdc += funding_cashflow
        totals["funding_usdc"] += funding_cashflow
        residual = spot_qty + (prior_short if final else short_qty)
        max_residual = max(max_residual, abs(residual) * s.opening)
        mark = idle_usdc + hedge_usdc + spot_qty * s.opening
        peak = max(peak, mark)
        if final:
            discarded = (spot_qty - executable_qty) * s.opening
            record("USOL/USDC", sell)
            net_sale = sell["fill_notional"] - sell["fee"]
            haircut = net_sale * scenario.route_haircut_bps / 10_000 if stake else 0.0
            idle_usdc += net_sale - haircut
            totals["route_haircut_usdc"] += haircut
            totals["spot_dust_usdc"] += discarded
            spot_qty = 0.0
            mark = idle_usdc + hedge_usdc
            complete = True
        else:
            stressed_mark = idle_usdc + adverse_hedge + spot_qty * s.low
            adverse_drawdown = max(adverse_drawdown, peak - stressed_mark)
        marks.append(mark)

    hurdle = CAPITAL * ((1 + CASH_EFFECTIVE_RATE) ** ((stop - start) / (365 * DAY)) - 1)
    net = idle_usdc + hedge_usdc - CAPITAL if complete else None
    booked = (totals["basis_usdc"] + totals["stake_rewards_usdc"] + totals["funding_usdc"]
              - sum(totals[name] for name in ("fees_usdc", "allowance_usdc", "bridge_reserve_usdc",
                                             "redeem_fee_usdc", "spot_dust_usdc", "route_haircut_usdc")))
    if complete and ((spot_qty or short_qty) or abs(net - booked) > 1e-8):
        raise ValueError(f"Stake-side and separate hedge ledgers fail to reconcile at {stamp(t)}")
    return {"complete": complete, "failure": {"utc": stamp(t), "reason": failure} if failure else None,
            "net_usdc": net, "cash_hurdle_usdc": hurdle, "excess_over_cash_usdc": net - hurdle if complete else None,
            **totals, "mark_drawdown_usdc": max_drawdown(marks), "adverse_envelope_drawdown_usdc": adverse_drawdown,
            "minimum_separate_hedge_margin_headroom_usdc": min_margin,
            "maximum_residual_SOL_mark_usdc": max_residual,
            "ending_hedge_usdc": hedge_usdc if complete else None,
            "ending_spot_cash_usdc": idle_usdc if complete else None,
            "minimum_leg_notional_usdc": min((fill["mark_notional"] for fill in trades), default=None),
            "maximum_last_completed_bar_volume_share": max((fill["volume_share"] for fill in trades), default=0.0),
            "initial_funding": initial_funding, "mint": mint, "burn": burn, "return_to_unit": arrival,
            "adjustments": adjustments, "trades": trades}


def run(root, native, selective):
    tape = Tape(native)
    shares = ConversionRates(tape.read(root / "jitosol-ratios.json", "Jito/jitosol-ratios.json"))
    spot_tape = SelectiveTape(selective, diagnostic=True, cash_carry_cache=None, trend_cache=native)
    market = spot_tape.verify("SOL")
    first, last = epoch(WINDOWS["development"][0]), epoch(END)
    spot = spot_tape.candles("SOL", "spot", first - BAR)
    perp = tape.candles("SOL")
    if any(t not in spot or t not in perp for t in range(first - BAR, last + BAR, BAR)):
        raise ValueError("SOL historical market grids do not cover all frozen windows")
    funding = tape.funding("SOL")
    periods = {}
    for name, (start, stop) in WINDOWS.items():
        periods[name] = {
            "start_utc": start, "close_utc": stop,
            "policies": {
                kind: {label: replay(spot, perp, funding, shares, market, epoch(start), epoch(stop), s, stake)
                       for label, s in SCENARIOS.items()}
                for kind, stake in (("JitoSOL", True), ("Unstaked_SOL", False))
            },
        }
    return {"capital_usdc": CAPITAL, "ratio_endpoint": RATIO_API, "market": market,
            "scenarios": {name: asdict(s) for name, s in SCENARIOS.items()},
            "ratio_tape": {"observations": len(shares.times), "oldest": stamp(shares.times[0]),
                           "newest": stamp(shares.times[-1]),
                           "longest_observation_gap_hours": max(b - a for a, b in zip(shares.times, shares.times[1:])) / HOUR},
            "source_audit": {**tape.audit, **spot_tape.audit}, "periods": periods}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True, help="native trend SOL perpetual and funding archive")
    parser.add_argument("--spot", type=Path, required=True, help="selective carry Unit SOL spot archive")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.data, args.native, args.spot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as target:
        json.dump(report, target, indent=2, allow_nan=False)
        target.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
