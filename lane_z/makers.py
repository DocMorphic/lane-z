"""Paper-test responses to displayed liquidity changes and aggressive flow."""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from .data import US_PER_MS, Book
from .forward import utc_window
from .model import GRID_MS, LOCAL_FEATURES, Ridge, local_features
from .replay import SPREAD_CAP_BPS, markout, replay
from .reversion import VENUES, funding_guarded, make_session, quote_coverage, read_crypto_hour, source_file
from .study import TARGETS, breakdown


SPLIT = {
    "train": ("2026-10-26", "2026-10-27", "2026-10-28"),
    "validation": ("2026-10-29", "2026-10-30"),
    "test": ("2026-11-02", "2026-11-03", "2026-11-06"),
}
BASE = replace(VENUES["lighter"], hold_ms=5_000, notional_usd=100)
SCENARIOS = {
    "base_500ms": BASE,
    "stress_900ms": replace(BASE, delay_ms=900),
    "slippage_1bp_per_side": replace(BASE, slippage_bps=1),
}
MARGIN_BPS = 0.25
MIN_ROUND_TRIPS = 25
SECOND = 1_000 // GRID_MS
NANOSECONDS_PER_MICROSECOND = 1_000
MAX_OBSERVATION_AGE_MS = 750
FLOW_FEATURES = ("aggressor_imbalance_1s", "aggressive_volume_to_depth_1s")
MAKER_FEATURES = (
    "bid_minus_ask_replenished_1s", "ask_minus_bid_removed_1s", "total_removed_1s",
    "spread_change_1s", "flow_times_replenishment_1s", "binance_ret_250ms",
    "binance_ret_1s", "binance_ofi_1s", "binance_minus_lighter_ret_1s",
)
FEATURES = {
    "local": LOCAL_FEATURES,
    "flow": LOCAL_FEATURES + FLOW_FEATURES,
    "book_and_leader": LOCAL_FEATURES + FLOW_FEATURES + MAKER_FEATURES,
}
POLICIES = {**{name: name for name in FEATURES}, "adaptive": "book_and_leader"}


def aggressor_flow(received_ns, size, buyer_maker, grid):
    """Sum signed and total base units received in the trailing second."""
    cutoff = grid * NANOSECONDS_PER_MICROSECOND
    start = np.searchsorted(received_ns, cutoff - 1_000_000_000, side="right")
    end = np.searchsorted(received_ns, cutoff, side="right")
    signed = np.r_[0.0, np.cumsum(np.where(buyer_maker, -size, size))]
    total = np.r_[0.0, np.cumsum(size)]
    return signed[end] - signed[start], total[end] - total[start]


def read_trades(root, day, symbol, grid):
    import pyarrow.parquet as parquet

    path = root / f"lighter_{day}_15_{symbol}_trades.parquet"
    url = f"https://api.cryptohftdata.com/download?file=lighter/{day}/15/{symbol}_trades.parquet"
    columns = ["symbol", "trade_id", "trade_time", "received_time", "quantity", "is_buyer_maker"]
    table = parquet.read_table(path, columns=columns).combine_chunks()
    symbols, ids, matched_ms, received_ns, size, buyer_maker = (
        table[name].to_numpy(zero_copy_only=False) for name in columns
    )
    size = size.astype(float)
    if not len(ids) or np.any(symbols != symbol) or np.any(~np.isfinite(size) | (size <= 0)):
        raise ValueError(f"{path} has missing or invalid trades")
    if np.any(np.diff(received_ns) < 0):
        raise ValueError(f"{path} is out of receipt order")
    on_clock = matched_ms * 1_000_000 <= received_ns
    valid = np.flatnonzero(on_clock)
    _, unique = np.unique(ids[valid], return_index=True)
    valid = valid[np.sort(unique)]
    flow = aggressor_flow(received_ns[valid], size[valid], buyer_maker[valid], grid)
    return flow, {
        **source_file(path, url), "trade_rows": len(ids), "usable_trade_rows": len(valid),
        "clock_rejections": int((~on_clock).sum()), "duplicate_ids": int(on_clock.sum()) - len(valid),
    }


