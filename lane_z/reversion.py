"""Compare a fixed long-only liquidity-shock reversal across four venues."""

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .archive import arca_quotes, depth_quotes
from .data import BPS, US_PER_HOUR, US_PER_MS, Book, read_quotes, sample_path, timestamp
from .forward import utc_window
from .model import GRID_MS, Features, delta_bps, local_features
from .replay import SPREAD_CAP_BPS, Execution, Session, replay
from .study import TARGETS, breakdown


HOLD_MS = 60_000
EXIT_BUFFER_MS = 120_000
WEEKDAY_DATES = (
    "2026-11-04", "2026-11-11", "2026-11-13",
    "2026-11-18", "2026-11-19", "2026-11-20",
)
ARCA_SESSIONS = {"2026-07-01": ("SPY", "IWM"), "2026-07-02": ("SPY",)}
BASE = Execution(HOLD_MS, delay_ms=500, slippage_bps=0.5, notional_usd=500)
VENUES = {
    "hyperliquid": BASE,
    "binance": replace(BASE, fee_bps=4, lot_size=0.01, min_order_usd=5),
    "lighter": replace(BASE, fee_bps=0),
    "arca": replace(BASE, fee_bps=0.5, fee_per_unit_usd=0.003, lot_size=1, min_order_usd=0, notional_usd=1_000),
}


def trailing_move_bps(mid: np.ndarray, elapsed_ms: int) -> np.ndarray:
    steps = elapsed_ms // GRID_MS
    move = np.full(len(mid), np.nan)
    if steps < len(mid):
        move[steps:] = delta_bps(mid[steps:], mid[:-steps])
    return move


def funding_guarded(session: Session, execution: Execution) -> np.ndarray:
    time_until_hour = US_PER_HOUR - session.grid % US_PER_HOUR
    # Include both simulated order delays, grid rounding and a retry allowance.
    reserve_ms = execution.hold_ms + 2 * execution.delay_ms + GRID_MS + 5_000
    return session.can_signal & (time_until_hour >= reserve_ms * US_PER_MS)


def onset(eligible: np.ndarray) -> np.ndarray:
    return (eligible & ~np.r_[False, eligible[:-1]]).astype(np.int8)


def reversal_direction(session: Session, execution: Execution) -> np.ndarray:
    features = session.features
    mid = features.mid
    shock = -trailing_move_bps(mid, 10_000)
    rebound = features.local[:, 1]
    imbalance = features.local[:, 3]
    spread = features.local[:, 5]
    with np.errstate(invalid="ignore", divide="ignore"):
        unit_fee_bps = BPS * execution.fee_per_unit_usd / mid
    hurdle = 2 * (spread + execution.fee_bps + execution.slippage_bps + unit_fee_bps)
    eligible = funding_guarded(session, execution)
    eligible &= (shock >= np.maximum(8, hurdle)) & (rebound >= 0.5) & (imbalance > 0)
    eligible &= spread <= SPREAD_CAP_BPS
    return onset(eligible)


def make_session(day, symbol, quotes, start, end):
    if len(quotes) < 2:
        raise ValueError(f"{day} {symbol} has too few quotes")
    grid = np.arange(start, end, GRID_MS * US_PER_MS)
    local, mid, observed = local_features(Book.observed(quotes), grid)
    return Session(day, symbol, grid, end - EXIT_BUFFER_MS * US_PER_MS,
                   Features(local, local, mid, observed), Book.executed(quotes))


def source_file(path: Path, url: str | None = None) -> dict:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    result = {"file": path.name, "bytes": path.stat().st_size, "sha256": digest}
    if url is not None:
        result["url"] = url
    return result


def quote_coverage(quotes, start, end) -> dict:
    core = quotes[(quotes["receive"] >= start) & (quotes["receive"] < end)]
    if len(core) < 2:
        raise ValueError("fewer than two quote messages in requested window")
    age = (core["receive"] - core["venue"]) / US_PER_MS
    return {
        "messages": len(core),
        "age_ms_p50": round(float(np.median(age)), 3),
        "age_ms_p90": round(float(np.quantile(age, 0.9)), 3),
        "spacing_ms_p50": round(float(np.median(np.diff(core["receive"])) / US_PER_MS), 3),
        "nonpositive_age_messages": int((age <= 0).sum()),
    }


