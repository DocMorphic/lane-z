"""Replay a paper limUSD allocation from point-in-time issuer share values."""

import argparse
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen

METRICS = "https://api.liminal.money/sdk/tokenized/limUSD/metrics?range=max"
DATES = tuple(date.fromisoformat(day) for day in ("2026-05-17", "2026-07-19", "2026-09-20", "2026-11-22"))
CAPITAL = Decimal(2000)
DAILY_VALUE_MAX_DEVIATION = Decimal("0.000001")
INSTANT_REDEMPTION_FEE = Decimal("0.003")
STRESS_INSTANT = {"fee": INSTANT_REDEMPTION_FEE, "haircut": Decimal("0.001"),
                  "entry_reserve": Decimal("0.0005"), "gas": 3}


def metrics(raw):
    marks = {}
    max_deviation = Decimal(0)
    for row in json.loads(raw):
        ms = int(row["timestamp"])
        if ms % 86_400_000 != 86_399_999:
            raise ValueError("The share marks must be UTC end-of-day observations")
        day = datetime.fromtimestamp(ms / 1_000, UTC).date()
        if not DATES[0] <= day <= DATES[-1]:
            continue
        pps, assets, shares = (Decimal(row[name]) for name in ("pps", "tvl", "totalSupply"))
        if min(pps, assets, shares) <= 0 or day in marks:
            raise ValueError(f"Invalid or duplicate share mark for {day}")
        deviation = abs(pps - assets / shares)
        max_deviation = max(max_deviation, deviation)
        marks[day] = (pps, assets, shares)
    expected = {DATES[0] + timedelta(days=day) for day in range((DATES[-1] - DATES[0]).days + 1)}
    if marks.keys() != expected or max_deviation > DAILY_VALUE_MAX_DEVIATION:
        raise ValueError(f"Missing daily rows or inconsistent PPS; max ratio deviation {max_deviation}")
    return marks, max_deviation


def settle(opening_pps, closing_pps, *, fee, haircut, entry_reserve, gas):
    shares = (CAPITAL - gas) * (1 - entry_reserve) / opening_pps
    gross = shares * closing_pps * (1 - haircut)
    return gross * (1 - fee)


def report(raw):
    marks, max_deviation = metrics(raw)
    windows = []
    for start, end in zip(DATES, DATES[1:]):
        days = (end - start).days
        hurdle = CAPITAL * (Decimal("1.04") ** (Decimal(days) / 365) - 1)
        opening = marks[start][0]
        closing = marks[end][0]
        queue = min(marks[end - timedelta(days=d)][0] for d in range(4))
        cases = {
            "base_instant": settle(opening, closing, fee=INSTANT_REDEMPTION_FEE, haircut=0, entry_reserve=0, gas=1),
            "stress_instant": settle(opening, closing, **STRESS_INSTANT),
            "stress_queued": settle(opening, queue, fee=0, haircut=0, entry_reserve=Decimal("0.0005"), gas=3),
            "additional_100bp_exit_loss": settle(opening, closing, fee=INSTANT_REDEMPTION_FEE,
                                                 haircut=Decimal("0.011"), entry_reserve=Decimal("0.0005"), gas=3),
        }
        peak = opening
        drawdown = Decimal(0)
        for i in range(days + 1):
            price = marks[start + timedelta(days=i)][0]
            peak = max(peak, price)
            drawdown = max(drawdown, 1 - price / peak)
        windows.append({
            "start_mark_utc_day": start.isoformat(), "end_mark_utc_day": end.isoformat(), "days": days,
            "opening_pps": str(opening), "closing_pps": str(closing), "pessimistic_queue_pps": str(queue),
            "cash_pnl_usdc": str(hurdle), "gross_share_pnl_usdc": str(CAPITAL * (closing / opening - 1)),
            "issuer_mark_max_drawdown_pct": str(100 * drawdown),
            "scenarios": {name: {"net_pnl_usdc": str(value - CAPITAL), "excess_vs_cash_usdc": str(value - CAPITAL - hurdle)}
                          for name, value in cases.items()},
        })
    rolling_days = (DATES[1] - DATES[0]).days
    rolling_cash = CAPITAL * (Decimal("1.04") ** (Decimal(rolling_days) / 365))
    rolling_excess = [
        settle(marks[day][0], marks[day + timedelta(days=rolling_days)][0], **STRESS_INSTANT) - rolling_cash
        for day in sorted(marks) if day + timedelta(days=rolling_days) in marks
    ]
    return {
        "source_endpoint": METRICS,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "daily_marks": len(marks),
        "maximum_absolute_deviation_from_assets_over_shares_usdc": str(max_deviation),
        "starting_paper_usdc": str(CAPITAL),
        "annual_cash_rate": "0.04",
        "issuer_nav_screen_pass": all(Decimal(w["scenarios"]["stress_instant"]["excess_vs_cash_usdc"]) > 0 for w in windows),
        "windows": windows,
        "overlapping_63_day_endpoint_sensitivity": {
            "windows": len(rolling_excess),
            "above_cash": sum(excess > 0 for excess in rolling_excess),
            "smallest_cash_excess_usdc": str(min(rolling_excess)),
            "largest_cash_excess_usdc": str(max(rolling_excess)),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        with urlopen(METRICS, timeout=30) as response:
            raw = response.read()
        args.data.parent.mkdir(parents=True, exist_ok=True)
        args.data.write_bytes(raw)
    result = report(args.data.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
