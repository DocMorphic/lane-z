"""Read-only replay of native USDC supply to the HyperLend core pool."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from .liminal_forward import DataUnavailable, RPCS, USDC_TOKEN, header, rpc


POOL = "0x00a89d7a5a02160f20150ebea7a2b5e4879a1a8b"
DATA_PROVIDER = "0x4f4d4ca1e0a8a21fe0b460613bebe917f2eb4326"
HUSDC = "0x744e4f26ee30213989216e1632d9be3547c4885b"
BLOCKS = {"2026-05-17": 35_387_475, "2026-07-19": 40_920_592,
          "2026-09-20": 46_453_895, "2026-11-22": 51_987_233}
RAY, MICRO = 10**27, 10**6
CAPITAL = 2_000 * MICRO
SCENARIOS = {"zero_cost": (0, 0, 0), "base": (1, 0, 0),
             "stress": (3, 5, 10), "extra_100bp_loss": (3, 5, 110)}


def abi_words(endpoint, block, address, selector, size, *args):
    arguments = "".join(f"{int(arg, 16) if isinstance(arg, str) else arg:064x}" for arg in args)
    reference = {"blockHash": block["hash"], "requireCanonical": True}
    result = rpc(endpoint, "eth_call", [{"to": address, "data": f"0x{selector}{arguments}"}, reference])
    if not result.startswith("0x") or len(result) != 2 + 64 * size:
        raise DataUnavailable(f"Invalid result size for EVM selector {selector}")
    return tuple(int(result[offset:offset + 64], 16) for offset in range(2, len(result), 64))


def optional_abi_word(endpoint, block, address, selector, *args):
    try:
        return abi_words(endpoint, block, address, selector, 1, *args)[0]
    except DataUnavailable as exc:
        if "execution reverted" not in str(exc).lower() and "invalid result size" not in str(exc).lower():
            raise
        return None


def capture_state(endpoint, label, height):
    block = header(endpoint, height)
    after = header(endpoint, height + 1)
    end = date.fromisoformat(label)
    target = (end - date(1970, 1, 1)).days * 86_400 + 86_399
    if not block["timestamp"] <= target < after["timestamp"]:
        raise ValueError(f"Wrong end-of-day boundary for {label}")

    def get(address, selector, *args):
        return abi_words(endpoint, block, address, selector, 1, *args)[0]

    if get(USDC_TOKEN, "313ce567") != 6 or get(HUSDC, "313ce567") != 6:
        raise ValueError("Unexpected native USDC or hUSDC decimals")
    if get(HUSDC, "b16a19de") != int(USDC_TOKEN, 16):
        raise ValueError("hUSDC is not backed by native USDC")
    reserve_tokens = abi_words(endpoint, block, DATA_PROVIDER, "d2493b6c", 3, USDC_TOKEN)
    if reserve_tokens[0] != int(HUSDC, 16):
        raise ValueError("Historical HyperLend reserve token differs from the published hUSDC")
    index = get(POOL, "d15e0053", USDC_TOKEN)
    config = get(POOL, "c44b11f7", USDC_TOKEN)
    _, cap = abi_words(endpoint, block, DATA_PROVIDER, "46fbe558", 2, USDC_TOKEN)
    if (config >> 116) & (2**36 - 1) != cap or (config >> 48) & 255 != 6:
        raise ValueError("HyperLend cap or reserve decimals disagree with its bit-packed configuration")
    data = abi_words(endpoint, block, DATA_PROVIDER, "35ea6a75", 12, USDC_TOKEN)
    supply = get(HUSDC, "18160ddd")
    scaled = get(HUSDC, "b1bf962d")
    if abs(supply - scaled * index // RAY) > 1 or data[2] != supply:
        raise ValueError("HyperLend normalized index does not reconcile with hUSDC supply")

    actual = get(USDC_TOKEN, "70a08231", HUSDC)
    virtual = optional_abi_word(endpoint, block, POOL, "6fb07f96", USDC_TOKEN)
    deficit = optional_abi_word(endpoint, block, POOL, "c952485d", USDC_TOKEN)
    healthy_token = not (get(USDC_TOKEN, "5c975abb") or get(USDC_TOKEN, "fe575a87", HUSDC)
                         or get(USDC_TOKEN, "fe575a87", POOL))
    flags = {"active": bool(config & 1 << 56), "frozen": bool(config & 1 << 57),
             "paused": bool(config & 1 << 60), "native_usdc_transfer_enabled": healthy_token}
    return {"date": label, "block": block, "next_block": after, "flags": flags, "index_ray": str(index),
            "native_usdc_balance_micro": str(actual), "virtual_balance_micro": str(virtual) if virtual is not None else None,
            "deficit_micro": str(deficit) if deficit is not None else None, "supply_cap_usdc": str(cap),
            "hUSDC_supply_micro": str(supply), "hUSDC_scaled_supply_micro": str(scaled),
            "accrued_treasury_scaled_micro": str(data[1]), "quoted_supply_apr_ray": str(data[5]),
            "variable_debt_micro": str(data[4]), "reserve_config_word": str(config)}


def capture(path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        chain = list(pool.map(lambda node: rpc(node, "eth_chainId", []), RPCS))
        if chain != ["0x3e7"] * len(RPCS):
            raise DataUnavailable("RPCs do not all serve HyperEVM")
        pairs = [(node, day, height) for day, height in BLOCKS.items() for node in RPCS]
        result = list(pool.map(lambda item: capture_state(*item), pairs))
    snapshots = []
    for first, second in zip(result[::2], result[1::2]):
        if first != second:
            raise DataUnavailable(f"HyperLend providers disagree at {first['date']}")
        snapshots.append(first)
    path.write_text(json.dumps({"providers": list(RPCS), "snapshots": snapshots}, indent=2) + "\n")


def replay(entry, exit, gas, entry_bps, exit_bps):
    invested = (CAPITAL - gas * MICRO) * (10_000 - entry_bps) // 10_000
    scaled = invested * RAY // int(entry["index_ray"])
    payout = scaled * int(exit["index_ray"]) // RAY
    marked = payout * (10_000 - exit_bps) // 10_000
    flags = entry["flags"]
    can_supply = flags["active"] and not flags["frozen"] and not flags["paused"] and flags["native_usdc_transfer_enabled"]
    cap = int(entry["supply_cap_usdc"]) * MICRO
    existing_scaled = int(entry["hUSDC_scaled_supply_micro"]) + int(entry["accrued_treasury_scaled_micro"])
    can_supply = can_supply and (cap == 0 or (existing_scaled + scaled) * int(entry["index_ray"]) // RAY <= cap)
    cash_capacity = int(exit["native_usdc_balance_micro"])
    if exit["virtual_balance_micro"] is not None:
        cash_capacity = min(cash_capacity, int(exit["virtual_balance_micro"]))
    flags = exit["flags"]
    can_withdraw = (flags["active"] and not flags["paused"] and flags["native_usdc_transfer_enabled"]
                    and cash_capacity >= payout)
    return {"deposit_micro_usdc": str(invested), "scaled_hUSDC_micro": str(scaled),
            "gross_exit_micro_usdc": str(payout), "marked_exit_micro_usdc": str(marked),
            "net_pnl_micro_usdc": str(marked - CAPITAL), "entry_capacity": can_supply,
            "exit_capacity": can_withdraw, "exit_cash_capacity_micro_usdc": str(cash_capacity)}


def run(path):
    raw = json.loads(path.read_text())
    windows = []
    for entry, exit in zip(raw["snapshots"], raw["snapshots"][1:]):
        days = (date.fromisoformat(exit["date"]) - date.fromisoformat(entry["date"])).days
        cash = Decimal(CAPITAL) * (Decimal("1.04") ** (Decimal(days) / 365) - 1) / MICRO
        cases = {key: replay(entry, exit, *inputs) for key, inputs in SCENARIOS.items()}
        for case in cases.values():
            case["excess_over_cash_usdc"] = str(Decimal(case["net_pnl_micro_usdc"]) / MICRO - cash)
        windows.append({"entry": entry["date"], "exit": exit["date"], "days": days,
                        "cash_hurdle_pnl_usdc": str(cash), "scenarios": cases})
    return {"vault": POOL, "hUSDC": HUSDC, "usdc": USDC_TOKEN, **raw, "windows": windows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "run"))
    parser.add_argument("--data", type=Path, default=Path("data/agent-hyperlend-usdc/state.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "fetch":
        args.data.parent.mkdir(parents=True, exist_ok=True)
        capture(args.data)
    else:
        report = json.dumps(run(args.data), indent=2) + "\n"
        if args.output:
            args.output.write_text(report)
        else:
            print(report, end="")


if __name__ == "__main__":
    main()
