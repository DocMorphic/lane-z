"""Replay a two-venue, same-stock basis trade with independent paper fills."""

import argparse
import json
from dataclasses import asdict, dataclass, replace
from itertools import groupby
from math import ceil, isclose
from pathlib import Path

import numpy as np

from .data import BPS, US_PER_HOUR, US_PER_MS
from .forward import utc_window
from .model import GRID_MS, LOCAL_FEATURES, relative_basis
from .replay import ENTRY_LIMIT_BPS, EXIT_LIMIT_BPS, Execution, Session, fill, price_limit
from .reversion import VENUES, funding_guarded, make_session, quote_coverage, read_crypto_hour
from .study import TARGETS


SPLIT = {
    "validation": tuple(f"2026-10-{day}" for day in (13, 14, 15)),
    "test": tuple(f"2026-10-{day}" for day in (16, 19, 20, 21, 22, 23)),
}
HOLD_MS = 30_000
SIZE_USD = 100
COMMON_LOT = 0.01
SPREAD_CAP_BPS = 8
MAX_AGE_MS = 750
EXTRA_EDGE_BPS = 2
LIGHTER = replace(VENUES["lighter"], hold_ms=HOLD_MS, notional_usd=SIZE_USD)
BINANCE = replace(VENUES["binance"], hold_ms=HOLD_MS, notional_usd=SIZE_USD)
SCENARIOS = {
    "base": (LIGHTER, BINANCE),
    "lighter_900ms": (replace(LIGHTER, delay_ms=900), BINANCE),
    "binance_900ms": (LIGHTER, replace(BINANCE, delay_ms=900)),
    "standard_binance_fee": (LIGHTER, replace(BINANCE, fee_bps=5)),
    "slippage_1bp_per_fill": (replace(LIGHTER, slippage_bps=1), replace(BINANCE, slippage_bps=1)),
}


@dataclass(frozen=True)
class Fill:
    lots: int
    quantity: float
    price: float
    time: int
    quote_time: int


@dataclass(frozen=True)
class Leg:
    session: Session
    execution: Execution
    arrival: np.ndarray
    quotes: np.ndarray
    valid: np.ndarray

    @classmethod
    def with_execution(cls, session, execution):
        arrival = session.grid + execution.delay_ms * US_PER_MS
        quotes, valid = session.venue.asof(arrival, execution.max_venue_age_ms * US_PER_MS)
        return cls(session, execution, arrival, quotes, valid)

    def touch(self, index, side):
        return self.session.features.quote["ask" if side > 0 else "bid"][index]

    def execute(self, index, side, lots, cushion, last_quote=None):
        quote = self.quotes[index]
        if not np.isfinite(self.session.features.mid[index]) or quote["venue"] == last_quote:
            return None
        ceiling = price_limit(self.touch(index, side), side, cushion)
        executed, price = fill(quote, self.valid[index], side, ceiling, lots, self.execution.lot_size)
        if executed:
            return Fill(executed, executed * self.execution.lot_size, price,
                        int(self.arrival[index]), int(quote["venue"]))
        return None


@dataclass
class Position:
    leg: Leg
    side: int
    entry: Fill
    exits: list[Fill]
    remaining_lots: int

    def close(self, index):
        last_quote = self.exits[-1].quote_time if self.exits else None
        match = self.leg.execute(index, -self.side, self.remaining_lots, EXIT_LIMIT_BPS, last_quote)
        if match:
            self.remaining_lots -= match.lots
            self.exits.append(match)

    def pnl(self):
        bought_or_sold = self.entry.quantity * self.entry.price
        closed = sum(match.quantity * match.price for match in self.exits)
        turnover = bought_or_sold + closed
        terms = self.leg.execution
        return (
            self.side * (closed - bought_or_sold),
            turnover * terms.fee_bps / BPS,
            turnover * terms.slippage_bps / BPS,
            bought_or_sold,
        )


