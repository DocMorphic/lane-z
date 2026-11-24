"""Replay a fixed public-settlement forecast on stable-collateral Boros markets."""

import argparse
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from .boros_lock import ARCHIVE, CASH_PNL, CAPITAL, HOLD, archive_path, markets_for, maturities, read_archive

HOUR = 3600
YEAR_HOURS = Decimal(8760)
WEEK_HOURS = 7 * 24
ENTRY_RATE_STRESS = Decimal('0.01')
COLLATERAL_STRESS = Decimal('0.0025')
MARK_RATE_STRESS = Decimal('0.10')


def read_zip(root, category, market, month, audit, download):
    name = archive_path(category, market, month)
    rows, digest = read_archive(root / name, f'{ARCHIVE}/{name}', download)
    if digest is not None:
        audit[name] = digest
    return rows


def funding_inputs(directories, audit):
    series = {}
    for asset in ('BTC', 'ETH', 'SOL', 'HYPE'):
        hours = {}
        for folder in directories:
            for source in sorted(folder.glob(f'{asset}-funding-*.json')):
                raw = source.read_bytes()
                payments = json.loads(raw)
                audit[str(source)] = sha256(raw).hexdigest()
                for row in payments:
                    event_time = int(row['time'])
                    hour = event_time // 1000 // HOUR * HOUR
                    payment = (event_time, Decimal(row['fundingRate']) * YEAR_HOURS)
                    if row['coin'] != asset or hours.setdefault(hour, payment) != payment:
                        raise ValueError(f'Conflicting settlement observations in {source}')
        series[asset] = hours
    return series


def forecast(hours, decision):
    end = decision - 2 * HOUR
    first = end - 4 * WEEK_HOURS * HOUR
    weekly = []
    for start in range(first, end, WEEK_HOURS * HOUR):
        events = [hours.get(hour) for hour in range(start, start + WEEK_HOURS * HOUR, HOUR)]
        if any(event is None or event[0] >= decision * 1000 for event in events):
            return None
        weekly.append(sum(event[1] for event in events) / WEEK_HOURS)
    return median(weekly)


def quote_rates(row):
    if row is None or row['bestBid'] is None or row['bestAsk'] is None:
        return None
    bid, ask = Decimal(row['bestBid']), Decimal(row['bestAsk'])
    if not bid.is_finite() or not ask.is_finite() or bid > ask:
        return None
    return {-1: bid, 1: ask}


def fees(market):
    return (Decimal(market['config']['takerFee']) / 10**18,
            Decimal(market['extConfig']['settleFeeRate']) / 10**18)


def projection(market, guess, side, quote, term):
    swap_fee, settle_fee = fees(market)
    gain = CAPITAL * (side * (guess - quote) - ENTRY_RATE_STRESS - swap_fee - settle_fee) * term
    return (CAPITAL + gain - 4) * (1 - COLLATERAL_STRESS) - CAPITAL


