"""Screen public Hyperliquid daily binary books for complete-set costs."""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError

from .event_hedge import ask_cost, read_public
from .spot_carry import ENDPOINT

QUANTITY = Decimal(100)
ASSETS = ("BTC", "ETH", "SOL", "HYPE")
ROUNDS = 36
CADENCE_SECONDS = 10


def now():
    return datetime.now(UTC).isoformat()


def read_book(coin):
    try:
        return read_public(ENDPOINT, {"type": "l2Book", "coin": coin})
    except (URLError, TimeoutError):
        return None


def capture():
    metadata = read_public(ENDPOINT, {"type": "outcomeMeta"})
    contracts = {}
    for row in metadata["outcomes"]:
        tags = dict(field.split(":", 1) for field in row["description"].split("|") if ":" in field)
        asset = tags.get("underlying")
        if (row.get("venue") or row.get("quoteToken") != "USDC" or tags.get("class") != "priceBinary"
                or tags.get("period") != "1d" or asset not in ASSETS):
            continue
        if asset in contracts or len(row["sideSpecs"]) != 2:
            raise ValueError(f"Expected one two-sided daily market for {asset}")
        contracts[asset] = {"expiry": tags["expiry"], "coins": [f"#{10 * row['outcome'] + side}" for side in (0, 1)]}
    if set(contracts) != set(ASSETS):
        raise ValueError("Missing a designated public daily binary market")
    coins = [coin for asset in ASSETS for coin in contracts[asset]["coins"]]
    rounds = []
    with ThreadPoolExecutor(max_workers=len(coins)) as pool:
        origin = time.monotonic()
        for index in range(ROUNDS):
            if index:
                time.sleep(max(0, origin + index * CADENCE_SECONDS - time.monotonic()))
            start = now()
            books = dict(zip(coins, pool.map(read_book, coins), strict=True))
            rounds.append({"started_utc": start, "ended_utc": now(), "books": books})
    return {"contracts": contracts, "rounds": rounds}


def screen(snapshot):
    batches = []
    for offset in range(0, len(snapshot["rounds"]), 12):
        batch = snapshot["rounds"][offset:offset + 12]
        checked = time_mismatch = insufficient = 0
        minima = {}
        candidates = []
        for tick in batch:
            for asset, contract in snapshot["contracts"].items():
                books = [tick["books"].get(coin) for coin in contract["coins"]]
                if not all(books) or abs(books[0]["time"] - books[1]["time"]) > 250:
                    time_mismatch += 1
                    continue
                costs = [
                    ask_cost({"min_order_size": 0, "asks": [
                        {"price": ask["px"], "size": ask["sz"]} for ask in book["levels"][1]
                    ]}, QUANTITY)
                    for book in books
                ]
                if any(cost is None or cost < 10 for cost in costs):
                    insufficient += 1
                    continue
                checked += 1
                unit_cost = sum(costs) / QUANTITY
                minima[asset] = min(minima.get(asset, unit_cost), unit_cost)
                if unit_cost < 1:
                    candidates.append({"asset": asset, "started_utc": tick["started_utc"], "before_fee_cost": str(unit_cost)})
        batches.append({
            "first_poll_utc": batch[0]["started_utc"], "last_poll_utc": batch[-1]["started_utc"],
            "complete_depth_pairs_checked": checked, "missing_or_time_mismatched_pairs": time_mismatch,
            "insufficient_depth_or_10_usdc_per_side": insufficient,
            "min_before_fee_cost_per_set": {name: str(cost) for name, cost in minima.items()},
            "before_fee_candidates": candidates,
        })
    return {"endpoint": ENDPOINT, "sets_per_pair": int(QUANTITY), "contracts": snapshot["contracts"], "batches": batches}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    if args.capture:
        args.data.parent.mkdir(parents=True, exist_ok=True)
        with args.data.open("x") as out:
            json.dump(capture(), out)
    raw = args.data.read_bytes()
    report = screen(json.loads(raw))
    report["source_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
