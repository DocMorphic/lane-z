# Results

**Decision on November 21, 2026.** Stay flat. The strongest version uses same-stock Binance quotes to forecast a short-lived move in the Trade[XYZ] perp. Its two-date held-out replay earned $0.64 at 250 ms per order leg. The model did not meet the pre-specified validation minimum, and its result was sensitive to the freshness of the Binance feed.

## Strategy qualification

The study used 67 unique quote files covering 11 distinct dates in 2026. Each arm fit on three dates, chose its policy on a separate validation date, and then replayed one or two later dates. The entry gate requires the modeled price move to cover the observed round-trip spread, fees, slippage, and any selected additional margin.

- **Trade[XYZ] regular session.** None of the nine validation configurations produced a completed round trip. The 5 s reference also stayed flat in the holdout.
- **Trade[XYZ] weekend and holiday.** The index-only model had just one validation round trip across all nine configurations, losing $0.075. Its 15 s reference stayed flat on November 1. At that hold, common forecast coverage was only 663 NVDA and 68 TSLA grid rows. Weekend liquidity and sample size limit this result.
- **Binance same-stock features.** Eight validation round trips at a 5 s hold earned $0.34, and 14 at a 15 s hold earned $0.03. Both fell short of the 25-round-trip minimum. The 5 s reference is specified as the middle horizon when no policy qualifies. The regular and Binance arms reuse the same holdout dates, so their comparison is exploratory.

## What the Binance reference did

The 5 s model evaluated NVDA and TSLA at scheduled 250 ms decision intervals. It required the predicted magnitude to clear a fee and spread gate, and submitted simulated IOC limits to Trade[XYZ]. This is a directional cross-venue forecast with a single execution leg on Hyperliquid.

| Regular holdout scenario | Attempted entries | Round trips | Net USD | Net bps on filled entry notional |
| --- | ---: | ---: | ---: | ---: |
| 250 ms execution per leg, growth fees | 140 | 56 | +$0.64 | +1.84 |
| 500 ms execution per leg | 135 | 8 | +$0.10 | +2.21 |
| 900 ms execution per leg | 133 | 4 | −$0.13 | −4.23 |
| 125 ms extra Binance quote delay | 146 | 34 | −$0.05 | −0.27 |
| 250 ms extra Binance quote delay | 148 | 10 | −$0.14 | −2.76 |
| Require venue quotes at most 250 ms old at execution | 140 | 56 | +$0.65 | +1.85 |
| 1 bp per side slippage reserve | 140 | 56 | +$0.12 | +0.34 |
| 9 bps per side standard taker fee | 140 | 56 | −$5.01 | −14.36 |
| $1,000 requested per entry | 140 | 56 | +$3.28 | +1.83 |

The base case earned $0.07 on Sep 1 and $0.57 on Oct 1. It realized $1.45 after crossing both spreads and before $0.63 in fees and $0.17 in modeled slippage. It recorded 84 missed entries, 32 partial entries and four delayed exits, with no unresolved positions or funding crossings. TSLA supplied 50 of 56 round trips. At $1,000 requested, 43 of the 56 entries were partial. This size stress uses only displayed top-level capacity.

The held-out 5 s midpoint correlation rose from 0.066 to 0.150 for NVDA and 0.045 to 0.145 for TSLA after adding the Binance features, using identical eligible rows. Correlations are descriptive given overlapping horizons. Removing the economic gate and trading whenever the Binance model forecast exceeded 0.25 bps in magnitude lost $158.31 after costs across 6,918 simulated round trips. The analogous regular and weekend diagnostics lost $121.27 and $4.84.

## What remains uncertain

Tardis's historical capture clock showed median target quote ages of about 244–280 ms for Hyperliquid and 2.5–3.2 ms for Binance on the two regular holdout dates. This timing difference is part of the tested opportunity. Independent capture clocks, feed transport, and Hyperliquid block publication can all contribute to it.

A separate 35-second public feed smoke test captured 2,392 validly formatted rows across both exchanges and all seven feeds. Median quote ages for the two Binance feeds were about 125–126 ms on the local development host. The session was outside the regular trading window. It verifies ingestion and illustrates timing sensitivity, with no implication for forward P&L.

The simulator fills on sampled BBO and has no full block-level queue, hidden liquidity, competition, or actual order-latency distribution. The held-out dates are sparse, the absolute gain is small, and the 500 ms result represents eight fills. A positive day-block lower bound on at least 30 fresh relevant sessions after measured delays and costs remains unverified. The public recorder and frozen-model replay provide a way to measure that without routing an order.

## Artifacts

- [Regular-session report](../results/regular.json)
- [Weekend and holiday report](../results/internal.json)
- [Binance-feature report](../results/binance.json)

The JSON records every validation configuration, held-out P&L split by date and symbol, stress scenarios, comparable forecast metrics, fitted weights, and historical input checksums.
