"""Observe the prospective paired limUSD queue and instant paper exits."""

import argparse
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import json
from pathlib import Path
from time import sleep, time

from .liminal_forward import (CAPITAL, CASH_APY, DAY, EXIT_HAIRCUT, HOLD_DAYS, INVEST, MICRO, REDEEM,
                              RPCS, TAIL_HAIRCUT, DataUnavailable, lock_state, rpc, save_state, utc_time,
                              verify_capture)
from .liminal_queued import (IMPLEMENTATION, IMPLEMENTATION_SLOT, LEAD_DAYS, MIN_COMPARABLE_MICRO,
                             collect_details, collect_logs, read_tape, reconstruct)


def initialize(path, seed_path):
    with lock_state(path):
        if path.exists():
            raise ValueError('This prospective cohort already has state')
        raw = seed_path.read_bytes()
        seed = json.loads(raw)
        if (seed['mode'], seed['capital_usdc'], seed['hold_days'], seed['days_in_queue_model'],
                seed['extra_exit_haircut']) != ('paper_only', str(CAPITAL), HOLD_DAYS, LEAD_DAYS, str(EXIT_HAIRCUT)):
            raise ValueError('The entry record does not match the frozen paired study')
        block = seed['captured']['block']
        shares = int(seed['captured']['entry']['shares_wei'])
        confirmation = verify_capture(block['timestamp'], shares, block['timestamp'], block['number'] - 8)
        if (confirmation['block'] != block or confirmation['entry'] != seed['captured']['entry']
                or confirmation['entry']['deposit_micro_usdc'] != INVEST):
            raise ValueError('Could not reproduce the original paired entry')
        deadline = block['timestamp'] + HOLD_DAYS * DAY
        request = deadline - LEAD_DAYS * DAY
        if seed['request_after_utc'] != utc_time(request) or seed['deadline_utc'] != utc_time(deadline):
            raise ValueError('Paired cohort schedule differs from the frozen calendar')
        state = {'status': 'open', 'entry_sha256': sha256(raw).hexdigest(), 'entry': seed,
                 'next_at': request, 'last_block': block, 'marks': [], 'deadline': deadline}
        save_state(path, state)
        return state


def queue_logic_verified(block):
    values = [rpc(endpoint, 'eth_getStorageAt', [REDEEM, IMPLEMENTATION_SLOT, hex(block['number'])]) for endpoint in RPCS]
    if values[0] != values[1]:
        raise DataUnavailable('Redemption implementation differs across providers')
    return '0x' + values[0][-40:] == IMPLEMENTATION


