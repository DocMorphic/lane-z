# Lane Z

Millisecond-scale paper research for Trade[XYZ] NVDA and TSLA equity perpetuals on Hyperliquid.

**Result as of November 21, 2026.** Same-stock Binance quotes improved short-horizon prediction and produced a small positive paper result under the fastest execution assumption. The validation sample was too small to qualify, and slower inputs or orders erased the gains. The strategy selected by the study is to stay flat.

## The strategy

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
