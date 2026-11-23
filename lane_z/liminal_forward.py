"""Run Lane Z's fixed, read-only forward limUSD experiment."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
import fcntl
from functools import partial
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep, time
from urllib.error import URLError
from urllib.request import Request, urlopen

RPCS = ('https://hyperliquid.drpc.org', 'https://hyperliquid-rpc.publicnode.com')
DEPOSIT = '0xe56d115dD2107e1F7199c7AcCf68D68835A0fAa9'
REDEEM = '0x2e8c1256356e261150120a384d95dc0d0d00ae29'
SHARE_MANAGER = '0x1822bd335489d84abdd0779a7dcaeda0625e83c8'
NAV_ORACLE = '0x47f8d4847f528c18ea5a6dcb9e0940f6b2977ca7'
USDC_TOKEN = '0xb88339cb7199b77e23db6e890353e22632ba630f'
# Underlying asset in the documented ERC-7201 RedemptionPipe layout.
REDEMPTION_ASSET_SLOT = '0x29501c6d0a5cf7bef3f2db502c4a21ddfa1dc6ae30f842b9bba4cfdd2f8c2a02'
DAY = 86_400
HOLD_DAYS = 63
TARGET_STREAK = 3
CAPITAL = Decimal(2000)
CASH_APY = Decimal('0.04')
GAS_RESERVE = Decimal(3)
ENTRY_RESERVE = Decimal('0.0005')
EXIT_HAIRCUT = Decimal('0.001')
TAIL_HAIRCUT = Decimal('0.01')
MICRO = 10**6
INVEST = int((CAPITAL - GAS_RESERVE) * (1 - ENTRY_RESERVE) * MICRO)
SELECTORS = {
    'getNAV': '0126c606', 'totalSupply': '18160ddd', 'maxSupply': 'd5abeb01',
    'maxWithdraw': 'ac7a1b5b', 'maxDeposit': '6083e59a', 'previewDeposit': 'ef8b30f7', 'previewRedeem': '4cdad506',
    'convertToAssets': '07a2d13a', 'instantRedeemFeeBps': '80c5b9a8',
    'MIN_AMOUNT_SHARES': '381e793d', 'liquidityProvider': '5b8bec55', 'asset': '38d52e0f',
    'paused': '5c975abb', 'isBlacklisted': 'fe575a87', 'balanceOf': '70a08231', 'allowance': 'dd62ed3e',
}


class DataUnavailable(RuntimeError):
    """The checkpoint could not yet be independently verified."""


def utc_time(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def rpc(endpoint, method, params):
    request = Request(
        endpoint,
        data=json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1}).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': 'Lane-Z paper research'},
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.load(response)
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise DataUnavailable(f'{endpoint} {method}: {error}') from error
    if not isinstance(payload, dict):
        raise DataUnavailable(f'{endpoint} {method}: invalid RPC response')
    if payload.get('result') is None:
        raise DataUnavailable(f'{endpoint} {method}: {payload.get("error", "missing result")}')
    return payload['result']


def header(endpoint, height):
    result = rpc(endpoint, 'eth_getBlockByNumber', [hex(height), False])
    return {'number': int(result['number'], 16), 'timestamp': int(result['timestamp'], 16), 'hash': result['hash']}


def word(endpoint, block, address, function, *args):
    encoded = ''.join(f'{(int(arg, 16) if isinstance(arg, str) else arg):064x}' for arg in args)
    tx = {'to': address, 'data': f'0x{SELECTORS[function]}{encoded}'}
    block_ref = {'blockHash': block['hash'], 'requireCanonical': True}
    response = rpc(endpoint, 'eth_call', [tx, block_ref])
    if not isinstance(response, str) or len(response) != 66:
        raise DataUnavailable(f'{endpoint} returned an invalid {function} word')
    return int(response, 16)


def inspect(endpoint, block, held_shares, check_entry):
    read = partial(word, endpoint, block)
    redeem_asset = rpc(endpoint, 'eth_getStorageAt', [REDEEM, REDEMPTION_ASSET_SLOT, hex(block['number'])])
    if f'0x{read(DEPOSIT, "asset"):040x}' != USDC_TOKEN or int(redeem_asset, 16) != int(USDC_TOKEN, 16):
        raise DataUnavailable('A pipe asset no longer matches the documented native USDC contract')
    supply = read(SHARE_MANAGER, 'totalSupply')
    nav = read(NAV_ORACLE, 'getNAV')
    if supply <= 0 or nav <= 0:
        raise DataUnavailable('The NAV or share supply could not be verified')
    minimum = read(REDEEM, 'MIN_AMOUNT_SHARES')
    maximum = read(SHARE_MANAGER, 'maxWithdraw')
    payment_source = f'0x{read(REDEEM, "liquidityProvider"):040x}'
    reserve = min(read(USDC_TOKEN, 'balanceOf', payment_source), read(USDC_TOKEN, 'allowance', payment_source, REDEEM))
    fee = read(REDEEM, 'instantRedeemFeeBps')
    paused = bool(read(REDEEM, 'paused') or read(USDC_TOKEN, 'paused'))
    token_restricted = bool(
        read(USDC_TOKEN, 'isBlacklisted', payment_source) or read(USDC_TOKEN, 'isBlacklisted', REDEEM)
    )
    quote = {'gross_micro_usdc': None, 'net_micro_usdc': None, 'reason': None}
    if held_shares is not None:
        gross = read(REDEEM, 'convertToAssets', held_shares)
        quote['gross_micro_usdc'] = gross
        if paused or token_restricted:
            quote['reason'] = 'redemption_paused_or_payment_restricted'
        elif not minimum <= held_shares <= maximum:
            quote['reason'] = 'position_outside_redemption_size_limits'
        else:
            net = read(REDEEM, 'previewRedeem', held_shares)
            if net != gross - (gross * fee // 10_000):
                raise DataUnavailable('The redemption preview no longer matches the fee formula')
            quote['net_micro_usdc'] = net
            if net == 0 or reserve < net:
                quote['reason'] = 'insufficient_instant_redemption_reserve'

    entry = None
    if check_entry and not paused and not token_restricted and not read(DEPOSIT, 'paused'):
        shares = read(DEPOSIT, 'previewDeposit', INVEST)
        mint_capacity = read(SHARE_MANAGER, 'maxSupply') - supply
        deposit_min = read(DEPOSIT, 'MIN_AMOUNT_SHARES')
        deposit_max = read(SHARE_MANAGER, 'maxDeposit')
        if max(1, minimum, deposit_min) <= shares <= min(maximum, mint_capacity, deposit_max):
            quoted_exit = read(REDEEM, 'previewRedeem', shares)
            if 0 < quoted_exit <= reserve:
                entry = {'deposit_micro_usdc': INVEST, 'shares_wei': str(shares)}
    return {
        'price_per_share_usdc': str(Decimal(nav) / supply), 'liquidity_provider': payment_source,
        'spendable_reserve_micro_usdc': reserve, 'fee_bps': fee, 'quote': quote, 'entry': entry,
    }


def verify_capture(target, held_shares, earliest_entry_target, lower):
    with ThreadPoolExecutor(max_workers=2) as pool:
        if list(pool.map(lambda endpoint: rpc(endpoint, 'eth_chainId', []), RPCS)) != ['0x3e7'] * 2:
            raise DataUnavailable('One provider returned the wrong chain ID')
        right = min(pool.map(lambda endpoint: int(rpc(endpoint, 'eth_blockNumber', []), 16), RPCS)) - 8
        if right <= lower or header(RPCS[0], right)['timestamp'] < target:
            raise DataUnavailable('Waiting for both provider heads to confirm the checkpoint')
        left = lower
        if header(RPCS[0], left)['timestamp'] >= target:
            raise DataUnavailable('The prior block did not precede the target')
        while right - left > 1:
            mid = (right + left) // 2
            if header(RPCS[0], mid)['timestamp'] < target:
                left = mid
            else:
                right = mid
        block = header(RPCS[0], right)
        prior = header(RPCS[0], left)
        if block != header(RPCS[1], right) or prior != header(RPCS[1], left):
            raise DataUnavailable('Provider block headers disagree')
        check_entry = block['timestamp'] >= earliest_entry_target
        snapshots = list(pool.map(lambda endpoint: inspect(endpoint, block, held_shares, check_entry), RPCS))
    if snapshots[0] != snapshots[1]:
        raise DataUnavailable('Provider fee, reserve, or preview values disagree')
    read_at = int(time())
    return {
        'target_utc': utc_time(target), 'block': block, 'providers': list(RPCS),
        'captured_at_utc': utc_time(read_at),
        'observation': 'contemporaneous' if read_at - block['timestamp'] <= 300 else 'archival_recovery',
        **snapshots[0],
    }


def position(block, shares):
    return {'entry_block': block, 'shares_wei': shares, 'due': block['timestamp'] + HOLD_DAYS * DAY,
            'awaiting_exit': False}


def apply_capture(state, snapshot):
    active = state['active']
    block = snapshot['block']
    state['last_block'] = block
    state['observations'].append(snapshot)
    state['next_at'] += DAY
    while state['next_at'] <= block['timestamp']:
        state['next_at'] += DAY
    quote = snapshot['quote']
    if active is not None:
        elapsed = Decimal(block['timestamp'] - active['entry_block']['timestamp']) / DAY
        cash = CAPITAL * (1 + CASH_APY) ** (elapsed / 365)
        if quote['reason'] is None:
            net = Decimal(quote['net_micro_usdc']) / MICRO
            liquidation = net * (1 - EXIT_HAIRCUT)
            snapshot['paper_pnl_if_redeemed_usdc'] = str(liquidation - CAPITAL)
            snapshot['cash_excess_if_redeemed_usdc'] = str(liquidation - cash)
        if block['timestamp'] < active['due']:
            state['next_at'] = min(state['next_at'], active['due'])
            return
        if not active['awaiting_exit']:
            passed = quote['reason'] is None and liquidation > cash
            state['streak'] = state['streak'] + 1 if passed else 0
            result = {
                'entry_block': active['entry_block'], 'exit_block': block, 'passed': passed,
                'observation': snapshot['observation'], 'cash_end_usdc': str(cash), 'reason': quote['reason'],
            }
            if quote['reason'] is None:
                result['net_pnl_usdc'] = str(liquidation - CAPITAL)
                result['above_cash_usdc'] = str(liquidation - cash)
                result['tail_pnl_usdc'] = str(net * (1 - EXIT_HAIRCUT - TAIL_HAIRCUT) - CAPITAL)
            state['results'].append(result)
        if quote['reason'] is not None:
            active['awaiting_exit'] = True
            return
        state['active'] = None
        if state['streak'] == TARGET_STREAK:
            state['status'] = 'qualified'
            state['next_at'] = None
            return

    if snapshot['entry'] is None:
        state['streak'] = 0
        return
    state['active'] = position(block, snapshot['entry']['shares_wei'])
    state['next_at'] = block['timestamp'] + DAY


def save_state(path, state):
    with NamedTemporaryFile('w', dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        json.dump(state, out, indent=2)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


@contextmanager
def lock_state(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def initialize(path, source):
    with lock_state(path):
        if path.exists():
            raise ValueError('Existing forward state is already initialized')
        raw = source.read_bytes()
        seed = json.loads(raw)
        protocol = seed['protocol']
        if (protocol['mode'], protocol['simulation_capital_usdc'], protocol['hold_days'], protocol['modeled_entry_cost_bps'],
                protocol['gas_budget_usdc'], protocol['extra_exit_haircut_bps'], protocol['annual_cash_rate']) != (
                'paper_only', str(CAPITAL), HOLD_DAYS, 5, str(GAS_RESERVE), 10, str(CASH_APY)):
            raise ValueError('The frozen entry must match the rolling paper protocol')
        block = seed['entry_block']
        shares = seed['entry']['shares_wei']
        check = verify_capture(block['timestamp'], int(shares), block['timestamp'], block['number'] - 8)
        if (block != check['block'] or check['entry'] != {'deposit_micro_usdc': INVEST, 'shares_wei': shares}
                or check['quote']['reason'] is not None or int(seed['entry']['deposit_micro_usdc']) != INVEST
                or seed['scheduled_paper_exit_utc'] != utc_time(block['timestamp'] + HOLD_DAYS * DAY)):
            raise ValueError('Two-provider replay did not match the original position')
        state = {
            'mode': 'paper_only', 'status': 'running', 'protocol_locked_at_utc': utc_time(time()),
            'initial_entry_sha256': sha256(raw).hexdigest(), 'initial_entry': seed,
            'active': position(block, shares), 'last_block': block, 'next_at': block['timestamp'] + DAY,
            'streak_target': TARGET_STREAK, 'streak': 0, 'observations': [], 'results': [],
        }
        save_state(path, state)
        return state


def tick(path):
    with lock_state(path):
        state = json.loads(path.read_text())
        if state['next_at'] is None or state['next_at'] > time():
            return state, False
        active = state['active']
        target = state['next_at']
        try:
            snapshot = verify_capture(
                target, None if active is None else int(active['shares_wei']),
                target if active is None else active['due'], state['last_block']['number'],
            )
        except DataUnavailable as error:
            if active is None or target >= active['due'] or time() < target + DAY:
                raise
            # A stale interim mark is diagnostic. The terminal checkpoint is mandatory.
            state.setdefault('missing_marks', []).append({'target_utc': utc_time(target), 'reason': str(error)})
            state['next_at'] = min(target + DAY, active['due'])
            save_state(path, state)
            return state, True
        apply_capture(state, snapshot)
        save_state(path, state)
        return state, True


def summary(state):
    return {
        'status': state['status'], 'completed_periods': len(state['results']),
        'streak': state['streak'], 'streak_target': state['streak_target'],
        'next_check_utc': utc_time(state['next_at']) if state['next_at'] else None,
        'first_or_current_checkpoint_utc': utc_time(state['active']['due']) if state['active'] else None,
        'awaiting_exit': state['active']['awaiting_exit'] if state['active'] else False,
        'missing_daily_marks': len(state.get('missing_marks', [])),
        'last_observation': state['observations'][-1] if state['observations'] else None,
        'last_result': state['results'][-1] if state['results'] else None,
    }


def watch(path):
    print(json.dumps(summary(json.loads(path.read_text()))), flush=True)
    retry_delay = 30
    while True:
        try:
            state, changed = tick(path)
        except DataUnavailable as error:
            print(f'{utc_time(time())} {error}', flush=True)
            sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 600)
            continue
        retry_delay = 30
        if changed:
            print(json.dumps(summary(state)), flush=True)
        if state['next_at'] is None:
            return
        sleep(max(2, min(60, state['next_at'] - time())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init').add_argument('entry', type=Path)
    sub.add_parser('status')
    sub.add_parser('tick')
    sub.add_parser('watch')
    args = parser.parse_args()
    if args.command == 'watch':
        watch(args.state)
        return
    if args.command == 'init':
        state = initialize(args.state, args.entry)
    elif args.command == 'tick':
        state, _ = tick(args.state)
    else:
        state = json.loads(args.state.read_text())
    print(json.dumps(summary(state), indent=2))


if __name__ == '__main__':
    main()
