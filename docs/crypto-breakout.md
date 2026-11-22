# Multi-month crypto breakout paper test

Lane Z's overnight tests paid costs on seconds of potential price movement. This independent test looks for moves that persist over weeks or months. Research documents time-series momentum in futures across several asset classes and in cryptocurrency returns. Neither study tested the Coinbase spot rule below. [Moskowitz, Ooi, and Pedersen](https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data) · [Liu and Tsyvinski](https://www.nber.org/papers/w24877)

## Frozen protocol

- Start with $1,000 split equally between BTC/USD and ETH/USD. Each sleeve is fully funded and holds either USD cash or the asset. Profits stay in their sleeve. The simulation uses no leverage, borrowing, or funding payments.
- At the end of a UTC daily bar, enter when its close exceeds the highest close of the preceding 100 days. Exit when its close falls below the lowest close of the preceding 50 days. Otherwise keep the current signal. Allow a full day after the signal becomes known before using the following daily opening print as the fill reference. Use the terminal opening print to liquidate and charge exit costs. At a sample boundary each sleeve starts with its own $500 and acts on the already observable signal.
- Model 60 bps taker fees and a 20 bps spread, impact, and slippage reserve on every fill. Stress the reserve at 90 bps per fill, for a 150 bps total charge each way. Treat first trades in the daily bar as fill proxies. They contain no order-book size, queue, or millisecond receipt timing. [Coinbase candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles) · [Coinbase Exchange fee tiers](https://help.coinbase.com/en/exchange/trading-and-funding/exchange-fees)
- Coinbase provides the main two-asset history. The development window runs from January 1, 2018 through the January 1, 2021 open. The first historical holdout runs January 1, 2021 through the January 1, 2024 open. The second starts January 1, 2024 and ends at the November 22, 2026 open. Report each holdout separately and split the latest one by year as a diagnostic. The rule and cost scenarios are fixed before reading strategy returns.
- Check the same rule on Kraken BTC/USD and ETH/USD from April 1, 2025 through the November 22, 2026 open. Its public API returns up to 720 daily candles, which supplies the warmup and defines the shorter window. Both exchanges are exposed to the same market trend. This is a price-source and venue check. The two Coinbase holdouts provide the time separation. [Kraken OHLC endpoint](https://docs.kraken.com/api/docs/rest-api/get-ohlc-data/)
- Compare each run against buy and hold of the same two assets with the same entry and exit costs, zero-return USD cash, and a fixed 4% annual cash hurdle. Report after-cost return, maximum daily marked drawdown, time invested, and fill count. A positive spot backtest can reflect the assets' appreciation. The benchmarks distinguish that from improvement due to timing.

## Results

Each period starts at $1,000. Net paper P&L includes terminal liquidation, taker fees, and the reserve. Drawdown uses daily opening-price marks. The Kraken check uses the same conservative cost assumption as Coinbase for comparison.

| Period | Breakout net | Breakout net, cost stress | Buy and hold net | 4% cash hurdle | Maximum drawdown, breakout / buy and hold |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coinbase 2018–2020, development | +$1,378.78 | +$1,238.45 | +$518.82 | +$124.98 | 56.65% / 88.20% |
| Coinbase 2021–2023, first holdout | +$1,192.13 | +$1,033.70 | +$1,238.90 | +$124.86 | 58.55% / 77.40% |
| Coinbase 2024–November 2026, second holdout | +$354.49 | +$265.51 | +$62.06 | +$120.16 | 28.40% / 58.59% |
| Coinbase 2026 alone | −$59.07 | −$72.15 | −$383.06 | +$35.54 | 14.01% / 46.50% |
| Kraken April 2025–November 2026, venue check | +$30.71 | −$3.29 | −$187.38 | +$66.60 | 28.31% / 61.71% |

The rule earned positive returns in both multi-year holdouts under either cost assumption. Buy and hold earned more in the first holdout. The second holdout return depends on the earlier part of the window. In the separate 2026 run the rule lost money, and the venue check missed the cash hurdle at both costs. The test supports keeping the rule as a benchmark for reducing drawdowns. The recent profit result is too weak to establish a current candidate. Both assets can fall together, and a daily marked drawdown can miss larger intraday excursions.

[Machine-readable results and source hashes](../results/follow-up/crypto-breakout.json)

```sh
python -m lane_z.crypto_breakout fetch --data data/crypto-breakout
python -m lane_z.crypto_breakout run --data data/crypto-breakout --output data/crypto-breakout-check.json
```

The raw API responses are cached in the named directory. The Kraken endpoint is rolling. Reproducing the original coverage after November 22 requires keeping those source responses from the evaluation date. The terminal day's unfinished bar contributes its opening trade only.
