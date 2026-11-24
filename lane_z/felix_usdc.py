"""Read-only historical quotes for the HyperEVM Felix flagship USDC vault."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from .hyperlend_usdc import BLOCKS, CAPITAL, MICRO, SCENARIOS, abi_words, optional_abi_word
from .liminal_forward import RPCS, USDC_TOKEN, header


VAULT = "0x8a862fd6c12f9ad34c9c2ff45ab2b6712e8cea27"


def deposit_amount(gas, entry_bps):
    return (CAPITAL - gas * MICRO) * (10_000 - entry_bps) // 10_000


def word(endpoint, block, to, selector, *args):
    return abi_words(endpoint, block, to, selector, 1, *args)[0]


def capture_snapshot(endpoint, label, height):
    block, successor = header(endpoint, height), header(endpoint, height + 1)
    target = (date.fromisoformat(label) - date(1970, 1, 1)).days * 86_400 + 86_399
    if not block["timestamp"] <= target < successor["timestamp"]:
        raise ValueError(f"Wrong archive day boundary for {label}")

    def vault(selector, *args):
        return word(endpoint, block, VAULT, selector, *args)

    def usdc(selector, *args):
        return word(endpoint, block, USDC_TOKEN, selector, *args)

    if vault("38d52e0f") != int(USDC_TOKEN, 16) or usdc("313ce567") != 6:
        raise ValueError("Vault asset is not native six-decimal USDC")
    morpho = f"0x{vault('3acb5624'):040x}"
    actual = usdc("70a08231", morpho)
    supply = [vault("f7d18521", i) for i in range(vault("a17b3130"))]
    withdrawals = [vault("62518ddf", i) for i in range(vault("33f91ebb"))]
    if len(supply) != len(set(supply)) or len(withdrawals) != len(set(withdrawals)):
        raise ValueError("Duplicate market ID in a vault queue")

    ready = []
    for market_id in withdrawals:
        params = abi_words(endpoint, block, morpho, "2c3c9157", 5, market_id)
        if params[0] != int(USDC_TOKEN, 16):
            raise ValueError("Unsupported market loan asset")
        assets, shares, borrowed, _, _, _ = abi_words(endpoint, block, morpho, "5c60e39a", 6, market_id)
        vault_shares, _, _ = abi_words(endpoint, block, morpho, "93c52062", 3, market_id, VAULT)
        # Morpho Blue uses a virtual asset and a million virtual shares. Unaccrued
        # interest raises collateralized lenders' share value and adds the same
        # amount to supply and borrowing, making this a lower bound.
        asset_share = vault_shares * (assets + 1) // (shares + MICRO)
        available = min(asset_share, assets - borrowed)
        ready.append({"market": f"0x{market_id:064x}", "collateral_token": f"0x{params[1]:040x}",
                      "supply_assets_micro": str(assets), "supply_shares": str(shares), "borrow_assets_micro": str(borrowed),
                      "vault_market_shares": str(vault_shares), "withdrawable_lower_bound_micro": str(available)})

    lost = optional_abi_word(endpoint, block, VAULT, "21cb4b14")
    native_active = not (usdc("5c975abb") or usdc("fe575a87", VAULT) or usdc("fe575a87", morpho))
    quotes = {name: str(vault("ef8b30f7", deposit_amount(gas, entry)))
              for name, (gas, entry, _) in SCENARIOS.items()}
    return {"date": label, "canonical": block, "successor": successor, "morpho": morpho,
            "share_decimals": vault("313ce567"), "total_shares": str(vault("18160ddd")),
            "accounting_assets_micro": str(vault("01e1d114")), "performance_fee_wad": str(vault("ddca3f43")),
            "recorded_lost_assets_micro": str(lost) if lost is not None else None,
            "native_usdc_active": native_active, "core_cash_balance_micro": str(actual),
            "deposit_capacity_micro": str(vault("402d267d", 0)), "quoted_shares": quotes,
            "supply_queue": [f"0x{i:064x}" for i in supply], "withdrawal_markets": ready,
            "withdrawable_lower_bound_micro": str(min(actual, sum(int(x["withdrawable_lower_bound_micro"]) for x in ready)))}


def capture(path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        first, second = RPCS
        requests = [(node, label, height) for label, height in BLOCKS.items() for node in (first, second)]
        rows = list(pool.map(lambda args: capture_snapshot(*args), requests))
        snapshots = []
        for a, b in zip(rows[::2], rows[1::2]):
            if a != b:
                raise ValueError(f"Archives disagree at {a['date']}")
            snapshots.append(a)

        def quote_exit(exit_row, shares):
            returns = [word(node, exit_row["canonical"], VAULT, "4cdad506", shares) for node in (first, second)]
            if returns[0] != returns[1]:
                raise ValueError(f"Archives disagree on exit quote at {exit_row['date']}")
            return str(returns[0])

        for start, end in zip(snapshots, snapshots[1:]):
            end["redemption_quote_micro"] = {name: quote_exit(end, int(shares)) for name, shares in start["quoted_shares"].items()}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"vault": VAULT, "providers": list(RPCS), "snapshots": snapshots}, indent=2) + "\n")


def run(path):
    report = json.loads(path.read_text())
    windows = []
    for start, end in zip(report["snapshots"], report["snapshots"][1:]):
        days = (date.fromisoformat(end["date"]) - date.fromisoformat(start["date"])).days
        cash = Decimal(CAPITAL) / MICRO * (Decimal("1.04") ** (Decimal(days) / 365) - 1)
        cases = {}
        for name, (gas, entry_bps, exit_bps) in SCENARIOS.items():
            invested = deposit_amount(gas, entry_bps)
            shares = int(start["quoted_shares"][name])
            payout = int(end["redemption_quote_micro"][name])
            marked = payout * (10_000 - exit_bps) // 10_000
            pnl = Decimal(marked - CAPITAL) / MICRO
            entry = bool(shares > 0 and start["native_usdc_active"] and int(start["deposit_capacity_micro"]) >= invested)
            exit = bool(payout > 0 and end["native_usdc_active"] and int(end["withdrawable_lower_bound_micro"]) >= payout)
            cases[name] = {"deposit_micro_usdc": str(invested), "share_units": str(shares),
                           "quoted_exit_micro_usdc": str(payout), "marked_exit_micro_usdc": str(marked),
                           "entry_capacity": entry, "exit_capacity": exit, "net_pnl_usdc": str(pnl),
                           "excess_usdc": str(pnl - cash), "passes": entry and exit and pnl > cash}
        windows.append({"entry": start["date"], "exit": end["date"], "days": days, "cash_pnl_usdc": str(cash), "cases": cases})
    report["windows"] = windows
    report["qualifies"] = all(row["cases"][key]["passes"] for row in windows for key in ("base", "stress"))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/agent-felix-usdc/state.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "fetch":
        capture(args.data)
    else:
        result = json.dumps(run(args.data), indent=2) + "\n"
        if args.output:
            args.output.write_text(result)
        else:
            print(result, end="")


if __name__ == "__main__":
    main()
