"""Compare fixed xToken paper vintages using issuer marks and historical public contract calls."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .liminal_allocation import CAPITAL, DATES, settle
from .liminal_forward import DataUnavailable, RPCS, SELECTORS, USDC_TOKEN, header, rpc


D = Decimal
MICRO = 10**6
DAY_MS = 86_400_000
MAX_MARK_ERROR = D("0.000001")
PAPER_HOLDER = "0x000000000000000000000000000000000000dEaD"
REDEEM_LAYOUT = int("29501c6d0a5cf7bef3f2db502c4a21ddfa1dc6ae30f842b9bba4cfdd2f8c2a00", 16)
DEPOSIT_LAYOUT = int("8181ed37ae785402ef857f5d1a6b18f3cfc3c3050c29b53fffd1ba0acd9e0600", 16)
FEE_LAYOUT = int("4b4022d112866c56e7ce4fba50b143067d70699d5800adf7d8c91134a5bcaf00", 16)
IMPLEMENTATION_SLOT = int("360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc", 16)
ADMIN_SLOT = int("b53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103", 16)
NULL_ADDRESS = "0x" + "0" * 40
DRPC_NEXT_REQUEST = 0.0

CASES = {
    "base": {"gas": D(1), "entry_reserve": D(0), "haircut": D(0)},
    "stress": {"gas": D(3), "entry_reserve": D("0.0005"), "haircut": D("0.001")},
}
METHODS = {
    **SELECTORS,
    "timeLockController": "5396526c", "getMinDelay": "f27a0c92", "lastUpdateTime": "c8f33c91",
    "hasRole": "91d14854", "MINTER_ROLE": "d5391393", "BURNER_ROLE": "282c51f3",
    "strategist": "1fe4a686", "feeManager": "d0fb0203", "decimals": "313ce567",
    "getThreshold": "e75235b8", "getOwners": "a0e67e2b",
    "maxDepositForHolder": "402d267d", "owner": "8da5cb5b",
}


@dataclass(frozen=True)
class Route:
    share: str
    deposit: str
    redeem: str
    oracle: str


ROUTES = {
    "xBTC": Route("0x97df58ce4489896f4ec7d16b59b64ad0a56243a8", "0x629010d62e54cfa49d6ac35e4e3de2240d4ce4bf",
                  "0xfdf08966a8957fafe1ab47488fc97d15217e50fe", "0x2a6448fc3a0fade5811bb0087836a090eaa34715"),
    "xHYPE": Route("0xac962fa04bf91b7fd0dc0c5c32414e0ce3c51e03", "0xe7e0b7d87c4869549a4a47a8f216e362d0efc9f9",
                   "0x19f4881cdb479d01ce214f6908c99b4fe76c03e8", "0xbf97a22b1229b3ffba65003c01df8ba9e7bff042"),
    "xLEND": Route("0x95f6d66c09a22e6f2bb693306b3ed69663066cbb", "0x309cf12b14decb39852eba8869dca5bdf9a5b754",
                   "0xce12c415019657083a8b985e7cd5cd6fc486f978", "0xdbb4da0f1548f6237df6960532563804a901c2ae"),
}


def addr(number):
    return f"0x{number & ((1 << 160) - 1):040x}"


def metrics_url(symbol):
    return f"https://api.liminal.money/sdk/tokenized/{symbol}/metrics?range=max"


def read_marks(path):
    raw = path.read_bytes()
    marks = {}
    observed = []
    for row in json.loads(raw):
        ts = int(row["timestamp"])
        if ts % DAY_MS != DAY_MS - 1:
            raise ValueError(f"{path} has observations outside UTC day ends")
        day = datetime.fromtimestamp(ts / 1000, UTC).date()
        observed.append(day)
        if not DATES[0] <= day <= DATES[-1]:
            continue
        if day in marks:
            raise ValueError(f"{path} has duplicate marks on {day}")
        price, tvl, supply = (D(str(row[k])) for k in ("pps", "tvl", "totalSupply"))
        if not all(value.is_finite() and value > 0 for value in (price, tvl, supply)):
            raise ValueError(f"{path} has invalid marks on {day}")
        marks[day] = (price, abs(price - tvl / supply))
    return marks, {"sha256": sha256(raw).hexdigest(), "observations": len(observed),
                   "first_day": str(min(observed)), "last_day": str(max(observed)),
                   "scoped_observations": len(marks)}


class PublicState:
    def __init__(self, endpoint, block):
        self.endpoint = endpoint
        self.block_number = hex(block["number"])
        self.block_hash = {"blockHash": block["hash"], "requireCanonical": True}

    def batch(self, queries):
        # dRPC's public endpoint accepts a maximum of three calls per request.
        limit = 3 if "drpc.org" in self.endpoint else 30
        requests = list(queries.items())
        result = {}
        for start in range(0, len(requests), limit):
            result.update(self.request(dict(requests[start:start + limit])))
        return result

    def request(self, queries):
        global DRPC_NEXT_REQUEST
        payload = [{"jsonrpc": "2.0", "method": method, "params": params, "id": index + 1}
                   for index, (method, params) in enumerate(queries.values())]
        request = Request(self.endpoint, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "User-Agent": "Lane-Z paper research",
        })
        for attempt in range(4):
            if "drpc.org" in self.endpoint:
                sleep(max(0.0, DRPC_NEXT_REQUEST - monotonic()))
                DRPC_NEXT_REQUEST = monotonic() + 0.5
            try:
                with urlopen(request, timeout=20) as response:
                    raw = json.load(response)
                replies = {record["id"]: record for record in raw}
                if set(replies) != set(range(1, len(payload) + 1)):
                    raise ValueError("incomplete response")
                return {label: replies[index].get("result", replies[index].get("error"))
                        for index, label in enumerate(queries, 1)}
            except (URLError, TimeoutError, OSError, ValueError, TypeError, KeyError) as error:
                if attempt == 3:
                    raise DataUnavailable(f"{self.endpoint} missing historical batch: {error}") from error
                rate_limited = isinstance(error, HTTPError) and error.code == 429
                sleep((8 if rate_limited else 1) * 2**attempt)
        raise AssertionError("RPC retry loop did not return or raise")

    def code(self, address):
        return "eth_getCode", [address, self.block_number]

    def store(self, address, slot):
        return "eth_getStorageAt", [address, f"0x{slot:064x}", self.block_number]

    def call(self, address, method, *arguments):
        payload = "".join(f"{(int(value, 16) if isinstance(value, str) else value):064x}" for value in arguments)
        call = {"to": address, "from": PAPER_HOLDER, "data": f"0x{METHODS[method]}{payload}"}
        return "eth_call", [call, self.block_hash]


def integer(value, label):
    if not isinstance(value, str) or len(value) != 66:
        raise DataUnavailable(f"Invalid archive response for {label}: {value}")
    return int(value, 16)


def ints(raw):
    return {k: integer(v, k) for k, v in raw.items()}


def inspect(endpoint, block, route):
    client = PublicState(endpoint, block)
    keystones = {"share": route.share, "deposit": route.deposit, "redeem": route.redeem}
    beginnings = client.batch({k: client.code(v) for k, v in keystones.items()})
    if any(not isinstance(v, str) for v in beginnings.values()):
        raise DataUnavailable(f"Invalid block archive bytecode at {block['number']}")
    missing = [k for k, v in beginnings.items() if v == "0x"]
    if missing:
        return {"unavailable": f"published route not deployed yet: {', '.join(missing)}"}

    links = ints(client.batch({
        "deposit_share": client.store(route.deposit, DEPOSIT_LAYOUT),
        "deposit_oracle": client.store(route.deposit, DEPOSIT_LAYOUT + 2),
        "deposit_underlying": client.store(route.deposit, DEPOSIT_LAYOUT + 4),
        "redeem_share": client.store(route.redeem, REDEEM_LAYOUT),
        "redeem_oracle": client.store(route.redeem, REDEEM_LAYOUT + 1),
        "redeem_underlying": client.store(route.redeem, REDEEM_LAYOUT + 2),
    }))
    oracle = addr(links["redeem_oracle"])
    if oracle == NULL_ADDRESS or oracle != route.oracle:
        raise DataUnavailable("Published and configured NAV oracle addresses disagree")
    if addr(links["deposit_oracle"]) != oracle or {addr(links[key]) for key in ("redeem_share", "deposit_share")} != {route.share}:
        raise DataUnavailable("Mint and redemption contract linkage disagrees")
    if {addr(links[key]) for key in ("redeem_underlying", "deposit_underlying")} != {USDC_TOKEN}:
        return {"unavailable": "configured underlying asset is not native USDC"}

    parties = {**keystones, "oracle": oracle}
    result = client.batch({
        "provider": client.call(route.redeem, "liquidityProvider"),
        "fee_manager": client.call(route.deposit, "feeManager"),
        "strategist": client.call(route.deposit, "strategist"),
        "deposit_asset": client.call(route.deposit, "asset"),
        "fee_bps": client.call(route.redeem, "instantRedeemFeeBps"),
        "share_decimals": client.call(route.share, "decimals"),
        "relative_update_time": client.call(oracle, "lastUpdateTime"),
        "nav": client.call(oracle, "getNAV"),
        "supply": client.call(route.share, "totalSupply"),
        "max_supply": client.call(route.share, "maxSupply"),
        "max_deposit_shares": client.call(route.share, "maxDeposit"),
        "max_deposit_micro": client.call(route.deposit, "maxDepositForHolder", PAPER_HOLDER),
        "max_redeem_shares": client.call(route.share, "maxWithdraw"),
        "min_deposit_shares": client.call(route.deposit, "MIN_AMOUNT_SHARES"),
        "min_redeem_shares": client.call(route.redeem, "MIN_AMOUNT_SHARES"),
        "holder_shares": client.call(route.share, "balanceOf", PAPER_HOLDER),
        "share_holder_blacklisted": client.call(route.share, "isBlacklisted", PAPER_HOLDER),
        "minter_role": client.call(route.share, "MINTER_ROLE"),
        "burner_role": client.call(route.share, "BURNER_ROLE"),
        **{f"{k}_paused": client.call(v, "paused") for k, v in keystones.items()},
        **{f"{k}_implementation": client.store(v, IMPLEMENTATION_SLOT) for k, v in parties.items()},
        **{f"{k}_proxy_admin": client.store(v, ADMIN_SLOT) for k, v in parties.items()},
        **{f"{k}_timelock": client.call(v, "timeLockController") for k, v in keystones.items()},
    })
    # Historical oracle releases do not all expose the update timestamp.
    observed_update = result.pop("relative_update_time")
    state = ints(result)
    state["oracle_update_timestamp"] = int(observed_update, 16) if isinstance(observed_update, str) and len(observed_update) == 66 else None
    if addr(state["deposit_asset"]) != USDC_TOKEN or state["share_decimals"] != 18:
        return {"unavailable": "deposit is not native USDC or share decimals differ"}
    if not state["nav"] or not state["supply"] or not 0 <= state["fee_bps"] <= 10_000:
        return {"unavailable": "invalid NAV, supply, or instant fee"}

    provider = addr(state["provider"])
    reserve = ints(client.batch({
        "provider_balance": client.call(USDC_TOKEN, "balanceOf", provider),
        "provider_allowance": client.call(USDC_TOKEN, "allowance", provider, route.redeem),
        "native_usdc_paused": client.call(USDC_TOKEN, "paused"),
        "holder_usdc_blacklisted": client.call(USDC_TOKEN, "isBlacklisted", PAPER_HOLDER),
        "provider_usdc_blacklisted": client.call(USDC_TOKEN, "isBlacklisted", provider),
        "deposit_usdc_blacklisted": client.call(USDC_TOKEN, "isBlacklisted", route.deposit),
        "redeem_usdc_blacklisted": client.call(USDC_TOKEN, "isBlacklisted", route.redeem),
        "strategist_usdc_blacklisted": client.call(USDC_TOKEN, "isBlacklisted", addr(state["strategist"])),
        "mint_authorized": client.call(route.share, "hasRole", state["minter_role"], route.deposit),
        "burn_authorized": client.call(route.share, "hasRole", state["burner_role"], route.redeem),
    }))
    state.update(reserve)
    state["spendable"] = min(state["provider_balance"], state["provider_allowance"])
    state["oracle"] = oracle
    common_flags = ("share_paused", "native_usdc_paused", "holder_usdc_blacklisted", "share_holder_blacklisted")
    state["entry_blockers"] = [flag for flag in (*common_flags, "deposit_paused", "deposit_usdc_blacklisted",
                                              "strategist_usdc_blacklisted") if state[flag]]
    state["exit_blockers"] = [flag for flag in (*common_flags, "redeem_paused", "redeem_usdc_blacklisted",
                                             "provider_usdc_blacklisted") if state[flag]]
    if not state["mint_authorized"]:
        state["entry_blockers"].append("mint privilege missing")
    if not state["burn_authorized"]:
        state["exit_blockers"].append("burn privilege missing")

    metadata = client.batch({
        "provider_code": client.code(provider),
        "provider_threshold": client.call(provider, "getThreshold"),
        "provider_owners": client.call(provider, "getOwners"),
        "redeem_timelock_delay": client.call(addr(state["redeem_timelock"]), "getMinDelay"),
        **{f"{k}_admin_owner": client.call(addr(state[f"{k}_proxy_admin"]), "owner") for k in parties
           if state[f"{k}_proxy_admin"]},
        **({f"fund_fee_{k}_bps": client.store(addr(state["fee_manager"]), FEE_LAYOUT + slot)
            for k, slot in (("management", 3), ("performance", 4))} if state["fee_manager"] else {}),
    })
    state["provider_account_type"] = "EOA or no code" if metadata["provider_code"] == "0x" else "contract"
    for key, value in metadata.items():
        if key == "provider_code":
            continue
        if key == "provider_owners":
            if isinstance(value, str) and len(value) >= 130 and int(value[2:66], 16) == 32:
                count = int(value[66:130], 16)
                if len(value) == 130 + count * 64:
                    state["provider_owner_count"] = count
            continue
        if isinstance(value, str) and len(value) == 66:
            state[key] = int(value, 16)
    queries = {}
    for case, config in CASES.items():
        amount = invest(config)
        if not state["entry_blockers"] and amount <= state["max_deposit_micro"]:
            queries[case] = client.call(route.deposit, "previewDeposit", amount)
    state["entry_previews"] = ints(client.batch(queries)) if queries else {}
    return state


def invest(config):
    return int((CAPITAL - config["gas"]) * (1 - config["entry_reserve"]) * MICRO)


def identical(block, route):
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda endpoint: inspect(endpoint, block, route), RPCS))
    if states[0] != states[1]:
        unequal = sorted(k for k in set(states[0]) | set(states[1]) if states[0].get(k) != states[1].get(k))
        raise DataUnavailable(f"Archive states disagree at {block['number']}: {unequal}")
    return states[0]


def exit_quote(endpoint, block, route, state, shares):
    if "unavailable" in state:
        return {"unavailable": state["unavailable"]}
    reasons = list(state["exit_blockers"])
    if not state["min_redeem_shares"] <= shares <= state["max_redeem_shares"]:
        reasons.append("position outside share redemption caps")
    if reasons:
        return {"unavailable": ", ".join(reasons)}
    client = PublicState(endpoint, block)
    amounts = ints(client.batch({k: client.call(route.redeem, method, shares) for k, method in
                               (("gross", "convertToAssets"), ("net", "previewRedeem"))}))
    gross, net = amounts["gross"], amounts["net"]
    if net != gross - gross * state["fee_bps"] // 10_000:
        raise DataUnavailable("Instant redemption quote does not match the historical fee")
    return {**amounts, "available": 0 < net <= state["spendable"]}


def both_quotes(block, route, state, shares):
    with ThreadPoolExecutor(max_workers=2) as pool:
        quotes = list(pool.map(lambda ep: exit_quote(ep, block, route, state, shares), RPCS))
    if quotes[0] != quotes[1]:
        raise DataUnavailable(f"Archive redemption quotes disagree at {block['number']}")
    return quotes[0]


def entry_eligible(state, shares):
    return (state["holder_shares"] + shares <= state["max_deposit_shares"]
            and state["supply"] + shares <= state["max_supply"]
            and shares >= state["min_deposit_shares"])


def boundaries(path):
    verified = json.loads(path.read_bytes())
    if any(rpc(endpoint, "eth_chainId", []) != "0x3e7" for endpoint in RPCS):
        raise DataUnavailable("Wrong chain ID returned by public provider")
    values = {}
    for day in DATES:
        block = verified[str(day)]["block"]
        following = verified[str(day)]["next_block"]
        opened = [header(endpoint, block["number"]) for endpoint in RPCS]
        after = [header(endpoint, following["number"]) for endpoint in RPCS]
        for expected, actual in ((block, opened), (following, after)):
            for candidate in actual:
                if candidate != {"number": expected["number"], "timestamp": expected["time"], "hash": expected["hash"]}:
                    raise DataUnavailable(f"Archive boundary disagreement for {day}")
        values[str(day)] = opened[0]
    return values


def current_block():
    height = min(int(rpc(ep, "eth_blockNumber", []), 16) for ep in RPCS) - 8
    replies = [header(ep, height) for ep in RPCS]
    if replies[0] != replies[1]:
        raise DataUnavailable("Public providers disagree on the current inspection block")
    return replies[0]


def cached(path, fetch):
    if path.exists():
        return json.loads(path.read_bytes())
    result = fetch()
    draft = path.with_suffix(".tmp")
    draft.write_text(json.dumps(result, indent=2) + "\n")
    draft.replace(path)
    return result


def capture(verified, cache):
    cache.mkdir(parents=True, exist_ok=True)
    blocks = boundaries(verified)
    blocks["current"] = cached(cache / "current-block.json", current_block)
    observations = {}
    for symbol, route in ROUTES.items():
        records = {}
        for day, block in blocks.items():
            print(f"Reading independent public archive state for {symbol} at {day}", flush=True)
            records[day] = cached(cache / f"{symbol}-{block['hash']}-state.json", lambda: identical(block, route))
        observations[symbol] = records

    fills = {}
    for symbol, route in ROUTES.items():
        states = observations[symbol]
        booked = {}
        for start, end in zip(DATES, DATES[1:]):
            start, end = str(start), str(end)
            st, et = states[start], states[end]
            window = {}
            for case in CASES:
                shares = st.get("entry_previews", {}).get(case)
                if shares is None or not entry_eligible(st, shares):
                    window[case] = {"unavailable": st.get("unavailable") or "entry caps or restrictions"}
                    continue
                start_quote = cached(cache / f"{symbol}-{blocks[start]['hash']}-exit-{shares}.json",
                                     lambda: both_quotes(blocks[start], route, st, shares))
                end_quote = cached(cache / f"{symbol}-{blocks[end]['hash']}-exit-{shares}.json",
                                   lambda: both_quotes(blocks[end], route, et, shares))
                window[case] = {"entry_shares": shares, "entry_same_day_exit": start_quote, "end_exit": end_quote}
            booked[f"{start}/{end}"] = window
        today = states["current"]
        shares = today.get("entry_previews", {}).get("stress")
        today_quote = (cached(cache / f"{symbol}-{blocks['current']['hash']}-exit-{shares}.json",
                              lambda: both_quotes(blocks["current"], route, today, shares))
                       if shares is not None and entry_eligible(today, shares) else None)
        fills[symbol] = {"windows": booked, "current_stressed_entry_shares": shares, "current_exit_quote": today_quote}
    return {"providers": list(RPCS), "blocks": blocks, "states": observations, "fills": fills}


def score_window(marks, state, fills, start, end, liminal):
    key = f"{start}/{end}"
    elapsed = (end - start).days
    hurdle = CAPITAL * (D("1.04") ** (D(elapsed) / 365) - 1)
    limusd_quote = liminal[key]["scenarios"]["stress_instant"]["actual_onchain_deposit_shares"]["net_usdc_microunits"]
    limusd = D(limusd_quote) / MICRO * (1 - CASES["stress"]["haircut"]) - CAPITAL
    report = {"start": str(start), "end": str(end), "cash_pnl_usdc": hurdle, "limusd_stress_pnl_usdc": limusd}
    required = [start + timedelta(days=d) for d in range(elapsed + 1)]
    if not all(day in marks for day in required):
        return {**report, "unavailable": "issuer daily NAV coverage incomplete"}
    invalid = [str(day) for day in required if marks[day][1] > MAX_MARK_ERROR]
    if invalid:
        return {**report, "unavailable": f"daily issuer PPS conflicts with assets divided by shares: {', '.join(invalid)}"}
    prices = [marks[day][0] for day in required]
    peak, drawdown = prices[0], D(0)
    for p in prices:
        peak = max(peak, p)
        drawdown = max(drawdown, 1 - p / peak)
    report.update(issuer_max_drawdown_pct=100 * drawdown, issuer_gross_pnl_usdc=CAPITAL * (prices[-1] / prices[0] - 1),
                  issuer_30bp_pnl_usdc={case: settle(prices[0], prices[-1], fee=D(".003"), **terms) - CAPITAL
                                        for case, terms in CASES.items()})
    endpoints = (state[str(start)], state[str(end)])
    deviations = []
    for archive, price in zip(endpoints, (prices[0], prices[-1])):
        if "unavailable" not in archive:
            deviations.append(abs(D(archive["nav"]) / archive["supply"] - price))
    report["archive_pps_max_absolute_difference_usdc"] = max(deviations, default=None)
    outcomes = {}
    for case, terms in CASES.items():
        record = fills[key][case]
        if "unavailable" in record:
            outcomes[case] = record
            continue
        opening, closing = record["entry_same_day_exit"], record["end_exit"]
        actual_headroom = endpoints[1].get("spendable")
        if not opening.get("available") or not closing.get("available"):
            outcomes[case] = {"unavailable": {"opening": opening, "closing": closing}}
            continue
        if not len(deviations) == 2 or max(deviations) > MAX_MARK_ERROR:
            outcomes[case] = {"unavailable": "end-of-day archived contract NAV does not corroborate the issuer marks"}
            continue
        quote = D(closing["net"]) / MICRO
        settle_with = lambda fee: D(closing["gross"] - closing["gross"] * fee // 10_000) / MICRO * (1 - terms["haircut"]) - CAPITAL
        actual_pnl = quote * (1 - terms["haircut"]) - CAPITAL
        outcomes[case] = {
            "actual_fee_bps": endpoints[1]["fee_bps"], "actual_fee_pnl_usdc": actual_pnl,
            "onchain_30bp_pnl_usdc": settle_with(30),
            "cash_excess_usdc": actual_pnl - hurdle, "limusd_excess_usdc": actual_pnl - limusd,
            "extra_100bp_loss_pnl_usdc": quote * (1 - terms["haircut"] - D(".01")) - CAPITAL,
            "after_fee_quote_usdc": quote,
            "reserve_to_exit_quote_multiple": D(actual_headroom) / closing["net"],
            "reserve_to_onchain_aum_pct": 100 * D(actual_headroom) * 10**12 / endpoints[1]["nav"],
        }
    report["outcomes"] = outcomes
    return report


def report(root, snapshots, liminal):
    markets = {}
    for symbol in ROUTES:
        marks, source = read_marks(root / f"{symbol}-metrics.json")
        states = snapshots["states"][symbol]
        fills = snapshots["fills"][symbol]
        windows = [score_window(marks, states, fills["windows"], open_day, close_day, liminal)
                   for open_day, close_day in zip(DATES, DATES[1:])]
        stresses = [r.get("outcomes", {}).get("stress", {}) for r in windows]
        today = states["current"]
        markets[symbol] = {
            "issuer_metrics_endpoint": metrics_url(symbol), **source, "windows": windows,
            "current_fee_bps": today.get("fee_bps"), "current_spendable_reserve_usdc": D(today.get("spendable", 0)) / MICRO,
            "current_reserve_quote": fills["current_exit_quote"],
            "current_exit_capacity_verified": bool((fills["current_exit_quote"] or {}).get("available")),
            "historical_challenger_pass": all(r.get("cash_excess_usdc", D("-Infinity")) > 0
                                              and r.get("limusd_excess_usdc", D("-Infinity")) > 0 for r in stresses),
        }
    return {"archive_capture_block_hashes": {day: row["hash"] for day, row in snapshots["blocks"].items()}, "markets": markets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--limusd-quotes", type=Path, default=Path("results/follow-up/liminal-archive-quotes.json"))
    parser.add_argument("--boundaries", type=Path, default=Path("results/follow-up/liminal-archive-state.json"))
    parser.add_argument("--capture", action="store_true", help="cache public historical contract state and previews")
    parser.add_argument("--output", type=Path, required=True)
    opts = parser.parse_args()
    snapshots = opts.data / "contract-snapshots.json"
    if opts.capture:
        if snapshots.exists():
            raise ValueError(f"Raw snapshots already exist at {snapshots}")
        raw = capture(opts.boundaries, opts.data / "rpc-cache")
        snapshots.write_text(json.dumps(raw, indent=2) + "\n")
    archive = json.loads(snapshots.read_bytes())
    liminal = json.loads(opts.limusd_quotes.read_bytes())
    scored = report(opts.data, archive, liminal)
    with opts.output.open("x") as output:
        json.dump(scored, output, indent=2, default=str)
        output.write("\n")


if __name__ == "__main__":
    main()
