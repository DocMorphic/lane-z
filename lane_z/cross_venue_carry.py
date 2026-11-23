"""Paper replay same-asset Hyperliquid and Binance perpetual funding spreads."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from urllib.parse import urlencode
from urllib.request import urlopen

from .selective_carry import TERM, WEEK, cash_return
from .spot_carry import (BAR, DAY, HOUR, MAINTENANCE_RATE, MIN_NOTIONAL_USD,
                         PERP_FEE, SCENARIOS, SLEEVE_DEPLOYMENT, Candle, Tape, epoch, stamp)


BEGIN = "2025-12-21"
WARMUP = "2025-11-23"
PRIMARY = "2026-09-27"
END = "2026-11-22"
ASSETS = ("BTC", "ETH")
VENUES = ("hyperliquid", "binance")
BINANCE = "https://fapi.binance.com/fapi/v1"
START_BALANCE = 1_000.0
MINIMUM_BAR_USDC = 100_000.0
MAX_PARTICIPATION = 0.01
BASIS_ALLOWANCE = 0.0015
EXTRA_SINGLE_EXIT = 0.01
SIZE_SCALE = 1_000
TAKER = {"hyperliquid": PERP_FEE, "binance": 0.0005}
USDC_PROMOTIONAL_TAKER = {**TAKER, "binance": 0.0004}
STRESS = SCENARIOS["funding_stress"]


def pull(root, file, endpoint, query):
    path = root / file
    if path.exists():
        return json.loads(path.read_bytes())
    with urlopen(f"{BINANCE}/{endpoint}?{urlencode(query)}", timeout=30) as response:
        raw = response.read()
    result = json.loads(raw)
    if not isinstance(result, (list, dict)) or not result or isinstance(result, dict) and "code" in result:
        raise ValueError(f"Invalid public Binance response for {file}")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    return result


def capture(root):
    root.mkdir(parents=True, exist_ok=True)
    pull(root, "exchange-info.json", "exchangeInfo", {})
    first, last = epoch(WARMUP), epoch(END)
    for asset in ASSETS:
        symbol = asset + "USDC"
        for begin in range(first, last + BAR, 120 * DAY):
            for kind, endpoint in (("trade", "klines"), ("mark", "markPriceKlines")):
                pull(root, f"{asset}-{kind}-bars-{stamp(begin)[:10]}.json", endpoint, {
                    "symbol": symbol, "interval": "4h", "limit": 1_000, "startTime": begin,
                    "endTime": min(begin + 120 * DAY - 1, last),
                })
        cursor = first - HOUR
        while cursor <= last:
            page = pull(root, f"{asset}-funding-{cursor}.json", "fundingRate", {
                "symbol": symbol, "limit": 1_000, "startTime": cursor, "endTime": last + HOUR - 1,
            })
            if len(page) < 1_000:
                break
            cursor = page[-1]["fundingTime"] + 1


@dataclass(frozen=True)
class Venue:
    trade: dict
    margin: dict
    funding: dict
    minimum_notional: float
    minimum_units: float
    oracle: dict | None = None


def from_hyperliquid(archive_root):
    archives = (Tape(archive_root / "older", "2025-08-01", "2025-12-01"),
                Tape(archive_root, "2025-12-01", END))
    venues = {}
    first, last = epoch(WARMUP), epoch(END)
    for asset, spot_id in (("BTC", "@142"), ("ETH", "@151")):
        prices, spot, funding = {}, {}, {}
        for source in archives:
            source.verify_market(asset)
            prices.update(source.candles(asset, "perp", asset))
            spot.update(source.candles(asset, "spot", spot_id))
            funding.update(source.funding(asset))
        # The bounds of the two caches touch at 2025-12-01 00:00 UTC.
        venues[asset] = Venue(
            {t: prices[t] for t in range(first, last + BAR, BAR)},
            {t: prices[t] for t in range(first, last + BAR, BAR)},
            {t: (funding[t], None) for t in range(first, last + HOUR, HOUR)},
            MIN_NOTIONAL_USD, 0.0001, {t: spot[t] for t in range(first, last + BAR, BAR)},
        )
    audit = {str(source.root / name): details for source in archives for name, details in source.audit.items()}
    return venues, audit


def binance_bars(read, root, asset, kind, first, last):
    series = {}
    for path in sorted(root.glob(f"{asset}-{kind}-bars-*.json")):
        for trading in read(path):
            timestamp, end = int(trading[0]), int(trading[6])
            opening, high, low, close, volume = map(float, trading[1:6])
            if (timestamp % BAR or end != timestamp + BAR - 1 or timestamp in series
                    or not all(math.isfinite(x) for x in (opening, high, low, close, volume))
                    or not 0 < low <= min(opening, close) <= max(opening, close) <= high
                    or kind == "trade" and volume <= 0):
                raise ValueError(f"Invalid Binance {kind} candle for {asset} at {stamp(timestamp)}")
            series[timestamp] = Candle(opening, close, low, high, volume)
    if series.keys() != set(range(first, last + BAR, BAR)):
        raise ValueError(f"Incomplete Binance {kind} bars for {asset}")
    return series


def from_binance(root):
    audit = {}

    def read(path):
        raw = path.read_bytes()
        audit[str(path)] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        return json.loads(raw)

    first, last = epoch(WARMUP), epoch(END)
    venues = {}
    products = {row["symbol"]: row for row in read(root / "exchange-info.json")["symbols"]}
    for asset in ASSETS:
        symbol = asset + "USDC"
        product = products[symbol]
        if (product["contractType"], product["quoteAsset"], product["marginAsset"], product["baseAsset"]) != (
            "PERPETUAL", "USDC", "USDC", asset
        ) or product["onboardDate"] > first:
            raise ValueError(f"Unexpected Binance geometry for {symbol}")
        filters = {row["filterType"]: row for row in product["filters"]}
        market_lot = filters["MARKET_LOT_SIZE"]
        if float(market_lot["stepSize"]) != 1 / SIZE_SCALE:
            raise ValueError(f"Unexpected Binance market lot for {symbol}")
        prices = binance_bars(read, root, asset, "trade", first, last)
        marks = binance_bars(read, root, asset, "mark", first, last)
        funding = {}
        for path in sorted(root.glob(f"{asset}-funding-*.json")):
            for item in read(path):
                timestamp = item["fundingTime"]
                hour = round(timestamp / HOUR) * HOUR
                if not first <= hour <= last:
                    continue
                rate, mark = float(item["fundingRate"]), float(item["markPrice"])
                if (item["symbol"] != symbol or abs(hour - timestamp) >= 60_000 or hour in funding
                        or not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0 or abs(rate) > 0.03):
                    raise ValueError(f"Invalid Binance funding for {symbol} at {stamp(timestamp)}")
                funding[hour] = rate, mark
        if funding.keys() != set(range(first, last + HOUR, 8 * HOUR)):
            raise ValueError(f"Verify the observed eight-hour settlement schedule for {symbol}")
        venues[asset] = Venue(prices, marks, funding, float(filters["MIN_NOTIONAL"]["notional"]),
                              float(market_lot["minQty"]))
    return venues, audit


def forecast(data, t, taker):
    previous = t - BAR
    history = range(t - TERM, t, BAR)
    a, b = (data[venue] for venue in VENUES)
    basis = a.trade[previous].close / b.trade[previous].close - 1
    volume = {name: median(venue.trade[bar].close * venue.trade[bar].volume for bar in history)
              for name, venue in data.items()}
    differences = {}
    for short in VENUES:
        weeks, stress_weeks = [], []
        for start in range(t - TERM, t, WEEK):
            flows = {name: [(1 if name == short else -1) * venue.funding[hour][0]
                            for hour in range(start, start + WEEK, HOUR) if hour in venue.funding]
                     for name, venue in data.items()}
            weeks.append(math.fsum(rate for leg in flows.values() for rate in leg))
            adjusted = [rate * (STRESS.positive_funding_credit if rate > 0 else 1)
                        * ((0.995 if rate > 0 else 1.005) if name == "hyperliquid" else 1)
                        for name, leg in flows.items() for rate in leg]
            stress_weeks.append(math.fsum(adjusted))
        differences[short] = {"base": 4 * median(weeks), "stress": 4 * median(stress_weeks)}
    eligible = abs(basis) <= BASIS_ALLOWANCE and min(volume.values()) >= MINIMUM_BAR_USDC
    threshold = (2 * sum(taker.values()) + 4 * STRESS.execution_bps / 10_000 + BASIS_ALLOWANCE
                 + cash_return(t, t + TERM) / SLEEVE_DEPLOYMENT)
    best = max(VENUES, key=lambda venue: differences[venue]["stress"])
    benchmark = max(VENUES, key=lambda venue: differences[venue]["base"])
    return {
        "median_4h_notional_usdc": volume, "prior_cross_venue_basis_bps": 10_000 * basis,
        "projected_28d_net_funding": differences, "threshold": threshold, "eligible": eligible,
        "selective_short_venue": best if eligible and differences[best]["stress"] > threshold else None,
        "benchmark_short_venue": benchmark if eligible else None,
    }


def settle(venue, tick, sign, scenario):
    rate, exact_mark = venue.funding[tick]
    rate *= -sign
    if exact_mark is None:
        proxy = venue.oracle[tick // BAR * BAR]
        if scenario.adverse_oracle:
            price = proxy.low * 0.995 if rate > 0 else proxy.high * 1.005
        else:
            price = proxy.opening
    else:
        price = exact_mark
    return rate * price * (scenario.positive_funding_credit if rate > 0 else 1)


def leg(venue, sign, units, capital, enter, close, scenario, taker):
    slip = scenario.execution_bps / 10_000
    enter_bar, exit_bar = venue.trade[enter], venue.trade[close]
    entry_price = enter_bar.opening * (1 + sign * slip)
    exit_price = exit_bar.opening * (1 - sign * slip)
    entry_fee = units * entry_price * taker
    exit_fee = units * exit_price * taker
    equity = capital - entry_fee
    minimum_headroom = math.inf
    funding = 0.0
    for t in range(enter, close, BAR):
        credits = [units * settle(venue, hour, sign, scenario) for hour in range(t, t + BAR, HOUR)
                   if hour in venue.funding and (hour != enter or -sign * venue.funding[hour][0] < 0)]
        worst_mark = venue.margin[t].low if sign > 0 else venue.margin[t].high
        pessimistic_funding = math.fsum(min(flow, 0) for flow in credits)
        minimum_headroom = min(minimum_headroom, equity + pessimistic_funding
                               + units * (sign * (worst_mark - entry_price) - worst_mark * MAINTENANCE_RATE))
        signed_flow = math.fsum(credits)
        funding += signed_flow
        equity += signed_flow
    if close in venue.funding and -sign * venue.funding[close][0] < 0:
        terminal_funding = units * settle(venue, close, sign, scenario)
        funding += terminal_funding
        equity += terminal_funding
    settlement_headroom = equity - exit_fee + units * (sign * (exit_price - entry_price)
                                                        - exit_price * MAINTENANCE_RATE)
    minimum_headroom = min(minimum_headroom, settlement_headroom)
    fees = entry_fee + exit_fee
    gross_price_pnl = units * sign * (exit_bar.opening - enter_bar.opening)
    costs = units * slip * (exit_bar.opening + enter_bar.opening)
    notional_valid = units >= venue.minimum_units and units * min(entry_price, exit_price) >= venue.minimum_notional
    initial_margin_ok = capital - entry_fee >= units * entry_price
    volume_fraction = units / min(enter_bar.volume, exit_bar.volume)
    return {
        "funding_usdc": funding, "price_pnl_usdc": gross_price_pnl, "slippage_usdc": costs, "fees_usdc": fees,
        "net_usdc": funding + gross_price_pnl - costs - fees, "entry_utc": stamp(enter), "close_utc": stamp(close),
        "allocated_usdc": capital, "units": units, "direction": "long" if sign > 0 else "short",
        "maximum_4h_volume_fraction": volume_fraction, "minimum_margin_headroom_usdc": minimum_headroom,
        "exit_payoff_penalty_100bp_usdc": units * exit_price * EXTRA_SINGLE_EXIT,
        "complete": notional_valid and initial_margin_ok and minimum_headroom >= 0 and volume_fraction <= MAX_PARTICIPATION,
    }


def replay(market, signals, start, close, balances, selective, scenario, taker):
    initial = sum(balances.values())
    venue_equity = {**balances}
    margin_by_venue = {name: value / len(ASSETS) for name, value in balances.items()}
    same_capital_basis = 2 * min(margin_by_venue.values())
    enter = start + BAR
    positions = {}
    for asset, signal in signals.items():
        short = signal["selective_short_venue" if selective else "benchmark_short_venue"]
        if short is None:
            continue
        prior = max(source.trade[start - BAR].close for source in market[asset].values())
        units = math.floor(SLEEVE_DEPLOYMENT * same_capital_basis / prior * SIZE_SCALE) / SIZE_SCALE
        fills = {venue: leg(market[asset][venue], -1 if venue == short else 1, units,
                            margin_by_venue[venue], enter, close, scenario, taker[venue]) for venue in VENUES}
        positions[asset] = fills
        for venue, ledger in fills.items():
            venue_equity[venue] += ledger["net_usdc"]
    result = {key: math.fsum(ledger[key] for pair in positions.values() for ledger in pair.values())
              for key in ("funding_usdc", "price_pnl_usdc", "slippage_usdc", "fees_usdc", "net_usdc")}
    net = sum(venue_equity.values()) - initial
    if not math.isclose(net, result["funding_usdc"] + result["price_pnl_usdc"]
                        - result["slippage_usdc"] - result["fees_usdc"], abs_tol=1e-8):
        raise ValueError("Venue ledgers do not reconcile")
    cash_net = initial * cash_return(start, close)
    return {
        **result, "begin_usdc": initial, "end_usdc": initial + net,
        "return_fraction": net / initial, "cash_4pct_usdc": cash_net, "cash_excess_usdc": net - cash_net,
        "unallocated_usdc": initial * (1 - len(positions) / len(ASSETS)),
        "allocated_margin_excess_at_richer_venue_usdc": len(positions) * abs(
            margin_by_venue["hyperliquid"] - margin_by_venue["binance"]
        ),
        "one_venue_exit_penalty_100bp_usdc": max(
            (ledger["exit_payoff_penalty_100bp_usdc"] for pair in positions.values() for ledger in pair.values()),
            default=0.0
        ),
        "positions": positions, "venue_begin_usdc": balances, "venue_end_usdc": venue_equity,
        "complete": all(ledger["complete"] for pair in positions.values() for ledger in pair.values()),
    }


def run(market, taker):
    scenarios = {"base": SCENARIOS["base"], "stress": STRESS}
    policies = {kind: {cost: dict.fromkeys(VENUES, START_BALANCE) for cost in scenarios}
                for kind in ("selective", "directional_benchmark")}
    windows = []
    for decision in range(epoch(BEGIN), epoch(END), TERM):
        close = decision + TERM
        signals = {asset: forecast(market[asset], decision, taker) for asset in ASSETS}
        performance = {}
        for kind, costs in policies.items():
            performance[kind] = {}
            for label, capital in costs.items():
                result = replay(market, signals, decision, close, capital, kind == "selective", scenarios[label], taker)
                performance[kind][label] = result
                costs[label] = result["venue_end_usdc"]
        windows.append({
            "decision_utc": stamp(decision), "end_utc": stamp(close), "primary": decision >= epoch(PRIMARY),
            "cash_4pct_return": cash_return(decision, close), "signals": signals, "performance": performance,
        })
    if windows[-1]["end_utc"] != stamp(epoch(END)):
        raise ValueError("The frozen evaluation range is not an exact multiple of 28 days")
    primary = [x["performance"]["selective"]["stress"] for x in windows if x["primary"]]
    return {
        "qualified": len(primary) == 2 and all(row["cash_excess_usdc"] > 0 and row["complete"] for row in primary),
        "windows": windows, "taker_fees_fraction": taker,
        "terminal_venue_balances": policies,
        "terminal_4pct_cash_usdc": 2 * START_BALANCE * (1 + cash_return(epoch(BEGIN), epoch(END))),
    }


def report(root, hyperliquid_root):
    hyperliquid, hl_audit = from_hyperliquid(hyperliquid_root)
    binance, bin_audit = from_binance(root)
    market = {asset: {"hyperliquid": hyperliquid[asset], "binance": binance[asset]} for asset in ASSETS}
    return {
        **run(market, TAKER),
        "binance_usdc_promotion": run(market, USDC_PROMOTIONAL_TAKER),
        "public_inputs": {**hl_audit, **bin_audit},
        "market_rows": {asset: {venue: {"4h_prices": len(source.trade), "funding_events": len(source.funding)}
                                for venue, source in data.items()} for asset, data in market.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true", help="Download public Binance market data")
    parser.add_argument("--data", type=Path, default=Path("data/agent-venue-carry"))
    parser.add_argument("--hyperliquid", type=Path, default=Path("data/agent-spot-carry"),
                        help="Root of the 2025–2026 public Hyperliquid spot-carry caches")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.capture:
        capture(args.data)
    result = report(args.data, args.hyperliquid)
    output = args.output or args.data / "results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"Qualified in both final periods: {result['qualified']}. Report written to {output}.")


if __name__ == "__main__":
    main()
