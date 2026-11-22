"""Replay a fixed monthly trend filter on official NSE NIFTYBEES daily prices."""

import argparse
import csv
import hashlib
import io
import json
import math
import time
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from itertools import groupby, pairwise
from pathlib import Path
from statistics import mean
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile

from .nse import BPS, EXCHANGE_RATE, GST_RATE, IPFT_RATE, SEBI_RATE


SYMBOL = "NIFTYBEES"
ISIN = "INF204KB14I2"
FIRST = date(2021, 1, 1)
LAST = date(2026, 11, 20)
PERIODS = {
    "development": (date(2022, 1, 1), date(2022, 12, 31)),
    "holdout": (date(2023, 1, 1), date(2024, 12, 31)),
    "confirmation": (date(2025, 1, 1), LAST),
}
CAPITAL = 100_000.0
MONTHS = 10
CASH_HURDLE = 0.06
BUY_STAMP = 0.00015
SELL_STT = 0.00001
DEMAT_FEE = 15.34
TRANSACTION_LEVY = (EXCHANGE_RATE + SEBI_RATE + IPFT_RATE) * (1 + GST_RATE)
MCP_URL = "https://mcp.nseindia.in/bhavcopy/cm/mcp"
MCP_PROTOCOL = "2025-03-26"
ARCHIVE = "https://nsearchives.nseindia.com/content"
FORMAT_CHANGE = date(2024, 7, 8)


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    previous: float
    volume: int

    def __post_init__(self):
        if not all(math.isfinite(x) and x > 0 for x in (self.open, self.high, self.low, self.close, self.previous)):
            raise ValueError(f"Invalid NSE price on {self.day}")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high or self.volume <= 0:
            raise ValueError(f"Inconsistent NSE bar on {self.day}")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")


class NseHistory:
    """Read-only anonymous session for the exchange's public Bhavcopy endpoint."""

    def __init__(self):
        self.headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.request_id = 0
        payload, headers = self.rpc("initialize", {
            "protocolVersion": MCP_PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "lane-z-research", "version": "1"},
        })
        self.headers["Mcp-Session-Id"] = headers["Mcp-Session-Id"]
        self.headers["MCP-Protocol-Version"] = payload["result"]["protocolVersion"]
        self.rpc("notifications/initialized", {}, notification=True)

    def rpc(self, method: str, params: dict, notification: bool = False):
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            self.request_id += 1
            request["id"] = self.request_id
        with urlopen(Request(MCP_URL, data=json.dumps(request).encode(), headers=self.headers), timeout=10) as response:
            text = response.read().decode()
            headers = response.headers
        if not text:
            return {}, headers
        if text.startswith("event:"):
            text = next(row.removeprefix("data:") for row in text.splitlines() if row.startswith("data:"))
        message = json.loads(text)
        if "error" in message or message.get("id") != self.request_id:
            raise ValueError(f"Unexpected NSE response for {method}: {message}")
        return message, headers

    def call(self, name: str, arguments: dict) -> dict:
        message, _ = self.rpc("tools/call", {"name": name, "arguments": arguments})
        return json.loads(message["result"]["content"][0]["text"])


def archive_url(day: date) -> str:
    if day >= FORMAT_CHANGE:
        return f"{ARCHIVE}/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    label = day.strftime("%b").upper()
    return f"{ARCHIVE}/historical/EQUITIES/{day.year}/{label}/cm{day.day:02d}{label}{day.year}bhav.csv.zip"


def archive_row(path: Path, day: date) -> Bar:
    columns = (
        ("TckrSymb", "SctySrs", "TradDt", "OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric", "TtlTradgVol")
        if day >= FORMAT_CHANGE else
        ("SYMBOL", "SERIES", "TIMESTAMP", "OPEN", "HIGH", "LOW", "CLOSE", "PREVCLOSE", "TOTTRDQTY")
    )
    symbol, series, traded, *prices = columns
    with ZipFile(path) as archive:
        (member,) = archive.namelist()
        with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as source:
            (record,) = (row for row in csv.DictReader(source) if row[symbol] == SYMBOL and row[series] == "EQ")
    stamp = record[traded]
    actual = date.fromisoformat(stamp) if day >= FORMAT_CHANGE else datetime.strptime(stamp, "%d-%b-%Y").date()
    if actual != day or record["ISIN"] != ISIN:
        raise ValueError(f"Unexpected NSE date or ISIN in {path}")
    return Bar(day, *(float(record[column]) for column in prices[:5]), int(record[prices[-1]]))


