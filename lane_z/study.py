"""Chronological validation, forecast baselines, and held-out execution stress tests."""

import argparse
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from .data import (
    INTERNAL_SESSIONS, SESSIONS, SYMBOLS, US_PER_MS, Book, fetch, quote_audit, read_quotes,
    sample_path, timestamp,
)
from .model import CROSS_FEATURES, GRID_MS, LOCAL_FEATURES, Ridge, features, local_features
from .replay import EXIT_BUFFER_MS, SPREAD_CAP_BPS, Execution, Session, markout, replay, sides, summarize


TARGETS = ("NVDA", "TSLA")
MARGIN_BPS = (0.0, 1.0, 2.0)
MIN_VALIDATION_TRADES = 25


@dataclass(frozen=True)
class Split:
    train: tuple[str, ...]
    validation: tuple[str, ...]
    test: tuple[str, ...]
    holds_ms: tuple[int, ...]

    @property
    def days(self) -> tuple[str, ...]:
        return self.train + self.validation + self.test


STUDIES = {
    "regular": Split(SESSIONS[:3], SESSIONS[3:4], SESSIONS[4:], (1_000, 5_000, 15_000)),
    "internal": Split(
        INTERNAL_SESSIONS[:3], INTERNAL_SESSIONS[3:4], INTERNAL_SESSIONS[4:], (5_000, 15_000, 60_000),
    ),
}


def load_sessions(root: Path, days: tuple[str, ...]):
    sessions = {}
    audit = []
    for day in days:
        end = timestamp(day, 15, 55)
        grid = np.arange(
            timestamp(day, 9, 35), end + EXIT_BUFFER_MS * US_PER_MS,
            GRID_MS * US_PER_MS,
        )
        target_books = {}
        observed = {}
        for symbol in SYMBOLS:
            path = sample_path(root, day, symbol)
            rows = read_quotes(path, symbol)
            audit.append(quote_audit(path, rows, day, symbol))
            observed[symbol] = local_features(Book.observed(rows), grid)
            if symbol in TARGETS:
                target_books[symbol] = Book.executed(rows)
        for symbol in TARGETS:
            matrix = features(
                observed[symbol], observed["XYZ100"], [observed["AAPL"], observed["MSFT"]],
            )
            sessions[(day, symbol)] = Session(day, symbol, grid, end, matrix, target_books[symbol])
    return sessions, audit


def predictions(sessions, days, hold, models):
    forecasts = {}
    for day in days:
        for symbol in TARGETS:
            session = sessions[day, symbol]
            scores = {
                variant: models[symbol, hold, variant].predict(getattr(session.features, variant))
                for variant in ("local", "cross")
            }
            scores["local"] = np.where(np.isfinite(scores["cross"]), scores["local"], np.nan)
            forecasts[day, symbol] = scores
    return forecasts


def policy(sessions, forecast, execution, variant, margin, minimum_forecast=None):
    outputs = {}
    for key, values in forecast.items():
        session = sessions[key]
        prediction = values[variant]
        direction = sides(session, prediction, margin)
        if minimum_forecast is not None:
            spread = session.features.local[:, 5]
            eligible = session.can_signal & (spread <= SPREAD_CAP_BPS) & (abs(prediction) > minimum_forecast)
            direction = np.where(eligible, np.sign(prediction), 0).astype(np.int8)
        outputs[key] = replay(session, direction, execution)
    return outputs


def breakdown(outputs: dict) -> dict:
    all_results = list(outputs.values())
    by_day = {}
    for day in dict.fromkeys(key[0] for key in outputs):
        by_day[day] = summarize([value for key, value in outputs.items() if key[0] == day])
    by_symbol = {}
    for symbol in TARGETS:
        by_symbol[symbol] = summarize([value for key, value in outputs.items() if key[1] == symbol])
    return {"all": summarize(all_results), "by_day": by_day, "by_symbol": by_symbol}


