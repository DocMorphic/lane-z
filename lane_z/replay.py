"""Latency-aware paper IOC replay against sampled historical best quotes."""

from dataclasses import dataclass

import numpy as np

from .data import BPS, US_PER_HOUR, US_PER_MS, Book
from .model import GRID_MS, MAX_AGE_MS, Features, delta_bps


FEE_BPS = 0.9
SLIPPAGE_BPS = 0.25
SPREAD_CAP_BPS = 5.0
MIN_ORDER_USD = 10.0
LOT = 0.001
ENTRY_LIMIT_BPS = 2.0
EXIT_LIMIT_BPS = 10.0
FUNDING_GUARD_MS = 30_000
EXIT_BUFFER_MS = 120_000


@dataclass(frozen=True)
class Session:
    day: str
    symbol: str
    grid: np.ndarray
    signal_end: int
    features: Features
    venue: Book

    @property
    def can_signal(self) -> np.ndarray:
        offset = self.grid % US_PER_HOUR
        guard = FUNDING_GUARD_MS * US_PER_MS
        return (self.grid < self.signal_end) & (offset >= guard) & (offset < US_PER_HOUR - guard)


@dataclass(frozen=True)
class Execution:
    hold_ms: int
    delay_ms: int = 250
    fee_bps: float = FEE_BPS
    slippage_bps: float = SLIPPAGE_BPS
    notional_usd: float = 100.0
    max_venue_age_ms: int = MAX_AGE_MS
    lot_size: float = LOT
    min_order_usd: float = MIN_ORDER_USD
    fee_per_unit_usd: float = 0.0


@dataclass(frozen=True)
class Trade:
    decision: int
    entry_time: int
    exit_time: int
    side: int
    size: float
    entry_usd: float
    gross_usd: float
    midpoint_move_usd: float
    fees_usd: float
    slippage_usd: float

    @property
    def net_usd(self) -> float:
        return self.gross_usd - self.fees_usd - self.slippage_usd


@dataclass
class ReplayResult:
    trades: list[Trade]
    attempts: int = 0
    missed_entries: int = 0
    partial_entries: int = 0
    delayed_exits: int = 0
    unresolved_positions: int = 0
    funding_crossings: int = 0


def markout(session: Session, execution: Execution) -> np.ndarray:
    """Midpoint target after order arrival and after the scheduled exit arrives."""
    entry = session.grid + execution.delay_ms * US_PER_MS
    exit_time = entry + exit_delay_steps(execution) * GRID_MS * US_PER_MS
    quotes, valid = session.venue.asof(
        np.concatenate([entry, exit_time]), MAX_AGE_MS * US_PER_MS,
    )
    mid = (quotes["bid"] + quotes["ask"]) / 2
    before, after = np.split(np.where(valid, mid, np.nan), 2)
    return np.where(session.can_signal, delta_bps(after, before), np.nan)


def sides(session: Session, prediction: np.ndarray, margin_bps: float, *,
          fee_bps: float = FEE_BPS, slippage_bps: float = SLIPPAGE_BPS) -> np.ndarray:
    """Select direction at decision time using the displayed spread and cost reserve."""
    observed = session.features.quote
    with np.errstate(invalid="ignore", divide="ignore"):
        spread = BPS * (observed["ask"] - observed["bid"]) / session.features.mid
    reserve = spread + 2 * (fee_bps + slippage_bps) + margin_bps
    eligible = session.can_signal & (spread <= SPREAD_CAP_BPS) & (abs(prediction) > reserve)
    return np.where(eligible, np.sign(prediction), 0).astype(np.int8)


def price_limit(reference: float, side: int, cushion_bps: float) -> float:
    return reference * (1 + side * cushion_bps / BPS)


def fill(quote, valid: bool, side: int, limit: float, requested_lots: int, lot_size: float):
    if not valid:
        return 0, 0.0
    price, available = (quote["ask"], quote["ask_size"]) if side > 0 else (quote["bid"], quote["bid_size"])
    if side * (price - limit) > 0:
        return 0, 0.0
    displayed_lots = int(np.floor(available / lot_size + 1e-8))
    return min(requested_lots, displayed_lots), float(price)


def exit_delay_steps(execution: Execution) -> int:
    return (execution.hold_ms + execution.delay_ms + GRID_MS - 1) // GRID_MS


