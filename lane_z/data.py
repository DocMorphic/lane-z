"""Tardis book_ticker downloads and separate receipt/venue clock views."""

import csv
import gzip
import hashlib
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import numpy as np


US_PER_MS = 1_000
US_PER_HOUR = 3_600_000_000
BPS = 10_000
SYMBOLS = ("NVDA", "TSLA", "XYZ100", "AAPL", "MSFT")
SESSIONS = (
    "2026-04-01", "2026-05-01", "2026-06-01",
    "2026-07-01", "2026-09-01", "2026-10-01",
)
INTERNAL_SESSIONS = ("2026-01-01", "2026-02-01", "2026-03-01", "2026-08-01", "2026-11-01")
NEW_YORK = ZoneInfo("America/New_York")
DATA_ROOT = "https://datasets.tardis.dev/v1"
HYPERLIQUID = "hyperliquid"
BINANCE = "binance-futures"
QUOTE_DTYPE = np.dtype([
    ("venue", "i8"), ("receive", "i8"), ("bid", "f8"), ("ask", "f8"),
    ("bid_size", "f8"), ("ask_size", "f8"),
])


def dataset_symbol(symbol: str, exchange: str) -> str:
    return f"XYZ-{symbol}" if exchange == HYPERLIQUID else f"{symbol}USDT"


def sample_url(day: str, symbol: str, exchange: str = HYPERLIQUID) -> str:
    return f"{DATA_ROOT}/{exchange}/book_ticker/{day.replace('-', '/')}/{dataset_symbol(symbol, exchange)}.csv.gz"


def sample_path(root: Path, day: str, symbol: str, exchange: str = HYPERLIQUID) -> Path:
    return root / f"{day}-{dataset_symbol(symbol, exchange)}.csv.gz"


def fetch(root: Path, days: tuple[str, ...], markets: tuple[tuple[str, str], ...]) -> None:
    root.mkdir(parents=True, exist_ok=True)

    def download(task: tuple[str, str, str]) -> None:
        day, symbol, exchange = task
        destination = sample_path(root, day, symbol, exchange)
        if destination.exists():
            return
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as tmp:
            temporary = Path(tmp.name)
            try:
                with urlopen(sample_url(day, symbol, exchange), timeout=90) as response:
                    shutil.copyfileobj(response, tmp)
                tmp.close()
                read_quotes(temporary, symbol, exchange)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, [(day, symbol, exchange) for day in days for symbol, exchange in markets]))


def timestamp(day: str, hour: int, minute: int) -> int:
    dt = datetime.combine(date.fromisoformat(day), time(hour, minute), NEW_YORK)
    return int(dt.timestamp()) * 1_000_000


def number(value: str) -> float:
    return float(value) if value else float("nan")


def read_quotes(path: Path, symbol: str, exchange: str = HYPERLIQUID) -> np.ndarray:
    expected_symbol = dataset_symbol(symbol, exchange).replace("-", ":")
    with gzip.open(path, "rt", newline="") as handle:
        rows = csv.DictReader(handle)

        def parse():
            for row in rows:
                if row["exchange"] != exchange or row["symbol"] != expected_symbol:
                    raise ValueError(f"{path} contains a different market")
                yield (
                    int(row["timestamp"]), int(row["local_timestamp"]),
                    number(row["bid_price"]), number(row["ask_price"]),
                    number(row["bid_amount"]), number(row["ask_amount"]),
                )

        quotes = np.fromiter(parse(), dtype=QUOTE_DTYPE)
    if not len(quotes):
        raise ValueError(f"empty quote file: {path}")
    if np.any(np.diff(quotes["receive"]) < 0):
        raise ValueError(f"receipt clock out of order: {path}")
    return quotes


@dataclass(frozen=True)
class Book:
    clock: np.ndarray
    rows: np.ndarray

    @classmethod
    def observed(cls, rows: np.ndarray) -> "Book":
        # Do not overwrite a newer venue state with a late older message.
        rows = rows[rows["venue"] <= rows["receive"]]
        latest = np.maximum.accumulate(rows["venue"])
        rows = rows[rows["venue"] == latest]
        if not len(rows):
            raise ValueError("no quotes with a usable receipt clock")
        return cls(rows["receive"], rows)

    @classmethod
    def executed(cls, rows: np.ndarray) -> "Book":
        # File order breaks venue ties. This is a sampled BBO, not full L2.
        order = np.argsort(rows["venue"], kind="stable")
        rows = rows[order]
        return cls(rows["venue"], rows)

    def asof(self, clock: np.ndarray, max_age_us: int) -> tuple[np.ndarray, np.ndarray]:
        indices = np.searchsorted(self.clock, clock, side="right") - 1
        rows = self.rows[np.maximum(indices, 0)]
        valid = (indices >= 0) & (clock - rows["venue"] <= max_age_us)
        valid &= (rows["bid"] > 0) & (rows["ask"] > rows["bid"])
        valid &= (rows["bid_size"] > 0) & (rows["ask_size"] > 0)
        for field in ("bid", "ask", "bid_size", "ask_size"):
            valid &= np.isfinite(rows[field])
        return rows, valid


def quote_audit(path, rows, day, symbol, exchange=HYPERLIQUID, window=None) -> dict:
    start, end = window or (timestamp(day, 9, 35), timestamp(day, 15, 55))
    core = rows[(rows["receive"] >= start) & (rows["receive"] < end)]
    if len(core) < 2:
        raise ValueError(f"{path} has fewer than two core-session quotes")
    interval = np.diff(core["receive"]) / US_PER_MS
    age = (core["receive"] - core["venue"]) / US_PER_MS
    with path.open("rb") as handle:
        sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    return {
        "day": day,
        "symbol": symbol,
        "exchange": exchange,
        "url": sample_url(day, symbol, exchange),
        "sha256": sha256,
        "bytes": path.stat().st_size,
        "rows": len(rows),
        "session_rows": len(core),
        "arrival_spacing_ms_p50": float(np.median(interval)),
        "arrival_spacing_ms_p90": float(np.quantile(interval, 0.90)),
        "observation_age_ms_p50": float(np.median(age)),
        "observation_age_ms_p90": float(np.quantile(age, 0.90)),
        "venue_out_of_order": int(np.count_nonzero(np.diff(core["venue"]) < 0)),
        "negative_observation_age": int(np.count_nonzero(age < 0)),
    }
