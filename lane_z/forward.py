"""Replay new sessions with an unchanged model from a published research report."""

import argparse
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np

from .model import Ridge
from .replay import Execution
from .study import STUDIES, breakdown, feature_names, load_sessions, policy, predictions


def frozen_models(report, hold_ms):
    models = {}
    names = feature_names(report["regime"])
    for symbol, by_hold in report["model_parameters"].items():
        for variant, values in by_hold[str(hold_ms)].items():
            weights = values["standardized_weights_bps"]
            columns = names[variant]
            models[symbol, hold_ms, variant] = Ridge(
                np.array([values["training_feature_means"][name] for name in columns]),
                np.array([values["training_feature_std"][name] for name in columns]),
                np.array([weights[name] for name in columns]),
                values["intercept_bps"],
            )
    return models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("days", nargs="+", type=date.fromisoformat)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("results/binance.json"))
    parser.add_argument("--delay-ms", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    regime = report["regime"]
    days = tuple(sorted({day.isoformat() for day in args.days}))
    if days[0] <= max(STUDIES[regime].test):
        parser.error("forward dates must come after the source report's held-out dates")
    if args.delay_ms is not None and not 1 <= args.delay_ms <= 10_000:
        parser.error("--delay-ms must be between 1 and 10000")
    sessions, audit = load_sessions(args.data, days, regime)
    for item in audit:
        del item["url"]
    reference = report["reference_policy"]
    execution = Execution(**report["execution_assumptions"]["cross_base"])
    if args.delay_ms is not None:
        execution = replace(execution, delay_ms=args.delay_ms)
    scores = predictions(sessions, days, execution.hold_ms, frozen_models(report, execution.hold_ms))
    result = breakdown(policy(sessions, scores, execution, "cross", reference["margin_bps"]))
    payload = {
        "regime": regime,
        "days": days,
        "source_report": str(args.report),
        "policy_status": "selected" if report["selection"] else "unqualified_reference_diagnostic",
        "hold_ms": execution.hold_ms,
        "delay_ms": execution.delay_ms,
        "margin_bps": reference["margin_bps"],
        "performance": result,
        "data_audit": audit,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as file:
        json.dump(payload, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