def read_crypto_hour(root: Path, exchange: str, day: str, contract: str):
    path = root / f"{exchange}_{day}_15_{contract}.parquet"
    url = f"https://api.cryptohftdata.com/download?file={exchange}/{day}/15/{contract}_orderbook.parquet"
    quotes, diagnostics = depth_quotes(path)
    return quotes, {**source_file(path, url), **diagnostics}


def cases(root, venue, live_day=None, live_window=None, arca_feed_delay_ms=125):
    if venue == "arca":
        base = "https://ftp.nyse.com/Historical%20Data%20Samples/TAQ%20NYSE%20ARCA%20BBO/"
        for day, symbols in ARCA_SESSIONS.items():
            files = [root / f"EQY_US_TAQ_ARCA_BBO_{i}_{day.replace('-', '')}.gz" for i in (1, 2)]
            audit = [source_file(path, base + path.name) for path in files]
            quotes = arca_quotes(files, day, symbols, arca_feed_delay_ms)
            for symbol in symbols:
                yield day, symbol, quotes[symbol], timestamp(day, 9, 35), timestamp(day, 15, 57), audit
        return
    if venue == "hyperliquid":
        start, end = utc_window(live_day, live_window)
        for symbol in TARGETS:
            path = sample_path(root, live_day, symbol)
            yield live_day, symbol, read_quotes(path, symbol), start, end, [source_file(path)]
        return
    source = "binance_futures" if venue == "binance" else "lighter"
    for day in WEEKDAY_DATES:
        start, end = utc_window(day, ["15:02", "16:00"])
        for symbol in TARGETS:
            contract = f"{symbol}USDT" if venue == "binance" else symbol
            quotes, audit = read_crypto_hour(root, source, day, contract)
            yield day, symbol, quotes, start, end, [audit]


def run(root, venue, live_day=None, live_window=None, arca_feed_delay_ms=125):
    execution = VENUES[venue]
    scenarios = {"base_500ms": execution, "stress_900ms": replace(execution, delay_ms=900)}
    if venue == "binance":
        scenarios["standard_fee_5bps"] = replace(execution, fee_bps=5)
    outputs = {name: {} for name in scenarios}
    diagnostics = []
    for day, symbol, quotes, start, end, audit in cases(root, venue, live_day, live_window, arca_feed_delay_ms):
        session = make_session(day, symbol, quotes, start, end)
        key = day, symbol
        counts = {}
        for name, settings in scenarios.items():
            if venue == "lighter" and symbol == "TSLA":
                settings = replace(settings, lot_size=0.0001)
            direction = reversal_direction(session, settings)
            counts[name] = int(direction.sum())
            outputs[name][key] = replay(session, direction, settings)
        diagnostics.append({
            "day": day,
            "symbol": symbol,
            "data": audit,
            "start_utc": datetime.fromtimestamp(start / 1_000_000, timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(end / 1_000_000, timezone.utc).isoformat(),
            "usable_decision_rows": int((session.can_signal & np.isfinite(session.features.mid)).sum()),
            "funding_guarded_opportunities": counts,
            **quote_coverage(quotes, start, end),
        })
    return {
        "venue": venue,
        "arca_modeled_feed_delay_ms": arca_feed_delay_ms if venue == "arca" else None,
        "lighter_tsla_lot_size": 0.0001 if venue == "lighter" else None,
        "execution": {name: asdict(settings) for name, settings in scenarios.items()},
        "performance": {name: breakdown(results) for name, results in outputs.items()},
        "input_audit": diagnostics,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venue", choices=VENUES)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date")
    parser.add_argument("--utc-window", nargs=2, metavar=("START", "END"))
    parser.add_argument("--arca-feed-delay-ms", type=int, default=125)
    args = parser.parse_args()
    if args.venue == "hyperliquid" and (not args.date or not args.utc_window):
        parser.error("Hyperliquid live inputs require --date and --utc-window")
    if not 0 <= args.arca_feed_delay_ms <= 2_000:
        parser.error("--arca-feed-delay-ms must be between 0 and 2000")
    report = run(args.data, args.venue, args.date, args.utc_window, args.arca_feed_delay_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
