"""Replay fully funded same-venue carry against a compounded cash hurdle."""

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from urllib.request import urlopen

from .spot_carry import (BAR, DAY, HOUR, CASH_HURDLE_ANNUAL_RATE, PERP_FEE, SPOT_FEE,
                         SLEEVE_DEPLOYMENT, Candle, Holding, PublicArchive, SCENARIOS,
                         epoch, funding_pages, replay_holding, stamp)


MARKETS = {
    "BTC": ("UBTC", "@142", 5, "2025-02-17"),
    "ETH": ("UETH", "@151", 4, "2025-03-31"),
    "SOL": ("USOL", "@156", 2, "2025-05-19"),
    "HYPE": ("HYPE", "@107", 2, "2024-12-09"),
}
WARMUP = "2025-02-10"
FIRST_DECISION = "2025-03-10"
FIRST_PRIMARY = "2025-06-02"
PRIMARY_END = "2025-07-28"
SOURCE_SPLIT = "2025-08-01"
DIAGNOSTIC_END = "2026-11-22"
SHARED_SOL_END = "2026-11-21"
TERM = 28 * DAY
WEEK = 7 * DAY
INITIAL_USDC = 2_000.0
SLEEVES = 2
VOLUME_FLOOR = 100_000
BASIS_RESERVE = 0.0025
MAX_BAR_PARTICIPATION = 0.01
NEXT_EXIT_SURCHARGE = 0.01
TERMINAL_USDC_HAIRCUT = 0.01
STAKING_TOKEN_APY = 0.0237
STAKING_DATES = ("2026-08-02", "2026-09-27", DIAGNOSTIC_END)
STAKING_EXIT_LEAD = 7 * DAY + BAR
COMBINED = SCENARIOS["funding_stress"]
STRESSED_ROUND_TRIP_COST = 2 * (SPOT_FEE + PERP_FEE) + 4 * COMBINED.execution_bps / 10_000
LISTINGS_URL = "https://api.tardis.dev/v1/exchanges/hyperliquid"