def archive_download(root: Path, dates: list[date]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    spacing = Lock()
    next_request = 0.0

    def download(day: date):
        nonlocal next_request
        saved = root / f"{day}.zip"
        missing = root / f"{day}.404"
        if saved.exists() or missing.exists():
            return
        url = archive_url(day)
        for attempt in range(3):
            with spacing:
                time.sleep(max(0, next_request - time.monotonic()))
                next_request = time.monotonic() + 0.22
            host_url = url if attempt != 1 else url.replace("nsearchives.", "archives.", 1)
            request = Request(host_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/zip, */*"})
            try:
                with urlopen(request, timeout=9) as source:
                    raw = source.read()
                break
            except (URLError, TimeoutError) as error:
                if isinstance(error, HTTPError) and error.code == 404:
                    missing.touch()
                    return
                if attempt == 2:
                    raise
        temporary = saved.with_suffix(".part")
        temporary.write_bytes(raw)
        try:
            archive_row(temporary, day)
            temporary.replace(saved)
        finally:
            temporary.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(download, dates))


def mcp_bar(row: dict) -> Bar:
    if row["symbol"] != SYMBOL or row["series"] != "EQ":
        raise ValueError("Unexpected symbol/series in NSE history response")
    return Bar(date.fromisoformat(row["date"]), row["open"], row["high"], row["low"], row["close"],
               row["prevClose"], row["volume"])


def three_months_earlier(day: date) -> date:
    year, month = divmod(day.year * 12 + day.month - 4, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1])) - timedelta(days=1)


def missing_sessions(rows: list[Bar]) -> list[date]:
    ordered = sorted(rows, key=lambda bar: bar.day)
    gaps = [(FIRST - timedelta(days=1), ordered[0].day)]
    for previous, current in pairwise(ordered):
        if (current.day - previous.day).days > 5 or not math.isclose(current.previous, previous.close, abs_tol=0.011):
            gaps.append((previous.day, current.day))
        elif previous.day.month != current.day.month:
            gaps.append((previous.day, current.day))
    return sorted({start + timedelta(days=i) for start, stop in gaps for i in range(1, (stop - start).days)})


def fetch(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    mcp = NseHistory()
    actions = mcp.call("get_corporate_actions", {"symbol": SYMBOL, "fromDate": str(FIRST), "toDate": str(LAST)})
    write_json(root / "actions.json", actions)
    cursor = LAST
    rows = []
    while FIRST <= cursor:
        page_file = root / f"history-{cursor}.json"
        try:
            data = (json.loads(page_file.read_text()) if page_file.exists() else
                    mcp.call("get_stock_history", {"symbol": SYMBOL, "months": 3, "endDate": str(cursor)}))
        except (URLError, TimeoutError) as error:
            if isinstance(error, HTTPError) and error.code < 500:
                raise
            print(f"NSE history query through {cursor} failed. Checking the affected range with archived files.", flush=True)
            cursor = three_months_earlier(cursor)
            continue
        if data.get("error", "").startswith("Symbol not found"):
            cursor = three_months_earlier(cursor)
            continue
        batch = [mcp_bar(row) for row in data["data"]]
        first_row = min(bar.day for bar in batch)
        next_cursor = date.fromisoformat(data["next_end_date"])
        if next_cursor >= first_row or max(bar.day for bar in batch) > cursor:
            raise ValueError("NSE history cursor did not move backwards")
        if not page_file.exists():
            write_json(page_file, data)
        rows.extend(batch)
        cursor = next_cursor
    archive_download(root / "archive", missing_sessions(rows))


def file_audit(path: Path, count: int, url: str) -> dict:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"file": path.name, "rows": count, "sha256": digest, "source": url}


def load(root: Path) -> tuple[list[Bar], list[dict]]:
    rows, audit = [], []
    for path in sorted(root.glob("history-*.json")):
        page = json.loads(path.read_text())
        batch = [mcp_bar(row) for row in page["data"]]
        rows.extend(batch)
        audit.append(file_audit(path, len(batch), MCP_URL))
    for path in sorted((root / "archive").glob("*.zip")):
        day = date.fromisoformat(path.stem)
        rows.append(archive_row(path, day))
        audit.append(file_audit(path, 1, archive_url(day)))
    rows.sort(key=lambda bar: bar.day)
    if rows[0].day != FIRST or rows[-1].day != LAST:
        raise ValueError("NSE bars do not cover the frozen input window")
    actions = json.loads((root / "actions.json").read_text())
    if actions.get("symbol") != SYMBOL or actions.get("count") != 0:
        raise ValueError("Inspect corporate actions before replaying unadjusted NIFTYBEES prices")
    audit.append(file_audit(root / "actions.json", 0, MCP_URL))
    for previous, current in pairwise(rows):
        if current.day <= previous.day or not math.isclose(current.previous, previous.close, abs_tol=0.011):
            raise ValueError(f"Duplicate or unbridged NSE prices for {previous.day} and {current.day}")
    return rows, audit


def fee(notional: float, buy: bool) -> float:
    surcharge = notional * BUY_STAMP if buy else notional * SELL_STT + DEMAT_FEE
    return surcharge + notional * TRANSACTION_LEVY


def month_signals(bars: list[Bar]) -> dict[tuple[int, int], tuple[date, bool]]:
    closes = [list(group)[-1] for _, group in groupby(bars, key=lambda item: (item.day.year, item.day.month))]
    decisions = {}
    for i, bar in enumerate(closes[MONTHS - 1:-1], start=MONTHS - 1):
        following = closes[i + 1].day
        month = (following.year, following.month)
        decisions[month] = (bar.day, bar.close > mean(value.close for value in closes[i - MONTHS + 1:i + 1]))
    return decisions


def simulate(bars: list[Bar], decisions: dict, strategy: str, slip: int,
             cash_rate: float = 0, gain_haircut: float = 0) -> dict:
    cash, held, basis = CAPITAL, 0, 0.0
    fees, slip_paid, tax, interest, invested_days = 0.0, 0.0, 0.0, 0.0, 0
    previous = bars[0].day
    equity = []
    executions = []

    def trade(bar: Bar, signal_day: date | None, buy: bool, reference: float):
        nonlocal cash, held, basis, fees, slip_paid, tax
        price = reference * (1 + (slip if buy else -slip) / BPS)
        units = math.floor(cash / (price + fee(price, True))) if buy else held
        if not units:
            return
        notional = units * price
        cost = fee(notional, buy)
        fees += cost
        execution_reserve = abs(reference - price) * units
        slip_paid += execution_reserve
        gain = None
        if buy:
            held = units
            basis = notional + cost
            cash -= basis
        else:
            gain = notional - cost - basis
            haircut = max(gain, 0) * gain_haircut
            tax += haircut
            cash += notional - cost - haircut
            held, basis = 0, 0.0
        executions.append({"signal_day": signal_day, "execution_day": bar.day, "buy": buy,
                           "units": units, "fee_inr": cost, "execution_reserve_inr": execution_reserve,
                           "realized_gain_inr_before_haircut": gain})

    current_month = None
    for bar in bars:
        passed = (bar.day - previous).days
        accrued = cash * ((1 + cash_rate) ** (passed / 365.25) - 1)
        cash += accrued
        interest += accrued
        invested_days += passed * bool(held)
        previous = bar.day
        month = (bar.day.year, bar.day.month)
        if month != current_month:
            current_month = month
            if strategy == "trend":
                signal_day, want = decisions[month]
            else:
                signal_day, want = None, strategy == "buy_hold"
            if want != bool(held):
                trade(bar, signal_day, want, bar.open)
        equity.append(cash + held * bar.close)
    if held:
        trade(bars[-1], None, False, bars[-1].close)
        equity[-1] = cash
    high = CAPITAL
    drawdown = 0.0
    for value in equity:
        high = max(high, value)
        drawdown = max(drawdown, 1 - value / high)
    elapsed = (bars[-1].day - bars[0].day).days
    return {"pnl_inr": cash - CAPITAL, "return_pct": 100 * (cash / CAPITAL - 1),
            "annualized_pct": 100 * ((cash / CAPITAL) ** (365.25 / elapsed) - 1),
            "drawdown_pct": 100 * drawdown, "exposure_pct": 100 * invested_days / elapsed,
            "fees_inr": fees, "execution_reserve_inr": slip_paid, "gain_haircut_inr": tax,
            "cash_interest_inr": interest, "completed_round_trips": sum(not item["buy"] for item in executions),
            "execution_events": executions}


def run(root: Path) -> dict:
    rows, audit = load(root)
    actions = json.loads((root / "actions.json").read_text())
    decisions = month_signals(rows)
    results = {}
    for name, (start, stop) in PERIODS.items():
        bars = [bar for bar in rows if start <= bar.day <= stop]
        costs = {slip: {strategy: simulate(bars, decisions, strategy, slip) for strategy in ("trend", "buy_hold")}
                 for slip in (5, 15, 30)}
        results[name] = {
            "first_session": bars[0].day, "last_session": bars[-1].day, "sessions": len(bars),
            "cost_cases_bps_per_side": costs, "idle_cash": simulate(bars, decisions, "cash", 5),
            "cash_hurdle": simulate(bars, decisions, "cash", 5, CASH_HURDLE),
            "trend_with_cash_interest": simulate(bars, decisions, "trend", 5, CASH_HURDLE),
            "positive_gain_haircut": {strategy: simulate(bars, decisions, strategy, 5, gain_haircut=0.25)
                                      for strategy in ("trend", "buy_hold")},
        }
    return {"symbol": SYMBOL, "isin": ISIN, "capital_inr": CAPITAL, "cash_hurdle_annual": CASH_HURDLE,
            "months": MONTHS, "input_sessions": len(rows), "input_audit": audit, "corporate_actions": actions,
            "price_series_sha256": hashlib.sha256(json.dumps([asdict(bar) for bar in rows], default=str).encode()).hexdigest(),
            "periods": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/agent-nse-trend"))
    parser.add_argument("--output", type=Path, default=Path("data/agent-nse-trend/report.json"))
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.data)
    else:
        if args.output.exists():
            raise FileExistsError(args.output)
        write_json(args.output, run(args.data))


if __name__ == "__main__":
    main()
