"""Replay a fixed three-minute public Hyperliquid breakout in paper only."""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time
from hashlib import sha256
import json
from math import isfinite, log
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

from .spot_carry import HOUR, PublicArchive, stamp


COINS = ('xyz:XYZ100', 'xyz:NVDA', 'xyz:SILVER')
WINDOWS = (('2026-11-16', '2026-11-17'), ('2026-11-18', '2026-11-19'), ('2026-11-20', '2026-11-23'))
MARKET_TZ = ZoneInfo('America/New_York')
BAR, BPS, LOT = 180_000, 10_000, 100
FILL_CASES = {'gross': (1, 0, 0), 'fee_only': (1, .9, 0), 'base': (1, .9, 1), 'stress': (2, .9, 2.5)}


@dataclass(frozen=True)
class Candle:
    opening: float
    close: float
    high: float
    low: float
    volume: float


def session(day):
    label = date.fromisoformat(day)
    return tuple(int(datetime.combine(label, edge, MARKET_TZ).timestamp() * 1000)
                 for edge in (time(9, 30), time(16)))


def fetch(root):
    archive = PublicArchive(root)
    archive.get('meta.json', {'type': 'meta', 'dex': 'xyz'}, 20)
    start, end = session(WINDOWS[0][0])[0], session(WINDOWS[-1][-1])[1] - 1
    for coin in COINS:
        archive.get(f'{coin[4:]}-3m.json', {'type': 'candleSnapshot', 'req': {
            'coin': coin, 'interval': '3m', 'startTime': start, 'endTime': end}}, 78)
        archive.get(f'{coin[4:]}-book.json', {'type': 'l2Book', 'coin': coin}, 2)


def read(root):
    hashes = {}

    def load(name):
        payload = (root / name).read_bytes()
        hashes[name] = sha256(payload).hexdigest()
        return json.loads(payload)

    meta = load('meta.json')
    if meta['collateralToken'] != 0:
        raise ValueError('XYZ collateral is not native USDC')
    states, candles = {}, {}
    for coin in COINS:
        product, recorded = next(row for row in meta['universe'] if row['name'] == coin), {}
        book = load(f'{coin[4:]}-book.json')
        bid, ask = (float(side[0]['px']) for side in book['levels'])
        if not 0 < bid <= ask:
            raise ValueError(f'Invalid reference spread for {coin}')
        states[coin] = {'growth_mode': product.get('growthMode'), 'delisted': product.get('isDelisted', False),
                        'deployer_fee_scale': product.get('deployerFeeScale'),
                        'last_fee_scale_change': product.get('lastFeeScaleChangeTime'),
                        'snapshot_spread_bps': 2 * BPS * (ask - bid) / (ask + bid), 'snapshot_time': stamp(book['time'])}
        for row in load(f'{coin[4:]}-3m.json'):
            t = row['t']
            opening, close, high, low, volume = values = tuple(float(row[k]) for k in ('o', 'c', 'h', 'l', 'v'))
            if (row['s'] != coin or row['i'] != '3m' or t % BAR or row['T'] != t + BAR - 1 or t in recorded
                    or not all(isfinite(v) for v in values) or not 0 < low <= min(opening, close) <= max(opening, close) <= high
                    or volume < 0 or row['n'] < 0):
                raise ValueError(f'Invalid {coin} candle at {stamp(t)}')
            if row['n'] and volume:
                recorded[t] = Candle(*values)
        candles[coin] = recorded
    return candles, states, hashes


def detect(bars, start, end):
    candidates = []
    for current in range(start + 20 * BAR, end, BAR):
        prior = [bars.get(t) for t in range(current - 20 * BAR, current, BAR)]
        signal = bars.get(current)
        if signal is None or any(bar is None for bar in prior):
            continue
        body = BPS * log(signal.close / signal.opening)
        if abs(body) < 8 or signal.volume < 1.5 * median(bar.volume for bar in prior):
            continue
        if body > 0 and signal.close > max(bar.high for bar in prior[-5:]):
            candidates.append((current, 1))
        if body < 0 and signal.close < min(bar.low for bar in prior[-5:]):
            candidates.append((current, -1))
    return candidates


def score(bars, candidates, start, end, lag, fee_bps, price_bps):
    trades, ready = [], start
    for signal, side in candidates:
        entry, exit = signal + lag * BAR, signal + (lag + 1) * BAR
        if signal + BAR <= ready or exit >= end or entry // HOUR != exit // HOUR:
            continue
        if any(t not in bars for t in range(signal, exit + BAR, BAR)):
            continue
        paid = bars[entry].opening * (1 + side * price_bps / BPS)
        received = bars[exit].opening * (1 - side * price_bps / BPS)
        ratio = received / paid
        trades.append(BPS * side * (ratio - 1) - fee_bps * (1 + ratio))
        ready = exit
    return trades


def summarize(trades):
    return {'trades': len(trades), 'mean_bps': mean(trades) if trades else None,
            'net_usdc_at_100_notional_per_trade': LOT / BPS * sum(trades),
            'positive_trades': sum(result > 0 for result in trades)}


def run(root):
    bars, metadata, hashes = read(root)
    windows = []
    for days in WINDOWS:
        pnl = {name: {coin: [] for coin in COINS} for name in FILL_CASES}
        coverage, candidates = {}, {}
        for day in days:
            start, end = session(day)
            coverage[day], candidates[day] = {}, {}
            for coin in COINS:
                coverage[day][coin] = sum(t in bars[coin] for t in range(start, end, BAR))
                signals = detect(bars[coin], start, end)
                candidates[day][coin] = len(signals)
                for name, params in FILL_CASES.items():
                    pnl[name][coin].extend(score(bars[coin], signals, start, end, *params))
        cases = {name: {'basket': summarize([trade for trades in per_coin.values() for trade in trades]),
                        'markets': {coin: summarize(trades) for coin, trades in per_coin.items()}}
                 for name, per_coin in pnl.items()}
        windows.append({'dates_in_new_york': days, 'valid_session_bars_out_of_130': coverage,
                        'raw_signals': candidates, 'cases': cases})
    fees_confirmed = all(row['growth_mode'] == 'enabled' and not row['delisted']
                         and row['deployer_fee_scale'] is not None and 0 <= float(row['deployer_fee_scale']) <= 1
                         and row['last_fee_scale_change'] and row['last_fee_scale_change'][:10] < WINDOWS[0][0]
                         for row in metadata.values())
    qualifies = fees_confirmed and all(
        window['cases'][name]['basket']['trades'] >= 30
        and window['cases'][name]['basket']['net_usdc_at_100_notional_per_trade'] > 0
        for window in windows for name in ('base', 'stress'))
    return {'growth_fee_assumption_supported': fees_confirmed,
            'current_metadata_and_reference_book': metadata, 'windows': windows, 'input_sha256': hashes,
            'passes_candle_screen': qualifies}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch', 'run'))
    parser.add_argument('--data', type=Path, default=Path('data/agent-hip3-volume-breakout'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'fetch':
        fetch(args.data)
        return
    report = json.dumps(run(args.data), indent=2) + '\n'
    if args.output:
        args.output.write_text(report)
    else:
        print(report, end='')


if __name__ == '__main__':
    main()
