"""Paper replay of monthly Deribit BTC inverse futures hedged with BTC spot."""

import argparse
import calendar
import hashlib
import json
import time
from datetime import UTC, date, datetime, timedelta
from math import floor, isfinite
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


START_CAPITAL = 1_000.0
HURDLE = 0.04
FEE = 0.0005
DELIVERY_FEE = 0.00025
ENTRY_DAYS = 21
FIRST = date(2023, 6, 1)
CUTOFF = date(2026, 11, 22)
DAY = timedelta(days=1)
SPOT = "BTC_USDC"
HISTORY = "https://history.deribit.com/api/v2/public/"
PUBLIC = "https://www.deribit.com/api/v2/public/"
COHORTS = {
    "development": (FIRST, date(2025, 1, 1)),
    "holdout_2025": (date(2025, 1, 1), date(2026, 1, 1)),
    "holdout_2026": (date(2026, 1, 1), CUTOFF),
}


def ms(day, hour):
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp()) * 1_000


def expiry_dates():
    for year in range(FIRST.year, CUTOFF.year + 1):
        for month in range(1, 13):
            last = date(year, month, calendar.monthrange(year, month)[1])
            expiry = last - DAY * ((last.weekday() - 4) % 7)
            if FIRST <= expiry < CUTOFF:
                yield expiry


def contract(expiry):
    return "BTC-" + expiry.strftime("%d%b%y").upper()


def source(root, name, endpoint=None, method=None, **parameters):
    path = root / (name + ".json")
    if not path.exists() and endpoint is not None:
        url = endpoint + method + "?" + urlencode(parameters)
        request = Request(url, headers={"User-Agent": "Lane-Z-public-market-research/1"})
        for retry in range(3):
            try:
                with urlopen(request, timeout=30) as reply:
                    response = json.load(reply)
                break
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or retry == 2:
                    raise
                time.sleep(2 ** (retry + 1))
        if "result" not in response:
            raise ValueError(f"Missing {method} result")
        with path.open("x") as output:
            json.dump({"url": url, "data": response["result"]}, output)
        time.sleep(0.28)
    return json.loads(path.read_bytes())["data"]


def chart(root, key, instrument, start, stop, interval):
    return source(root, key, HISTORY, "get_tradingview_chart_data", instrument_name=instrument,
                  start_timestamp=start, end_timestamp=stop, resolution=interval)


def fetch(root):
    root.mkdir(parents=True, exist_ok=True)
    source(root, "catalog", HISTORY, "get_instruments", currency="BTC", kind="future", expired="true")
    offset = 0
    while True:
        result = source(root, f"delivery-{offset}", PUBLIC, "get_delivery_prices",
                        index_name="btc_usd", count=100, offset=offset)
        if date.fromisoformat(result["data"][-1]["date"]) <= FIRST:
            break
        offset += 100
    for expiry in expiry_dates():
        name = contract(expiry)
        signal = expiry - ENTRY_DAYS * DAY
        chart(root, f"{expiry}-spot-entry", SPOT, ms(signal, 7), ms(signal, 9), "60")
        chart(root, f"{expiry}-future-entry", name, ms(signal, 7), ms(signal, 9), "60")
        chart(root, f"{expiry}-spot-exit", SPOT, ms(expiry, 9), ms(expiry, 9), "60")
        chart(root, f"{expiry}-future-range", name, ms(signal, 0), ms(expiry, 0), "1D")


def bars(data):
    if data.get("status") != "ok":
        return {}
    result = {}
    columns = ("ticks", "open", "high", "low", "close", "volume", "cost")
    for stamp, *values in zip(*(data[key] for key in columns), strict=True):
        opening, high, low, close, volume, cost = values
        if (stamp in result or min(opening, close, low) <= 0 or min(volume, cost) < 0
                or low > min(opening, close) or high < max(opening, close) or not all(map(isfinite, values))):
            raise ValueError(f"Invalid chart bucket at timestamp {stamp}")
        result[stamp] = dict(zip(columns[1:], values, strict=True))
    return result


def inverse_trade(notional, quantity, spot_open, future_open, delivery, spot_exit, reserve):
    buy = spot_open * (1 + reserve)
    sold_future = future_open * (1 - reserve)
    sell = spot_exit * (1 - reserve)
    initial_btc = quantity - notional / sold_future * FEE
    inverse_pnl_btc = notional * (1 / delivery - 1 / sold_future)
    final_btc = initial_btc + inverse_pnl_btc - notional / delivery * DELIVERY_FEE
    spend = quantity * buy * (1 + FEE)
    proceeds = final_btc * sell * (1 - FEE)
    return {"pnl_usdc": proceeds - spend, "spend_usdc": spend, "sale_proceeds_usdc": proceeds,
            "basis_gross_usdc": (quantity + notional * (1 / delivery - 1 / future_open)) * spot_exit - quantity * spot_open,
            "btc_at_expiry": final_btc, "btc_after_entry_fee": initial_btc, "future_entry_price": sold_future}


