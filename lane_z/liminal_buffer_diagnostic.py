"""Compare four predeclared limUSD request buffers using cached public data."""

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from .liminal_allocation import CAPITAL, DAILY_VALUE_MAX_DEVIATION, STRESS_INSTANT, settle
from .liminal_queued import (DAY, FULFILL, MICRO, MIN_COMPARABLE_MICRO, REQUEST, WEI,
                              read_tape, until_midnight, words)


FIRST_MARK = date(2026, 4, 23)
FIRST_ENTRY = date(2026, 4, 24)
LAST_MARK = date(2026, 11, 22)
TERM_DAYS = 63
LEADS = (3, 4, 5, 7)
EXTRA_HAIRCUT = Decimal("0.0025")
CASH = CAPITAL * (Decimal("1.04") ** (Decimal(TERM_DAYS) / 365) - 1)


def days(first, last):
    return (first + timedelta(days=i) for i in range((last - first).days + 1))


def extract_marks(raw):
    prices = {}
    for item in json.loads(raw):
        ms = int(item["timestamp"])
        day = datetime.fromtimestamp(ms / 1000, UTC).date()
        if not FIRST_MARK <= day <= LAST_MARK:
            continue
        pps, assets, supply = (Decimal(item[k]) for k in ("pps", "tvl", "totalSupply"))
        if (ms % 86_400_000 != 86_399_999 or day in prices or min(pps, assets, supply) <= 0
                or abs(pps - assets / supply) > DAILY_VALUE_MAX_DEVIATION):
            raise ValueError(f"Invalid UTC issuer mark on {day}")
        prices[day] = pps
    if prices.keys() != set(days(FIRST_MARK, LAST_MARK)):
        raise ValueError("Issuer marks do not cover the required uninterrupted daily grid")
    return prices


@dataclass
class Episode:
    request: int
    original: int
    pending: int
    redeemed: int = 0
    paid_micro: int = 0
    multiple_requests: bool = False
    fifo_paid: int | None = None
    lifo_paid: int | None = None


def episodes(events, verified_payouts):
    active, complete = {}, []
    for row in events:
        kind = row["topics"][0]
        if kind not in (REQUEST, FULFILL):
            continue
        owner = row["topics"][1]
        clock = int(row["blockTimestamp"], 16)
        if kind == REQUEST:
            (shares,) = words(row["data"], 1)
            if owner in active:
                active[owner].multiple_requests = True
                active[owner].pending += shares
            else:
                active[owner] = Episode(clock, shares, shares)
            continue
        payment, shares = words(row["data"], 2)
        receipt_key = (row["transactionHash"], int(row["logIndex"], 16))
        episode = active.get(owner)
        if episode is None or not 0 < shares <= episode.pending or verified_payouts[receipt_key] != payment:
            raise ValueError("Invalid owner escrow balance or receipt")
        episode.redeemed += shares
        episode.pending -= shares
        episode.paid_micro += payment
        if episode.fifo_paid is None and episode.redeemed >= episode.original:
            episode.fifo_paid = clock
        if episode.pending == 0:
            episode.lifo_paid = clock
            complete.append(active.pop(owner))
    return [*complete, *active.values()]


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).date()


def initially_full_size(episode, prices):
    previous_day = day_of(episode.request) - timedelta(days=1)
    prior_pps = prices.get(previous_day)
    return (prior_pps is not None and Decimal(episode.original) / WEI * prior_pps
            >= Decimal(MIN_COMPARABLE_MICRO) / MICRO)


def service_bounds(all_episodes, prices, cutoff):
    sized = [x for x in all_episodes if initially_full_size(x, prices)]
    # A common mature cohort supports direct timing comparisons across all leads.
    matured = [x for x in sized if x.request + max(LEADS) * DAY <= cutoff]
    recent = [x for x in sized if x.request + max(LEADS) * DAY > cutoff]
    summaries = {}
    for lead in LEADS:
        age = lead * DAY
        earliest = sum(x.fifo_paid is not None and x.fifo_paid <= x.request + age for x in matured)
        latest = sum(x.lifo_paid is not None and x.lifo_paid <= x.request + age for x in matured)
        summaries[lead] = {
            "mature_full_size_requests": len(matured),
            "fifo_complete_by_lead": earliest,
            "lifo_complete_by_lead": latest,
            "even_fifo_late_by_lead": len(matured) - earliest,
            "ambiguous_within_owner_priority": earliest - latest,
            "recent_full_size_requests_without_seven_days_followup": len(recent),
            "recent_full_size_episodes_open_at_final_cutoff": sum(x.lifo_paid is None for x in recent),
        }
    return summaries


