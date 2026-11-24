"""Replay a frozen Hyperliquid BBO and trade-flow continuation screen."""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median

from .spot_carry import PERP_FEE


SIZES = {"BTC": 5, "ETH": 4, "SOL": 2}
LOOKBACK = 3_000
HOLD = 10_000
QUOTE_WAIT = 1_000
SOURCE_MAX_AGE = 2_000
MIN_NOTIONAL = 10
MAX_NOTIONAL = 200
WINDOW_MS = 60_000
WARMUP_MS = 20_000
SCENARIOS = {"base": (750, 0.0), "stress": (1500, 0.5)}
MAX_ROUND_TRIP_MS = HOLD + 2 * (max(lag for lag, _ in SCENARIOS.values()) + QUOTE_WAIT)


def stamp(t):
    return datetime.fromtimestamp(t / 1000, UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Quote:
    received: int
    venue: int
    bid: float
    ask: float
    bid_size: float
    ask_size: float

    @property
    def valid(self):
        return (0 < self.bid < self.ask and min(self.bid_size, self.ask_size) > 0
                and -100 <= self.received - self.venue <= SOURCE_MAX_AGE)

    @property
    def mid(self):
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Trade:
    received: int
    venue: int
    direction: int
    notional: float


def tape(path):
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    if not rows or rows[0]["channel"] != "start" or rows[-1].get("reason") != "duration":
        raise ValueError("A complete, uninterrupted WebSocket capture is required")
    clock = [row["received"] for row in rows]
    if clock != sorted(clock) or clock[-1] - clock[0] < WARMUP_MS + 2 * WINDOW_MS:
        raise ValueError("The capture did not cover both fixed receipt-time segments")
    quotes = {a: [] for a in SIZES}
    trades = {a: [] for a in SIZES}
    seen = {a: set() for a in SIZES}
    for row in rows[1:-1]:
        asset, t, source = row["coin"], row["received"], row["time"]
        if row["channel"] == "bbo":
            bid, ask = row["bid"], row["ask"]
            prices = [float(side[k]) if side else 0.0 for k in ("px", "sz") for side in (bid, ask)]
            if not all(math.isfinite(p) for p in prices):
                raise ValueError("Invalid order-book values")
            quotes[asset].append(Quote(t, source, *prices))
        elif row["channel"] == "trades" and row["tid"] not in seen[asset]:
            px, qty = float(row["px"]), float(row["sz"])
            if row["side"] not in ("B", "A") or not math.isfinite(px * qty) or min(px, qty) <= 0:
                raise ValueError("Invalid trade feed")
            seen[asset].add(row["tid"])
            if -100 <= t - source <= SOURCE_MAX_AGE:
                trades[asset].append(Trade(t, source, 1 if row["side"] == "B" else -1, px * qty))
    if any(not quotes[asset] or not trades[asset] for asset in SIZES):
        raise ValueError("The capture is missing at least one native quote or trade stream")
    return clock[0], clock[-1], quotes, trades, sha256(raw).hexdigest()


def ofi(before, after):
    return ((after.bid >= before.bid) * after.bid_size - (after.bid <= before.bid) * before.bid_size
            - (after.ask <= before.ask) * after.ask_size + (after.ask >= before.ask) * before.ask_size)


def candidates(quotes, flow, start, end):
    quote_clock = [q.received for q in quotes]
    trade_clock = [t.received for t in flow]
    passed = []
    counters = Counter()
    for t in range(start, end - MAX_ROUND_TRIP_MS + 1, 1_000):
        if t // 3_600_000 != (t + MAX_ROUND_TRIP_MS) // 3_600_000:
            continue
        counters["eligible_times"] += 1
        q0 = bisect_right(quote_clock, t - LOOKBACK) - 1
        q1 = bisect_right(quote_clock, t)
        if q0 < 0:
            continue
        panel = quotes[q0:q1]
        if (len(panel) < 2 or not all(q.valid for q in panel) or t - panel[-1].received > QUOTE_WAIT
                or t - LOOKBACK - panel[0].received > QUOTE_WAIT):
            continue
        first, last = panel[0], panel[-1]
        if 10_000 * (last.ask - last.bid) / last.mid > 2:
            continue
        counters["fresh_narrow_quotes"] += 1
        prints = flow[bisect_right(trade_clock, t - LOOKBACK):bisect_right(trade_clock, t)]
        if len(prints) < 6:
            continue
        signed = math.fsum(x.direction * x.notional for x in prints)
        absolute = math.fsum(x.notional for x in prints)
        if abs(signed) < 0.4 * absolute:
            continue
        counters["taker_dominance"] += 1
        direction = 1 if signed > 0 else -1
        if direction * (last.mid / first.mid - 1) * 10_000 < 1:
            continue
        counters["midpoint_continuation_filter"] += 1
        average_size = math.fsum(q.ask_size + q.bid_size for q in panel) / (2 * len(panel))
        strength = direction * math.fsum(ofi(a, b) for a, b in zip(panel, panel[1:])) / average_size
        if strength >= 0.5:
            passed.append((t, direction))
    return passed, counters


def fill_quote(quotes, clock, target):
    index = bisect_left(clock, target)
    if index == len(quotes):
        return None
    quote = quotes[index]
    return quote if quote.valid and quote.received <= target + QUOTE_WAIT else None


def replay(asset, quotes, signals, lag, extra, trade_cap=MAX_NOTIONAL):
    clock = [q.received for q in quotes]
    ledgers = []
    failures = Counter()
    unlocked = 0
    for t, direction in signals:
        if t < unlocked:
            failures["one_position_limit"] += 1
            continue
        entry = fill_quote(quotes, clock, t + lag)
        if entry is None:
            unlocked = t + lag + QUOTE_WAIT
            failures["entry_quote_missing_or_stale"] += 1
            continue
        entry_touch, entry_size = (entry.ask, entry.ask_size) if direction > 0 else (entry.bid, entry.bid_size)
        scale = 10 ** SIZES[asset]
        slip = extra / 10_000
        open_px = entry_touch * (1 + direction * slip)
        qty = math.floor(scale * min(trade_cap / open_px, entry_size)) / scale
        notional = qty * open_px
        if notional < MIN_NOTIONAL:
            failures["insufficient_entry_touch"] += 1
            continue
        exit_target = entry.received + HOLD + lag
        exit_ = fill_quote(quotes, clock, exit_target)
        unlocked = exit_target + QUOTE_WAIT if exit_ is None else exit_.received
        if exit_ is None:
            failures["unresolved_exit_quote"] += 1
            break
        exit_touch, exit_size = (exit_.bid, exit_.bid_size) if direction > 0 else (exit_.ask, exit_.ask_size)
        if exit_size < qty and not math.isclose(exit_size, qty, rel_tol=1e-12):
            failures["unresolved_exit_touch_size"] += 1
            break
        close_px = exit_touch * (1 - direction * slip)
        gross = direction * qty * (exit_.mid - entry.mid)
        spread = gross - direction * qty * (exit_touch - entry_touch)
        execution_reserve = qty * slip * (entry_touch + exit_touch)
        fee = qty * PERP_FEE * (open_px + close_px)
        ledgers.append({
            "asset": asset, "decision_utc": stamp(t), "entry_utc": stamp(entry.received),
            "exit_utc": stamp(exit_.received), "direction": direction, "entry_usdc": notional,
            "midpoint_pnl_usdc": gross, "spread_usdc": spread, "execution_reserve_usdc": execution_reserve,
            "fee_usdc": fee, "net_usdc": gross - spread - execution_reserve - fee,
            "observed_entry_delay_ms": entry.received - t, "observed_exit_extra_delay_ms": exit_.received - exit_target,
        })
    return ledgers, failures


def report(path, trade_cap=MAX_NOTIONAL):
    if not MIN_NOTIONAL <= trade_cap <= MAX_NOTIONAL:
        raise ValueError("The displayed-touch replay cap must be between 10 and 200 USDC")
    first, last, quotes, trades, digest = tape(path)
    periods = []
    for start in (first + WARMUP_MS, first + WARMUP_MS + WINDOW_MS):
        end = start + WINDOW_MS
        signal_model = {a: candidates(quotes[a], trades[a], start, end) for a in SIZES}
        scenarios = {}
        for name, (delay, slip) in SCENARIOS.items():
            rows = []
            failures = Counter()
            for asset in SIZES:
                ledger, errors = replay(asset, quotes[asset], signal_model[asset][0], delay, slip, trade_cap)
                rows.extend(ledger)
                failures.update(errors)
            keys = ("entry_usdc", "midpoint_pnl_usdc", "spread_usdc", "execution_reserve_usdc", "fee_usdc", "net_usdc")
            totals = {k: math.fsum(row[k] for row in rows) for k in keys}
            scenarios[name] = {
                **totals,
                "net_bps_on_entry_notional": 10_000 * totals["net_usdc"] / totals["entry_usdc"] if rows else None,
                "complete": not any(failures[k] for k in ("unresolved_exit_quote", "unresolved_exit_touch_size")),
                "closed_paper_trades": len(rows), "outcomes": dict(failures), "ledger": rows,
            }
        periods.append({
            "start_utc": stamp(start), "end_utc": stamp(end), "replay": scenarios,
            "signal_counts": {a: {"signals": len(signals), **count} for a, (signals, count) in signal_model.items()},
        })
    return {
        "capture_start_utc": stamp(first), "capture_end_utc": stamp(last), "source_sha256": digest,
        "trade_notional_cap_usdc": trade_cap,
        "coverage": {a: {"bbo": len(quotes[a]), "valid_bbo": sum(q.valid for q in quotes[a]), "trades": len(trades[a]),
                         "bbo_median_receive_minus_venue_ms": median(q.received - q.venue for q in quotes[a])}
                     for a in SIZES},
        "windows": periods,
        "beat_zero_after_stress_in_both": all(w["replay"]["stress"]["complete"] and w["replay"]["stress"]["net_usdc"] > 0
                                             for w in periods),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--trade-cap", type=float, default=MAX_NOTIONAL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    encoded = json.dumps(report(args.capture, args.trade_cap), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