def replay(market, mark_by_hour, settlement_by_hour, execution, maturity, side, rate, stress):
    term = Decimal((maturity - execution) // HOUR) / YEAR_HOURS
    swap, settle = fees(market)
    fixed = rate + side * ENTRY_RATE_STRESS if stress else rate
    opening_cost = CAPITAL * swap * term + (4 if stress else 2)
    floating = Decimal(0)
    fixed_net = Decimal(0)
    settled_fees = Decimal(0)
    closest = CAPITAL
    worst_balance = CAPITAL
    margin_config = market['config']
    margin_floor = Decimal(str(market['imData']['marginFloor']))
    for hour in range(execution, maturity + HOUR, HOUR):
        if hour > execution:
            floating += side * CAPITAL * settlement_by_hour[hour] / YEAR_HOURS
            fixed_net -= side * CAPITAL * fixed / YEAR_HOURS
            settled_fees += CAPITAL * settle / YEAR_HOURS
        remaining = Decimal(maturity - hour) / (YEAR_HOURS * HOUR)
        equity = CAPITAL - opening_cost + floating + fixed_net - settled_fees
        if hour < maturity:
            mark = Decimal(mark_by_hour[hour]['markApr'])
            adverse = mark - side * MARK_RATE_STRESS if stress else mark
            margin_rate = max(abs(adverse), margin_floor)
            margin_term = max(remaining, Decimal(margin_config['tThresh']) / (YEAR_HOURS * HOUR))
            multiple = Decimal(margin_config['kIM' if hour == execution else 'kMM']) / 10**18
            required = CAPITAL * multiple * margin_rate * margin_term
            equity += side * CAPITAL * (adverse - fixed) * remaining
            closest = min(closest, equity - required)
            worst_balance = min(worst_balance, equity)
            if equity < required:
                return {'status': 'modeled_margin_breach', 'minimum_margin_headroom_usdc': str(closest)}
    collateral_loss = equity * COLLATERAL_STRESS if stress else Decimal(0)
    net = equity - CAPITAL - collateral_loss
    return {'status': 'matured', 'pnl_usdc': str(net), 'excess_vs_cash_usdc': str(net - CASH_PNL),
            'fixed_apr': str(fixed), 'floating_usdc': str(floating), 'fixed_usdc': str(fixed_net),
            'opening_cost_usdc': str(opening_cost), 'settlement_fees_usdc': str(settled_fees),
            'collateral_haircut_usdc': str(collateral_loss), 'minimum_margin_headroom_usdc': str(closest),
            'minimum_marked_balance_usdc': str(worst_balance)}


def run(root, funding_paths, download):
    raw = (root / 'markets-all.json').read_bytes()
    markets = json.loads(raw)
    audit = {'markets-all.json': sha256(raw).hexdigest()}
    histories = funding_inputs(funding_paths, audit)
    windows = []
    streak = longest = 0
    for maturity in list(maturities())[:-1]:
        end = int(maturity.timestamp())
        entry = maturity - HOLD
        decision = int(entry.timestamp())
        execution = decision + HOUR
        term = Decimal((end - execution) // HOUR) / YEAR_HOURS
        contenders = []
        for market in markets_for(markets, maturity):
            asset = market['metadata']['name'].removesuffix('USDC')
            guess = forecast(histories[asset], decision)
            tape = read_zip(root, 'market-data', market, f'{entry:%Y-%m}', audit, download)
            marks = {row['timestamp']: row for row in tape}
            rates = quote_rates(marks.get(decision))
            if guess is None or rates is None:
                continue
            for side, rate in rates.items():
                contenders.append((projection(market, guess, side, rate, term), market, guess, side, marks, rate))
        row = {'entry_utc': entry.isoformat(), 'maturity_utc': maturity.isoformat(),
               'capital_cash_pnl_usdc': str(CASH_PNL), 'supported_directions': len(contenders)}
        best = max(contenders, key=lambda x: x[0], default=None)
        if best is None:
            status = 'no_supported_signal'
        else:
            projected, market, guess, side, marks, original_rate = best
            row |= {'market_id': market['marketId'], 'asset': market['metadata']['name'],
                    'direction': 'receive_floating' if side > 0 else 'receive_fixed', 'forecast_apr': str(guess),
                    'decision_apr': str(original_rate), 'projected_stress_pnl_usdc': str(projected)}
            next_quote = quote_rates(marks.get(execution))
            if projected <= CASH_PNL:
                status = 'forecast_below_cash_hurdle'
            elif next_quote is None or projection(market, guess, side, next_quote[side], term) <= CASH_PNL:
                status = 'delayed_quote_failed'
            else:
                months = sorted({f'{entry:%Y-%m}', f'{maturity:%Y-%m}'})
                for month in months:
                    if month != f'{entry:%Y-%m}':
                        marks.update({event['timestamp']: event for event in
                                      read_zip(root, 'market-data', market, month, audit, download)})
                events = [event for month in months for event in
                          read_zip(root, 'settlement', market, month, audit, download)]
                settlements = {}
                for event in events:
                    hour = event['timestamp'] // HOUR * HOUR
                    if execution < hour <= end:
                        value = Decimal(event['settlementApr'])
                        if event['timestamp'] - hour > 900 or hour in settlements or not value.is_finite():
                            raise ValueError('Ambiguous or late Boros on-chain settlement event')
                        settlements[hour] = value
                required_settlements = set(range(execution + HOUR, end + HOUR, HOUR))
                missing_mark = any(t not in marks or marks[t]['markApr'] is None for t in range(execution, end, HOUR))
                row['settlement_hours'] = len(settlements)
                row['execution_apr_proxy'] = str(next_quote[side])
                if settlements.keys() != required_settlements or missing_mark:
                    status = 'incomplete_maturity_ledger'
                else:
                    row['base'] = replay(market, marks, settlements, execution, end, side, next_quote[side], False)
                    row['stress'] = replay(market, marks, settlements, execution, end, side, next_quote[side], True)
                    status = 'settled_proxy' if row['stress']['status'] == 'matured' else 'modeled_margin_breach'
        passed = status == 'settled_proxy' and Decimal(row['stress']['excess_vs_cash_usdc']) > 0
        streak = streak + 1 if passed else 0
        longest = max(longest, streak)
        windows.append({**row, 'status': status, 'stress_cash_pass': passed})
    return {'capital_usdc': str(CAPITAL), 'longest_cost_pass_streak': longest, 'windows': windows, 'inputs_sha256': audit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--funding', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    result = run(args.data, args.funding, args.fetch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
