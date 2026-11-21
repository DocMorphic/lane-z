"""Reconstruct top quotes from CryptoHFTData depth and NYSE Arca sample BBO."""

import gzip
import heapq
from itertools import groupby
from pathlib import Path

import numpy as np

from .data import QUOTE_DTYPE, US_PER_MS, timestamp


class DepthSide:
    """Track live sizes and lazily discard removed prices from the priority heap."""

    def __init__(self, sign: int):
        self.sign = sign
        self.sizes: dict[float, float] = {}
        self.heap: list[float] = []

    def apply(self, price: float, size: float) -> None:
        if size == 0:
            self.sizes.pop(price, None)
            return
        if price not in self.sizes:
            heapq.heappush(self.heap, self.sign * price)
        self.sizes[price] = size

    def best(self) -> tuple[float, float]:
        while self.heap:
            price = self.sign * self.heap[0]
            if price in self.sizes:
                return price, self.sizes[price]
            heapq.heappop(self.heap)
        return float("nan"), 0.0


DEPTH_COLUMNS = (
    "received_time", "event_time", "transaction_time", "event_type", "final_update_id",
    "prev_final_update_id", "side", "price", "quantity",
)


def depth_updates(path: Path):
    import pyarrow.parquet as parquet

    for batch in parquet.ParquetFile(path).iter_batches(columns=DEPTH_COLUMNS):
        yield from zip(*(column.to_pylist() for column in batch.columns))


def reconstruct_depth(updates, audit: dict):
    last_sequence = None
    last_received = -1
    for key, message in groupby(updates, key=lambda row: row[:6]):
        received_ns, event_ms, transaction_ms, kind, sequence, previous = key
        received = received_ns // 1_000
        venue = (transaction_ms if transaction_ms is not None else event_ms) * US_PER_MS
        if received < last_received:
            raise ValueError("depth archive is out of receipt order")
        last_received = received
        if kind == "snapshot":
            bid = DepthSide(-1)
            ask = DepthSide(1)
            audit["snapshots"] += 1
        elif kind != "update":
            raise ValueError(f"unexpected depth message type {kind!r}")
        elif last_sequence is None or (previous is not None and previous != last_sequence):
            audit["discarded_updates"] += 1
            last_sequence = None
            yield venue, received, np.nan, np.nan, 0.0, 0.0
            continue
        for _, _, _, _, _, _, side, raw_price, raw_size in message:
            if side not in ("bid", "ask"):
                raise ValueError(f"unexpected depth side {side!r}")
            price, size = float(raw_price), float(raw_size)
            if price <= 0 or size < 0 or not np.isfinite(price + size):
                raise ValueError("invalid price or quantity in depth update")
            (bid if side == "bid" else ask).apply(price, size)
        last_sequence = sequence
        best_bid, bid_size = bid.best()
        best_ask, ask_size = ask.best()
        yield venue, received, best_bid, best_ask, bid_size, ask_size


def depth_quotes(path: Path) -> tuple[np.ndarray, dict]:
    audit = {"snapshots": 0, "discarded_updates": 0}
    quotes = np.fromiter(reconstruct_depth(depth_updates(path), audit), dtype=QUOTE_DTYPE)
    if not len(quotes) or not audit["snapshots"]:
        raise ValueError(f"{path} lacks quote snapshots")
    return quotes, audit


def us_since_midnight(stamp: str) -> int:
    hour, minute, seconds = stamp.split(":")
    whole, fraction = seconds.split(".")
    return (int(hour) * 3_600 + int(minute) * 60 + int(whole)) * 1_000_000 + int(fraction[:6].ljust(6, "0"))


def arca_quotes(files: list[Path], day: str, symbols: tuple[str, ...], feed_delay_ms: int):
    midnight = timestamp(day, 0, 0)
    by_symbol = {symbol: [] for symbol in symbols}
    patterns = tuple(f",{symbol},".encode() for symbol in symbols)
    for path in files:
        with gzip.open(path, "rb") as source:
            for line in source:
                if not line.startswith(b"140,") or not any(pattern in line for pattern in patterns):
                    continue
                _, seq, stamp, symbol, *fields = line.decode("ascii").split(",")
                if symbol not in by_symbol:
                    continue
                _, ask, ask_size, bid, bid_size, condition, *_ = fields
                venue = midnight + us_since_midnight(stamp)
                receive = venue + feed_delay_ms * US_PER_MS
                if condition == "R":
                    quote = venue, receive, float(bid), float(ask), float(bid_size), float(ask_size)
                else:
                    quote = venue, receive, np.nan, np.nan, 0.0, 0.0
                by_symbol[symbol].append((venue, int(seq), quote))
    return {
        symbol: np.array([quote for _, _, quote in sorted(rows, key=lambda row: row[:2])], dtype=QUOTE_DTYPE)
        for symbol, rows in by_symbol.items()
    }