def residual_exposure(positions):
    events = [(p.entry.time, p.side * p.entry.quantity) for p in positions]
    events.extend((match.time, -p.side * match.quantity) for p in positions for match in p.exits)
    open_quantity = peak = 0.0
    duration = 0
    previous = min(timestamp for timestamp, _ in events)
    for timestamp, matches in groupby(sorted(events), key=lambda row: row[0]):
        if abs(open_quantity) > 1e-9:
            duration += timestamp - previous
        open_quantity += sum(quantity for _, quantity in matches)
        peak = max(peak, abs(open_quantity))
        previous = timestamp
    return duration / US_PER_MS, peak


def directions(lighter, binance):
    gap = relative_basis(lighter.features.mid, binance.features.mid)
    cost = np.full(len(gap), EXTRA_EDGE_BPS, dtype=float)
    eligible = np.ones(len(gap), dtype=bool)
    for session, execution in ((lighter, LIGHTER), (binance, BINANCE)):
        quote = session.features.quote
        age = (session.grid - quote["venue"]) / US_PER_MS
        spread = session.features.local[:, LOCAL_FEATURES.index("spread")]
        eligible &= funding_guarded(session, execution) & (age <= MAX_AGE_MS) & (spread <= SPREAD_CAP_BPS)
        cost += spread + 2 * (execution.fee_bps + execution.slippage_bps)
    side = np.sign(np.where(eligible & (abs(gap) > cost), gap, 0)).astype(np.int8)
    starts = np.where(side != np.r_[0, side[:-1]], side, 0)
    return starts, {"common_eligible_rows": int((eligible & np.isfinite(gap)).sum()),
                    "signal_onsets": int(np.count_nonzero(starts))}


