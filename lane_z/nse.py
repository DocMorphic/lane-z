"""NSE India daily-bar intraday screen with resident-retail equity fee assumptions."""

import argparse
import csv
import hashlib
import io
import json
import math
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from urllib.error import URLError
from urllib.request import urlopen
from zipfile import ZipFile


ARCHIVE_ROOT = "https://archives.nseindia.com/content/cm"
FIRST_DAY = date(2026, 6, 1)
LAST_DAY = date(2026, 11, 20)
HOLDOUT_START = date(2026, 9, 1)
CHECK_FIRST_DAY = date(2026, 1, 1)
CHECK_LAST_DAY = date(2026, 5, 29)
HOLIDAYS = {
    date(2026, 1, 15), date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26), date(2026, 3, 31),
    date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1), date(2026, 5, 28),
    date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2),
    date(2026, 10, 20), date(2026, 11, 10),
}
SYMBOLS = (
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "SBIN", "ITC", "LT", "BHARTIARTL", "AXISBANK",
)
BPS = 10_000
BUDGET_INR = 50_000.0
BROKERAGE_RATE = 0.0003
BROKERAGE_CAP_INR = 20.0
EXCHANGE_RATE = 0.0000307
SEBI_RATE = 0.000001
IPFT_RATE = 0.000000001
GST_RATE = 0.18
BUY_STAMP_RATE = 0.00003
SELL_STT_RATE = 0.00025


@dataclass(frozen=True)
class Prices:
    open: float
    last: float
    close: float
    prior_close: float


def sessions(earlier_check: bool = False) -> tuple[date, ...]:
    first, last = (CHECK_FIRST_DAY, CHECK_LAST_DAY) if earlier_check else (FIRST_DAY, LAST_DAY)
    dates = (first + timedelta(days=i) for i in range((last - first).days + 1))
    return tuple(day for day in dates if day.weekday() < 5 and day not in HOLIDAYS)


def source_url(day: date) -> str:
    return f"{ARCHIVE_ROOT}/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def read_bhav(path: Path, day: date) -> dict[str, Prices]:
    result = {}
    with ZipFile(path) as archive:
        (csv_member,) = archive.namelist()
        with archive.open(csv_member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                symbol = row["TckrSymb"]
                if symbol not in SYMBOLS or row["SctySrs"] != "EQ":
                    continue
                if row["TradDt"] != day.isoformat() or row["Src"] != "NSE" or row["Sgmt"] != "CM":
                    raise ValueError(f"{path} contains a different session or exchange")
                if symbol in result:
                    raise ValueError(f"{path} contains multiple rows for {symbol}")
                values = tuple(float(row[name]) for name in ("OpnPric", "LastPric", "ClsPric", "PrvsClsgPric"))
                if not all(math.isfinite(value) and value > 0 for value in values):
                    raise ValueError(f"{path} contains invalid prices for {symbol}")
                result[symbol] = Prices(*values)
    if result.keys() != set(SYMBOLS):
        missing = sorted(set(SYMBOLS) - result.keys())
        raise ValueError(f"{path} is missing EQ records for {', '.join(missing)}")
    return result


def fetch(root: Path, earlier_check: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)

    def download(day: date) -> None:
        path = root / f"{day}.zip"
        if path.exists():
            read_bhav(path, day)
            return
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as buffer:
            temporary = Path(buffer.name)
            try:
                try:
                    with urlopen(source_url(day), timeout=30) as response:
                        buffer.write(response.read())
                except URLError as error:
                    raise RuntimeError(f"NSE archive download failed for {day}: {error}") from error
                buffer.close()
                read_bhav(temporary, day)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, sessions(earlier_check)))


def fee(notional: float, is_buy: bool) -> float:
    brokerage = min(notional * BROKERAGE_RATE, BROKERAGE_CAP_INR)
    gst_basis = brokerage + notional * (EXCHANGE_RATE + SEBI_RATE + IPFT_RATE)
    tax = BUY_STAMP_RATE if is_buy else SELL_STT_RATE
    return gst_basis * (1 + GST_RATE) + notional * tax


def choose(prices: dict[str, Prices]) -> tuple[str, float, float] | None:
    returns = {symbol: data.close / data.prior_close - 1 for symbol, data in prices.items()}
    universe_median = median(returns.values())
    worst = min(returns, key=lambda symbol: (returns[symbol], symbol))
    residual = returns[worst] - universe_median
    if returns[worst] <= -0.01 and residual <= -0.015:
        return worst, returns[worst], residual
    return None


