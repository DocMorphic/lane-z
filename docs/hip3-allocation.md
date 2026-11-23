# Hyperliquid equity and gold paper allocation

## Frozen protocol

Test whether an observable funding-cost filter improves a diversified allocation to the trade[XYZ] `xyz:XYZ100` equity index and `xyz:GOLD` gold perpetuals. Allocate at most 35% of portfolio equity to each long sleeve at each rebalance, leaving at least 30% unassigned before price movement and accounting for position rounding. The assets represent two different underlying exposures. The equity index is a custom U.S. large-cap benchmark and the gold contract references one troy ounce of spot gold. [trade[XYZ] specifications](https://docs.trade.xyz/perpetuals/specifications-and-schedules/specification-index.md)

At 16:00 UTC each Monday, annualize the past 168 hourly long funding rates using 365.25 days per year. Approve a sleeve for the following week only if its annualized cost is 4% or less, equal to the separate cash hurdle. Use only timestamps strictly before the decision. At the scheduled open reference, reduce unapproved sleeves to zero and rebalance approved sleeves to 35% using the asset size step. Freeze the same targets in stress and use the first four-hour open four hours later to implement them. Start each vintage from $100,000-equivalent USDC and assume USDC at par. Do not credit interest on any collateral. The three consecutive, separate vintages run from January 5 to April 6, April 6 to July 6, and July 6 to November 20, 2026. Each begins and ends at 16:00 UTC. The last trade is a scheduled liquidation of the paper holdings. [Hyperliquid funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) · [trade[XYZ] funding](https://docs.trade.xyz/perpetuals/mechanics/funding.md)

Use every available published hourly funding rate in the holding period, including weekends. For settlement valuation approximate the historical oracle with the most recent completed four-hour trade close, since the anonymous historical endpoint publishes rates and premium values without oracle snapshots. Apply the timestamp’s funding event to the position carried into that hour. Charge standard 9 bps taker fees for both assets and both sides. Reserve an additional 3 bps per side for adverse execution, and 15 bps per side in the delayed scenario. In that stress charge 10% extra on funding debits and credit 10% less of positive carry to account for valuation uncertainty. Four-hour exchange candles supply price proxies, and the simulation does not establish executable depth or millisecond latency. [trade[XYZ] standard and growth fees](https://docs.trade.xyz/perpetuals/mechanics/fees.md)

Compare the funding gate with always-on exposure on the same Hyperliquid instruments, rebalanced to 35% each at the same scheduled times. Also compare a passive portfolio starting each vintage with 35% QQQ, 35% GLD and 30% in cash earning zero. Use whole ETF shares, Nasdaq market prices, issuer QQQ distributions, a 1 bp reserve each way and a 0.3 bp fee allowance on sales. Comparison dates align by U.S. trading day with ETF first regular open and final regular close, so timestamp basis remains. QQQ and GLD are investable proxies and have benchmark construction and fee differences. Finally compare whole-portfolio cash compounded at 4% without exposure. Demonstrated cash-hurdle paper profit requires all three base and stress vintages to beat full cash. Any timing edge versus the same-instrument always-on policy is a separate comparison. [Invesco QQQ](https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html) · [SPDR Gold Shares](https://www.spdrgoldshares.com/usa/gld/)

## Paper results

| 2026 vintage | Gated base P&L | Gated stress P&L | Always-on base P&L | Passive QQQ and GLD P&L | 4% compounded cash P&L |
| --- | ---: | ---: | ---: | ---: | ---: |
| Jan. 5–Apr. 6 | −$4,984.23 | −$5,329.41 | −$758.55 | +$127.45 | +$981.95 |
| Apr. 6–July 6 | +$7,108.40 | +$6,934.44 | +$2,478.40 | +$4,304.37 | +$981.95 |
| July 6–Nov. 20 | −$1,302.19 | −$1,822.70 | −$11,169.67 | −$10,604.86 | +$1,481.99 |

**Decision.** Retire this funding-cost gate as a standalone profit candidate. It cleared the hurdle only in the middle vintage. In the first vintage, the gate lost about $4,463 on price movements before $521 in modeled funding and execution costs. Lower funding costs did little to offset the price moves. In the last vintage, the gate cut drawdown from 13.71% to 3.05% versus always-on exposure, with about 10% mean gross exposure and only six of 40 possible sleeve-weeks active. Cash outperformed both strategies. These results come from two markets over less than a year.

## Data and reproduction

Both exchange tapes contain 1,967 four-hour bars and 7,865 hourly funding observations with no gaps from December 28, 2025 through the November 20 boundary. Historical funding rates come from Hyperliquid. Their dollar amounts use sampled traded closes as oracle proxies, including in the stress case. A decimal ledger independently recomputed each of the 12 Hyperliquid scenarios’ signals, fees, funding and net P&L. Reports include source payload hashes. [Hyperliquid historical endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals) · [Nasdaq GLD history](https://www.nasdaq.com/market-activity/etf/gld/historical)

```sh
python -m lane_z.nasdaq_trend fetch
python -m lane_z.hip3_allocation fetch
python -m lane_z.hip3_allocation run --output data/agent-hip3-allocation/report.json
```