def replay(session: Session, direction: np.ndarray, execution: Execution) -> ReplayResult:
    result = ReplayResult([])
    grid = session.grid
    step = GRID_MS * US_PER_MS
    delay = execution.delay_ms * US_PER_MS
    arrival = grid + delay
    lot_size = execution.lot_size
    holding_steps = exit_delay_steps(execution)
    venue_quotes, venue_valid = session.venue.asof(arrival, execution.max_venue_age_ms * US_PER_MS)
    observed = session.features.quote
    available_after = int(grid[0]) - step

    for index in np.flatnonzero((direction != 0) & session.can_signal):
        decision = int(grid[index])
        if decision < available_after or not np.isfinite(session.features.mid[index]):
            continue
        side = int(direction[index])
        result.attempts += 1
        entry_time = int(arrival[index])
        available_after = entry_time + delay
        entry_reference = observed["ask"][index] if side > 0 else observed["bid"][index]
        requested = int(execution.notional_usd / entry_reference / lot_size)
        entry_limit = price_limit(entry_reference, side, ENTRY_LIMIT_BPS)
        if requested == 0 or requested * lot_size * entry_limit < execution.min_order_usd:
            result.missed_entries += 1
            continue
        lots, entry_px = fill(venue_quotes[index], venue_valid[index], side, entry_limit, requested, lot_size)
        if not lots:
            result.missed_entries += 1
            continue
        result.partial_entries += int(lots < requested)

        remains = lots
        exit_usd = 0.0
        exit_midpoint_usd = 0.0
        last_consumed = None
        first_exit = index + holding_steps
        retry_step = max(1, int(np.ceil(2 * execution.delay_ms / GRID_MS)))
        for cursor in range(first_exit, len(grid), retry_step):
            # Each retry is a new order using only the quote visible when sent.
            if arrival[cursor] >= grid[-1] or not np.isfinite(session.features.mid[cursor]):
                continue
            quote = venue_quotes[cursor]
            if quote["venue"] == last_consumed:
                continue
            reference = observed["bid"][cursor] if side > 0 else observed["ask"][cursor]
            limit = price_limit(reference, -side, EXIT_LIMIT_BPS)
            exited, exit_px = fill(quote, venue_valid[cursor], -side, limit, remains, lot_size)
            if exited:
                last_consumed = quote["venue"]
                remains -= exited
                exited_quantity = exited * lot_size
                exit_usd += exited_quantity * exit_px
                exit_midpoint_usd += exited_quantity * (quote["bid"] + quote["ask"]) / 2
            if not remains:
                break

        if remains:
            result.unresolved_positions += 1
            break

        exit_time = int(arrival[cursor])
        result.delayed_exits += int(cursor > first_exit)
        available_after = exit_time + delay
        if entry_time // US_PER_HOUR != exit_time // US_PER_HOUR:
            result.funding_crossings += 1

        quantity = lots * lot_size
        entry_usd = quantity * entry_px
        entry_midpoint_usd = quantity * (venue_quotes[index]["bid"] + venue_quotes[index]["ask"]) / 2
        turnover = entry_usd + exit_usd
        result.trades.append(Trade(
            decision, entry_time, exit_time, side, quantity, entry_usd,
            side * (exit_usd - entry_usd),
            side * (exit_midpoint_usd - entry_midpoint_usd),
            turnover * execution.fee_bps / BPS + 2 * quantity * execution.fee_per_unit_usd,
            turnover * execution.slippage_bps / BPS,
        ))
    return result


def summarize(reports: list[ReplayResult]) -> dict:
    trades = sorted(
        (trade for report in reports for trade in report.trades),
        key=lambda trade: trade.exit_time,
    )
    gross = sum(trade.gross_usd for trade in trades)
    midpoint_move = sum(trade.midpoint_move_usd for trade in trades)
    fees = sum(trade.fees_usd for trade in trades)
    slippage = sum(trade.slippage_usd for trade in trades)
    entry = sum(trade.entry_usd for trade in trades)
    net = sum(trade.net_usd for trade in trades)
    equity = np.r_[0.0, np.cumsum([trade.net_usd for trade in trades])]
    result = {
        field: sum(getattr(report, field) for report in reports)
        for field in (
            "attempts", "missed_entries", "partial_entries", "delayed_exits",
            "unresolved_positions", "funding_crossings",
        )
    }
    result.update({
        "round_trips": len(trades),
        "entry_notional_usd": entry,
        "midpoint_move_usd": midpoint_move,
        "spread_cost_usd": midpoint_move - gross,
        "gross_before_fees_usd": gross,
        "fees_usd": fees,
        "slippage_reserve_usd": slippage,
        "net_usd": net,
        "net_bps_per_entry_notional": BPS * net / entry if entry else None,
        "win_rate": float(np.mean([trade.net_usd > 0 for trade in trades])) if entry else None,
        "realized_drawdown_usd": float(np.max(np.maximum.accumulate(equity) - equity)),
        "hold_ms_p50": float(np.median([(t.exit_time - t.entry_time) / US_PER_MS for t in trades]))
        if trades else None,
        "complete": result["unresolved_positions"] == 0 and result["funding_crossings"] == 0,
    })
    return result