def screen(data: dict[date, dict[str, Prices]], slippage_bps: float) -> list[dict]:
    trades = []
    dates = sorted(data)
    for signal_day, today in zip(dates, dates[1:]):
        signal = choose(data[signal_day])
        if signal is None:
            continue
        symbol, prior_return, residual = signal
        entry_limit = data[signal_day][symbol].close * 1.02
        shares = math.floor(BUDGET_INR / entry_limit)
        price = data[today][symbol]
        buy = price.open * (1 + slippage_bps / BPS)
        sell = price.last * (1 - slippage_bps / BPS)
        trade = {
            "signal_day": str(signal_day),
            "execution_day": str(today),
            "symbol": symbol,
            "prior_return_bps": prior_return * BPS,
            "prior_relative_return_bps": residual * BPS,
        }
        if buy > entry_limit or not shares:
            trades.append({**trade, "filled": False})
            continue
        entry = shares * buy
        exit_value = shares * sell
        fees = fee(entry, True) + fee(exit_value, False)
        reserve = shares * (buy - price.open + price.last - sell)
        gross = shares * (price.last - price.open)
        benchmark = sum(item.last / item.open - 1 for item in data[today].values()) / len(SYMBOLS)
        trades.append({
            **trade,
            "filled": True,
            "shares": shares,
            "entry_notional_inr": entry,
            "gross_inr": gross,
            "execution_allowance_inr": reserve,
            "fees_inr": fees,
            "net_inr": gross - reserve - fees,
            "matched_equal_weight_gross_bps": benchmark * BPS,
        })
    return trades


def summarize(trades: list[dict]) -> dict:
    closed = [trade for trade in trades if trade["filled"]]
    amounts = {
        key: sum(trade[key] for trade in closed)
        for key in ("entry_notional_inr", "gross_inr", "fees_inr", "execution_allowance_inr", "net_inr")
    }
    entry = amounts["entry_notional_inr"]
    return {
        **amounts,
        "setups": len(trades),
        "round_trips_at_price_proxies": len(closed),
        "net_bps_on_entry_notional": BPS * amounts["net_inr"] / entry if entry else None,
        "matched_equal_weight_gross_bps": sum(
            trade["matched_equal_weight_gross_bps"] * trade["entry_notional_inr"] for trade in closed
        ) / entry if entry else None,
        "net_positive_round_trips": sum(trade["net_inr"] > 0 for trade in closed),
    }


def run(root: Path, earlier_check: bool = False) -> dict:
    holdout_start = CHECK_FIRST_DAY if earlier_check else HOLDOUT_START
    panel = {}
    audit = []
    for day in sessions(earlier_check):
        path = root / f"{day}.zip"
        panel[day] = read_bhav(path, day)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        audit.append({"session": str(day), "url": source_url(day), "sha256": digest})
    outcomes = {}
    for allowance in (2.0, 5.0, 10.0, 20.0):
        trades = screen(panel, allowance)
        outcomes[str(allowance)] = {
            "earlier": summarize([trade for trade in trades if trade["execution_day"] < str(holdout_start)]),
            "held_out": summarize([trade for trade in trades if trade["execution_day"] >= str(holdout_start)]),
            "trades": trades,
        }
    return {
        "venue": "National Stock Exchange of India",
        "data_resolution": "daily Bhavcopy",
        "currency": "INR",
        "symbols": SYMBOLS,
        "held_out_start": str(holdout_start),
        "separate_earlier_period": earlier_check,
        "order_budget_inr": BUDGET_INR,
        "fee_example_at_equal_50000_inr_legs": {
            "buy_inr": fee(50_000, True),
            "sell_inr": fee(50_000, False),
            "round_trip_bps_on_entry_notional": (fee(50_000, True) + fee(50_000, False)) / 50_000 * BPS,
        },
        "allowance_bps_per_side": outcomes,
        "authentic_order_book_sample_used": False,
        "input_audit": audit,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/nse/bhav"))
    parser.add_argument("--output", type=Path, default=Path("results/follow-up/nse-india.json"))
    parser.add_argument("--earlier-check", action="store_true")
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.data, args.earlier_check)
        return
    report = run(args.data, args.earlier_check)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