def run(root):
    catalog = {row["instrument_name"]: row for row in source(root, "catalog")}
    settlements = {}
    for path in root.glob("delivery-*.json"):
        for row in source(root, path.stem)["data"]:
            settling = row["delivery_price"]
            if settling <= 0 or not isfinite(settling) or settlements.setdefault(row["date"], settling) != settling:
                raise ValueError(f"Invalid settlement on {row['date']}")
    windows = {}
    for key, (first, stop) in COHORTS.items():
        results = {}
        for case, reserve in (("base", 0.0005), ("adverse_hourly_prices", 0.0015)):
            capital = START_CAPITAL
            closed = 0
            rows = []
            for expiry in (d for d in expiry_dates() if first <= d < stop):
                row = {"expiry": str(expiry), "instrument": contract(expiry)}
                rows.append(row)
                metadata = catalog.get(contract(expiry))
                signal_day = expiry - ENTRY_DAYS * DAY
                if metadata is None or metadata["creation_timestamp"] > ms(signal_day, 8):
                    row["status"] = "unlisted_at_signal"
                    continue
                if (metadata["expiration_timestamp"] != ms(expiry, 8) or metadata["contract_size"] != 10
                        or metadata["future_type"] != "reversed"):
                    raise ValueError(f"Unexpected specification for {contract(expiry)}")
                spot = bars(source(root, f"{expiry}-spot-entry"))
                future = bars(source(root, f"{expiry}-future-entry"))
                prior = ms(signal_day, 7)
                if any(prior not in table or table[prior]["volume"] == 0 for table in (spot, future)):
                    row["status"] = "no_prior_trade"
                    continue
                spot_reference, future_reference = (table[prior]["close"] for table in (spot, future))
                notional = floor(capital * 0.9 / 10) * 10
                quantity = floor(notional / future_reference * 100_000_000) / 100_000_000
                proposed = inverse_trade(notional, quantity, spot_reference, future_reference,
                                         spot_reference, spot_reference, 0.0005)
                hurdle = capital * ((1 + HURDLE) ** (ENTRY_DAYS / 365) - 1)
                row.update({"projected_pnl_usdc": proposed["pnl_usdc"], "decision_hurdle_usdc": hurdle})
                if proposed["pnl_usdc"] <= hurdle:
                    row["status"] = "below_cash_hurdle"
                    continue
                when = ms(signal_day, 9)
                expiry_spot = bars(source(root, f"{expiry}-spot-exit")).get(ms(expiry, 9))
                delivery = settlements.get(str(expiry))
                matches = (spot.get(when), future.get(when), expiry_spot)
                if delivery is None or any(item is None or item["volume"] <= 0 or item["cost"] < 10 * notional
                                           for item in matches):
                    row["status"] = "incomplete_entry_or_exit_tape"
                    continue
                if case == "base":
                    spot_open, future_open, spot_exit = (item["open"] for item in matches)
                else:
                    spot_open, future_open, spot_exit = (item[field] for item, field in zip(matches, ("high", "low", "low")))
                trade = inverse_trade(notional, quantity, spot_open, future_open, delivery, spot_exit, reserve)
                observed_range = bars(source(root, f"{expiry}-future-range"))
                # Deribit daily candles follow its 08:00 UTC settlement session.
                margin_days = [ms(signal_day + i * DAY, 8) for i in range(ENTRY_DAYS)]
                if not all(day in observed_range for day in margin_days):
                    row["status"] = "incomplete_collateral_range"
                    continue
                margin_ratio = min(
                    1 + (trade["btc_after_entry_fee"] / notional - 1 / trade["future_entry_price"]) * mark
                    for day in margin_days for mark in (observed_range[day]["low"], observed_range[day]["high"])
                )
                if trade["spend_usdc"] > capital or trade["btc_at_expiry"] <= 0 or margin_ratio <= 0.04:
                    row["status"] = "incomplete_capital_or_collateral"
                    continue
                capital += trade["pnl_usdc"]
                closed += 1
                row.update({"status": "paper_closed", "pnl_usdc": trade["pnl_usdc"], "capital_usdc": capital,
                            "gross_at_proxies_usdc": trade["basis_gross_usdc"], "min_daily_margin_ratio": margin_ratio})
            unresolved_count = sum(row["status"].startswith("incomplete") for row in rows)
            results[case] = {"closed_positions": closed, "closed_net_usdc": capital - START_CAPITAL,
                             "active_days": closed * ENTRY_DAYS, "incomplete": unresolved_count, "months": rows}
        windows[key] = {
            "first": str(first), "end_exclusive": str(stop),
            "cash_hurdle_pnl_usdc": START_CAPITAL * ((1 + HURDLE) ** ((stop - first).days / 365) - 1),
            **results,
        }
    audit = []
    for path in sorted(root.glob("*.json")):
        raw = path.read_bytes()
        audit.append({"file": path.name, "url": json.loads(raw)["url"], "sha256": hashlib.sha256(raw).hexdigest()})
    return {"capital_per_cohort_usdc": START_CAPITAL, "spot_symbol": SPOT, "windows": windows, "sources": audit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.data)
    else:
        if not args.output:
            parser.error("run requires --output")
        result = run(args.data)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as output:
            json.dump(result, output, indent=2, allow_nan=False)
            output.write("\n")


if __name__ == "__main__":
    main()
