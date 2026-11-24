"""Screen a fully funded Hyperliquid spot/perp position with a fixed Boros rate."""

import argparse
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import ZipFile

API = 'https://api.boros.finance/apis/v1/markets'
ARCHIVE = 'https://historical-data.boros.finance'
ASSETS = {'BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'HYPEUSDC'}
STABLE_TOKEN_ID = 3  # USD₮0 on Boros
CAPITAL = Decimal(2000)
NOTIONAL = Decimal(800)
HOLD = timedelta(days=28)
TERM = Decimal(HOLD.days) / 365
HL_FEES = NOTIONAL * Decimal('0.0023')
CASH_PNL = CAPITAL * (Decimal('1.04') ** TERM - 1)
FIXED_AND_HL_EXECUTION_STRESS = 4 + NOTIONAL * Decimal('0.004')
OBSERVATION_CUTOFF = datetime(2026, 11, 24, tzinfo=UTC)
HEADERS = {'User-Agent': 'Lane-Z paper research'}


def maturities():
    for month in range(1, 12):
        end = datetime(2026, month, monthrange(2026, month)[1], tzinfo=UTC)
        yield end - timedelta(days=(end.weekday() - 4) % 7)


def market_slug(market):
    return f'{market["marketId"]}-{market["imData"]["symbol"]}'


def archive_path(category, market, month):
    return f'{category}/{market_slug(market)}/{month}.ndjson.zip'


def read_archive(path, url, download):
    if download and not path.exists():
        try:
            with urlopen(Request(url, headers=HEADERS), timeout=30) as response:
                raw = response.read()
        except HTTPError as error:
            if error.code == 404:
                return [], None
            raise
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if not path.exists():
        return [], None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith('.ndjson')]
        if len(names) != 1:
            raise ValueError(f'Invalid archive layout for {path.name}')
        with archive.open(names[0]) as tape:
            return [json.loads(raw, parse_float=Decimal) for raw in tape], digest


def fetch_markets(directory):
    rows = []
    token = None
    while True:
        suffix = '?' + urlencode({'resumeToken': token}) if token else ''
        with urlopen(Request(API + suffix, headers=HEADERS), timeout=30) as response:
            page = json.load(response)
        rows.extend(page['results'])
        next_token = page.get('resumeToken')
        if not next_token:
            break
        if next_token == token:
            raise ValueError('Boros market pagination did not advance')
        token = next_token
    (directory / 'markets-all.json').write_text(json.dumps(rows) + '\n')
    return rows


def markets_for(rows, maturity):
    timestamp = int(maturity.timestamp())
    return [market for market in rows if market['tokenId'] == STABLE_TOKEN_ID
            and market['metadata']['name'] in ASSETS and not market['metadata']['isSpreadMarket']
            and market['imData']['symbol'].startswith('HYPERLIQUID-')
            and market['imData']['maturity'] == timestamp and market['extConfig']['paymentPeriod'] == 3600]


def archive_quote(directory, market, entry, download):
    name = archive_path('market-data', market, f'{entry:%Y-%m}')
    path = directory / name
    url = f'{ARCHIVE}/{name}'
    result = {'market_id': market['marketId'], 'market': market_slug(market), 'source': url}
    rows, digest = read_archive(path, url, download)
    if digest is None:
        return {**result, 'status': 'archive_not_exported' if download else 'archive_not_cached'}
    result['sha256'] = digest
    target = int(entry.timestamp())
    quote = next((row for row in rows if row['timestamp'] == target), None)
    if quote is None or quote['bestBid'] is None:
        return {**result, 'status': 'missing_scheduled_bid'}
    apr = Decimal(quote['bestBid'])
    if not apr.is_finite():
        raise ValueError(f'Invalid best bid for {slug}')
    gross = apr * NOTIONAL * TERM
    swap_cost = NOTIONAL * Decimal(market['config']['takerFee']) / 10**18 * TERM
    settlement_cost = NOTIONAL * Decimal(market['extConfig']['settleFeeRate']) / 10**18 * TERM
    return {
        **result, 'status': 'bid_observed', 'arbitrum_block': quote['blockNumber'], 'fixed_bid_apr': str(apr),
        'gross_fixed_approx_usdc': str(gross), 'optimistic_net_usdc': str(gross - HL_FEES),
        'boros_taker_usdc': str(swap_cost), 'boros_settlement_usdc': str(settlement_cost),
        'cost_aware_net_usdc': str(gross - HL_FEES - swap_cost - settlement_cost - FIXED_AND_HL_EXECUTION_STRESS),
    }


def run(directory, rows, download):
    dates = list(maturities())
    market_sets = [markets_for(rows, maturity) for maturity in dates]
    with ThreadPoolExecutor(max_workers=6) as workers:
        pending = [[workers.submit(archive_quote, directory, market, maturity - HOLD, download) for market in markets]
                   for maturity, markets in zip(dates, market_sets, strict=True)]
        windows = []
        streak = longest = 0
        for expiry, tasks in zip(dates, pending, strict=True):
            values = [task.result() for task in tasks]
            bids = [row for row in values if row['status'] == 'bid_observed']
            best = max(bids, key=lambda row: Decimal(row['cost_aware_net_usdc']), default=None)
            matured = expiry <= OBSERVATION_CUTOFF
            passed = matured and best is not None and Decimal(best['cost_aware_net_usdc']) > CASH_PNL
            streak = streak + 1 if passed else 0
            longest = max(longest, streak)
            windows.append({'entry_utc': (expiry - HOLD).isoformat(), 'maturity_utc': expiry.isoformat(),
                            'matured_at_cutoff': matured, 'candidates': values,
                            'selected_market': best['market'] if best else None, 'cash_pnl_usdc': str(CASH_PNL),
                            'cost_screen_pass': passed})
    return {'cutoff_utc': OBSERVATION_CUTOFF.isoformat(), 'full_starting_capital_usdc': str(CAPITAL),
            'common_notional_usdc': str(NOTIONAL), 'longest_matured_cost_pass_streak': longest,
            'threshold_for_full_replay_met': longest >= 3, 'windows': windows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    markets = args.data / 'markets-all.json'
    rows = fetch_markets(args.data) if args.fetch and not markets.exists() else json.loads(markets.read_text())
    report = run(args.data, rows, args.fetch)
    report['markets_sha256'] = hashlib.sha256(markets.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
