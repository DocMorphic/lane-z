# Volatility-scaled native-perpetual trend

Liu and Tsyvinski report time-series momentum in their cryptocurrency sample. Babu and coauthors find evidence for combining trends across horizons in other futures markets. Moreira and Muir motivate reducing exposure when realized volatility rises. The fixed rule below applies these ideas to BTC, ETH and SOL native Hyperliquid perpetuals. The shorter horizons and the specific sizing formula are this study's own hypothesis. [Crypto momentum](https://www.nber.org/papers/w24877) · [Trends Everywhere](https://images.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-Trends-Everywhere_JOIM.pdf) · [Volatility-managed portfolios](https://www.nber.org/papers/w22208)

## Frozen paper rules

- Every Monday at 00:00 UTC, use the previous fully completed UTC day close. Average the signs of each asset's 14-, 42- and 84-day close-to-close returns. Use the standard deviation of its preceding 28 daily log returns, annualized with 365 days, with a 25% volatility floor. The desired signed fraction of current paper equity is the average sign times the smaller of 30% and `40% / (3 × annualized volatility)`. Scale all three weights proportionately if aggregate absolute exposure exceeds 75%. No parameter is fitted.
- Start every chronological window flat with 3,000 USDC. Rebalance by trading only the change in the desired position. Set the quantity from the last known close and contemporaneous mark equity, rounded toward zero to the official size increment. Skip a rebalance delta with notional below 10 USDC at decision or at the execution proxy. Apply 4.5 bps taker fees at the modeled fill prices. Model fills at the next four-hour bar's first trade after the decision. Add a 5 bp adverse execution allowance on each trade delta. An eight-hour delay tests timing. The combined stress applies that delay, a 15 bp execution allowance and the funding stress below. At each window endpoint, close the actual positions and charge the same costs. Candle opens are price proxies and lack an exact quote, depth or order-execution time. [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) · [Candle endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#candle-snapshot)
- Apply the actual hourly funding rates to signed positions. A positive rate debits long positions and credits short positions. The public endpoint omits the historical oracle price, so the corresponding four-hour first-trade price proxies funding notional. The combined stress values positive payments at the bar low less 50 bps, values negative payments at the bar high plus 50 bps and discounts positive payments by another 25%. At the final liquidation proxy, use the opening price instead of extremes occurring after the sample. For an entry, resize or exit at a funding boundary, take the smaller cash flow from the before and after positions. This assumes the less favorable timing when the first trade and hourly settlement are not ordered by the candle data. [Funding formula](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) · [Historical funding API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals#retrieve-historical-funding-rates)
- Use a shared paper account with no borrowed capital or assumed idle yield. Report marked drawdown and maintenance headroom. Use the sum of each open position's adverse four-hour extreme, assume negative intra-bar funding arrives before the adverse mark, and require at least 5% maintenance on the adverse gross exposure. Treat a modeled breach as incomplete. Check that exposure is fully backed by account equity at fills. The historical traded-price envelope and joint worst mark are risk proxies, with no intrabar liquidation simulation or guarantee about available collateral. [Hyperliquid margining](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining)
- Use a matched native-perp buy-and-hold comparison. Buy an equal-weight BTC, ETH and SOL basket targeting 75% of initial equity at each window start plus the scenario's delay, hold through the endpoint and apply the same funding, reserves and costs. Compare both with zero-yield idle collateral and a 4% effective annual cash hurdle on all 3,000 USDC, compounded over the elapsed period. A favorable result needs to exceed cash under both baseline and combined stress in the independently dated holdouts, without an incomplete margin or funding path.

## Chronology and inputs

Use 4-hour Hyperliquid candles from May 1, 2025 for signal warmup and actual public hourly funding from August 1, 2025. All start and end boundaries are 00:00 UTC. Each period uses an independent paper account. The dates and primary rule were specified before requesting new prices or examining return outcomes. The asset selection reflects the three named, established native contracts. It is not a historical ranking of the full listing universe.

| Window | Start | End |
| --- | --- | --- |
| Development | 2025-08-01 | 2025-12-01 |
| Holdout 1 | 2025-12-01 | 2026-04-01 |
| Holdout 2 | 2026-04-01 | 2026-08-01 |
| Holdout 3 | 2026-08-01 | 2026-11-21 |

## Results and decision

All figures use 3,000 USDC of paper capital per window, including reserves. The compound cash hurdle is `3000 × (1.04^(days/365) − 1)`. An independent fixed-point calculation matched all 24 scenario ledgers to cached raw candles, funding and fees, and checked all 83 source digests.

| Period | 4% compounded cash | Trend baseline | Trend timing stress | Trend combined stress | Equal-long baseline |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development | +39.59 | +63.25 | +77.23 | +52.06 | −538.07 |
| Holdout 1 | +39.26 | +87.69 | +170.05 | +145.18 | −597.81 |
| Holdout 2 | +39.59 | −201.30 | −197.65 | −218.21 | −269.93 |
| Holdout 3 | +36.32 | +72.58 | +41.53 | +19.45 | −264.22 |

The rule failed to beat cash in holdout 2 even before expenses. Its baseline price P&L was −190.04 USDC in that window. Holdout 3 cleared cash at baseline and fell 16.88 USDC short under the combined stress. Retire this fixed rule. The matched long basket lost in every holdout. The trend's first and third windows also had baseline mark drawdowns of 431.49 and 404.60 USDC despite finishing with gains, alongside 342.81 in the losing second window.

No scenario had a modeled margin breach or an order below 10 USDC. The smallest continuing fill notional was 10.06 USDC and the smallest final unwind leg was 230.28 USDC. The trend's minimum joint adverse maintenance headroom was 2,475.46 USDC, while its largest sampled gross exposure was 83.20% of equity after price drift. The four-hour price proxies do not capture order-book depth or auto-deleveraging. This study provides no reason to further tune the fixed trend signal on the same outcomes.

## Reproduction

```sh
python -m lane_z.native_trend --capture --data data/agent-native-trend \
  --reuse-carry data/agent-spot-carry --output data/agent-native-trend/report.json
```

Without `--reuse-carry`, the capture option downloads BTC and ETH funding from the same public Hyperliquid endpoint. Rerun an existing tape without `--capture`, and choose a new output filename. The endpoint serves only the latest 5,000 candles per market and interval, so retain the cached inputs to reproduce the exact historical windows after they age out. Raw tape files remain under `data/`. [Public data API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
