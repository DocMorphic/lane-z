"""Reconcile a public TSLA order tape and simulate a small passive paper order."""

import argparse
import hashlib
import json
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from itertools import groupby
from pathlib import Path

import pyarrow.parquet as parquet


COIN = "xyz:TSLA"
DATASETS = ("l2_orderbook", "l4_checkpoints", "l4_orderbook", "l4_orders", "trades")
OBSERVATION_DELAY_MS = 500
MAX_AGE_MS = 2_000
PRIORITY_WINDOW_MS = 400
LOT_SCALE = 1_000


def timestamp_ms(value):
    return round(value.timestamp() * 1_000)


def lot_count(value):
    size = Decimal(str(value)) * LOT_SCALE
    if size != int(size) or size < 0:
        raise ValueError(f"Unexpected TSLA quantity {value}")
    return int(size)


def verified_sources(root):
    manifest = json.loads((root / "manifest.json").read_text())
    entries = {row["dataset"]: row for row in manifest["artifacts"] if row.get("symbol") == COIN}
    audit = {}
    for name in DATASETS:
        entry = entries[name]
        file = entry["download"]
        path = root / file["filename"]
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != file["sha256"] or path.stat().st_size != file["bytes"]:
            raise ValueError(f"Bytes disagree with the manifest for {name}")
        rows = parquet.ParquetFile(path).metadata.num_rows
        if rows != entry["row_count"]:
            raise ValueError(f"Wrong row count for {name}")
        audit[name] = {"url": "https://0xarchive.io" + file["url"], "sha256": digest, "rows": rows}
    return audit


@dataclass(frozen=True)
class Order:
    side: str
    price: Decimal
    size: int


def checkpoint(row):
    orders = {}
    for level in json.loads(row["data"]):
        for _, raw in level:
            oid = int(raw["oid"])
            if oid in orders or raw["coin"] != COIN or raw["isTrigger"]:
                raise ValueError("Unexpected order in L4 checkpoint")
            orders[oid] = Order(raw["side"], Decimal(raw["limitPx"]), lot_count(raw["sz"]))
    return orders


@dataclass(frozen=True)
class Touch:
    price: Decimal
    size: int
    order_ids: frozenset[int]


class L4Book:
    def __init__(self, orders):
        self.orders = orders.copy()
        self.levels = {"B": {}, "A": {}}
        for oid, order in self.orders.items():
            self.levels[order.side].setdefault(order.price, {})[oid] = order.size

    def apply(self, event):
        oid, kind = event["oid"], event["diff_type"]
        side, price = event["side"], Decimal(str(event["price"]))
        levels = self.levels[side]
        prior = self.orders.get(oid)
        if event["coin"] != COIN or kind not in ("new", "update", "remove"):
            raise ValueError("Unrecognized order-book event")
        if kind == "new":
            if prior is not None:
                raise ValueError(f"Order {oid} already exists")
        elif prior is None or (prior.side, prior.price) != (side, price):
            raise ValueError(f"Order {oid} update or removal does not match resting order")

        if kind == "remove":
            del self.orders[oid]
            del levels[price][oid]
            if not levels[price]:
                del levels[price]
        else:
            size = lot_count(event["new_size"])
            self.orders[oid] = Order(side, price, size)
            levels.setdefault(price, {})[oid] = size

    def touch(self, side):
        levels = self.levels[side]
        price = max(levels) if side == "B" else min(levels)
        orders = levels[price]
        return Touch(price, sum(orders.values()), frozenset(orders))


@dataclass(frozen=True)
class Print:
    oid: int
    side: str
    price: Decimal
    lots: int


@dataclass(frozen=True)
class Block:
    time: int
    bid: Touch
    ask: Touch
    added: tuple
    prints: tuple[Print, ...]

    def touch(self, side):
        return self.bid if side == "B" else self.ask


def pair_prints(trades, start, end):
    by_id = defaultdict(list)
    for trade in trades:
        time = timestamp_ms(trade["timestamp"])
        if start < time <= end:
            if trade["coin"] != COIN:
                raise ValueError("Unexpected instrument in trade rows")
            by_id[time, trade["trade_id"]].append(trade)

    recorded = defaultdict(list)
    for (time, _), pair in by_id.items():
        if len(pair) != 2 or pair[0]["crossed"] == pair[1]["crossed"]:
            raise ValueError(f"No matching maker and taker at {time}")
        maker, taker = pair[::-1] if pair[0]["crossed"] else pair
        if (maker["price"], maker["size"]) != (taker["price"], taker["size"]) or maker["side"] == taker["side"]:
            raise ValueError(f"Execution terms disagree at {time}")
        recorded[time].append(Print(maker["order_id"], maker["side"], Decimal(str(maker["price"])),
                                    lot_count(maker["size"])))
    return recorded


