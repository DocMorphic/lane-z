"""Audit limUSD's standard queue and replay the seven-day paper challenger."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import Request, urlopen

from .liminal_allocation import CAPITAL, DATES, STRESS_INSTANT, metrics, settle
from .liminal_forward import (DEPOSIT, REDEEM, RPCS, SHARE_MANAGER, USDC_TOKEN, DataUnavailable,
                              header, rpc, word)
from .spot_carry import BAR, PublicArchive, epoch


DEPLOY = 33_253_579
FINAL = 51_987_233
IMPLEMENTATION = "0x3fac21fdd343486280b04447aede477f689881c7"
IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
ABI_URL = f"https://sourcify.dev/server/v2/contract/999/{IMPLEMENTATION}?fields=abi,deployment"
REQUEST = "0x27ac880482db68b319bd05f3cdb9150b50e87bd5ff96b0e727bb45733829a5b9"
FULFILL = "0x3cc0421d60663c2a79b9876a19116dbb3491b8982b6a4fc0f783bd9bdfcd3415"
UPGRADED = "0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPICS = (REQUEST, FULFILL, UPGRADED)
LEAD_DAYS = 7
MIN_COMPARABLE_MICRO = 2_050_000_000
MICRO = 10**6
WEI = 10**18
DAY = 86_400
CHUNK = 50_000
WORKERS = 5
OPENING_QUOTES = Path("results/follow-up/liminal-archive-quotes.json")


def ranges(final_block=FINAL):
    if final_block < FINAL:
        raise ValueError("A continuation must include the frozen historical cutoff")
    historical = [(start, min(start + CHUNK - 1, FINAL)) for start in range(DEPLOY, FINAL + 1, CHUNK)]
    continuation = [(start, min(start + CHUNK - 1, final_block))
                    for start in range(FINAL + 1, final_block + 1, CHUNK)]
    return historical + continuation


def deployment_name(final_block):
    return "deployment.json" if final_block == FINAL else f"deployment-{final_block}.json"


def log_path(root, start, end):
    return root / "logs" / f"{start}-{end}.json"


def cache_json(path, get):
    if path.is_file():
        return json.loads(path.read_bytes())
    payload = get()
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.stem + ".", suffix=".tmp", delete=False) as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    Path(handle.name).replace(path)
    return payload


class PublicReads:
    def __init__(self):
        self.lock = Lock()
        self.slot = 0.0

    def query(self, endpoint, method, params):
        for attempt in range(4):
            with self.lock:
                now = monotonic()
                allowed = max(now, self.slot)
                self.slot = allowed + 0.20
            if allowed > now:
                sleep(allowed - now)
            try:
                return rpc(endpoint, method, params)
            except DataUnavailable as error:
                if attempt == 3 or any(code in str(error) for code in ("HTTP Error 400", "HTTP Error 401")):
                    raise
                sleep(2 ** attempt)


def deployment_snapshot(final_block=FINAL):
    if final_block < FINAL:
        raise ValueError("A continuation must include the frozen historical cutoff")
    providers = []
    for endpoint in RPCS:
        if rpc(endpoint, "eth_chainId", []) != "0x3e7":
            raise DataUnavailable("Incorrect chain ID")
        before = rpc(endpoint, "eth_getCode", [REDEEM, hex(DEPLOY - 1)])
        anchors = {str(n): header(endpoint, n) for n in (DEPLOY, final_block)}
        versions = {str(n): rpc(endpoint, "eth_getStorageAt", [REDEEM, IMPLEMENTATION_SLOT, hex(n)])
                    for n in (DEPLOY, final_block)}
        share_balance = word(endpoint, anchors[str(final_block)], SHARE_MANAGER, "balanceOf", REDEEM)
        providers.append({"deployment_preceding_code_absent": before == "0x", "blocks": anchors,
                          "implementations": versions, "terminal_escrow_shares_wei": share_balance})
    if providers[0] != providers[1] or not providers[0]["deployment_preceding_code_absent"]:
        raise DataUnavailable("Canonical archive snapshots disagree on deployment or escrow")
    if any("0x" + slot[-40:] != IMPLEMENTATION for slot in providers[0]["implementations"].values()):
        raise DataUnavailable("The verified ABI is not the expected deployment implementation")
    return {"endpoints": list(RPCS), **providers[0]}


def official_abi():
    with urlopen(ABI_URL, timeout=25) as response:
        info = json.load(response)
    return info


def collect_logs(root, final_block=FINAL):
    spans = ranges(final_block)
    cache_json(root / "implementation-abi.json", official_abi)
    cache_json(root / deployment_name(final_block), lambda: deployment_snapshot(final_block))
    reader = PublicReads()

    def fetch(span):
        start, end = span
        filters = {"address": REDEEM, "fromBlock": hex(start), "toBlock": hex(end), "topics": [list(TOPICS)]}
        return cache_json(log_path(root, start, end), lambda: reader.query(RPCS[1], "eth_getLogs", [filters]))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        counts = [len(page) for page in pool.map(fetch, spans)]
    print(f"Cached {len(counts)} contiguous public log segments and {sum(counts)} records.", flush=True)


def collect_details(root, final_block=FINAL, *, independent_since=None):
    reader = PublicReads()
    events = [log for start, end in ranges(final_block) for log in json.loads(log_path(root, start, end).read_bytes())]
    transactions = sorted({log["transactionHash"] for log in events})

    def receipt(tx):
        return cache_json(root / "receipts" / f"{tx}.json",
                          lambda: reader.query(RPCS[0], "eth_getTransactionReceipt", [tx]))

    # Independent verification samples the whole hash-sorted set at fixed intervals.
    # Continuations keep every original receipt choice and sample only new transactions.
    sample_transactions = transactions if final_block == FINAL else sorted(
        {log["transactionHash"] for log in events if int(log["blockNumber"], 16) > FINAL}
    )
    stride = max(1, len(sample_transactions) // 32)
    independent_transactions = set(sample_transactions[::stride])
    if independent_since is not None:
        independent_transactions.update(log["transactionHash"] for log in events
                                        if int(log["blockTimestamp"], 16) >= independent_since)
    for tx in sorted(independent_transactions):
        receipt(tx)
    missing = [tx for tx in transactions if not (root / "receipts" / f"{tx}.json").is_file()
               and not (root / "receipts-public" / f"{tx}.json").is_file()]
    for i in range(0, len(missing), 10):
        for tx, payload in receipt_batch(missing[i:i + 10]).items():
            cache_json(root / "receipts-public" / f"{tx}.json", lambda: payload)
        sleep(0.40)
    independent = sum((root / "receipts" / f"{tx}.json").is_file() for tx in transactions)
    print(f"Cached {len(transactions)} receipts, including {independent} from an independent archive.", flush=True)


def receipt_batch(transactions):
    jobs = [{"jsonrpc": "2.0", "method": "eth_getTransactionReceipt", "params": [tx], "id": i}
            for i, tx in enumerate(transactions)]
    req = Request(RPCS[1], data=json.dumps(jobs).encode(),
                  headers={"Content-Type": "application/json", "User-Agent": "Lane-Z paper research"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=25) as response:
                batch = json.load(response)
            by_id = {row["id"]: row["result"] for row in batch if row.get("result") is not None}
            if by_id.keys() != set(range(len(transactions))):
                raise DataUnavailable("Missing one or more transaction receipts")
            return {tx: by_id[i] for i, tx in enumerate(transactions)}
        except (URLError, TimeoutError, DataUnavailable) as error:
            if attempt == 3:
                raise DataUnavailable(f"PublicNode receipt batch failed: {error}") from error
            sleep(2 ** (attempt + 1))


def check_abi(payload):
    if (payload["chainId"] != "999" or payload["address"].lower() != IMPLEMENTATION
            or payload.get("runtimeMatch") not in {"match", "exact_match"}):
        raise ValueError("The official Sourcify implementation is unverified")
    events = {row["name"]: [(arg["type"], arg["indexed"]) for arg in row["inputs"]]
              for row in payload["abi"] if row["type"] == "event"}
    functions = {row["name"]: [arg["type"] for arg in row["inputs"]]
                 for row in payload["abi"] if row["type"] == "function"}
    if (events["RedeemRequested"] != [("address", True), ("address", True), ("uint256", False)]
            or events["RedeemFulfilled"] != events["RedeemRequested"] + [("uint256", False)]
            or functions["requestRedeem"] != ["uint256", "address", "address", "address"]
            or functions["fulfillRedeems"] != ["address[]", "uint256[]"]
            or any("cancel" in name.lower() for name in functions)):
        raise ValueError("The verified contract schema differs from the frozen queue protocol")


def read_tape(root, final_block=FINAL):
    spans = ranges(final_block)
    digests = {name: sha256() for name in ("log_pages", "receipts", "abi_and_headers")}

    def read(name, category):
        raw = (root / name).read_bytes()
        digests[category].update(sha256(raw).digest())
        return json.loads(raw)

    check_abi(read("implementation-abi.json", "abi_and_headers"))
    deployment = read(deployment_name(final_block), "abi_and_headers")
    anchors = (deployment["blocks"][str(DEPLOY)], deployment["blocks"][str(final_block)])
    if (not deployment["deployment_preceding_code_absent"] or anchors[0]["number"] != DEPLOY
            or anchors[1]["number"] != final_block or anchors[0]["timestamp"] >= anchors[1]["timestamp"]
            or any(len(block["hash"]) != 66 for block in anchors)
            or any("0x" + deployment["implementations"][str(h)][-40:] != IMPLEMENTATION
                   for h in (DEPLOY, final_block))):
        raise ValueError("Invalid cached canonical deployment or continuation anchor")
    events = []
    for start, end in spans:
        file = log_path(root, start, end).relative_to(root)
        page = read(file, "log_pages")
        for log in page:
            if (not start <= int(log["blockNumber"], 16) <= end or log["removed"]
                    or log["address"].lower() != REDEEM or log["topics"][0] not in TOPICS):
                raise ValueError("The public log page included an unrelated or removed event")
            if not anchors[0]["timestamp"] <= int(log["blockTimestamp"], 16) <= anchors[1]["timestamp"]:
                raise ValueError("A public log timestamp fell outside the canonical scan")
            if any(int(log["blockNumber"], 16) == anchor["number"] and log["blockHash"] != anchor["hash"]
                   for anchor in anchors):
                raise ValueError("A public log block hash disagreed with a canonical scan anchor")
            events.append(log)
    events.sort(key=chain_order)
    if len(events) != len({chain_order(r) for r in events}):
        raise ValueError("An on-chain log was duplicated")
    upgrades = [log for log in events if log["topics"][0] == UPGRADED]
    if (len(upgrades) != 1 or int(upgrades[0]["blockNumber"], 16) != DEPLOY
            or "0x" + upgrades[0]["topics"][1][-40:] != IMPLEMENTATION):
        raise ValueError("The address changed implementations during the study")
    by_transaction = defaultdict(list)
    for log in events:
        by_transaction[log["transactionHash"]].append(log)
    receipts = {}
    independent_receipts = 0
    for tx in sorted(by_transaction):
        direct = Path("receipts") / f"{tx}.json"
        independent = (root / direct).is_file()
        source = direct if independent else Path("receipts-public") / f"{tx}.json"
        receipts[tx] = read(source, "receipts")
        independent_receipts += independent
    verified_payouts = {}
    gas = {}
    for tx, receipt in receipts.items():
        if receipt["transactionHash"] != tx or int(receipt["status"], 16) != 1:
            raise ValueError("A queue event did not match a successful canonical receipt")
        transfers = [(i, log["topics"][2], int(log["data"], 16))
                     for i, log in enumerate(receipt["logs"]) if log["address"].lower() == USDC_TOKEN
                     and len(log["topics"]) == 3 and log["topics"][0] == TRANSFER]
        # The two providers sometimes number receipt logs differently. Compare their
        # byte-identical payloads in execution order, and retain the source log index.
        queue_receipt = [(i, log) for i, log in enumerate(receipt["logs"]) if log["address"].lower() == REDEEM
                         and log["topics"] and log["topics"][0] in TOPICS]
        indexed = by_transaction[tx]
        if len(queue_receipt) != len(indexed):
            raise ValueError("The receipt and block-range query contain different queue event counts")
        for (ordinal, log), expected in zip(queue_receipt, indexed):
            if any(expected[k] != log[k] for k in ("address", "blockHash", "blockNumber",
                                                   "blockTimestamp", "data", "topics")):
                raise ValueError("The receipt disagreed on an indexed queue log or its block timestamp")
            if log["topics"][0] != FULFILL:
                continue
            assets, _ = words(log["data"], 2)
            matches = [(i, to, amount) for i, to, amount in transfers
                       if i < ordinal and to == log["topics"][2] and amount == assets]
            if not matches:
                raise ValueError("A standard fulfillment is missing its native USDC transfer")
            transfers.remove(matches[-1])
            verified_payouts[tx, int(expected["logIndex"], 16)] = assets
        gas[tx] = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16)
    evidence = {"transaction_receipts": len(receipts), "independent_archive_receipts": independent_receipts,
                "log_segments": len(spans)}
    return events, verified_payouts, gas, deployment, evidence, {k: digest.hexdigest() for k, digest in digests.items()}


def chain_order(log):
    return tuple(int(log[name], 16) for name in ("blockNumber", "transactionIndex", "logIndex"))


def words(data, expected):
    if not data.startswith("0x") or len(data) != 2 + 64 * expected:
        raise ValueError("Invalid verified ABI payload")
    return [int(data[i:i + 64], 16) for i in range(2, len(data), 64)]


@dataclass
class QueueEpisode:
    requested_at: int
    remaining_shares: int
    receiver: str
    requests: int = 1
    installments: int = 0
    assets_micro: int = 0
    settled_at: int | None = None


def reconstruct(events, verified_payouts):
    active, settled, request_times = {}, [], {}
    counts = Counter()
    for log in events:
        topic = log["topics"][0]
        if topic == UPGRADED:
            continue
        if len(log["topics"]) != 3:
            raise ValueError("Invalid sender and receiver topics")
        owner, receiver = log["topics"][1:]
        timestamp = int(log["blockTimestamp"], 16)
        transaction = log["transactionHash"]
        if topic == REQUEST:
            (shares,) = words(log["data"], 1)
            if shares <= 0:
                raise ValueError("Invalid standard redemption share amount")
            if owner in active:
                active[owner].remaining_shares += shares
                active[owner].receiver = receiver
                active[owner].requests += 1
            else:
                active[owner] = QueueEpisode(timestamp, shares, receiver)
            request_times[transaction] = timestamp
            counts["request_events"] += 1
            continue
        assets, shares = words(log["data"], 2)
        episode = active.get(owner)
        if (episode is None or episode.receiver != receiver or not 0 < shares <= episode.remaining_shares
                or verified_payouts[transaction, int(log["logIndex"], 16)] != assets):
            raise ValueError("A fulfilled event did not match a reconstructed standard escrow")
        episode.remaining_shares -= shares
        episode.assets_micro += assets
        episode.installments += 1
        counts["fulfillment_events"] += 1
        counts["paid_usdc_micro"] += assets
        if not episode.remaining_shares:
            episode.settled_at = timestamp
            settled.append(active.pop(owner))
    return list(active.values()), settled, request_times, dict(counts)


def percentile(values, quantile):
    if not values:
        return None
    return sorted(values)[math.ceil(len(values) * quantile) - 1]


def until_midnight(day):
    return int(datetime.combine(day, time(23, 59, 59), UTC).timestamp())


def from_micro(value):
    return str(Decimal(value) / MICRO)


def lowest_mark(marks, request_day):
    return min((marks[day][0], day)
               for day in (request_day + timedelta(days=n) for n in range(LEAD_DAYS + 1)))


def boundary_before(target, upper_block):
    right = upper_block
    left = max(DEPLOY, right - 300_000)
    while header(RPCS[0], left)["timestamp"] > target and left > DEPLOY:
        right, left = left, max(DEPLOY, left - 300_000)
    while right - left > 1:
        mid = (left + right) // 2
        if header(RPCS[0], mid)["timestamp"] <= target:
            left = mid
        else:
            right = mid
    pairs = [[header(endpoint, n) for n in (left, right)] for endpoint in RPCS]
    if pairs[0] != pairs[1] or not pairs[0][0]["timestamp"] <= target < pairs[0][1]["timestamp"]:
        raise DataUnavailable("The two public archives disagreed on the sampled queue mark boundary")
    return pairs[0][0]


def verify_exact_mark_quotes(marks_raw, historical_quotes_raw):
    marks, _ = metrics(marks_raw)
    published = json.loads(historical_quotes_raw)
    entry_amount = int((CAPITAL - STRESS_INSTANT["gas"]) * (1 - STRESS_INSTANT["entry_reserve"]) * MICRO)
    windows = []
    for start, end in zip(DATES, DATES[1:]):
        original = published[f"{start.isoformat()}/{end.isoformat()}"]
        _, low_day = lowest_mark(marks, end - timedelta(days=LEAD_DAYS))
        snapshot = boundary_before(until_midnight(low_day), original["exit_block"]["number"] + 1)
        opening = original["entry_block"]
        held = [word(e, opening, DEPOSIT, "previewDeposit", entry_amount) for e in RPCS]
        original_shares = int(original["scenarios"]["stress_instant"]["actual_onchain_deposit_shares"]["shares_wei"])
        if held != [original_shares] * 2:
            raise DataUnavailable("The exact originally minted queue model units did not independently match")
        gross = [word(e, snapshot, REDEEM, "convertToAssets", held[0]) for e in RPCS]
        if gross[0] != gross[1]:
            raise DataUnavailable("The archives returned different no-fee share valuations")
        payout = Decimal(gross[0]) / MICRO * (1 - STRESS_INSTANT["haircut"])
        windows.append({
            "vintage_start_utc_day": start.isoformat(), "vintage_end_utc_day": end.isoformat(),
            "lowest_sample_utc_day": low_day.isoformat(), "lowest_sample_canonical_block": snapshot,
            "deposit_micro_usdc": str(entry_amount), "held_exact_shares_wei": str(held[0]),
            "quoted_no_queue_fee_micro_usdc": str(gross[0]),
            "exact_queued_mark_pnl_with_haircut_usdc": str(payout - CAPITAL),
        })
    return {"marks_sha256": sha256(marks_raw).hexdigest(),
            "original_quotes_sha256": sha256(historical_quotes_raw).hexdigest(),
            "windows": windows}


def gas_observations(values, request_times, hype_path):
    raw = hype_path.read_bytes()
    candles = {}
    period = 4 * 3600
    for row in json.loads(raw):
        timestamp = int(row["t"]) // 1000
        opening_close = Decimal(row["c"])
        if row["s"] != "@107" or row["i"] != "4h" or timestamp % period or opening_close <= 0:
            raise ValueError("A gas price proxy is not native HYPE/USDC")
        if timestamp in candles:
            raise ValueError("HYPE gas price data contain duplicate bars")
        candles[timestamp] = opening_close
    fees_hype = [Decimal(values[tx]) / WEI for tx in request_times]
    fees_usdc = [Decimal(values[tx]) / WEI * candles[time_ // period * period - period]
                 for tx, time_ in request_times.items()]
    return {
        "observed_unique_confirmed_request_transactions": len(fees_hype),
        "median_request_gas_hype": str(percentile(fees_hype, 0.5)),
        "p95_request_gas_hype": str(percentile(fees_hype, 0.95)),
        "peak_request_gas_hype": str(max(fees_hype, default=0)),
        "median_request_gas_usdc_at_prior_hype_mark": str(percentile(fees_usdc, 0.5)),
        "p95_request_gas_usdc_at_prior_hype_mark": str(percentile(fees_usdc, 0.95)),
        "peak_request_gas_usdc_at_prior_hype_mark": str(max(fees_usdc, default=0)),
        "hype_candle_input_sha256": sha256(raw).hexdigest(),
    }


def queue_stats(active, settled, counts, horizon, reference_escrow):
    clean = [x for x in settled if x.requests == 1]
    delays = [x.settled_at - x.requested_at for x in clean]
    comparable = [episode for episode in clean if episode.assets_micro >= MIN_COMPARABLE_MICRO]
    large_delays = [episode.settled_at - episode.requested_at for episode in comparable]
    censored = [horizon - x.requested_at for x in active if x.requests == 1]
    standard_escrow = sum(x.remaining_shares for x in active)
    return {
        **counts,
        "settled_episodes": len(settled), "settled_single_request_episodes": len(clean),
        "settled_multiple_request_episodes": len(settled) - len(clean),
        "settled_single_request_episodes_with_multiple_installments": sum(x.installments > 1 for x in clean),
        "settled_single_request_p50_hours": percentile(delays, 0.5) / 3600 if delays else None,
        "settled_single_request_p90_hours": percentile(delays, 0.9) / 3600 if delays else None,
        "settled_single_request_p95_hours": percentile(delays, 0.95) / 3600 if delays else None,
        "settled_single_request_max_hours": max(delays) / 3600 if delays else None,
        "settled_single_request_over_3_days": sum(x > 3 * DAY for x in delays),
        "settled_single_request_over_7_days": sum(x > LEAD_DAYS * DAY for x in delays),
        "settled_comparable_single_request_count": len(large_delays),
        "settled_comparable_single_request_p95_hours": percentile(large_delays, 0.95) / 3600 if large_delays else None,
        "settled_comparable_single_request_over_7_days": sum(x > LEAD_DAYS * DAY for x in large_delays),
        "open_episodes_at_cutoff": len(active), "open_single_request_episodes": len(censored),
        "open_single_request_episodes_age_at_least_3_days": sum(x >= 3 * DAY for x in censored),
        "open_single_request_episodes_age_at_least_7_days": sum(x >= LEAD_DAYS * DAY for x in censored),
        "escrowed_shares_in_standard_ledger_wei": str(standard_escrow),
        "escrowed_shares_onchain_wei": str(reference_escrow),
        "standard_ledger_matches_escrow": standard_escrow == reference_escrow,
    }


def window_scores(marks, events, receipts):
    windows = []
    for start, end in zip(DATES, DATES[1:]):
        request_day = end - timedelta(days=LEAD_DAYS)
        request_time, cutoff = until_midnight(request_day), until_midnight(end)
        active, settled, _, _ = reconstruct(
            (log for log in events if int(log["blockTimestamp"], 16) <= cutoff), receipts
        )
        observations = [x for x in (*settled, *active) if x.requests == 1
                        and request_time <= x.requested_at < cutoff]
        timely = [x for x in observations if x.settled_at is not None and x.settled_at <= cutoff]
        comparable = [x for x in timely if x.assets_micro >= MIN_COMPARABLE_MICRO]
        historical_max_payout = max((x.assets_micro for x in timely), default=0)
        low, _ = lowest_mark(marks, request_day)
        cash = CAPITAL * (Decimal("1.04") ** (Decimal((end - start).days) / 365) - 1)
        instant = settle(marks[start][0], marks[end][0], **STRESS_INSTANT) - CAPITAL
        payout = settle(marks[start][0], low, **{**STRESS_INSTANT, "fee": Decimal(0)}) - CAPITAL
        tail = settle(marks[start][0], low, **{**STRESS_INSTANT, "fee": Decimal(0),
                                              "haircut": Decimal("0.011")}) - CAPITAL
        windows.append({
            "start_utc_day": start.isoformat(), "request_utc_day": request_day.isoformat(),
            "deadline_utc_day": end.isoformat(), "cash_4pct_net_usdc": str(cash),
            "instant_stress_net_usdc": str(instant), "queued_stress_if_settled_net_usdc": str(payout),
            "queued_stress_gain_vs_instant_usdc": str(payout - instant),
            "queued_stress_excess_cash_if_settled_usdc": str(payout - cash),
            "queued_extra_100bp_tail_if_settled_net_usdc": str(tail),
            "minimum_modeled_pps_in_queue_usdc": str(low),
            "observed_unambiguous_requests_during_service_window": len(observations),
            "observed_full_payouts_by_deadline": len(timely),
            "observed_full_comparable_payouts_by_deadline": len(comparable),
            "observed_largest_single_full_payout_by_deadline_usdc": from_micro(historical_max_payout),
            "observed_total_unambiguous_payout_by_deadline_usdc": from_micro(sum(x.assets_micro for x in timely)),
            "right_censored_or_late_for_deadline": len(observations) - len(timely),
            "historical_capacity_gate": bool(comparable),
            "passes_historical_screen": bool(comparable) and payout > cash,
        })
    return windows


def report(root, mark_path, hype_path):
    events, receipts, gas, deployment, evidence, digests = read_tape(root)
    active, settled, request_times, counts = reconstruct(events, receipts)
    scan_first = deployment["blocks"][str(DEPLOY)]
    scan_last = deployment["blocks"][str(FINAL)]
    if not all(scan_first["timestamp"] <= int(log["blockTimestamp"], 16) <= scan_last["timestamp"]
               for log in events):
        raise ValueError("A reconstructed event falls outside the canonical scan horizon")
    raw = mark_path.read_bytes()
    marks, _ = metrics(raw)
    windows = window_scores(marks, events, receipts)
    exact = json.loads((root / "exact-mark-quotes.json").read_bytes())
    if exact["marks_sha256"] != sha256(raw).hexdigest() or len(exact["windows"]) != len(windows):
        raise ValueError("The exact quote audit uses different issuer marks")
    for replay_window, exact_window in zip(windows, exact["windows"]):
        if (replay_window["start_utc_day"], replay_window["deadline_utc_day"]) != (
            exact_window["vintage_start_utc_day"], exact_window["vintage_end_utc_day"]
        ):
            raise ValueError("The exact quote audit uses different paper vintages")
        exact_pnl = Decimal(exact_window["exact_queued_mark_pnl_with_haircut_usdc"])
        issuer_pnl = Decimal(replay_window["queued_stress_if_settled_net_usdc"])
        replay_window["exact_quote_minus_issuer_net_usdc"] = str(exact_pnl - issuer_pnl)
        replay_window["conservative_queued_net_if_settled_usdc"] = str(min(exact_pnl, issuer_pnl))
        replay_window["passes_historical_screen"] = (
            replay_window["passes_historical_screen"] and exact_pnl > Decimal(replay_window["cash_4pct_net_usdc"])
        )
    state = queue_stats(active, settled, counts, scan_last["timestamp"], deployment["terminal_escrow_shares_wei"])
    return {
        "protocol": "limusd-seven-day-standard-redemption-v1",
        "contract": REDEEM, "implementation": IMPLEMENTATION,
        "canonical_scan_from": scan_first, "canonical_scan_through": scan_last,
        "archive_providers": deployment["endpoints"], "started_usdc": str(CAPITAL),
        "evidence_counts": evidence,
        "minimum_comparable_payout_usdc": from_micro(MIN_COMPARABLE_MICRO),
        "total_gas_reserved_per_vintage_usdc": str(STRESS_INSTANT["gas"]),
        "queue_lead_days": LEAD_DAYS, "holders_or_transaction_details_published": False,
        "raw_input_batch_sha256": digests, "issuer_mark_sha256": sha256(raw).hexdigest(),
        "exact_quote_check": exact,
        "queue_statistics": state, "request_gas": gas_observations(gas, request_times, hype_path),
        "windows": windows, "passes_historical_screen": state["standard_ledger_matches_escrow"] and all(
            row["passes_historical_screen"] for row in windows
        ),
    }


def grab_hype(path):
    first, last = epoch("2026-04-22"), epoch("2026-11-22") + 5 * BAR
    archive = PublicArchive(path.parent)
    archive.get(path.name, {"type": "candleSnapshot", "req": {
        "coin": "@107", "interval": "4h", "startTime": first, "endTime": last,
    }}, 20 + math.ceil((1 + (last - first) // BAR) / 60))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/agent-liminal-queued"))
    parser.add_argument("--capture", action="store_true", help="Collect read-only public logs and receipts")
    parser.add_argument("--marks", type=Path, default=Path("results/follow-up/liminal-issuer-marks.json"))
    parser.add_argument("--opening-quotes", type=Path, default=OPENING_QUOTES)
    parser.add_argument("--hype", type=Path, help="Optional previously cached native HYPE/USDC 4-hour bars")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    hype = args.hype or args.data / "hype-4h.json"
    if args.capture:
        if not hype.is_file():
            grab_hype(hype)
        collect_logs(args.data)
        collect_details(args.data)
        cache_json(args.data / "exact-mark-quotes.json", lambda: verify_exact_mark_quotes(
            args.marks.read_bytes(), args.opening_quotes.read_bytes()
        ))
    result = report(args.data, args.marks, hype)
    path = args.output or args.data / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(f"Historical standard queue screen passes: {result['passes_historical_screen']}. Wrote {path}.")


if __name__ == "__main__":
    main()
