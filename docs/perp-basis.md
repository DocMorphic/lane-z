# Hyperliquid cross-sectional perpetual basis

## Paper protocol fixed before scoring on November 23, 2026

Chi et al. find a short-horizon cross-sectional premium for long futures priced relatively cheap against spot, with weaker evidence at weekly horizons. Cao et al. identify basis as a pricing factor for perpetual futures. Hyperliquid publishes the impact-price premium used to calculate hourly funding. This experiment asks whether its trailing premium ranking predicts a one-day tradable relative return after fees and funding. The hourly premium is a proxy for spot minus futures basis with the sign reversed. [Chi et al.](https://doi.org/10.1002/fut.22425) · [Cao et al.](https://www.research.ed.ac.uk/en/publications/anatomy-of-cryptocurrency-perpetual-futures-returns/) · [Hyperliquid premium and funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)

Fix BTC, ETH, SOL and HYPE with native USD-linear perpetuals. At midnight UTC rank the assets by the average of the previous 120 completed hourly premium observations, excluding the timestamped payment at midnight itself. Long the cheapest by this measure and short the richest, allocating 25% of current marked equity to each leg, rounded down to the venue size lot. Skip entries when the extremes tie. Same-dollar entry hedges initial net notional. Relative moves and unequal token betas still create loss risk. A daily rebalance trades only the quantity change, with the exchange minimum notional applied to discretionary orders. Reserve unallocated capital in zero-yield USDC. No signed calls or orders.

Use official four-hour candles and hourly venue funding. Base fills use the bar open four hours after decision, 4.5 bp Tier 0 fee plus 5 bp execution reserve per changed leg, and actual paid/received funding. Stress fills use the next bar eight hours after decision, 4.5 bp fee plus 15 bp execution reserve, a 25% haircut to positive funding, and adverse funding-notional proxies. In base, use the enclosing price bar open as the hourly funding notional proxy. In stress, use the higher of the available trailing bar high or opening price at the payment for funding debits and the lower of the trailing low or opening price for credits. Apply each payment to the position held before any same-hour trade. Mark by opens and measure a long-low/short-high 4-hour range stress separately, with a conservative 10% of gross notional maintenance floor. Full account size is 2,000 USDC, across both legs and any idle cash. [Native perp fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)

Score five untouched rule windows, 2025-09-01 to 2025-12-01, 2025-12-01 to 2026-04-01, 2026-04-01 to 2026-07-01, 2026-07-01 to 2026-10-01, and 2026-10-01 to 2026-11-22. Inputs overlap earlier experiments for unrelated rules. No outcomes from this rule shaped the windows or parameters. Each opens from cash and exits at its boundary, pays exit costs and compares with 4% annual compounded on full capital. Publish gross price P&L, signed funding, fees, execution reserve, simulated drawdown and sensitivity to excluding the best day. To qualify, require cash excess in each of the four newest windows in stress, sound independent accounting and a credible liquidity check. An interval's best day is removed only as a concentration diagnostic. It is not a trade decision.

## Results and decision

The ranking lost paper money in all five periods at baseline and under stress. Stressed per-window marked drawdown ranged from 5.82% to 24.15%. All results start with 2,000 paper USDC, including unallocated collateral.

| Window | 4% cash | Base net | Stressed net | Stressed price P&L | Stressed funding | Stressed fees and execution |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sep–Nov 2025 | +19.65 | −135.73 | −157.27 | −119.43 | +7.53 | −45.37 |
| Dec 2025–Mar 2026 | +26.17 | −310.24 | −311.58 | −265.73 | +11.83 | −57.69 |
| Apr–Jun 2026 | +19.65 | −320.51 | −365.34 | −324.13 | +4.85 | −46.06 |
| Jul–Sep 2026 | +19.87 | −44.01 | −103.07 | −33.29 | −0.07 | −69.71 |
| Oct–Nov 2026 | +11.21 | −10.80 | −20.54 | +6.79 | +1.79 | −29.11 |

Residual price exposure was the dominant loss in the first three periods. In the latest period, the price and funding gains together covered less than a third of execution costs. The fixed premium rank was retired after failing across both cost cases. Ranking the opposite way on the same data would be a new hypothesis informed by these outcomes and would require separate evidence.

The replay uses candle-open price proxies. It has no historical synchronized depth, execution confirmation, or auto-deleveraging events. The four-hour adverse price envelope is a margin screen. Other crypto markets, holding times, and premium definitions could produce different results. [Frozen reports and input digests](../results/follow-up/perp-basis.json)

To replay the original tapes from the project root:

```sh
python -m lane_z.perp_basis --data data/perp-basis data/agent-native-trend \
  data/agent-spot-carry data/agent-spot-carry/older data/agent-selective-carry/late \
  --output data/perp-basis-reproduced.json
```

To fetch an independent official archive into an empty directory, use `--fetch --data data/perp-basis-new` instead of the original `--data` paths. The public candle endpoint serves the latest 5,000 candles per interval. Preserve the fetched raw inputs for later reproduction.