def liquidity_features(session, signed, volume, leader):
    local = session.features.local
    quote = session.features.quote
    valid = np.isfinite(session.features.mid)
    depth = np.where(valid, quote["bid_size"] + quote["ask_size"], np.nan)
    spread = local[:, LOCAL_FEATURES.index("spread")]
    bid = quote["bid_size"]
    ask = quote["ask_size"]
    changes = np.column_stack([
        np.where(quote["bid"][1:] == quote["bid"][:-1], np.diff(bid), 0),
        np.where(quote["ask"][1:] == quote["ask"][:-1], np.diff(ask), 0),
    ])
    changes[~(valid[1:] & valid[:-1])] = np.nan
    additions = np.maximum(changes, 0)
    removals = np.maximum(-changes, 0)
    deltas = np.column_stack([
        additions[:, 0] - additions[:, 1], removals[:, 1] - removals[:, 0], removals.sum(axis=1),
    ])
    depth_changes = np.full((len(depth), deltas.shape[1]), np.nan)
    depth_changes[SECOND:] = np.lib.stride_tricks.sliding_window_view(
        deltas, SECOND, axis=0,
    ).sum(axis=2) / depth[SECOND:, None]
    spread_change = np.r_[np.full(SECOND, np.nan), spread[SECOND:] - spread[:-SECOND]]
    flow = np.divide(signed, volume, out=np.zeros_like(signed), where=volume > 0)
    with_flow = np.column_stack([local, flow, volume / depth])
    full = np.column_stack([
        with_flow, depth_changes, spread_change, flow * depth_changes[:, 0],
        leader[:, [0, 1, 4]], leader[:, 1] - local[:, 1],
    ])
    wide = (spread[SECOND:] > 2.5) & (spread[SECOND:] >= 2 * spread[:-SECOND])
    thin = depth[SECOND:] < 0.25 * depth[:-SECOND]
    withdrawal = np.r_[np.zeros(SECOND, dtype=bool), wide | thin]
    return {"local": local, "flow": with_flow, "book_and_leader": full}, withdrawal


def load(root, days):
    data = {}
    audit = []
    for day in days:
        start, end = utc_window(day, ["15:02", "16:00"])
        for symbol in TARGETS:
            quotes, local_audit = read_crypto_hour(root, "lighter", day, symbol)
            external, leader_audit = read_crypto_hour(root, "binance_futures", day, symbol + "USDT")
            session = make_session(day, symbol, quotes, start, end)
            leader, _, _ = local_features(Book.observed(external), session.grid)
            flow, trade_audit = read_trades(root, day, symbol, session.grid)
            matrices, withdrawal = liquidity_features(session, *flow, leader)
            common = np.isfinite(matrices["book_and_leader"]).all(axis=1)
            target = np.where(common, markout(session, BASE), np.nan)
            data[day, symbol] = session, matrices, withdrawal, target
            audit.append({
                "day": day, "symbol": symbol,
                "labeled_common_rows": int(np.isfinite(target).sum()),
                "common_live_rows": int((common & session.can_signal).sum()),
                "defensive_rows": int((common & session.can_signal & withdrawal).sum()),
                "lighter": {**local_audit, **quote_coverage(quotes, start, end)},
                "binance": {**leader_audit, **quote_coverage(external, start, end)},
                "trades": trade_audit,
            })
    return data, audit


def direction(session, prediction, withdrawal):
    quote = session.features.quote
    age = (session.grid - quote["venue"]) / US_PER_MS
    spread = session.features.local[:, LOCAL_FEATURES.index("spread")]
    hurdle = spread + 2 * (BASE.fee_bps + BASE.slippage_bps) + MARGIN_BPS
    eligible = funding_guarded(session, BASE) & (age <= MAX_OBSERVATION_AGE_MS)
    eligible &= (spread <= SPREAD_CAP_BPS) & (abs(prediction) > hurdle) & ~withdrawal
    return np.where(eligible, np.sign(prediction), 0).astype(np.int8)