def supported_prints(prints, prior_orders, after_orders):
    size_by_order = Counter()
    for print_ in prints:
        size_by_order[print_.oid] += print_.lots

    eligible = []
    for print_ in prints:
        prior = prior_orders[print_.oid]
        if prior is None or (prior.side, prior.price) != (print_.side, print_.price):
            continue
        after = after_orders.get(print_.oid, Order(prior.side, prior.price, 0))
        if prior.size - after.size >= size_by_order[print_.oid]:
            eligible.append(print_)
    return tuple(eligible)


def replay_book(root):
    time_rows = parquet.read_table(root / "l4_checkpoints.parquet").to_pylist()
    checks = {row["last_block_number"]: checkpoint(row) for row in time_rows}
    first, last = time_rows[0], time_rows[-1]
    first_block, last_block = first["last_block_number"], last["last_block_number"]
    start, end = timestamp_ms(first["timestamp"]), timestamp_ms(last["timestamp"])
    raw_diffs = parquet.read_table(root / "l4_orderbook.parquet").to_pylist()
    keys = [(r["block_number"], r["seq"]) for r in raw_diffs]
    if any(a >= b for a, b in zip(keys, keys[1:])):
        raise ValueError("Block and sequence keys are not strictly increasing")
    diffs = [r for r in raw_diffs if first_block < r["block_number"] <= last_block]
    raw_trades = parquet.read_table(root / "trades.parquet").to_pylist()
    prints = pair_prints(raw_trades, start, end)
    lifecycle = parquet.read_table(root / "l4_orders.parquet", columns=["block_number", "status"])
    statuses = Counter(r["status"] for r in lifecycle.to_pylist() if first_block < r["block_number"] <= last_block)

    book = L4Book(checks[first_block])
    frames = [Block(start, book.touch("B"), book.touch("A"), (), ())]
    audited = []
    last_count = 0
    event_count = 0
    for block, events in groupby(diffs, key=lambda row: row["block_number"]):
        events = list(events)
        time = timestamp_ms(events[0]["block_time"])
        if time <= frames[-1].time or any(timestamp_ms(e["block_time"]) != time for e in events):
            raise ValueError("Inconsistent order-book block timestamps")
        prints_at_block = prints.pop(time, [])
        prior = {p.oid: book.orders.get(p.oid) for p in prints_at_block}
        added = []
        zeroed = []
        for event in events:
            book.apply(event)
            if event["diff_type"] == "new":
                added.append((event["oid"], book.orders[event["oid"]]))
            if event["new_size"] == 0:
                zeroed.append(event["oid"])
        if any(oid in book.orders and book.orders[oid].size == 0 for oid in zeroed):
            raise ValueError("A zero-size order remained after its block")
        eligible = supported_prints(prints_at_block, prior, book.orders)
        frames.append(Block(time, book.touch("B"), book.touch("A"), tuple(added), eligible))
        event_count += len(events)
        if block in checks:
            if book.orders != checks[block]:
                raise ValueError(f"L4 replay does not match checkpoint block {block}")
            audited.append({"block": block, "orders": len(book.orders), "applied_diffs": event_count - last_count})
            last_count = event_count
    if prints or frames[-1].time != end or len(audited) != len(checks) - 1:
        raise ValueError("Missing trades, blocks or checkpoints in replay")

    paired_prints = sum(start < timestamp_ms(row["timestamp"]) <= end for row in raw_trades) // 2
    audit = {
        "first_checkpoint": {"utc": first["timestamp"].isoformat(), "block": first_block,
                             "orders": len(checks[first_block])},
        "last_checkpoint_utc": last["timestamp"].isoformat(),
        "matched_checkpoints": audited, "market_blocks_with_diffs": len(frames) - 1,
        "lifecycle_status_counts": dict(statuses), "maker_taker_matched_prints": paired_prints,
        "supported_previously_resting_maker_prints": sum(len(frame.prints) for frame in frames),
    }
    return frames, audit


def entry_fills(frames, index, cancel, side, price, requested):
    resting = frames[index]
    ahead = set(resting.touch(side).order_ids)
    first_fill = None
    credited = 0
    evidence = Counter()
    for block in frames[index + 1:cancel + 1]:
        if block.time <= resting.time + PRIORITY_WINDOW_MS:
            ahead.update(oid for oid, order in block.added if order.side == side and order.price == price)
            continue
        for print_ in block.prints:
            if print_.side != side:
                continue
            at_price_behind = print_.price == price and print_.oid not in ahead
            through = print_.price < price if side == "B" else print_.price > price
            if through or at_price_behind:
                quantity = min(print_.lots, requested - credited)
                credited += quantity
                evidence["strictly_through" if through else "same_price_behind"] += quantity
                first_fill = first_fill or block.time
            if credited == requested:
                return credited, first_fill, evidence
    return credited, first_fill, evidence


