"""Capture historical, amount-specific Pendle PT and LP quotes without transactions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .hyperlend_usdc import abi_words
from .liminal_forward import DEPOSIT, REDEEM, RPCS, SHARE_MANAGER, USDC_TOKEN, header, rpc, word


MARKET = "0xfc93d1ada3277e6274c3905db0a2c81ac1252025"
SY = "0xcf6b4c156f51818922b98524dc832e352f40d516"
PT = "0x6b7cea9d070bbffd8205f256897319139cdef399"
YT = "0x6df3fbb4373fe1409e4697f214f723ab61640130"
STATIC = "0x6813d43782395a1f2aab42f39aeede03ac655e09"
ROUTER = "0x888888888889758f76e7103c6cbf23abbf58f946"
BASE = "https://api-v2.pendle.finance/core"
REGISTRY = f"{BASE}/v2/markets/all?chainId=999&limit=100&skip=0"
MATURES = int(datetime.fromisoformat("2026-11-26T00:00:00+00:00").timestamp())
TIMES = ("2026-07-23T00:00:00+00:00", "2026-09-24T00:00:00+00:00",
         "2026-10-29T00:00:00+00:00", "2026-11-23T23:59:59+00:00")
MICRO, WAD = 10**6, 10**18
CAPITAL = 2_000 * MICRO
GAS = {"base": 1, "stress": 3}
AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


def archive_block(target):
    lower, upper = 31_000_000, min(int(rpc(node, "eth_blockNumber", []), 16) for node in RPCS)
    if not header(RPCS[0], lower)["timestamp"] < target < header(RPCS[0], upper)["timestamp"]:
        raise ValueError("Cannot bound the requested historical timestamp")
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if header(RPCS[0], middle)["timestamp"] <= target:
            lower = middle
        else:
            upper = middle
    boundary, after = header(RPCS[0], lower), header(RPCS[0], upper)
    if boundary != header(RPCS[1], lower) or after != header(RPCS[1], upper):
        raise ValueError("HyperEVM providers disagree on the quote block")
    return boundary, after


def http_json(url):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": AGENT,
                                    "Origin": "https://app.pendle.finance", "Referer": "https://app.pendle.finance/"})
    with urlopen(request, timeout=15) as response:
        return response.read()


def hosted_quote(token_out, usdc_in):
    query = urlencode({"receiver": "0x000000000000000000000000000000000000dEaD", "slippage": "0.001",
                       "tokensIn": USDC_TOKEN, "tokensOut": token_out, "amountsIn": usdc_in,
                       "enableAggregator": "false", "additionalData": "impliedApy,effectiveApy"})
    raw = json.loads(http_json(f"{BASE}/v2/sdk/999/convert?{query}"))
    route = raw["routes"][0]
    return {"timestamp": datetime.now(UTC).isoformat(), "inputs": raw["inputs"],
            "outputs": route["outputs"], "quote": route["data"]}


def read_state(endpoint, block):
    def get(address, selector, size, *args):
        return abi_words(endpoint, block, address, selector, size, *args)

    def one(address, selector, *args):
        return get(address, selector, 1, *args)[0]

    if tuple(int(x, 16) for x in (SY, PT, YT)) != get(MARKET, "2c8ce6bc", 3):
        raise ValueError("Pendle market token tuple differs from the listing")
    _, accounting, accounting_decimals = get(SY, "a40bee50", 3)
    if (accounting, accounting_decimals) != (int(USDC_TOKEN, 16), 6):
        raise ValueError("Accounting asset is not native six-decimal USDC")
    if one(MARKET, "e184c9be") != MATURES or one(PT, "313ce567") != accounting_decimals:
        raise ValueError("Unexpected PT expiry or token decimals")

    exchange_rate = one(SY, "3ba0b9a9")
    watermark = one(YT, "d2a3584e")
    index = max(exchange_rate, watermark)
    parameters = get(MARKET, "794052f3", 9, ROUTER)
    share_room = word(endpoint, block, SHARE_MANAGER, "maxSupply") - word(endpoint, block, SHARE_MANAGER, "totalSupply")
    funding_address = f"0x{word(endpoint, block, REDEEM, 'liquidityProvider'):040x}"
    reserve = min(word(endpoint, block, USDC_TOKEN, "balanceOf", funding_address),
                  word(endpoint, block, USDC_TOKEN, "allowance", funding_address, REDEEM))
    fee_bps = word(endpoint, block, REDEEM, "instantRedeemFeeBps")
    deposit_paused = bool(word(endpoint, block, DEPOSIT, "paused"))
    redeem_paused = bool(word(endpoint, block, REDEEM, "paused"))

    def projected_cash(net_pt):
        sy_out = net_pt * WAD // index
        shares = one(SY, "cbe52ae3", SHARE_MANAGER, sy_out)
        gross = word(endpoint, block, REDEEM, "convertToAssets", shares)
        net = word(endpoint, block, REDEEM, "previewRedeem", shares)
        quote = one(SY, "cbe52ae3", USDC_TOKEN, sy_out)
        if gross - (gross * fee_bps // 10_000) != net:
            raise ValueError("Liminal share quote does not match collected instant fee")
        return {"conditional_sy_units": str(sy_out), "limUSD_share_wei": str(shares),
                "gross_usdc_micro": str(gross), "direct_liminal_cash_usdc_micro": str(net),
                "direct_sy_cash_usdc_micro": str(quote), "capacity_for_liminal_cash_quote": reserve >= net and not redeem_paused}

    scenarios = {}
    for label, gas in GAS.items():
        assets = CAPITAL - gas * MICRO
        pt_out, sy_in, sy_fee, impact, _ = get(STATIC, "26e44ccf", 5, MARKET, USDC_TOKEN, assets)
        wrapped = one(SY, "b8f82b26", USDC_TOKEN, assets)
        underlying_preview = word(endpoint, block, DEPOSIT, "previewDeposit", assets)
        if sy_in != wrapped:
            raise ValueError("Static route and SY wrap prequote disagree")
        if (pt_out, sy_fee, impact) != get(STATIC, "b1f36065", 4, MARKET, sy_in)[:3]:
            raise ValueError("Token and SY routes disagree on exact pool quote")
        scenarios[label] = {"initial_quote_input_micro_usdc": str(assets), "pt_output_micro": str(pt_out),
                            "sy_minted_raw": str(sy_in), "swap_fee_sy_raw": str(sy_fee),
                            "price_impact_raw": str(impact), "mint_capacity": not deposit_paused and share_room >= underlying_preview,
                            **projected_cash(pt_out)}
    lp_out, net_pt, lp_fee, lp_impact, _, lp_sy, _ = get(STATIC, "a3ccc850", 7, MARKET, USDC_TOKEN, CAPITAL - MICRO)
    return {"market_balances": {"pt_raw": str(parameters[0]), "sy_raw": str(parameters[1]), "lp_raw": str(parameters[2]),
                                "ln_fee_rate_root": str(parameters[6]), "reserve_fee_percent": parameters[7]},
            "pt_total_supply_raw": str(one(PT, "18160ddd")),
            "sy_decimals": one(SY, "313ce567"), "lp_decimals": one(MARKET, "313ce567"),
            "exchange_rate_raw": str(exchange_rate), "watermark_raw": str(watermark),
            "current_redemption_index_raw": str(index), "instant_redeem_fee_bps": fee_bps,
            "liquidity_provider": funding_address, "spendable_usdc_micro": str(reserve),
            "remaining_share_capacity_wei": str(share_room), "quotes": scenarios,
            "base_single_token_lp": {"lp_raw": str(lp_out), "pt_from_swap_raw": str(net_pt),
                                     "swap_fee_sy_raw": str(lp_fee), "price_impact_raw": str(lp_impact), "sy_minted_raw": str(lp_sy)}}


def capture(root):
    root.mkdir(parents=True, exist_ok=True)
    raw = http_json(REGISTRY)
    (root / "registry.json").write_bytes(raw)
    registry = json.loads(raw)
    if registry["total"] != len(registry["results"]):
        raise ValueError("Expected one complete HyperEVM market registry page")

    def snapshot(iso):
        t = int(datetime.fromisoformat(iso).timestamp())
        block, after = archive_block(t)
        state, corroboration = [read_state(endpoint, block) for endpoint in RPCS]
        if state != corroboration:
            raise ValueError(f"Historical state mismatch for {iso}")
        return {"target": iso, "canonical": block, "successor": after, **state}

    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(snapshot, TIMES))
    limusd = [{"name": row["name"], "address": row["address"], "expiry": row["expiry"]}
              for row in registry["results"] if row["underlyingAsset"] == f"999-{SHARE_MANAGER.lower()}"]
    result = {"market": MARKET, "pt": PT, "sy": SY, "yt": YT, "expiry": MATURES, "rpcs": list(RPCS),
              "registry_sha256": sha256(raw).hexdigest(), "HyperEVM_market_count": registry["total"],
              "limUSD_markets": limusd, "snapshots": states}
    (root / "archive.json").write_text(json.dumps(result, indent=2) + "\n")


def run(root):
    data = json.loads((root / "archive.json").read_text())
    for row in data["snapshots"]:
        days = Decimal(MATURES - row["canonical"]["timestamp"]) / 86_400
        cash = Decimal(2_000) * (Decimal("1.04") ** (days / 365) - 1)
        row["holding_days"] = str(days)
        row["cash_hurdle_pnl_usdc"] = str(cash)
        for name, quote in row["quotes"].items():
            payout = Decimal(quote["direct_liminal_cash_usdc_micro"]) / MICRO
            loss = Decimal("0.001") if name == "stress" else Decimal(0)
            net = payout * (1 - loss) - 2_000
            extra_loss = payout * (1 - loss) * Decimal("0.99") - 2_000
            quote["conditional_net_pnl_usdc"] = str(net)
            quote["conditional_excess_over_cash_usdc"] = str(net - cash)
            quote["conditional_100bp_rate_fall_net_pnl_usdc"] = str(extra_loss)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "run", "current-quote"))
    parser.add_argument("--data", type=Path, default=Path("data/agent-pendle-pt"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "fetch":
        capture(args.data)
        return
    if args.action == "current-quote":
        result = {"PT": hosted_quote(PT, CAPITAL - MICRO), "LP": hosted_quote(MARKET, CAPITAL - MICRO)}
    else:
        result = run(args.data)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
