"""Replay a frozen long-or-cash QQQ trend rule against passive ETF and cash."""

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import floor
from pathlib import Path
from statistics import mean
from urllib.request import Request, urlopen


CAPITAL = 100_000.0
FIRST = date(2017, 1, 1)
LAST = date(2026, 11, 20)
VINTAGES = (
    (date(2018, 1, 1), date(2019, 12, 31)),
    (date(2020, 1, 1), date(2021, 12, 31)),
    (date(2022, 1, 1), date(2023, 12, 31)),
    (date(2024, 1, 1), LAST),
)
HISTORICAL = "nasdaq-qqq-historical.json"
VENUE_DIVIDENDS = "nasdaq-qqq-dividends.json"
ISSUER_DIVIDENDS = "invesco-qqq-distribution.json"
ISSUER_PRICE_CHECK = "invesco-qqq-prices.json"
SOURCES = {
    HISTORICAL: ("https://api.nasdaq.com/api/quote/QQQ/historical?assetclass=etf"
                 f"&fromdate={FIRST}&todate={LAST}&limit=5000"),
    VENUE_DIVIDENDS: "https://api.nasdaq.com/api/quote/QQQ/dividends?assetclass=etf",
    ISSUER_DIVIDENDS: ("https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46090E103/"
                       "distribution?idType=cusip&productType=ETF"),
    ISSUER_PRICE_CHECK: ("https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46090E103/"
                         "prices?idType=cusip&variationType=priceListing&productType=ETF"),
}
SCENARIOS = ((1, 1), (5, 1), (15, 1), (1, 2), (15, 2))
SELL_FEE_BPS = 0.3


@dataclass(frozen=True)
class Bar:
    day: date
    opening: float
    close: float


@dataclass(frozen=True)
class Distribution:
    ex_day: date
    payable: date
    amount: Decimal