def forecast_metrics(sessions, forecasts, execution):
    diagnostics = {}
    for symbol in TARGETS:
        keys = [key for key in forecasts if key[1] == symbol]
        y = np.concatenate([markout(sessions[key], execution) for key in keys])
        pred = {
            variant: np.concatenate([forecasts[key][variant] for key in keys])
            for variant in ("local", "cross")
        }
        use = np.isfinite(y) & np.isfinite(pred["local"]) & np.isfinite(pred["cross"])
        actual = y[use]
        variants = {"flat": np.zeros(len(actual)), **{key: score[use] for key, score in pred.items()}}
        result = {}
        for variant, score in variants.items():
            if not len(score):
                result[variant] = {"samples": 0}
                continue
            nonzero = score != 0
            result[variant] = {
                "samples": len(score),
                "rmse_bps": float(np.sqrt(np.mean((score - actual) ** 2))),
                "correlation": float(np.corrcoef(score, actual)[0, 1])
                if score.std() > 0 and actual.std() > 0 else None,
                "signed_mid_markout_bps": float(np.mean(np.sign(score[nonzero]) * actual[nonzero]))
                if np.any(nonzero) else None,
            }
        diagnostics[symbol] = result
    return diagnostics


def training(sessions, split):
    models = {}
    weights = {}
    for symbol in TARGETS:
        train = [sessions[day, symbol] for day in split.train]
        weights[symbol] = {}
        for hold in split.holds_ms:
            labels = [markout(session, Execution(hold)) for session in train]
            weights[symbol][str(hold)] = {}
            # Give both variants the same rows so the ablation tests only features.
            for index, session in enumerate(train):
                labels[index][~np.isfinite(session.features.cross).all(axis=1)] = np.nan
            for variant, names in (("local", LOCAL_FEATURES), ("cross", LOCAL_FEATURES + CROSS_FEATURES)):
                model = Ridge.fit([getattr(session.features, variant) for session in train], labels)
                models[symbol, hold, variant] = model
                weights[symbol][str(hold)][variant] = model.serializable(names)
    return models, weights


def choose_policy(sessions, forecasts):
    candidates = []
    for hold in forecasts:
        for margin in MARGIN_BPS:
            outputs = policy(sessions, forecasts[hold], Execution(hold), "cross", margin)
            performance = summarize(list(outputs.values()))
            candidates.append({"hold_ms": hold, "margin_bps": margin, **performance})
    eligible = [
        item for item in candidates
        if item["complete"] and item["round_trips"] >= MIN_VALIDATION_TRADES and item["net_usd"] > 0
    ]
    if not eligible:
        return None, candidates
    chosen = max(eligible, key=lambda item: item["net_usd"])
    return {"hold_ms": chosen["hold_ms"], "margin_bps": chosen["margin_bps"]}, candidates


def run(root: Path, regime: str) -> dict:
    split = STUDIES[regime]
    development, dev_audit = load_sessions(root, split.train + split.validation)
    models, weights = training(development, split)
    validation = {
        hold: predictions(development, split.validation, hold, models)
        for hold in split.holds_ms
    }
    selected, candidates = choose_policy(development, validation)

    # Holdout is loaded only after the selection has been made.
    del development, validation
    holdout, test_audit = load_sessions(root, split.test)
    reference = selected or {"hold_ms": split.holds_ms[1], "margin_bps": 0.0}
    hold, margin = reference["hold_ms"], reference["margin_bps"]
    base = Execution(hold)
    scores = predictions(holdout, split.test, hold, models)
    scenarios = {
        "cross_base": base,
        "cross_900ms": replace(base, delay_ms=900),
        "cross_9bps_fee": replace(base, fee_bps=9),
        "cross_1bp_slippage_per_side": replace(base, slippage_bps=1),
        "cross_1000usd": replace(base, notional_usd=1_000),
    }
    performance = {
        name: breakdown(policy(holdout, scores, execution, "cross", margin))
        for name, execution in scenarios.items()
    }
    performance["local_base"] = breakdown(policy(holdout, scores, base, "local", margin))
    performance["cross_unguarded_diagnostic"] = breakdown(
        policy(holdout, scores, base, "cross", margin, minimum_forecast=0.25),
    )
    diagnostic = {}
    for horizon in split.holds_ms:
        forecast = predictions(holdout, split.test, horizon, models)
        diagnostic[str(horizon)] = forecast_metrics(holdout, forecast, Execution(horizon))
    return {
        "regime": regime,
        "splits": asdict(split),
        "selection": selected,
        "reference_policy": reference,
        "validation_candidates": candidates,
        "holdout": performance,
        "holdout_forecast_quality": diagnostic,
        "execution_assumptions": {name: asdict(execution) for name, execution in scenarios.items()},
        "model_parameters": weights,
        "data_audit": dev_audit + test_audit,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/tardis"))
    parser.add_argument("--regime", choices=STUDIES, default="regular")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.data, STUDIES[args.regime].days)
        return
    report = run(args.data, args.regime)
    output = args.output or Path(f"results/{args.regime}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
