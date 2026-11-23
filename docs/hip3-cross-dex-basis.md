# Sandisk cross-market basis paper test on Hyperliquid

## Frozen protocol

Independent trade[XYZ] and Entropy perpetual books both quote one Sandisk share in USDC during overlapping U.S. equity hours. Test whether temporary differences in their traded prices converge after an observable delay. The position is long the cheaper contract and short the richer one in equal share-equivalent size. Different matching priorities, funding rules and oracle inputs leave residual price basis and execution risk. [trade[XYZ] contract specifications](https://docs.trade.xyz/perpetuals/specifications-and-schedules/specification-index.md) · [Entropy equity assets](https://docs.entropy.io/asset-directory/equity-assets.md) · [Entropy oracle](https://docs.entropy.io/equity-perp-mechanics/oracle-price.md)

Use the completed 10:00–11:00 New York time trade candle each regular stock session. Require at least 30 trades on each venue in that hour and an absolute log-price difference of at least 50 bps. Price both entries using the 12:00 ET candle opens and both exits using the 15:00 ET candle opens. Allocate at most 40% of the $10,000-equivalent USDC portfolio to each leg before fees, sized to the venues’ coarser quantity precision. Skip venue orders below $10 of initial notional. Hold the remaining collateral unremunerated. Reset the portfolio independently for three disjoint date ranges ending in 2026, August 24–September 21, September 21–October 19, and October 19–November 18. Boundary days are assigned to the later window, with November 18 included. Avoid the scheduled November 19 eight-for-one split and potential contract settlements. [Nasdaq session calendar](https://www.nasdaqtrader.com/Trader.aspx?id=Calendar) · [Sandisk 8-K](https://investor.sandisk.com/static-files/91c17135-5e20-4671-820e-736c80b4765c)

Model no staking or fee discounts beyond venue Growth Mode. Charge 0.9 bps taker fees and 4 bps of adverse execution per fill in base. Stress at 10 bps of adverse execution with the standard 9 bps taker fee, and separately hold the base fees and delay Entropy entry by another hour and exit to 16:00, leaving exposure until both legs fill. Use actual signed funding rates with the prior hourly trade close as oracle proxy. Apply hourly settlements to the positions carried into an event just before placing scheduled orders. Test a same-hour 11:00 entry only to measure how much of the price discrepancy dissolves during the hour of delay. Report post-cost P&L, before-fee convergence, execution participation, and 4% compounded cash on all capital. All three base and cost-stress windows must clear cash to progress the strategy. Historical trade prints have no synchronized book depth or fill confirmation, so even a positive screen would require separately collected forward quotes. [Hyperliquid fee mechanics](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) · [trade[XYZ] published rates](https://docs.trade.xyz/perpetuals/mechanics/fees.md) · [Entropy funding](https://docs.entropy.io/equity-perp-mechanics/funding-rate.md)

## Paper results

| 2026 vintage | Qualified sessions | Median absolute basis | Largest absolute basis | Trades | 4% compounded cash |
| --- | ---: | ---: | ---: | ---: | ---: |
| Aug. 24–Sept. 20 | 19 | 2.02 bps | 10.17 bps | 0 | +$30.11 |
| Sept. 21–Oct. 18 | 20 | 2.93 bps | 7.74 bps | 0 | +$30.11 |
| Oct. 19–Nov. 18 | 23 | 2.92 bps | 5.90 bps | 0 | +$33.34 |

**Decision.** Retire the fixed 11:00 ET convergence rule. All 62 observed sessions passed the minimum trade-count filter, and no observed price difference met the 50 bp threshold. The largest gap was also below the roughly 19.6 bp modeled base cost of four executions, measured against one leg’s notional. A full return of the largest observed gap to zero would leave the strategy short of those costs, before funding. Every paper scenario stayed in zero-yield cash. This result concerns the scheduled hourly snapshot. It leaves other intraday times and synchronized executable quotes untested.

## Data and reproduction

Each market’s official history contained 2,089 consecutive one-hour candles and funding records, from August 24 through November 19 at 00:00 UTC. The final eligible trade day was November 18. Venue metadata reported four quantity decimals for Entropy and three for trade[XYZ], and Growth Mode on both. An independent decimal calculation confirmed the basis maxima and liquidity-screen counts for all 62 sessions. An illustrative two-leg convergence trade separately verified simulated quantities, signed funding, and execution friction. [Hyperliquid candles](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#candle-snapshot) · [Hyperliquid historical funding](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)

```sh
python -m lane_z.nasdaq_trend fetch
python -m lane_z.hip3_cross_dex fetch
python -m lane_z.hip3_cross_dex run --output data/agent-hip3-cross-dex/report.json
```
