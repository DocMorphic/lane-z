"""Replay a paper HLP allocation from Hyperliquid's aggregate vault marks."""

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

ENDPOINT = "https://api.hyperliquid.xyz/info"
ADDRESS = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
CAPITAL = 2000.0
DAY_MS = 86_400_000
STARTS = tuple(
    datetime(year, month, 1, tzinfo=UTC)
    for year in (2025, 2026)
    for month in (1, 4, 7, 10)
)
CUTOFF = datetime(2026, 11, 23, tzinfo=UTC)
CASES = {
    "base": {"side_bps": 10, "redemption_haircut_bps": 0, "fixed_usdc": 1},
    "stress": {"side_bps": 25, "redemption_haircut_bps": 50, "fixed_usdc": 1},
}
FLOW_WEIGHTS = {"flow_at_end": 0.0, "flow_at_midpoint": 0.5, "flow_at_start": 1.0}


def millis(instant):
    return round(instant.timestamp() * 1_000)


def iso(timestamp):
    return datetime.fromtimestamp(timestamp / 1_000, UTC).isoformat()


def fetch(path):
    payload = json.dumps({"type": "vaultDetails", "vaultAddress": ADDRESS}).encode()
    request = Request(ENDPOINT, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        raw = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def extract_snapshot(raw):
    payload = json.loads(raw)
    if payload["vaultAddress"].lower() != ADDRESS or payload["leaderCommission"] != 0:
        raise ValueError("This replay requires the HLP protocol vault with zero leader commission")
    history = dict(payload["portfolio"])["allTime"]
    rows = []
    for (value_time, value), (pnl_time, pnl) in zip(
        history["accountValueHistory"], history["pnlHistory"], strict=True
    ):
        if value_time != pnl_time:
            raise ValueError("Account value and P&L must share a timestamp")
        if millis(STARTS[0] - timedelta(days=7)) <= value_time < millis(CUTOFF):
            rows.append([value_time, value, pnl])
    return {
        "vault_address": ADDRESS,
        "leader_commission": payload["leaderCommission"],
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_snapshot_at": iso(history["accountValueHistory"][-1][0]),
        "marks": rows,
    }


def evaluate_path(marks, flow_weight, costs):
    fee = costs["side_bps"] / 10_000
    haircut = costs["redemption_haircut_bps"] / 10_000
    equity = CAPITAL * (1 - fee)
    peak = CAPITAL
    drawdown = 1 - equity / peak
    for (_, opening_value, opening_pnl), (timestamp, value, pnl) in zip(marks, marks[1:]):
        pnl_change = pnl - opening_pnl
        net_flow = value - opening_value - pnl_change
        denominator = opening_value + flow_weight * net_flow
        if denominator <= 0 or pnl_change <= -denominator:
            raise ValueError(f"Invalid capital denominator or interval return at {iso(timestamp)}")
        equity *= 1 + pnl_change / denominator
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
    closing_equity = equity * (1 - fee) * (1 - haircut) - costs["fixed_usdc"]
    return {
        "net_pnl_usdc": closing_equity - CAPITAL,
        "sampled_max_drawdown_pct": 100 * max(drawdown, 1 - closing_equity / peak),
    }


def replay(snapshot):
    if snapshot["vault_address"] != ADDRESS or snapshot["leader_commission"] != 0:
        raise ValueError("Unexpected vault or commission in archived snapshot")
    marks = [(int(timestamp), float(value), float(pnl)) for timestamp, value, pnl in snapshot["marks"]]
    if len(marks) < len(STARTS) + 1:
        raise ValueError("Insufficient vault marks for the fixed windows")
    if any(value <= 0 or not math.isfinite(value) or not math.isfinite(pnl) for _, value, pnl in marks):
        raise ValueError("Invalid vault mark")
    if any(second[0] <= first[0] for first, second in zip(marks, marks[1:])):
        raise ValueError("Vault marks must be in strict chronological order")
    if marks[-1][0] >= millis(CUTOFF) or marks[-1][0] < millis(CUTOFF) - DAY_MS:
        raise ValueError("Missing the final UTC day or including a later mark")

    boundaries = []
    for start in STARTS:
        target = millis(start)
        index = min(range(len(marks)), key=lambda i: (abs(marks[i][0] - target), marks[i][0]))
        if abs(marks[index][0] - target) > 7 * DAY_MS:
            raise ValueError(f"No vault mark within seven days of {start.date()}")
        boundaries.append(index)
    boundaries.append(len(marks) - 1)

    windows = []
    for start, left, right in zip(STARTS, boundaries[:-1], boundaries[1:], strict=True):
        window = marks[left:right + 1]
        duration = (window[-1][0] - window[0][0]) / DAY_MS
        if duration < 4:
            raise ValueError("Test period must match or exceed the four-day lock")
        cash_pnl = CAPITAL * (1.04 ** (duration / 365) - 1)
        cases = {}
        for case, costs in CASES.items():
            cases[case] = {}
            for timing, weight in FLOW_WEIGHTS.items():
                result = evaluate_path(window, weight, costs)
                result["excess_vs_cash_usdc"] = result["net_pnl_usdc"] - cash_pnl
                cases[case][timing] = result
        windows.append({
            "label": f"{start.year} Q{(start.month + 2) // 3}",
            "first_mark_utc": iso(window[0][0]),
            "last_mark_utc": iso(window[-1][0]),
            "days": duration,
            "marks": len(window),
            "cash_pnl_usdc": cash_pnl,
            "cases": cases,
        })
    screen_pass = all(
        result["excess_vs_cash_usdc"] > 0
        for window in windows if window["label"].startswith("2026")
        for result in window["cases"]["stress"].values()
    )
    return {
        "source_sha256": snapshot["source_sha256"],
        "source_snapshot_at": snapshot["source_snapshot_at"],
        "paper_capital_usdc": CAPITAL,
        "annual_cash_rate": 0.04,
        "cost_scenarios": CASES,
        "screen_pass": screen_pass,
        "windows": windows,
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--raw", type=Path, help="Raw vaultDetails response")
    inputs.add_argument("--snapshot", type=Path, help="Archived aggregate marks, without depositor data")
    parser.add_argument("--fetch", action="store_true", help="Retrieve a fresh vaultDetails response to --raw")
    parser.add_argument("--save-snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.fetch and args.raw is None:
        parser.error("--fetch requires --raw")
    if args.fetch:
        fetch(args.raw)
    snapshot = extract_snapshot(args.raw.read_bytes()) if args.raw else json.loads(args.snapshot.read_bytes())
    report = replay(snapshot)
    if args.save_snapshot:
        write_json(args.save_snapshot, snapshot)
    write_json(args.output, report)


if __name__ == "__main__":
    main()