def observe(frames, times, now):
    observed = bisect_right(times, now - OBSERVATION_DELAY_MS) - 1
    if observed < 0 or now - times[observed] > MAX_AGE_MS:
        return None
    frame = frames[observed]
    return frame if frame.bid.price < frame.ask.price else None


def test_delay(frames, delay):
    times = [frame.time for frame in frames]
    counts = Counter({k: 0 for k in ("decision_slots", "entry_attempts", "rested", "moved_touch", "would_cross",
                                    "pending_skips", "stale_skips", "maker_fills", "partial_entry_fills", "closed")})
    gross = fees = reserve = Decimal(0)
    outstanding = None
    credited_lots = Counter()
    closed_after = times[0]
    for slot, send in enumerate(range(times[0] + 1_000, times[-1] - 8_000, 2_000)):
        counts["decision_slots"] += 1
        if send < closed_after:
            counts["pending_skips"] += 1
            continue
        source = observe(frames, times, send)
        if source is None:
            counts["stale_skips"] += 1
            continue
        side, opposite, direction = ("B", "A", 1) if slot % 2 == 0 else ("A", "B", -1)
        price = source.touch(side).price
        wanted = int(100 * LOT_SCALE / price)
        if price * wanted < 10 * LOT_SCALE:
            continue
        counts["entry_attempts"] += 1
        posted = bisect_left(times, send + delay)
        arrival = frames[posted]
        closed_after = arrival.time + delay
        if direction * (price - arrival.touch(opposite).price) >= 0:
            counts["would_cross"] += 1
            continue
        if price != arrival.touch(side).price:
            counts["moved_touch"] += 1
            continue

        counts["rested"] += 1
        cancel = bisect_left(times, arrival.time + delay + 1_000 + delay)
        closed_after = times[cancel] + delay
        filled, first_fill, evidence = entry_fills(frames, posted, cancel, side, price, wanted)
        if filled == 0:
            continue
        counts["maker_fills"] += 1
        counts["partial_entry_fills"] += int(filled < wanted)
        credited_lots.update(evidence)

        exit_send = max(closed_after, first_fill + 2_000)
        exit_source = observe(frames, times, exit_send)
        received = bisect_left(times, exit_send + delay)
        position = {"maker_side": "long" if side == "B" else "short", "entry_units": filled / LOT_SCALE}
        if exit_source is None or received >= len(times):
            outstanding = {**position, "ioc_executed_units": 0, "remaining_units": filled / LOT_SCALE,
                           "exit_issue": "no_valid_observation_or_arrival_block"}
            break
        exit_touch = frames[received].touch(side)
        exit_limit = exit_source.touch(side).price * (1 - direction * Decimal("0.001"))
        acceptable = direction * (exit_touch.price - exit_limit) >= 0
        executed = min(filled, exit_touch.size) if acceptable else 0
        if executed != filled:
            outstanding = {**position, "ioc_executed_units": executed / LOT_SCALE,
                           "remaining_units": (filled - executed) / LOT_SCALE,
                           "exit_issue": "insufficient_touch_size" if acceptable else "arrival_touch_outside_price_cap"}
            break

        size = Decimal(executed) / LOT_SCALE
        gross += direction * (exit_touch.price - price) * size
        fees += (price * Decimal("0.00003") + exit_touch.price * Decimal("0.00009")) * size
        reserve += exit_touch.price * Decimal("0.00005") * size
        counts["closed"] += 1
        closed_after = times[received] + delay

    return {**counts, "gross_closed_usd": float(gross), "fees_closed_usd": float(fees),
            "slippage_reserve_closed_usd": float(reserve), "net_closed_usd": float(gross - fees - reserve),
            "credited_entry_units_by_print_evidence": {kind: count / LOT_SCALE for kind, count in credited_lots.items()},
            "unresolved_position": outstanding, "complete": outstanding is None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = verified_sources(args.data)
    frames, audit = replay_book(args.data)
    performance = {str(delay): test_delay(frames, delay) for delay in (250, 500, 900)}
    result = {"symbol": COIN, "protocol": "single_sample_zero_priority_paper_smoke",
              "modeled_market_data_delay_ms": OBSERVATION_DELAY_MS, "sources": sources,
              "order_book_audit": audit, "by_per_message_delay_ms": performance}
    with args.output.open("x") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
