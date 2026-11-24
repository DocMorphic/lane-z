"""Replay the frozen 30-second native Hyperliquid flow experiment."""

import argparse
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
import gzip
from hashlib import sha256
import json
from math import floor, isfinite, log
from pathlib import Path


COINS = ('BTC', 'ETH', 'SOL', 'HYPE', 'DOGE', 'SUI')
GRID = 250
AGE = 1_000
HOLD = 30_000
HOUR = 3_600_000
BPS = 10_000
NOTIONAL = 100
MIN_ORDER = 10
FEE = 4.5
SCENARIOS = ((250, 1), (500, 1), (900, 1), (900, 2))


@dataclass(frozen=True)
class Book:
    received: int
    venue: int
    bids: tuple
    asks: tuple

    @property
    def mid(self):
        return (self.bids[0][0] + self.asks[0][0]) / 2

    @property
    def spread(self):
        return (self.asks[0][0] - self.bids[0][0]) / self.mid * BPS


@dataclass(frozen=True)
class Signal:
    time: int
    side: int
    book: Book


def utc(ms):
    return datetime.fromtimestamp(ms / 1_000, UTC).isoformat(timespec='milliseconds')


def load_capture(path):
    books = {coin: [] for coin in COINS}
    trades = {coin: [] for coin in COINS}
    counts = {coin: {'books_dropped': 0, 'trades_dropped': 0} for coin in COINS}
    seen = set()
    start = end = None
    book_channel = None
    previous = -1
    with gzip.open(path, 'rt') as source:
        for line in source:
            row = json.loads(line)
            received, channel, payload = (row[k] for k in ('received_ms', 'channel', 'data'))
            if received < previous or end is not None:
                raise ValueError('Unordered capture')
            previous = received
            if channel == 'connected' and start is None and tuple(payload) == COINS:
                start = received
            elif channel == 'ended' and start is not None and not payload['incomplete']:
                end = received
            elif channel in ('l2Book', 'bbo') and start is not None:
                if book_channel is not None and book_channel != channel:
                    raise ValueError('Capture mixed book channels')
                book_channel = channel
                coin, venue = payload['coin'], payload['time']
                levels = payload['levels'] if channel == 'l2Book' else [[level] for level in payload['bbo']]
                bids, asks = [tuple((float(level['px']), float(level['sz'])) for level in side if level is not None)
                              for side in levels]
                if (not 0 <= received - venue <= AGE or not bids or not asks or bids[0][0] >= asks[0][0]
                        or books[coin] and venue < books[coin][-1].venue
                        or any(not isfinite(px * sz) or min(px, sz) <= 0 for px, sz in (*bids, *asks))):
                    counts[coin]['books_dropped'] += 1
                    continue
                books[coin].append(Book(received, venue, bids, asks))
            elif channel == 'trades' and start is not None:
                for trade in payload:
                    coin, venue, tid = (trade[k] for k in ('coin', 'time', 'tid'))
                    key = coin, venue, tid
                    gross = float(trade['px']) * float(trade['sz'])
                    if (not 0 <= received - venue <= AGE or key in seen or trade['side'] not in ('A', 'B')
                            or not isfinite(gross) or gross <= 0):
                        counts[coin]['trades_dropped'] += 1
                        continue
                    seen.add(key)
                    trades[coin].append((received, gross, gross if trade['side'] == 'B' else -gross))
            else:
                raise ValueError('Invalid or incomplete capture')
    if start is None or end is None or end - start < HOLD or any(not books[coin] for coin in COINS):
        raise ValueError('Missing complete capture or market coverage')
    return start, end, book_channel, books, trades, counts


def asof(books, times, target):
    index = bisect_right(times, target) - 1
    return books[index] if index >= 0 and target - books[index].venue <= AGE else None


