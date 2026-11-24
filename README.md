# Lane Z

Paper research for Hyperliquid equity perpetuals, crypto derivatives, and longer-horizon allocations.

**Result as of November 23, 2026.** A paper allocation to limUSD, an externally managed Hyperliquid yield portfolio, beat compounded 4% cash in three consecutive 63-day periods after modeled fees and redemptions. The stressed excess was 0.42, 2.69 and 4.06 USDC on 2,000 USDC each. Historical on-chain quotes and reserves support those paper results. Lane Z’s own trading rules fell short of the repeated cash hurdle. [limUSD replay and audit](docs/liminal-allocation.md)

A separate, prospective limUSD paper run began on November 23. The fixed rolling test measures full-size redemption and after-cost excess over 4% cash for three consecutive 63-day periods. [Forward protocol and runner](docs/liminal-forward.md)

A paired paper cohort opened on November 24 to compare seven-day standard redemption with instant redemption on the same entry. In the three historical windows, the queue model improved stressed paper P&L by 2.70 to 3.65 USDC on 2,000 USDC if settled by the deadline. Verified third-party payouts established comparable throughput in each window. [Historical queue audit](docs/liminal-queued.md) · [Paired forward test](docs/liminal-queue-forward.md)

## Longer-horizon paper tests

The simplest funding-carry rule held equal spot and short perpetual quantities. It used observed hourly funding rates, four-hour price proxies, full short collateral, and simulated trading costs. The higher-cost scenario with a 25% haircut to positive funding earned 9.03, 2.85, and 14.44 USDC in the three windows, each on 2,000 USDC of starting capital. A forced-exit stress lost money. There were no live orders. [Funding results, risks, and reproduction](docs/spot-carry.md)

Separate tests retired [equity-perp relative strength](docs/relative-strength.md), [NSE ETF timing](docs/nse-trend.md), and [dated BTC futures carry](docs/dated-futures-carry.md) after their holdout or execution-stress results. A [BTC/ETH spot breakout](docs/crypto-breakout.md) reduced drawdown relative to buy-and-hold. Its separate 2026 run lost money.

Additional tests retired [native trend](docs/native-trend.md), [selective carry and HYPE staking](docs/selective-carry.md), [daily relative basis](docs/perp-basis.md), and a funding-gated [equity index and gold portfolio](docs/hip3-allocation.md). They include explicit held-out cash hurdles, full capital accounting and separate stress results.

The [HLP vault](docs/hlp-vault.md) beat cash in its first five periods and missed in two later quarters. Nasdaq [QQQ timing](docs/nasdaq-trend.md) cleared cash in four separate stressed vintages. Passive QQQ earned more in three of those vintages. The [overnight equity-index trade](docs/hip3-overnight.md), [cross-venue crypto carry](docs/cross-venue-carry.md), [JitoSOL hedge](docs/jito-hedge.md), and [cross-asset trend basket](docs/managed-futures.md) fell short. All tests were paper-only.

The Boros funding lock and funding forecast missed the cash hurdle. HyperLend and Felix USDC missed in the latest stressed window. The xHYPE and xLEND allocations passed cash in all three historical windows and trailed limUSD in at least one. The Pendle PT-limUSD screen had one expiry and no completed maturity at review. [Funding lock](docs/boros-lock.md) · [Funding forecast](docs/boros-floating.md) · [HyperLend](docs/hyperlend-usdc.md) · [Felix](docs/felix-usdc.md) · [xTokens](docs/hyperliquid-xtokens.md) · [Pendle PT](docs/pendle-pt.md)

## The millisecond strategy

Every 250 ms, a ridge model estimates the move in the Hyperliquid midpoint after the simulated entry arrives. It combines the target's own book pressure, XYZ100 and peer moves, and, in the strongest version, the Binance perp's returns and short-term basis for the same stock. Its paper orders cross the book only when the forecast clears the observed spread, the fee reserve, and a slippage buffer. The Binance arm's reference policy uses a 5,000 ms hold and 250 ms execution delay per leg. It takes one position at a time per symbol.

| Held-out experiment | Simulated round trips | Net P&L | Net bps on filled entry notional |
| --- | ---: | ---: | ---: |
| Regular session, Hyperliquid signals only | 0 | $0.00 | — |
| Weekend/holiday model, Hyperliquid signals only | 0 | $0.00 | — |
| Binance features, 250 ms order delay | 56 | +$0.64 | +1.84 |
| Binance features, 500 ms order delay | 8 | +$0.10 | +2.21 |
| Binance features, 900 ms order delay | 4 | −$0.13 | −4.23 |
| Binance features, 125 ms added Binance feed delay | 34 | −$0.05 | −0.27 |
| Binance features, 250 ms added Binance feed delay | 10 | −$0.14 | −2.76 |

The positive case uses two held-out regular sessions, roughly $100 requested per order, sampled best quotes, and 0.9 bps taker fees per side. It filled 56 of 140 attempted entries, with 32 partial fills. The weekend model has one held-out session. [Full results](docs/results.md) · [Study protocol](docs/protocol.md) · [Research and sources](docs/research.md)

## Follow-up venue tests

The frozen Binance model made no trades in the first live Saturday window. A separate reversal rule lost money in paper tests on Hyperliquid, Binance, and Lighter, and a five-second cash queue strategy lost money on the public NYSE Arca sample. [Venue comparison and commands](docs/venue-comparison.md)

India’s NSE cash screen earned ₹7,766 in its September–November price-proxy holdout and lost ₹12,793 in a separate January–May check. [NSE India results and data](docs/nse-india.md)

The market-maker response test added aggressive trades, changes in displayed depth, and Binance repricing to the Lighter model. No forecast met the executable cost gate in validation or on the holdout. [Market maker research and results](docs/market-makers.md)

The two-venue hedge made two attempts. On both, only one leg filled and had to be closed. A current Polymarket snapshot found no complete binary outcome set below its payout before fees. [Hedged paper trades](docs/hedges.md)

## Reproduce the study

Use Python 3.11 or newer. The downloader fetches about 250 MB of compressed first-of-month public samples across all three arms. It keeps quote files in the ignored `data/` directory. The reports record each file's SHA-256.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests

for regime in regular internal binance; do
  python -m lane_z.study fetch --regime "$regime"
  python -m lane_z.study run --regime "$regime"
done
```

## Replay a fresh paper session

The public recorder uses Node.js 22.4+ and records both exchanges from a common local clock. Start a session capture around 09:30 New York time and keep it running through 15:57. Use a new output directory per capture. The command takes the output directory and the capture duration in seconds.

```sh
node scripts/capture.mjs data/forward-quotes 24000
python -m lane_z.forward YYYY-MM-DD --data data/forward-quotes \
  --report results/binance.json --output data/forward-result.json
```

Replace `YYYY-MM-DD` with the captured New York session date. Use fresh U.S. regular-session dates for the Binance model. The replay loads the published weights without refitting and labels the result as an unqualified reference diagnostic. Use `--delay-ms` to change the simulated order delay. The recorder and replay use public data and paper positions.