def score(state, tape_dir):
    marks = state['marks']
    terminal = marks[-1]
    initial = state['entry']['captured']['block']['timestamp']
    end = terminal['block']['timestamp']
    elapsed_years = Decimal(end - initial) / (365 * DAY)
    cash_gains = CAPITAL * ((1 + CASH_APY) ** elapsed_years - 1)
    request_at = int(datetime.fromisoformat(state['entry']['request_after_utc']).timestamp())
    quote = terminal['quote']
    result = {'terminal_block': terminal['block'], 'cash_pnl_usdc': str(cash_gains),
              'instant_exit_reason': quote['reason'], 'instant_pnl_usdc': None,
              'queue_actual_settlement': 'unobserved_paper', 'queue_capacity_supported': False,
              'queue_payout_if_settled_usdc': None, 'queue_pnl_if_settled_usdc': None,
              'queue_beats_cash_if_settled_with_observed_capacity': False}
    if quote['reason'] is None:
        net = Decimal(quote['net_micro_usdc']) / MICRO
        result['instant_pnl_usdc'] = str(net * (1 - EXIT_HAIRCUT) - CAPITAL)
        result['instant_tail_pnl_usdc'] = str(net * (1 - EXIT_HAIRCUT - TAIL_HAIRCUT) - CAPITAL)

    request = marks[0]
    if (len(marks) != LEAD_DAYS + 1 or not all(mark['standard_fee_logic_verified'] for mark in marks)
            or request['quote']['reason'] not in {None, 'insufficient_instant_redemption_reserve'}):
        result['queue_status'] = 'request_or_required_marks_unverified'
        return result
    gross = min(Decimal(mark['quote']['gross_micro_usdc']) / MICRO for mark in marks)
    conditional = gross * (1 - EXIT_HAIRCUT)
    result['queue_payout_if_settled_usdc'] = str(conditional)
    result['queue_pnl_if_settled_usdc'] = str(conditional - CAPITAL)
    result['queue_excess_vs_cash_if_settled_usdc'] = str(conditional - CAPITAL - cash_gains)
    if result['instant_pnl_usdc'] is not None:
        result['queue_excess_vs_instant_if_settled_usdc'] = str(conditional - CAPITAL - Decimal(result['instant_pnl_usdc']))
    result['queue_tail_pnl_if_settled_usdc'] = str(gross * (1 - EXIT_HAIRCUT - TAIL_HAIRCUT) - CAPITAL)
    final = terminal['block']['number']
    collect_logs(tape_dir, final)
    collect_details(tape_dir, final, independent_since=request_at)
    events, payouts, _, anchors, evidence, digests = read_tape(tape_dir, final)
    if anchors['blocks'][str(final)] != terminal['block']:
        raise DataUnavailable('Queue logs do not end at the daily capture checkpoint')
    _, settled, _, _ = reconstruct(events, payouts)
    minimum = max(MIN_COMPARABLE_MICRO, int((gross * MICRO).to_integral_value(rounding=ROUND_CEILING)))
    comparable = [episode for episode in settled if episode.requests == 1 and
                  request_at <= episode.requested_at < end and episode.settled_at <= end and
                  episode.assets_micro >= minimum]
    result.update({'queue_capacity_supported': bool(comparable), 'comparable_full_payments': len(comparable),
                   'minimum_comparable_micro_usdc': minimum,
                   'queue_beats_cash_if_settled_with_observed_capacity': bool(comparable) and conditional - CAPITAL > cash_gains,
                   'queue_status': 'capacity_supported' if comparable else 'no_comparable_full_payment',
                   'reference_audit': evidence, 'source_digests': digests})
    return result


def advance(path, tape_dir):
    with lock_state(path):
        state = json.loads(path.read_text())
        if state['status'] == 'complete':
            return state, False
        if state['status'] == 'open':
            target = state['next_at']
            if target > time():
                return state, False
            shares = int(state['entry']['captured']['entry']['shares_wei'])
            snapshot = verify_capture(target, shares, state['deadline'] + DAY, state['last_block']['number'])
            block = snapshot['block']
            snapshot['standard_fee_logic_verified'] = queue_logic_verified(block)
            state['marks'].append(snapshot)
            state['last_block'] = block
            if block['timestamp'] >= state['deadline']:
                state['status'] = 'verifying_capacity'
                state['next_at'] = None
            else:
                next_at = target + DAY
                while next_at <= block['timestamp']:
                    next_at += DAY
                state['next_at'] = min(next_at, state['deadline'])
            save_state(path, state)
            return state, True
        state['result'] = score(state, tape_dir)
        state['status'] = 'complete'
        save_state(path, state)
        return state, True


def summary(state):
    return {'status': state['status'], 'marks': len(state['marks']), 'deadline_utc': utc_time(state['deadline']),
            'next_at_utc': utc_time(state['next_at']) if state['next_at'] else None, 'result': state.get('result')}


def watch(path, tape):
    print(json.dumps(summary(json.loads(path.read_text()))), flush=True)
    while True:
        try:
            state, changed = advance(path, tape)
        except DataUnavailable as error:
            print(f'{utc_time(time())} {error}', flush=True)
            sleep(120)
            continue
        if changed:
            print(json.dumps(summary(state)), flush=True)
        if state['status'] == 'complete':
            return
        next_at = state['next_at'] or time()
        sleep(max(2, min(60, next_at - time())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--tape', type=Path, default=Path('data/agent-liminal-queued'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init').add_argument('entry', type=Path)
    sub.add_parser('status')
    sub.add_parser('tick')
    sub.add_parser('watch')
    args = parser.parse_args()
    if args.command == 'watch':
        watch(args.state, args.tape)
        return
    if args.command == 'init':
        state = initialize(args.state, args.entry)
    elif args.command == 'tick':
        state, _ = advance(args.state, args.tape)
    else:
        state = json.loads(args.state.read_text())
    print(json.dumps(summary(state), indent=2))


if __name__ == '__main__':
    main()