def fetch(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    agent = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
    for name, url in SOURCES.items():
        referer = "https://www.nasdaq.com" if url.startswith("https://api.nasdaq.com/") else "https://www.invesco.com"
        request = Request(url, headers={"User-Agent": agent, "Accept": "application/json", "Origin": referer,
                                        "Referer": f"{referer}/"})
        with urlopen(request, timeout=30) as response:
            payload = response.read()
        json.loads(payload)
        (root / name).write_bytes(payload)


def american_date(value: str) -> date:
    return datetime.strptime(value, "%m/%d/%Y").date()


def load(root: Path) -> tuple[list[Bar], dict[date, Distribution], dict]:
    raw = {name: (root / name).read_bytes() for name in SOURCES}
    prices = json.loads(raw[HISTORICAL])["data"]
    price_rows = prices["tradesTable"]["rows"]
    bars = sorted((Bar(american_date(row["date"]), float(row["open"].replace(",", "")),
                       float(row["close"].replace(",", ""))) for row in price_rows), key=lambda row: row.day)
    if (prices["symbol"] != "QQQ" or prices["totalRecords"] != len(bars)
            or len({bar.day for bar in bars}) != len(bars)
            or bars[0].day != date(2017, 1, 3) or bars[-1].day != LAST
            or any(not (bar.opening > 0 and bar.close > 0) for bar in bars)):
        raise ValueError("Unexpected Nasdaq QQQ price coverage")

    issuer = json.loads(raw[ISSUER_DIVIDENDS], parse_float=Decimal)
    if issuer["cusip"] != "46090E103" or issuer["currencyCode"] != "USD":
        raise ValueError("Unexpected Invesco distribution instrument")
    distributions = [Distribution(date.fromisoformat(row["exDate"]), date.fromisoformat(row["payDate"]),
                                  Decimal(row["distributionAmountPerUnit"])) for row in issuer["distributions"]]
    selected = [item for item in distributions if FIRST <= item.ex_day <= LAST]
    by_ex_day = {item.ex_day: item for item in selected}
    dates = {bar.day for bar in bars}
    if (len(selected) != len(by_ex_day) or set(by_ex_day) - dates
            or any(item.amount <= 0 or item.payable < item.ex_day for item in selected)):
        raise ValueError("Invalid issuer distribution rows in the test period")

    venue_rows = json.loads(raw[VENUE_DIVIDENDS])["data"]["dividends"]["rows"]
    venue = {american_date(row["exOrEffDate"]): (american_date(row["paymentDate"]),
                                                Decimal(row["amount"].lstrip("$"))) for row in venue_rows}
    if {day for day in venue if FIRST <= day <= LAST} != set(by_ex_day):
        raise ValueError("Nasdaq and Invesco distribution calendars disagree")
    differences = []
    for day, distribution in sorted(by_ex_day.items()):
        payable, amount = venue[day]
        if payable != distribution.payable:
            raise ValueError(f"Nasdaq and Invesco payment dates disagree for {day}")
        if amount != distribution.amount:
            differences.append({"ex_date": day, "issuer_amount": str(distribution.amount), "exchange_amount": str(amount)})

    issuer_mark = json.loads(raw[ISSUER_PRICE_CHECK])
    mark_day = date.fromisoformat(issuer_mark["effectiveDate"])
    matching = next((bar for bar in bars if bar.day == mark_day), None)
    mark = None if matching is None else {
        "day": mark_day, "open_matches": issuer_mark["openingPrice"] == matching.opening,
        "close_matches": issuer_mark["closingPrice"] == matching.close,
    }
    audit = {"sessions": len(bars), "distributions": len(by_ex_day), "amount_differences": differences,
             "issuer_market_price_check": mark,
             "sources": {name: {"url": SOURCES[name], "sha256": sha256(payload).hexdigest()}
                         for name, payload in raw.items()}}
    return bars, by_ex_day, audit


def monthly_signals(bars: list[Bar], distributions: dict[date, Distribution], lag: int) -> dict[date, tuple[date, bool]]:
    averages = deque(maxlen=10)
    signals = {}
    wealth = 1.0
    for index, (bar, following) in enumerate(zip(bars, bars[1:])):
        if index:
            dividend = distributions.get(bar.day)
            wealth *= (bar.close + (float(dividend.amount) if dividend else 0.0)) / bars[index - 1].close
        if bar.day.month != following.day.month:
            averages.append(wealth)
            if len(averages) == averages.maxlen and index + lag < len(bars):
                signals[bars[index + lag].day] = (bar.day, wealth > mean(averages))
    return signals


def replay(bars: list[Bar], distributions: dict[date, Distribution],
           signals: dict[date, tuple[date, bool]], allowance: int, cash_rate: float, policy: str) -> dict:
    cash, shares = CAPITAL, 0
    exposure = 0
    spread_paid, sell_fees, interest, dividends = 0.0, 0.0, 0.0, 0.0
    previous = bars[0].day
    target, signal_day = False, None
    if policy == "trend":
        available = [day for day in signals if day <= previous]
        if not available:
            raise ValueError(f"No completed warm-up for {previous}")
        signal_day, target = signals[max(available)]
    payments = {}
    reinvest = False
    executions = []
    equity = [CAPITAL]

    def trade(day: date, reference: float, kind: str) -> None:
        nonlocal shares, cash, spread_paid, sell_fees
        buy = kind in {"enter", "reinvest"}
        price = reference * (1 + (allowance if buy else -allowance) / 10_000)
        quantity = floor(cash / price) if buy else shares
        if not quantity:
            return
        notional = quantity * price
        regulatory_fee = 0.0 if buy else notional * SELL_FEE_BPS / 10_000
        sell_fees += regulatory_fee
        spread_paid += quantity * abs(price - reference)
        cash += -notional if buy else notional - regulatory_fee
        shares += quantity if buy else -quantity
        executions.append({"day": day, "kind": kind, "shares": quantity, "reference": reference,
                           "decision_day": signal_day if kind in {"enter", "exit"} else None})

    for bar in bars:
        elapsed = (bar.day - previous).days
        exposure += elapsed * bool(shares)
        accrued = cash * ((1 + cash_rate) ** (elapsed / 365.25) - 1)
        cash += accrued
        interest += accrued
        previous = bar.day

        dividend = distributions.get(bar.day)
        if dividend and shares:
            earned = shares * float(dividend.amount)
            payments[dividend.payable] = payments.get(dividend.payable, 0.0) + earned
            dividends += earned
        early_payments = [day for day in payments if day < bar.day]
        for day in early_payments:
            cash += payments.pop(day)
        reinvest = reinvest or bool(early_payments)

        if policy == "trend" and bar.day in signals:
            signal_day, target = signals[bar.day]
        elif policy == "buy_hold":
            target = True
        if target and not shares:
            trade(bar.day, bar.opening, "enter")
        elif not target and shares:
            trade(bar.day, bar.opening, "exit")
        elif target and reinvest:
            trade(bar.day, bar.opening, "reinvest")

        paid = payments.pop(bar.day, 0.0)
        cash += paid
        reinvest = bool(paid)
        equity.append(cash + shares * bar.close + sum(payments.values()))

    if shares:
        trade(bars[-1].day, bars[-1].close, "terminal")
    final = cash + sum(payments.values())
    equity[-1] = final
    peak = CAPITAL
    drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    days = (bars[-1].day - bars[0].day).days
    return {"net_pnl_usd": final - CAPITAL, "return": final / CAPITAL - 1,
            "annualized_return": (final / CAPITAL) ** (365.25 / days) - 1, "maximum_drawdown": -drawdown,
            "exposure": exposure / days, "entries": sum(item["kind"] == "enter" for item in executions),
            "distribution_cash_earned_usd": dividends, "ending_receivables_usd": sum(payments.values()),
            "modeled_cash_interest_usd": interest, "spread_and_slippage_usd": spread_paid,
            "sell_fees_usd": sell_fees, "trade_count": len(executions), "executions": executions}


def run(root: Path) -> dict:
    bars, distributions, audit = load(root)
    lagged_signals = {lag: monthly_signals(bars, distributions, lag) for lag in (1, 2)}
    result = {"data": audit, "vintages": []}
    for start, end in VINTAGES:
        window = [bar for bar in bars if start <= bar.day <= end]
        days = (window[-1].day - window[0].day).days
        cases = []
        for allowance, lag in SCENARIOS:
            signals = lagged_signals[lag]
            cases.append({"allowance_bps_per_side": allowance, "lag_sessions_after_signal": lag,
                          "trend": replay(window, distributions, signals, allowance, 0, "trend"),
                          "trend_with_cash_yield": replay(window, distributions, signals, allowance, 0.04, "trend"),
                          "buy_hold": replay(window, distributions, signals, allowance, 0, "buy_hold")})
        result["vintages"].append({"start": window[0].day, "end": window[-1].day, "sessions": len(window),
                                   "cash_zero_pnl_usd": 0, "cash_4_percent_pnl_usd": CAPITAL * (1.04 ** (days / 365.25) - 1),
                                   "scenarios": cases})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/agent-nasdaq"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "fetch":
        fetch(args.data)
    else:
        data = json.dumps(run(args.data), default=str, indent=2) + "\n"
        if args.output is None:
            print(data, end="")
        else:
            args.output.write_text(data)


if __name__ == "__main__":
    main()