def daily_windows(prices, all_episodes):
    # Completed single-request episodes have no within-owner ordering ambiguity.
    peers = [x for x in all_episodes if x.lifo_paid is not None and not x.multiple_requests
             and x.paid_micro >= MIN_COMPARABLE_MICRO and initially_full_size(x, prices)]
    windows = []
    for start in days(FIRST_ENTRY, LAST_MARK - timedelta(days=TERM_DAYS)):
        end = start + timedelta(days=TERM_DAYS)
        deadline = until_midnight(end)
        instant = settle(prices[start], prices[end], **STRESS_INSTANT) - CAPITAL
        variants = {}
        for lead in LEADS:
            request_day = end - timedelta(days=lead)
            request = until_midnight(request_day)
            low = min(prices[d] for d in days(request_day, end))
            queue_costs = {**STRESS_INSTANT, "fee": Decimal(0)}
            queued = settle(prices[start], low, **queue_costs) - CAPITAL
            tougher = settle(prices[start], low, **{**queue_costs,
                             "haircut": queue_costs["haircut"] + EXTRA_HAIRCUT}) - CAPITAL
            paid = [x for x in peers if request <= x.request < deadline and x.lifo_paid <= deadline]
            payout_days = {day_of(x.lifo_paid) for x in paid}
            lead_sample = [x for x in all_episodes if request <= x.request < deadline and initially_full_size(x, prices)]
            variants[lead] = {
                "request_utc_day": request_day.isoformat(),
                "queued_net_if_settled_usdc": str(queued),
                "extra_25bp_queued_net_if_settled_usdc": str(tougher),
                "queued_excess_if_settled_usdc": str(queued - CASH),
                "improvement_vs_same_entry_instant_usdc": str(queued - instant),
                "completed_full_size_peers": len(paid),
                "distinct_utc_payout_days": len(payout_days),
                "first_24h_requested_full_size_peers": sum(x.request < request + DAY for x in paid),
                "full_size_fresh_owner_episodes_between_request_and_end": len(lead_sample),
                "full_size_episodes_open_or_late_at_deadline": sum(
                    x.lifo_paid is None or x.lifo_paid > deadline for x in lead_sample
                ),
            }
        windows.append({"start": start.isoformat(), "end": end.isoformat(),
                        "same_entry_instant_net_usdc": str(instant), "variants": variants})
    return windows


def counts(windows):
    grouped = {lead: [w["variants"][lead] for w in windows] for lead in LEADS}
    result = {}
    for lead, rows in grouped.items():
        pnl = [Decimal(x["queued_net_if_settled_usdc"]) for x in rows]
        improvement = [Decimal(x["improvement_vs_same_entry_instant_usdc"]) for x in rows]
        payers = [q["completed_full_size_peers"] for q in rows]
        result[lead] = {
            "daily_endpoints": len(rows),
            "any_full_size_peer": sum(n >= 1 for n in payers),
            "at_least_two_utc_payout_days": sum(r["distinct_utc_payout_days"] >= 2 for r in rows),
            "first_24h_entry_peers": sum(r["first_24h_requested_full_size_peers"] > 0 for r in rows),
            "conditional_net_above_cash": sum(x > CASH for x in pnl),
            "conditional_net_above_instant": sum(x > 0 for x in improvement),
            "with_peer_and_above_cash": sum(n >= 1 and x > CASH for n, x in zip(payers, pnl)),
            "with_peer_and_above_cash_extra_25bp": sum(
                n >= 1 and Decimal(r["extra_25bp_queued_net_if_settled_usdc"]) > CASH
                for n, r in zip(payers, rows)
            ),
            "median_improvement_if_settled_usdc": str(median(improvement)),
            "minimum_improvement_if_settled_usdc": str(min(improvement)),
            "median_excess_to_cash_if_settled_usdc": str(median(pnl) - CASH),
            "minimum_excess_to_cash_if_settled_usdc": str(min(pnl) - CASH),
        }
    return result


def run(data, issuer_path):
    raw = issuer_path.read_bytes()
    prices = extract_marks(raw)
    events, verified_payouts, _, anchor, _, input_digests = read_tape(data)
    cutoff = until_midnight(LAST_MARK)
    chain_end = max(x["timestamp"] for x in anchor["blocks"].values())
    if chain_end != cutoff:
        raise ValueError("Queue corpus must end exactly at the last nominal mark deadline")
    all_episodes = episodes(events, verified_payouts)
    if sum(x.pending for x in all_episodes) != anchor["terminal_escrow_shares_wei"]:
        raise ValueError("Owner escrow does not match archived contract holdings")
    windows = daily_windows(prices, all_episodes)
    disjoint = [w for w in windows if (date.fromisoformat(w["start"]) - FIRST_ENTRY).days % TERM_DAYS == 0]
    return {
        "issuer_sha256": sha256(raw).hexdigest(), "cached_queue_input_sha256": input_digests,
        "cash_pnl_usdc": str(CASH), "issuer_endpoints": len(prices),
        "minimum_initial_peer_mark_usdc": str(Decimal(MIN_COMPARABLE_MICRO) / MICRO),
        "service_bounds": service_bounds(all_episodes, prices, cutoff),
        "rolling_diagnostics": counts(windows), "disjoint_diagnostics": disjoint,
        "all_daily_windows": windows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/agent-liminal-queued"))
    parser.add_argument("--marks", type=Path, default=Path("results/follow-up/liminal-issuer-marks.json"))
    parser.add_argument("--output", type=Path, default=Path("data/agent-liminal-buffer/report.json"))
    args = parser.parse_args()
    result = run(args.data, args.marks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Replayed {len(result['all_daily_windows'])} successive dates. Wrote {args.output}.")


if __name__ == "__main__":
    main()