def signals(start, end, books, trades):
    by_receipt = [book.received for book in books]
    receipts = [trade[0] for trade in trades]
    prefixes = [(0.0, 0.0)]
    for _, gross, signed in trades:
        prefixes.append((prefixes[-1][0] + gross, prefixes[-1][1] + signed))

    def flow(target, window):
        left = bisect_right(receipts, target - window)
        right = bisect_right(receipts, target)
        gross, signed = (prefixes[right][i] - prefixes[left][i] for i in range(2))
        return right - left, gross, signed

    output = {'reversal': [], 'continuation': [], 'fresh_pairs': 0}
    pending = None
    prev_impulse = 0
    for now in range((start + 3_000 + GRID - 1) // GRID * GRID, end, GRID):
        current = asof(books, by_receipt, now)
        earlier = asof(books, by_receipt, now - 3_000)
        if pending is not None and now > pending[0] + 4_000:
            pending = None
        impulse = 0
        if current is not None and earlier is not None:
            output['fresh_pairs'] += 1
            move = log(current.mid / earlier.mid) * BPS
            side = 1 if move > 0 else -1
            count, gross, signed = flow(now, 3_000)
            if abs(move) >= 10 and count >= 4 and side * signed >= .60 * gross:
                impulse = side
                if impulse != prev_impulse:
                    if current.spread <= 5:
                        output['continuation'].append(Signal(now, side, current))
                    if pending is None:
                        pending = (now, side, current.mid)
        prev_impulse = impulse
        if pending is None or current is None or now == pending[0]:
            continue
        began, side, extreme = pending
        if side * current.mid > side * extreme:
            extreme = current.mid
            pending = (began, side, extreme)
        rebound = -side * log(current.mid / extreme) * BPS
        if rebound >= 2.5 and current.spread <= 5:
            count, gross, signed = flow(now, 1_000)
            if count >= 2 and -side * signed >= .25 * gross:
                output['reversal'].append(Signal(now, -side, current))
                pending = None
    return output


def sweep(book, side, lots, lot, limit):
    remaining = lots
    notional = 0.0
    for price, size in book.asks if side > 0 else book.bids:
        if side * (price - limit) > 0:
            break
        take = min(remaining, floor(size / lot + 1e-9))
        notional += price * take * lot
        remaining -= take
        if remaining == 0:
            break
    return lots - remaining, notional


def replay(books, signals_to_score, lot, end, delay, haircut):
    # Replace duplicate snapshots at the same venue time with the last observation.
    sequence = {book.venue: book for book in books}
    venues = sorted(sequence)
    available = [sequence[venue] for venue in venues]
    results = {'attempts': 0, 'missed_entries': 0, 'partial_entries': 0, 'unresolved_exits': 0,
               'eligible_signals': 0, 'trades': []}
    ready = 0

    def at_arrival(target, deadline):
        snapshot = asof(available, venues, target)
        if snapshot is not None:
            return target, snapshot
        index = bisect_right(venues, target)
        if index < len(venues) and venues[index] <= min(target + AGE, deadline):
            return venues[index], available[index]
        return None, None

    for signal in signals_to_score:
        deadline = min(end, (signal.time // HOUR + 1) * HOUR - 1)
        if signal.time + HOLD + 2 * (delay + AGE) > deadline:
            continue
        results['eligible_signals'] += 1
        if signal.time < ready:
            continue
        results['attempts'] += 1
        entry_time, entry_book = at_arrival(signal.time + delay, deadline)
        touch = signal.book.asks[0][0] if signal.side > 0 else signal.book.bids[0][0]
        limit = touch * (1 + signal.side * 5 / BPS)
        # Buy quantities use the worst allowed price. Short quantities are at most
        # 100 at the decision touch, with the fill-based notional also checked.
        requested = floor(NOTIONAL / (max(touch, limit) * lot))
        qty, entry = sweep(entry_book, signal.side, requested, lot, limit) if entry_book else (0, 0.0)
        ready = entry_time if entry_time is not None else signal.time + delay + AGE
        if entry < MIN_ORDER or entry > NOTIONAL:
            results['missed_entries'] += 1
            continue
        results['partial_entries'] += qty != requested
        exited = 0
        proceeds = 0.0
        target = entry_time + HOLD + delay
        exit_time, book = at_arrival(target, deadline)
        while book is not None and exit_time <= deadline:
            filled, cash = sweep(book, -signal.side, qty - exited, lot,
                                 float('inf') if signal.side < 0 else 0)
            exited += filled
            proceeds += cash
            if exited == qty:
                break
            # Only a later venue snapshot can supply residual displayed depth.
            index = bisect_right(venues, max(book.venue, exit_time + delay - 1))
            if index == len(venues):
                break
            exit_time, book = venues[index], available[index]
        if exited != qty:
            results['unresolved_exits'] += 1
            ready = deadline
            continue
        ready = exit_time
        gross = signal.side * (proceeds - entry)
        fees = (entry + proceeds) * FEE / BPS
        slippage = (entry + proceeds) * haircut / BPS
        results['trades'].append({'decision_utc': utc(signal.time), 'entry_utc': utc(entry_time),
                                  'exit_utc': utc(exit_time), 'side': signal.side, 'lot_count': qty,
                                  'entry_usdc': entry, 'gross_usdc': gross, 'fees_usdc': fees,
                                  'extra_slippage_usdc': slippage, 'net_usdc': gross - fees - slippage})
    return results


def run(path, meta):
    start, end, book_channel, books, trades, counts = load_capture(path)
    decimals = {asset['name']: asset['szDecimals'] for asset in json.loads(meta.read_bytes())['universe']
                if asset['name'] in COINS and not asset.get('isDelisted', False)}
    if set(decimals) != set(COINS):
        raise ValueError('Missing active lot sizes')
    policies = {coin: signals(start, end, books[coin], trades[coin]) for coin in COINS}
    cases = {}
    money = ('entry_usdc', 'gross_usdc', 'fees_usdc', 'extra_slippage_usdc', 'net_usdc')
    for name in ('reversal', 'continuation'):
        scenarios = {}
        for delay, haircut in SCENARIOS:
            markets = {coin: replay(books[coin], policies[coin][name], 10 ** -decimals[coin], end, delay, haircut)
                       for coin in COINS}
            completed = [trade for market in markets.values() for trade in market['trades']]
            scenarios[f'{delay}ms_{haircut}bp'] = {
                **{key: sum(market[key] for market in markets.values()) for key in
                   ('attempts', 'missed_entries', 'partial_entries', 'unresolved_exits', 'eligible_signals')},
                **{key: sum(trade[key] for trade in completed) for key in money},
                'completed': len(completed), 'markets': markets,
            }
        cases[name] = scenarios
    return {'mode': 'paper_only', 'book_channel': book_channel, 'capture': path.name, 'sha256': sha256(path.read_bytes()).hexdigest(),
            'metadata_sha256': sha256(meta.read_bytes()).hexdigest(), 'start_utc': utc(start), 'end_utc': utc(end),
            'taker_fee_bps_per_leg': FEE, 'lot_decimals': decimals,
            'coverage': {coin: {**counts[coin], 'books': len(books[coin]), 'trades': len(trades[coin]),
                                'median_book_lag_ms': sorted(b.received-b.venue for b in books[coin])[len(books[coin])//2],
                                'grid_with_fresh_price_pair': policies[coin]['fresh_pairs'],
                                'reversal_onsets': len(policies[coin]['reversal']),
                                'continuation_onsets': len(policies[coin]['continuation'])} for coin in COINS},
            'scenarios': cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--meta', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.capture, args.meta)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as out:
        json.dump(report, out, indent=2, allow_nan=False)
        out.write('\n')


if __name__ == '__main__':
    main()