def replay_pair(sessions, instructions, terms):
    grid = sessions[0].grid
    legs = tuple(Leg.with_execution(session, execution) for session, execution in zip(sessions, terms, strict=True))
    max_delay_ms = max(execution.delay_ms for execution in terms)
    ack_steps = ceil(2 * max_delay_ms / GRID_MS)
    held_steps = ceil((HOLD_MS + max_delay_ms) / GRID_MS)
    stop = int(np.searchsorted(grid, grid[-1] - max_delay_ms * US_PER_MS, side="right"))
    allowed = funding_guarded(sessions[0], replace(terms[0], delay_ms=max_delay_ms))
    next_order = 0
    episodes = []
    attempts = missed = one_leg = mismatched = unresolved = funding = 0
    for index in np.flatnonzero((instructions != 0) & allowed):
        if index < next_order:
            continue
        side = int(instructions[index])
        touches = (legs[0].touch(index, side), legs[1].touch(index, -side))
        quantity = int(SIZE_USD / max(touches) / COMMON_LOT) * COMMON_LOT
        lots = tuple(round(quantity / leg.execution.lot_size) for leg in legs)
        if any(quantity * touch < leg.execution.min_order_usd for leg, touch in zip(legs, touches, strict=True)):
            continue
        attempts += 1
        next_order = index + ack_steps
        positions = []
        for leg, leg_side, requested in zip(legs, (side, -side), lots, strict=True):
            match = leg.execute(index, leg_side, requested, ENTRY_LIMIT_BPS)
            if match:
                positions.append(Position(leg, leg_side, match, [], match.lots))
        if not positions:
            missed += 1
            continue
        paired = len(positions) == 2 and isclose(positions[0].entry.quantity, positions[1].entry.quantity,
                                                  rel_tol=0, abs_tol=1e-9)
        one_leg += int(len(positions) == 1)
        mismatched += int(len(positions) == 2 and not paired)
        first_exit = index + max(ack_steps, held_steps) if paired else next_order
        for cursor in range(first_exit, stop, ack_steps):
            for position in positions:
                if position.remaining_lots:
                    position.close(cursor)
            if all(position.remaining_lots == 0 for position in positions):
                break
        if any(position.remaining_lots for position in positions):
            unresolved += 1
            break
        next_order = cursor + ack_steps
        funding += int(any(p.entry.time // US_PER_HOUR != p.exits[-1].time // US_PER_HOUR for p in positions))
        gross, fee, reserve, entry = map(sum, zip(*(p.pnl() for p in positions), strict=True))
        residual_ms, peak_quantity = residual_exposure(positions)
        episodes.append((paired, gross, fee, reserve, entry, residual_ms, peak_quantity))
    return episodes, {"attempts": attempts, "no_entry_fills": missed, "one_leg_entries": one_leg,
                      "unequal_two_leg_entries": mismatched, "unresolved_episodes": unresolved,
                      "funding_crossings": funding}


def summarize(runs):
    episodes = [episode for episode_list, _ in runs for episode in episode_list]
    counters = {key: sum(metrics[key] for _, metrics in runs) for key in runs[0][1]}
    paired = sum(e[0] for e in episodes)
    net = sum(e[1] - e[2] - e[3] for e in episodes)
    forced_net = sum(e[1] - e[2] - e[3] for e in episodes if not e[0])
    residual = [e[5] for e in episodes if e[5] > 0]
    return {
        **counters, "closed_episodes": len(episodes), "hedged_round_trips": paired,
        "forced_unwinds_closed": len(episodes) - paired, "forced_unwind_net_usd": forced_net,
        "hedged_net_usd": net - forced_net, "net_usd": net,
        "gross_before_fees_usd": sum(e[1] for e in episodes), "fees_usd": sum(e[2] for e in episodes),
        "slippage_reserve_usd": sum(e[3] for e in episodes), "entry_notional_both_venues_usd": sum(e[4] for e in episodes),
        "episodes_with_unhedged_exposure": len(residual),
        "unhedged_ms_p90_if_exposed": float(np.quantile(residual, 0.9)) if residual else None,
        "max_unhedged_share_equivalents": max((e[6] for e in episodes), default=0),
        "complete": counters["unresolved_episodes"] == 0 and counters["funding_crossings"] == 0,
    }


def run(root):
    results = {}
    audit = []
    for stage, days in SPLIT.items():
        outputs = {name: {} for name in SCENARIOS}
        for day in days:
            start, end = utc_window(day, ["15:02", "16:00"])
            for symbol in TARGETS:
                local, local_audit = read_crypto_hour(root, "lighter", day, symbol)
                source, source_audit = read_crypto_hour(root, "binance_futures", day, symbol + "USDT")
                sessions = tuple(make_session(day, symbol, quotes, start, end) for quotes in (local, source))
                instructions, support = directions(*sessions)
                for name, (lighter, binance) in SCENARIOS.items():
                    if symbol == "TSLA":
                        lighter = replace(lighter, lot_size=0.0001)
                    outputs[name][day, symbol] = replay_pair(sessions, instructions, (lighter, binance))
                audit.append({
                    "stage": stage, "day": day, "symbol": symbol, **support,
                    "lighter": {**local_audit, **quote_coverage(local, start, end)},
                    "binance": {**source_audit, **quote_coverage(source, start, end)},
                })
        results[stage] = {
            name: {"all": summarize(list(runs.values())), "by_day": {
                day: summarize([runs[day, symbol] for symbol in TARGETS]) for day in days
            }, "by_symbol": {
                symbol: summarize([runs[day, symbol] for day in days]) for symbol in TARGETS
            }} for name, runs in outputs.items()
        }
    return {
        "split": SPLIT, "decision_window_utc": ["15:02", "15:58"],
        "venues": ["lighter", "binance_futures"], "base_currency_parity_assumed": True,
        "execution": {name: [asdict(term) for term in terms] for name, terms in SCENARIOS.items()},
        "lighter_tsla_lot_size": 0.0001, "common_entry_lot": COMMON_LOT,
        "extra_edge_bps": EXTRA_EDGE_BPS, "max_observation_age_ms": MAX_AGE_MS,
        "spread_cap_bps_each_venue": SPREAD_CAP_BPS, "input_audit": audit, **results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
