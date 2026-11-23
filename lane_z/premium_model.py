"""Walk-forward daily return forecasts for the native Hyperliquid basis replay."""

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .perp_basis import ASSETS, BAR, DAY, DATES, HOUR, SCENARIOS, MarketData, epoch, replay_window, stamp

TRAINING_DAYS = 180
MINIMUM_TRAINING_DAYS = 60
MINIMUM_SPREAD = 0.0084
REGULARIZATION = 0.20


def features(data, decision):
    days = range(decision - 14 * DAY, decision + 1, DAY)
    rows = []
    btc_close = data.candles["BTC"][decision - BAR].close
    btc_old = data.candles["BTC"][decision - 14 * DAY - BAR].close
    for asset in ASSETS:
        candles = data.candles[asset]
        funding = data.funding[asset]
        closes = np.array([candles[day - BAR].close for day in days])
        log_closes = np.log(closes)
        daily = np.diff(log_closes)
        trailing = [log_closes[-1] - log_closes[-1 - lag] for lag in (1, 4, 14)]
        dolvol = [
            math.fsum(candles[t].close * candles[t].volume for t in range(day, day + DAY, BAR))
            for day in range(decision - 7 * DAY, decision, DAY)
        ]
        last_24 = range(decision - DAY, decision, HOUR)
        last_120 = range(decision - 5 * DAY, decision, HOUR)
        rows.append([
            *trailing,
            float(np.sqrt(np.mean(daily[-7:] ** 2))),
            math.log(dolvol[-1] / (math.fsum(dolvol) / 7)),
            math.fsum(funding[t][0] for t in last_24) / 24,
            math.fsum(funding[t][1] for t in last_24) / 24,
            math.fsum(funding[t][1] for t in last_120) / 120,
            daily[-1] * (btc_close < btc_old),
        ])
    values = np.array(rows, dtype=float)
    return values - values.mean(axis=0)


def training_target(data, decision):
    entry = decision + BAR
    exit_ = entry + DAY
    returns = []
    for asset in ASSETS:
        candles = data.candles[asset]
        funding = data.funding[asset]
        paid = math.fsum(
            funding[t][0] * candles[t - t % BAR].opening
            for t in range(entry + HOUR, exit_ + 1, HOUR)
        )
        opening = candles[entry].opening
        returns.append((candles[exit_].opening - opening - paid) / opening)
    values = np.array(returns)
    return np.clip(values - values.mean(), -0.15, 0.15)


def forecast(samples, decision_features):
    features_, target = zip(*samples, strict=True)
    combined = np.concatenate(features_)
    labels = np.concatenate(target)
    center = combined.mean(axis=0)
    scale = combined.std(axis=0, ddof=1)
    scale[scale < 1e-12] = 1.0
    design = (combined - center) / scale
    penalty = np.eye(design.shape[1]) * len(labels) * REGULARIZATION
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ (labels - labels.mean()))
    return labels.mean() + ((decision_features - center) / scale) @ weights


def run(data):
    first = epoch(DATES[0]) + 14 * DAY
    last = epoch(DATES[-1])
    dated_features = {day: features(data, day) for day in range(first, last, DAY)}
    targets = {
        day: training_target(data, day) for day in dated_features
        if day + DAY + BAR <= last
    }
    pairs = {}
    scored = {}
    windows = []
    dates = [epoch(day) for day in DATES[1:]]
    for start, end in zip(dates, dates[1:]):
        for day in range(start, end, DAY):
            eligible = range(max(first, day - TRAINING_DAYS * DAY), day - DAY - BAR, DAY)
            if len(eligible) < MINIMUM_TRAINING_DAYS:
                raise ValueError(f"Too little matured training history at {stamp(day)}")
            samples = [(dated_features[t], targets[t]) for t in eligible]
            prediction = forecast(samples, dated_features[day])
            long, short = int(np.argmax(prediction)), int(np.argmin(prediction))
            spread = float(prediction[long] - prediction[short])
            pairs[day] = (ASSETS[long], ASSETS[short]) if spread > MINIMUM_SPREAD else ()
            scored[stamp(day)] = {
                "predicted_spread_bps": spread * 10_000,
                "selected_pair": pairs[day],
                "most_recent_training_exit_utc": stamp(eligible[-1] + DAY + BAR),
                "training_dates": len(eligible),
            }
        windows.append({
            "start_utc": stamp(start), "end_utc": stamp(end),
            "signal_days": sum(bool(pairs[day]) for day in range(start, end, DAY)),
            "scenarios": {name: replay_window(data, start, end, scenario, pairs) for name, scenario in SCENARIOS.items()},
        })
    return {
        "historical_screen_pass": all(window["scenarios"]["stress"]["excess_vs_cash_usdc"] > 0 for window in windows),
        "window_results": windows,
        "decisions": scored,
        "input_files": data.audit,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(MarketData(args.data))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, allow_nan=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
