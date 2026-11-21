"""Read public prediction-market books and screen prices of complete sets."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MARKETS = "https://gamma-api.polymarket.com/markets?" + urlencode({
    "active": "true", "closed": "false", "limit": 80, "order": "volume24hr", "ascending": "false",
})
BOOKS = "https://clob.polymarket.com/books"
QUANTITY = Decimal("10")


def read_public(url, payload=None):
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"User-Agent": "Lane-Z-paper-research/1", "Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def capture():
    started = datetime.now(timezone.utc).isoformat()
    markets = read_public(MARKETS)
    eligible = []
    for market in markets:
        if not (market["active"] and market["acceptingOrders"] and market["enableOrderBook"]):
            continue
        if market["closed"] or market.get("negRisk") or not market.get("clobTokenIds"):
            continue
        tokens = json.loads(market["clobTokenIds"])
        outcomes = json.loads(market["outcomes"])
        if len(tokens) == len(outcomes) == 2 and tokens[0] != tokens[1]:
            eligible.append({"condition": market["conditionId"], "slug": market["slug"], "tokens": tokens})
    books = {}
    for start in range(0, len(eligible), 20):
        payload = [{"token_id": token} for pair in eligible[start:start + 20] for token in pair["tokens"]]
        for book in read_public(BOOKS, payload):
            books[book["asset_id"]] = book
    return {"started_utc": started, "ended_utc": datetime.now(timezone.utc).isoformat(),
            "listed_markets": len(markets), "eligible": eligible, "books": books,
            "markets_endpoint": MARKETS, "depth_endpoint": BOOKS}


def ask_cost(book, quantity):
    if Decimal(book["min_order_size"]) > quantity:
        return None
    remaining = quantity
    cost = Decimal(0)
    prices = sorted((Decimal(row["price"]), Decimal(row["size"])) for row in book["asks"])
    for price, size in prices:
        matched = min(remaining, size)
        cost += matched * price
        remaining -= matched
        if not remaining:
            return cost
    return None


def screen(snapshot):
    candidates = []
    checked = 0
    best = None
    stale_pair = missing_book = insufficient_depth = 0
    for pair in snapshot["eligible"]:
        books = [snapshot["books"].get(token) for token in pair["tokens"]]
        if not all(books) or any(book["market"] != pair["condition"] or book["neg_risk"] for book in books):
            missing_book += 1
            continue
        times = [int(book["timestamp"]) for book in books]
        if abs(times[0] - times[1]) > 250:
            stale_pair += 1
            continue
        costs = [ask_cost(book, QUANTITY) for book in books]
        if any(cost is None for cost in costs):
            insufficient_depth += 1
            continue
        full_set = sum(costs) / QUANTITY
        checked += 1
        best = full_set if best is None else min(best, full_set)
        if full_set < 1:
            candidates.append({"market": pair["slug"], "pre_fee_cost_per_set_pusd": str(full_set),
                               "book_snapshot_ms": times})
    return {
        "listed_markets": snapshot["listed_markets"], "eligible_binary_markets": len(snapshot["eligible"]),
        "complete_depth_pairs_checked": checked, "missing_pair_books": missing_book, "time_mismatched_pairs": stale_pair,
        "insufficient_ask_depth_or_minimum_size": insufficient_depth, "sets_per_market": int(QUANTITY),
        "minimum_pre_fee_cost_per_set_pusd": str(best) if best is not None else None,
        "pre_fee_candidates": candidates,
        **{key: snapshot[key] for key in ("started_utc", "ended_utc", "markets_endpoint", "depth_endpoint")},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    if args.capture:
        snapshot = capture()
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        with args.snapshot.open("x") as destination:
            json.dump(snapshot, destination)
    raw = args.snapshot.read_bytes()
    result = screen(json.loads(raw))
    result["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        json.dump(result, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