def grab_candles(archive, asset, leg, coin, start, stop):
    archive.get(f"{asset}-{leg}-candles.json", {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "4h", "startTime": epoch(start), "endTime": epoch(stop),
    }}, 20 + math.ceil(((epoch(stop) - epoch(start)) // BAR + 1) / 60))


def grab_funding(archive, asset, start, stop):
    for label, begin, end in funding_pages(start, stop):
        if begin == end:
            continue
        weight = 20 + math.ceil(((end - begin) // HOUR + 1) / 20)
        archive.get(f"{asset}-funding-{label}.json", {
            "type": "fundingHistory", "coin": asset, "startTime": begin, "endTime": end,
        }, weight)
    final = epoch(stop)
    archive.get(f"{asset}-funding-close.json", {
        "type": "fundingHistory", "coin": asset, "startTime": final,
        "endTime": final + HOUR - 1,
    }, 21)


def capture(root, diagnostic=False, cash_carry_cache=None, trend_cache=None):
    metadata = PublicArchive(root)
    metadata.get("spot-meta.json", {"type": "spotMeta"}, 20)
    metadata.get("perp-meta.json", {"type": "meta"}, 20)
    listing_file = root / "tardis-listings.json"
    if not listing_file.exists():
        with urlopen(LISTINGS_URL, timeout=30) as response:
            raw = response.read()
        if not json.loads(raw).get("availableSymbols"):
            raise ValueError("Invalid public market listing archive")
        temporary = listing_file.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(listing_file)
    early = root / "early"
    early.mkdir(exist_ok=True)
    archive = metadata
    # Both stage directories share the same rolling API limit.
    archive.root = early
    for asset, (_, spot, _, certified) in MARKETS.items():
        start = max(WARMUP, certified)
        grab_candles(archive, asset, "spot", spot, start, SOURCE_SPLIT)
        if asset != "SOL" or trend_cache is None:
            grab_candles(archive, asset, "perp", asset, start, SOURCE_SPLIT)
        grab_funding(archive, asset, start, SOURCE_SPLIT)
    if not diagnostic:
        return
    late = root / "late"
    late.mkdir(exist_ok=True)
    archive.root = late
    for asset, (_, spot, _, _) in MARKETS.items():
        if asset in ("BTC", "ETH") and cash_carry_cache is not None:
            continue
        grab_candles(archive, asset, "spot", spot, SOURCE_SPLIT, DIAGNOSTIC_END)
        perp_start = SHARED_SOL_END if asset == "SOL" and trend_cache is not None else SOURCE_SPLIT
        grab_candles(archive, asset, "perp", asset, perp_start, DIAGNOSTIC_END)
        grab_funding(archive, asset, perp_start, DIAGNOSTIC_END)


class SelectiveTape:
    def __init__(self, root, diagnostic, cash_carry_cache, trend_cache):
        self.root = root
        self.cash_carry_cache = cash_carry_cache
        self.trend_cache = trend_cache
        self.diagnostic = diagnostic
        self.end = epoch(DIAGNOSTIC_END if diagnostic else PRIMARY_END)
        self.audit = {}
        self.spot_meta = self.read(root / "spot-meta.json")
        self.perp_meta = self.read(root / "perp-meta.json")
        self.listings = {market["id"]: market["availableSince"]
                         for market in self.read(root / "tardis-listings.json")["availableSymbols"]}

    def read(self, path):
        raw = path.read_bytes()
        payload = json.loads(raw)
        self.audit[str(path)] = {
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "rows_or_fields": len(payload),
        }
        return payload

    def verify(self, asset):
        spot_name, spot_symbol, decimals, certified = MARKETS[asset]
        spot = next(market for market in self.spot_meta["universe"] if market["name"] == spot_symbol)
        size = {token["index"]: token for token in self.spot_meta["tokens"]}
        base, quote = (size[token_id] for token_id in spot["tokens"])
        perp = next(market for market in self.perp_meta["universe"] if market["name"] == asset)
        if (base["name"], quote["name"], min(base["szDecimals"], perp["szDecimals"])) != (
            spot_name, "USDC", decimals
        ):
            raise ValueError(f"Unexpected spot pairing or common lot size for {asset}")
        archived_spot, archived_perp = self.listings[spot_symbol], self.listings[asset]
        if max(archived_spot[:10], archived_perp[:10]) > certified:
            raise ValueError(f"Archived availability starts after the frozen eligibility date for {asset}")
        return {
            "asset": asset, "base_token": spot_name, "base_token_id": base["tokenId"],
            "spot_symbol": spot_symbol, "perp_symbol": asset, "archive_spot_from_utc": archived_spot,
            "archive_perp_from_utc": archived_perp, "frozen_eligible_from_utc": certified,
            "spot_lot_decimals": base["szDecimals"], "perp_lot_decimals": perp["szDecimals"],
            "common_lot_decimals": decimals,
        }

    def roots(self, asset, kind):
        early = self.root / "early"
        late = self.root / "late"
        if asset == "SOL" and self.trend_cache is not None:
            if kind == "perp":
                return [self.trend_cache, late] if self.diagnostic else [self.trend_cache]
            if kind == "funding" and self.diagnostic:
                return [early, self.trend_cache, late]
        if not self.diagnostic:
            return [early]
        if asset in ("BTC", "ETH") and self.cash_carry_cache is not None:
            return [early, self.cash_carry_cache / "older", self.cash_carry_cache]
        return [early, late]

    def candles(self, asset, kind, start):
        symbol = MARKETS[asset][1] if kind == "spot" else asset
        series = {}
        for root in self.roots(asset, kind):
            for row in self.read(root / f"{asset}-{kind}-candles.json"):
                ts = row["t"]
                if not start <= ts <= self.end:
                    continue
                opening, close, low, high, volume = (float(row[field]) for field in ("o", "c", "l", "h", "v"))
                if (row["s"] != symbol or row["i"] != "4h" or ts % BAR or row["T"] != ts + BAR - 1
                        or not all(math.isfinite(x) for x in (opening, close, low, high, volume))
                        or not 0 < low <= min(opening, close) <= max(opening, close) <= high
                        or volume < 0 or row["n"] < 0):
                    raise ValueError(f"Invalid {symbol} candle at {stamp(ts)}")
                candle = Candle(opening, close, low, high, volume)
                # If two captures straddled a cutoff, their complete boundary bars should match.
                if series.setdefault(ts, candle) != candle:
                    raise ValueError(f"Conflicting {symbol} candles for {stamp(ts)}")
        return series

    def funding(self, asset, start):
        series = {}
        for root in self.roots(asset, "funding"):
            paths = sorted(root.glob(f"{asset}-funding-*.json"))
            if not paths:
                raise FileNotFoundError(f"No {asset} funding pages in {root}")
            for path in paths:
                for row in self.read(path):
                    ts = row["time"] // HOUR * HOUR
                    if not start <= ts <= self.end:
                        continue
                    rate = float(row["fundingRate"])
                    if row["coin"] != asset or not math.isfinite(rate) or abs(rate) > 0.00501:
                        raise ValueError(f"Unexpected {asset} funding event in {path}")
                    if series.setdefault(ts, (row["time"], rate)) != (row["time"], rate):
                        raise ValueError(f"Conflicting {asset} funding rows for {stamp(ts)}")
        return series

    def market(self, asset):
        meta = self.verify(asset)
        start = epoch(max(WARMUP, MARKETS[asset][3]))
        spot = self.candles(asset, "spot", start)
        perp = self.candles(asset, "perp", start)
        funding_events = self.funding(asset, start)
        funding = {ts: rate for ts, (_, rate) in funding_events.items()}
        for name, series, interval in (("spot", spot, BAR), ("perp", perp, BAR), ("funding", funding, HOUR)):
            expected = set(range(start, self.end + 1, interval))
            missing = sorted(expected - series.keys())
            if not series or missing:
                examples = ", ".join(stamp(ts) for ts in missing[:3])
                raise ValueError(f"{asset} {name} is missing {len(missing)} observations. Earliest {examples}")
            meta[f"{name}_first_utc"] = stamp(min(series))
            meta[f"{name}_last_utc"] = stamp(max(series))
            meta[f"{name}_rows"] = len(series)
        meta["funding_largest_settlement_delay_seconds"] = max((original - ts) / 1000
                                                               for ts, (original, _) in funding_events.items())
        return {"spot": spot, "perp": perp, "funding": funding, "meta": meta, "start": start}


def cash_return(start, stop):
    return (1 + CASH_HURDLE_ANNUAL_RATE) ** ((stop - start) / (365 * DAY)) - 1


def screen(market, decision):
    past_start = decision - TERM
    if past_start < market["start"]:
        return {"eligible": False, "reasons": ["listing_and_28_day_warmup"]}
    spot, perp, funding = (market[kind] for kind in ("spot", "perp", "funding"))
    lookback = range(past_start, decision, BAR)
    previous = decision - BAR
    median_volume = [median(tape[t].close * tape[t].volume for t in lookback) for tape in (spot, perp)]
    basis = perp[previous].close / spot[previous].close - 1

    def stressed_rate(rate):
        return COMBINED.positive_funding_credit * 0.995 * rate if rate >= 0 else 1.005 * rate

    weekly = [math.fsum(stressed_rate(funding[t]) for t in range(monday, monday + WEEK, HOUR))
              for monday in range(past_start, decision, WEEK)]
    projection = 4 * median(weekly)
    expected_return = SLEEVE_DEPLOYMENT * (projection - STRESSED_ROUND_TRIP_COST - BASIS_RESERVE)
    excess = expected_return - cash_return(decision, decision + TERM)
    reasons = []
    if min(median_volume) < VOLUME_FLOOR:
        reasons.append("trailing_volume_below_floor")
    if abs(basis) > BASIS_RESERVE:
        reasons.append("prior_basis_outside_reserve")
    eligible = not reasons
    if excess <= 0:
        reasons.append("forecast_below_cash_hurdle")
    return {
        "eligible": eligible, "reasons": reasons,
        "prior_basis_bps": 10_000 * basis, "projected_funding_bps": 10_000 * projection,
        "median_spot_4h_notional_usdc": median_volume[0], "median_perp_4h_notional_usdc": median_volume[1],
        "stressed_weekly_funding_bps": [10_000 * rate for rate in weekly],
        "forecast_return_bps_of_sleeve": 10_000 * expected_return,
        "forecast_excess_bps_of_sleeve": 10_000 * excess,
        "actual_trailing_28d_funding": math.fsum(funding[t] for t in range(past_start, decision, HOUR)),
    }


def allocation(signals, policy):
    eligible = [asset for asset, signal in signals.items() if signal["eligible"]]
    if policy == "always_on":
        return sorted(eligible), len(eligible) or 1
    candidates = [asset for asset in eligible if signals[asset]["forecast_excess_bps_of_sleeve"] > 0]
    ranked = sorted(candidates, key=lambda asset: (-signals[asset]["forecast_excess_bps_of_sleeve"], asset))
    return ranked[:SLEEVES], SLEEVES


def replay_window(markets, decision, close, cash, assets, sleeves, signals, scenario):
    ledgers = {}
    increments = {}
    initial_unallocated = cash * (sleeves - len(assets)) / sleeves
    for asset in assets:
        market = markets[asset]
        plan = Holding(decision, decision + BAR, close, signals[asset]["actual_trailing_28d_funding"])
        spot, perp = market["spot"], market["perp"]
        marks, ledger = replay_holding(spot, perp, market["funding"], cash / sleeves, plan,
                                       market["meta"]["common_lot_decimals"], scenario)
        ledger["allocation_usdc"] = cash / sleeves
        ledger["marked_extra_perp_exit_100bp_usdc"] = (
            NEXT_EXIT_SURCHARGE * ledger["quantity"] * perp[close].opening * (1 + scenario.execution_bps / 10_000)
        )
        ledger["execution_volume_supported"] = max(ledger["maximum_entry_proportion_of_bar_volume"],
                                                   ledger["maximum_exit_proportion_of_bar_volume"]) <= MAX_BAR_PARTICIPATION
        ledgers[asset] = ledger
        increments[asset] = marks
    path = {decision: cash}
    for t in range(decision + BAR, close + BAR, BAR):
        path[t] = initial_unallocated + math.fsum(marks[t] for marks in increments.values())
    peak = cash
    drawdown = 0.0
    for mark in path.values():
        peak = max(peak, mark)
        drawdown = max(drawdown, 1 - mark / peak)
    ending = path[close]
    ops = ("net_funding_usdc", "basis_pnl_before_costs_usdc", "fees_usdc", "execution_reserve_usdc",
           "marked_extra_perp_exit_100bp_usdc")
    rollup = {key: math.fsum(ledger[key] for ledger in ledgers.values()) for key in ops}
    if not math.isclose(ending - cash, rollup["net_funding_usdc"] + rollup["basis_pnl_before_costs_usdc"]
                        - rollup["fees_usdc"] - rollup["execution_reserve_usdc"], abs_tol=1e-9):
        raise ValueError("Portfolio P&L fails to reconcile")
    return {
        **rollup, "begin_usdc": cash, "end_usdc": ending, "net_usdc": ending - cash,
        "paper_return": ending / cash - 1, "cash_4pct_usdc": cash * cash_return(decision, close),
        "excess_to_cash_4pct_usdc": ending - cash * (1 + cash_return(decision, close)),
        "end_under_extra_perp_exit_100bp_usdc": ending - rollup["marked_extra_perp_exit_100bp_usdc"],
        "end_usd_under_1pct_terminal_mark": ending * (1 - TERMINAL_USDC_HAIRCUT),
        "unallocated_usdc": initial_unallocated, "drawdown_4h": drawdown,
        "minimum_headroom_usdc": min((leg["short_minimum_margin_headroom_usdc"]
                                      for leg in ledgers.values()), default=None),
        "complete_modeled_exposure": all(leg["initial_full_funding_satisfied"] and leg["margin_breach_utc"] is None
                                        and leg["execution_volume_supported"] for leg in ledgers.values()),
        "positions": ledgers,
    }


def phase(decision):
    if decision < epoch(FIRST_PRIMARY):
        return "descriptive"
    if decision < epoch(PRIMARY_END):
        return "untouched_primary"
    return "retrospective_late_diagnostic"


def staking_sensitivity(market):
    start, midpoint, end = (epoch(day) for day in STAKING_DATES)
    cash = INITIAL_USDC
    rows = []
    for decision, close in ((start, midpoint), (midpoint, end)):
        result = replay_window({"HYPE": market}, decision, close, cash, ["HYPE"], 1,
                               {"HYPE": {"actual_trailing_28d_funding": None}}, COMBINED)
        ledger = result["positions"]["HYPE"]
        exit_price_net = (market["spot"][close].opening * (1 - COMBINED.execution_bps / 10_000) * (1 - SPOT_FEE))
        stake_time = (close - STAKING_EXIT_LEAD - (decision + BAR)) / (365 * DAY)
        principal_at_exit = ledger["quantity"] * exit_price_net
        reward = principal_at_exit * ((1 + STAKING_TOKEN_APY) ** stake_time - 1)
        required_reward = max(0, -result["excess_to_cash_4pct_usdc"])
        result.update({
            "decision_utc": stamp(decision), "close_utc": stamp(close),
            "withdrawal_requested_utc": stamp(close - STAKING_EXIT_LEAD),
            "staking_reward_assumed_apy": STAKING_TOKEN_APY, "staking_reward_credit_usdc": reward,
            "cash_breakeven_token_reward_apy": (1 + required_reward / principal_at_exit) ** (1 / stake_time) - 1,
            "net_with_staking_usdc": result["net_usdc"] + reward,
            "excess_with_staking_usdc": result["excess_to_cash_4pct_usdc"] + reward,
            "net_with_staking_and_exit_100bp_usdc": (
                result["net_usdc"] + reward - result["marked_extra_perp_exit_100bp_usdc"]
            ),
        })
        rows.append(result)
        cash = result["end_usdc"] + reward
    return {
        "assumed_apy_sensitivity_only": True, "windows": rows, "end_usdc": cash,
        "cash_4pct_end_usdc": INITIAL_USDC * (1 + cash_return(start, end)),
        "beat_cash_in_both_windows_with_assumed_apy": all(
            row["complete_modeled_exposure"] and row["excess_with_staking_usdc"] > 0 for row in rows
        ),
    }


def report(tape):
    markets = {asset: tape.market(asset) for asset in MARKETS}
    policies = ("selective", "always_on")
    scenarios = {"base": SCENARIOS["base"], "funding_stress": COMBINED}
    equity = {policy: dict.fromkeys(scenarios, INITIAL_USDC) for policy in policies}
    windows = []
    for decision in range(epoch(FIRST_DECISION), tape.end, TERM):
        close = min(decision + TERM, tape.end)
        portfolio = {policy: {} for policy in policies}
        signals = {asset: screen(market, decision) for asset, market in markets.items()}
        for policy in policies:
            assets, sleeves = allocation(signals, policy)
            for label, scenario in scenarios.items():
                result = replay_window(markets, decision, close, equity[policy][label], assets, sleeves, signals, scenario)
                portfolio[policy][label] = result
                equity[policy][label] = result["end_usdc"]
        windows.append({"decision_utc": stamp(decision), "close_utc": stamp(close), "phase": phase(decision),
                        "cash_4pct_compounded_return": cash_return(decision, close), "signals": signals,
                        "policies": portfolio})

    primary = [window["policies"]["selective"]["funding_stress"] for window in windows
               if window["phase"] == "untouched_primary"]
    result = {
        "public_inputs": tape.audit, "markets": {asset: market["meta"] for asset, market in markets.items()},
        "initial_usdc": INITIAL_USDC, "decisions_utc_from": FIRST_DECISION,
        "end_utc": stamp(tape.end), "fixed_cash_4pct_end_usdc": INITIAL_USDC * (1 + cash_return(epoch(FIRST_DECISION), tape.end)),
        "idle_cash_end_usdc": INITIAL_USDC,
        "threshold_projected_stress_funding_bps": 10_000 * (STRESSED_ROUND_TRIP_COST + BASIS_RESERVE
                                                            + cash_return(0, TERM) / SLEEVE_DEPLOYMENT),
        "policy_end_usdc": equity, "windows": windows,
        "qualified": len(primary) == 2 and all(r["excess_to_cash_4pct_usdc"] > 0 and r["complete_modeled_exposure"]
                                                 for r in primary),
    }
    if tape.diagnostic:
        result["native_hype_staking_sensitivity"] = staking_sensitivity(markets["HYPE"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/agent-selective-carry"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--capture", action="store_true", help="Cache only public market and reference data")
    parser.add_argument("--diagnostic", action="store_true", help="Run additional retrospective windows through 2026-11-22")
    parser.add_argument("--spot-carry-cache", type=Path, help="Reuse the two public BTC and ETH archives from spot carry")
    parser.add_argument("--trend-cache", type=Path, help="Reuse the public native trend SOL perpetual and funding archive")
    args = parser.parse_args()
    if args.capture:
        capture(args.data, args.diagnostic, args.spot_carry_cache, args.trend_cache)
    result = report(SelectiveTape(args.data, args.diagnostic, args.spot_carry_cache, args.trend_cache))
    output = args.output or args.data / ("diagnostic.json" if args.diagnostic else "primary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"Qualified after combined stress: {result['qualified']}. Report written to {output}")


if __name__ == "__main__":
    main()
