"""Replay new sessions with an unchanged model from a published research report."""

import argparse
import json
from dataclasses import replace
from datetime import date, datetime, time, timezone
from pathlib import Path

import numpy as np

from .model import Ridge
from .replay import FEE_BPS, SLIPPAGE_BPS, Execution, replay, sides
from .study import STUDIES, breakdown, feature_names, load_sessions, predictions


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


def utc_window(day: str, times: list[str]) -> tuple[int, int]:
    start, end = (
        int(datetime.combine(date.fromisoformat(day), time.fromisoformat(value), timezone.utc).timestamp())
        * 1_000_000
        for value in times
    )
    if start >= end:
        raise ValueError("UTC window must end later on the same date")
    return start, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("days", nargs="+", type=date.fromisoformat)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("results/binance.json"))
    parser.add_argument("--delay-ms", type=int)
    parser.add_argument("--fee-bps", type=float)
    parser.add_argument("--slippage-bps", type=float)
    parser.add_argument("--gate-on-execution-costs", action="store_true",
                        help="require the forecast to clear the fees and reserve charged by the replay")
    parser.add_argument("--utc-window", nargs=2, metavar=("START", "END"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    regime = report["regime"]
    days = tuple(sorted({day.isoformat() for day in args.days}))
    if days[0] <= max(STUDIES[regime].test):
        parser.error("forward dates must come after the source report's held-out dates")
    if args.delay_ms is not None and not 1 <= args.delay_ms <= 10_000:
        parser.error("--delay-ms must be between 1 and 10000")
    window = None
    if args.utc_window:
        if len(days) != 1:
            parser.error("--utc-window takes a single date")
        try:
            window = utc_window(days[0], args.utc_window)
        except ValueError as error:
            parser.error(str(error))
    sessions, audit = load_sessions(args.data, days, regime, window=window)
    for item in audit:
        del item["url"]
    reference = report["reference_policy"]
    execution = Execution(**report["execution_assumptions"]["cross_base"])
    overrides = {"delay_ms": args.delay_ms}
    for name in ("fee_bps", "slippage_bps"):
        value = getattr(args, name)
        if value is not None and (not np.isfinite(value) or value < 0):
            parser.error(f"--{name.replace('_', '-')} must be finite and non-negative")
        overrides[name] = value
    execution = replace(execution, **{name: value for name, value in overrides.items() if value is not None})
    scores = predictions(sessions, days, execution.hold_ms, frozen_models(report, execution.hold_ms))
    gate = {"fee_bps": execution.fee_bps, "slippage_bps": execution.slippage_bps} if args.gate_on_execution_costs else {
        "fee_bps": FEE_BPS, "slippage_bps": SLIPPAGE_BPS,
    }
    outputs = {}
    coverage = []
    for (day, symbol), session in sessions.items():
        values = scores[day, symbol]["cross"]
        direction = sides(session, values, reference["margin_bps"], **gate)
        outputs[day, symbol] = replay(session, direction, execution)
        eligible = session.can_signal
        coverage.append({
            "day": day,
            "symbol": symbol,
            "decision_rows": int(eligible.sum()),
            "finite_forecasts": int(np.count_nonzero(eligible & np.isfinite(values))),
            "forecasts_past_entry_gate": int(np.count_nonzero(direction)),
        })
    payload = {
        "regime": regime,
        "days": days,
        "source_report": str(args.report),
        "policy_status": "selected" if report["selection"] else "unqualified_reference_diagnostic",
        "hold_ms": execution.hold_ms,
        "delay_ms": execution.delay_ms,
        "fee_bps": execution.fee_bps,
        "slippage_bps": execution.slippage_bps,
        "entry_cost_gate": gate,
        "utc_window": args.utc_window,
        "coverage": coverage,
        "margin_bps": reference["margin_bps"],
        "performance": breakdown(outputs),
        "data_audit": audit,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as file:
        json.dump(payload, file, indent=2, allow_nan=False)
        file.write("\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