def diagnostics(predictions, targets):
    pred = np.concatenate(predictions)
    target = np.concatenate(targets)
    valid = np.isfinite(target)
    if not valid.any():
        return {"observations": 0, "rmse_bps": None, "zero_forecast_rmse_bps": None, "correlation": None}
    pred, target = pred[valid], target[valid]
    return {
        "observations": len(target),
        "rmse_bps": float(np.sqrt(np.mean((target - pred) ** 2))),
        "zero_forecast_rmse_bps": float(np.sqrt(np.mean(target ** 2))),
        "correlation": float(np.corrcoef(pred, target)[0, 1]) if pred.std() and target.std() else None,
    }


def evaluate(data, models):
    outputs = {policy: {name: {} for name in SCENARIOS} for policy in POLICIES}
    forecasts = {variant: [] for variant in FEATURES}
    targets = []
    signals = []
    for (day, symbol), (session, matrices, withdrawal, target) in data.items():
        predictions = {variant: models[symbol, variant].predict(matrices[variant]) for variant in FEATURES}
        common = np.isfinite(predictions["book_and_leader"])
        for variant, pred in predictions.items():
            forecasts[variant].append(pred)
        targets.append(target)
        counts = {}
        for policy, variant in POLICIES.items():
            blocked = withdrawal if policy == "adaptive" else np.zeros_like(withdrawal)
            instructions = direction(session, np.where(common, predictions[variant], np.nan), blocked)
            counts[policy] = int(np.count_nonzero(instructions))
            for scenario, execution in SCENARIOS.items():
                funded = funding_guarded(session, execution)
                settings = replace(execution, lot_size=0.0001) if symbol == "TSLA" else execution
                outputs[policy][scenario][day, symbol] = replay(session, np.where(funded, instructions, 0), settings)
        signals.append({"day": day, "symbol": symbol, "qualifying_grid_rows": counts})
    return {
        "forecast": {name: diagnostics(pred, targets) for name, pred in forecasts.items()},
        "signals": signals,
        "performance": {
            policy: {name: breakdown(results) for name, results in scenarios.items()}
            for policy, scenarios in outputs.items()
        },
    }


def run(root):
    train, train_audit = load(root, SPLIT["train"])
    models = {}
    for symbol in TARGETS:
        samples = [train[day, symbol] for day in SPLIT["train"]]
        for name in FEATURES:
            models[symbol, name] = Ridge.fit([matrices[name] for _, matrices, _, _ in samples],
                                            [target for _, _, _, target in samples])
    stages = {}
    audit = {"train": train_audit}
    for stage in ("validation", "test"):
        data, audit[stage] = load(root, SPLIT[stage])
        stages[stage] = evaluate(data, models)

    validation = stages["validation"]["performance"]["adaptive"]["base_500ms"]["all"]
    test = stages["test"]["performance"]["adaptive"]
    qualification = {
        "validation_at_least_25_closed": validation["round_trips"] >= MIN_ROUND_TRIPS,
        "validation_positive_and_complete": validation["net_usd"] > 0 and validation["complete"],
        "test_at_least_25_closed": test["base_500ms"]["all"]["round_trips"] >= MIN_ROUND_TRIPS,
        "test_positive_at_least_two_dates": sum(
            metrics["net_usd"] > 0 for metrics in test["base_500ms"]["by_day"].values()
        ) >= 2,
        "all_test_scenarios_positive_and_complete": all(
            scenario["all"]["net_usd"] > 0 and scenario["all"]["complete"] for scenario in test.values()
        ),
    }
    return {
        "split": SPLIT, "decision_window_utc": ["15:02", "15:58"],
        "execution": {name: asdict(execution) for name, execution in SCENARIOS.items()},
        "lighter_tsla_lot_size": 0.0001, "required_margin_bps": MARGIN_BPS,
        "training_max_observation_age_ms": 2_000, "maximum_target_quote_age_at_signal_ms": MAX_OBSERVATION_AGE_MS,
        "fit": {symbol: {name: models[symbol, name].serializable(columns) for name, columns in FEATURES.items()}
                for symbol in TARGETS},
        "input_audit": audit, **stages, "qualification": qualification, "qualified": all(qualification.values()),
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
