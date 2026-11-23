"""Paper replay of a fixed native-perpetual premium ranking."""

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from .spot_carry import BAR, DAY, ENDPOINT, HOUR, Candle, PublicArchive, epoch, funding_pages, stamp

ASSETS = ("BTC", "ETH", "SOL", "HYPE")
DATES = ("2025-09-01", "2025-12-01", "2026-04-01", "2026-07-01", "2026-10-01", "2026-11-22")
WARMUP = "2025-08-27"
CAPITAL = 2_000.0
LOOKBACK_HOURS = 120
PERP_FEE = 0.00045
MINIMUM_ORDER = 10


@dataclass(frozen=True)
class Scenario:
    delay: int
    execution_bps: int
    funding_credit: float


SCENARIOS = {"base": Scenario(BAR, 5, 1.0), "stress": Scenario(2 * BAR, 15, 0.75)}


def capture(path):
    archive = PublicArchive(path)
    archive.get("perp-meta.json", {"type": "meta"}, 20)
    start, end = epoch(DATES[0]), epoch(DATES[-1])
    for asset in ASSETS:
        archive.get(f"{asset}-perp-candles.json", {"type": "candleSnapshot", "req": {
            "coin": asset, "interval": "4h", "startTime": start - BAR, "endTime": end,
        }}, 20 + math.ceil(((end - start) // BAR + 2) / 60))
        for label, left, right in funding_pages(WARMUP, DATES[-1]):
            final_hour = end + HOUR - 1 if right == end else right
            archive.get(f"{asset}-funding-{label}.json", {
                "type": "fundingHistory", "coin": asset, "startTime": left, "endTime": final_hour,
            }, 44)


class MarketData:
    def __init__(self, folders):
        self.audit = {}
        self.candles = {}
        self.funding = {}
        self.decimals = {}
        metadata = next((path / "perp-meta.json" for path in folders if (path / "perp-meta.json").is_file()), None)
        if metadata is None:
            raise ValueError("No perp-meta.json capture in supplied folders")
        entries = {item["name"]: item for item in self.read(metadata)["universe"]}
        for asset in ASSETS:
            self.decimals[asset] = entries[asset]["szDecimals"]
            self.candles[asset] = self.load_candles(asset, folders)
            self.funding[asset] = self.load_funding(asset, folders)

    def read(self, path):
        content = path.read_bytes()
        rows = json.loads(content)
        self.audit[path.as_posix()] = {"sha256": hashlib.sha256(content).hexdigest(), "rows_or_fields": len(rows)}
        return rows

    def load_candles(self, asset, folders):
        expected = set(range(epoch(DATES[0]) - BAR, epoch(DATES[-1]) + 1, BAR))
        series = {}
        for folder in folders:
            path = folder / f"{asset}-perp-candles.json"
            if not path.is_file():
                continue
            for row in self.read(path):
                t = row["t"]
                if t not in expected:
                    continue
                open_, close, low, high, volume = (float(row[key]) for key in "oclhv")
                if (row["s"] != asset or row["i"] != "4h" or row["T"] != t + BAR - 1
                        or not all(math.isfinite(p) for p in (open_, close, low, high, volume))
                        or not 0 < low <= min(open_, close) <= max(open_, close) <= high or volume <= 0):
                    raise ValueError(f"Invalid {asset} candle at {stamp(t)}")
                candle = Candle(open_, close, low, high, volume)
                if series.setdefault(t, candle) != candle:
                    raise ValueError(f"Conflicting {asset} candles at {stamp(t)}")
            if series.keys() == expected:
                break
        self.require_grid(series, expected, f"{asset} candles")
        return series

    def load_funding(self, asset, folders):
        expected = set(range(epoch(WARMUP), epoch(DATES[-1]) + 1, HOUR))
        series = {}
        for folder in folders:
            for path in sorted(folder.glob(f"{asset}-funding-*.json")):
                for row in self.read(path):
                    hour, offset = divmod(row["time"], HOUR)
                    t = hour * HOUR
                    if t not in expected:
                        continue
                    rate, premium = (float(row[key]) for key in ("fundingRate", "premium"))
                    if row["coin"] != asset or offset >= 60_000 or not (math.isfinite(rate) and math.isfinite(premium)):
                        raise ValueError(f"Invalid {asset} funding at {stamp(t)}")
                    event = (rate, premium)
                    if series.setdefault(t, event) != event:
                        raise ValueError(f"Conflicting {asset} funding at {stamp(t)}")
            if series.keys() == expected:
                break
        self.require_grid(series, expected, f"{asset} funding")
        return series

    @staticmethod
    def require_grid(series, expected, description):
        missing = sorted(expected - series.keys())
        if missing:
            raise ValueError(f"{description} missing {len(missing)} observations starting at {stamp(missing[0])}")


def premium_pair(funding, decision):
    periods = range(decision - LOOKBACK_HOURS * HOUR, decision, HOUR)
    scores = {asset: math.fsum(funding[asset][t][1] for t in periods) for asset in ASSETS}
    long = min(scores, key=scores.get)
    short = max(scores, key=scores.get)
    return (long, short) if scores[long] < scores[short] else ()


def funding_cashflow(quantity, rate, opening, previous_bar, scenario):
    raw_rate = -quantity * rate
    if raw_rate == 0:
        return 0.0
    if scenario.funding_credit == 1:
        return raw_rate * opening
    if raw_rate > 0:
        return raw_rate * min(opening, previous_bar.low) * scenario.funding_credit
    return raw_rate * max(opening, previous_bar.high)


def replay_window(data, start, end, scenario, pairs=None):
    if pairs is None:
        pairs = {t: premium_pair(data.funding, t) for t in range(start, end, DAY)}
    # Mark changes accrue to held positions. Trade cash costs and funding are tracked separately.
    holdings = dict.fromkeys(ASSETS, 0.0)
    targets = holdings.copy()
    price_pnl = funding_pnl = total_fees = total_reserve = turnover = 0.0
    peak = anchor = CAPITAL
    drawdown = 0.0
    minimum_headroom = CAPITAL
    daily_returns = []
    trades = trade_days = 0
    asset_price_pnl = dict.fromkeys(ASSETS, 0.0)
    previous_opens = {asset: data.candles[asset][start - BAR].opening for asset in ASSETS}
    for t in range(start, end + 1, BAR):
        bars = {asset: data.candles[asset][t] for asset in ASSETS}
        prior_bars = {asset: data.candles[asset][t - BAR] for asset in ASSETS}
        opens = {asset: bars[asset].opening for asset in ASSETS}
        raw_trade_pnl = {asset: holdings[asset] * (opens[asset] - previous_opens[asset]) for asset in ASSETS}
        for asset, value in raw_trade_pnl.items():
            asset_price_pnl[asset] += value
        price_pnl += math.fsum(raw_trade_pnl.values())
        opening_equity = CAPITAL + price_pnl + funding_pnl - total_fees - total_reserve
        if t < end and t % DAY == 0:
            if t > start:
                daily_returns.append(opening_equity / anchor - 1)
                anchor = opening_equity
            # Decision equity is known from the preceding close, before today's opening print.
            decision_equity = opening_equity + math.fsum(
                holdings[asset] * (prior_bars[asset].close - opens[asset]) for asset in ASSETS
            )
            pair = pairs[t]
            targets = dict.fromkeys(ASSETS, 0.0)
            if pair:
                for asset, sign in zip(pair, (1, -1), strict=True):
                    lot = 10 ** data.decimals[asset]
                    targets[asset] = sign * math.floor(max(0, decision_equity) * 0.25 / prior_bars[asset].close * lot) / lot
            for asset in ASSETS:
                if abs(targets[asset] - holdings[asset]) * prior_bars[asset].close < MINIMUM_ORDER:
                    targets[asset] = holdings[asset]

        boundary_funding = math.fsum(
            funding_cashflow(holdings[asset], data.funding[asset][t][0], opens[asset], prior_bars[asset], scenario)
            for asset in ASSETS
        )
        funding_pnl += boundary_funding
        if t == end or t % DAY == scenario.delay:
            if t == end:
                targets = dict.fromkeys(ASSETS, 0.0)
            filled = False
            for asset in ASSETS:
                trade = targets[asset] - holdings[asset]
                if trade == 0 or abs(trade) * opens[asset] < MINIMUM_ORDER:
                    continue
                reserve = abs(trade) * opens[asset] * scenario.execution_bps / 10_000
                fill_notional = abs(trade) * opens[asset] + math.copysign(reserve, trade)
                total_fees += fill_notional * PERP_FEE
                total_reserve += reserve
                turnover += fill_notional
                holdings[asset] = targets[asset]
                trades += 1
                filled = True
            trade_days += filled

        equity = CAPITAL + price_pnl + funding_pnl - total_fees - total_reserve
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        if t == end:
            daily_returns.append(equity / anchor - 1)
            break

        intra_funding = [
            funding_cashflow(holdings[asset], data.funding[asset][hour][0], opens[asset], prior_bars[asset], scenario)
            for hour in range(t + HOUR, t + BAR, HOUR)
            for asset in ASSETS
        ]
        adverse = {asset: bars[asset].low if holdings[asset] > 0 else bars[asset].high for asset in ASSETS}
        adverse_pnl = math.fsum(holdings[asset] * (adverse[asset] - opens[asset]) for asset in ASSETS)
        adverse_gross = math.fsum(abs(holdings[asset]) * adverse[asset] for asset in ASSETS)
        headroom = equity + adverse_pnl + math.fsum(min(0, amount) for amount in intra_funding) - 0.1 * adverse_gross
        minimum_headroom = min(minimum_headroom, headroom)
        if headroom <= 0:
            raise ValueError(f"Adverse four-hour margin screen breached at {stamp(t)}")
        funding_pnl += math.fsum(intra_funding)
        previous_opens = opens

    if any(holdings.values()):
        raise ValueError("A terminal leg could not be closed under the minimum order size")
    if not math.isclose(equity, CAPITAL * math.prod(1 + r for r in daily_returns), abs_tol=1e-8):
        raise ValueError("Daily returns did not reconcile to equity")
    cash = CAPITAL * (1.04 ** ((end - start) / DAY / 365) - 1)
    best_day = max(daily_returns)
    return {
        "net_pnl_usdc": equity - CAPITAL,
        "cash_pnl_usdc": cash,
        "excess_vs_cash_usdc": equity - CAPITAL - cash,
        "gross_price_pnl_usdc": price_pnl,
        "price_pnl_by_asset_usdc": asset_price_pnl,
        "funding_pnl_usdc": funding_pnl,
        "fees_usdc": total_fees,
        "execution_reserve_usdc": total_reserve,
        "trade_legs": trades,
        "trade_events": trade_days,
        "turnover_usdc": turnover,
        "max_marked_drawdown_pct": drawdown * 100,
        "minimum_adverse_margin_headroom_usdc": minimum_headroom,
        "best_day_pct": best_day * 100,
        "net_pnl_without_best_day_usdc": equity / (1 + best_day) - CAPITAL,
        "daily_returns": daily_returns,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, nargs="+", required=True)
    parser.add_argument("--fetch", action="store_true", help="Fetch an independent tape in the first data folder")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.fetch:
        capture(args.data[0])
    data = MarketData(args.data)
    dates = [epoch(day) for day in DATES]
    windows = []
    for start, end in zip(dates, dates[1:]):
        windows.append({
            "start_utc": stamp(start),
            "end_utc": stamp(end),
            "scenarios": {name: replay_window(data, start, end, case) for name, case in SCENARIOS.items()},
        })
    result = {
        "endpoint": ENDPOINT,
        "starting_full_capital_usdc": CAPITAL,
        "annual_cash_rate": 0.04,
        "historical_screen_pass": all(window["scenarios"]["stress"]["excess_vs_cash_usdc"] > 0 for window in windows[1:]),
        "input_files": data.audit,
        "windows": windows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, allow_nan=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
