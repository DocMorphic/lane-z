# Three-minute Hyperliquid volume breakout

## Frozen rule

Test U.S. regular-session momentum in `xyz:XYZ100`, `xyz:NVDA`, and `xyz:SILVER` on three-minute Hyperliquid trade candles. An eligible completed candle closes outside the previous five candles’ high or low. Its body must point in the breakout direction and be at least 8 bp, with base-unit volume at least 1.5 times the median of the prior 20 bars. Long for an upward breakout and short for a downward breakout. Require all 20 prior bars to be in the same regular session. Use one open trade per asset and no leverage or intrabar stops. Price the entry with the first trade in the next interval and the exit with the first trade three minutes later. Skip missing or zero-trade bars and any trades crossing an hourly funding timestamp. [Hyperliquid candle API](https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint)

Freeze the three non-overlapping session blocks of November 16–17, 18–19, and 20/23, 2026. Use 09:30–16:00 New York time on these six dates. Evaluate one rule across the basket without varying thresholds by asset. Require at least 30 completed trades per block and positive aggregate results in every block at both cost cases. The base case reserves 0.9 bp taker fee and 1 bp price friction per side. The stress test waits one extra three-minute bar before entry, then uses 0.9 bp and 2.5 bp per side for fees and price friction. Compare to a fee-only case to identify whether costs explain the result. Use current growth-mode metadata for a fee check and report what is known about historical fee coverage. No candles can verify executable spreads, precise receipt latency, queue priority, or the displayed liquidity of individual fills. [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)

## Paper results

| Sessions in November 2026 | Base trades | Gross bp/trade | Fee-only bp/trade | Base bp/trade | Stress trades | Stress bp/trade |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 16–17 | 47 | +1.68 | −0.13 | −2.13 | 40 | −5.00 |
| 18–19 | 44 | +6.46 | +4.66 | +2.66 | 40 | −13.69 |
| 20 and 23 | 49 | +0.44 | −1.36 | −3.36 | 49 | −14.36 |

The breakout failed the frozen rule. The taker fee alone consumed the mean gross move in two windows. After price friction, only the middle window made a profit. Under delay and higher friction, all three lost money. With 100 USDC reference notional per trade, base P&L totaled −1.47 USDC across 140 trades. The stress case totaled −14.51 USDC across 129 trades. The cases can have different trade counts because the later entries change which trades fit the session, funding, and one-position limits. [Paper replay report](../results/follow-up/hip3-volume-breakout.json)

Each contract had all 130 positive-volume three-minute bars on each test day. Venue metadata reported growth mode and a deployer fee scale of 1.0 for each contract, with their most recent reported fee changes before these windows. A November 24 reference snapshot showed quoted spreads of 0.75 bp on XYZ100, 1.26 bp on NVDA, and 0.24 bp on SILVER. Those point observations support the cost scale and leave historical spread and realized order timing unmeasured. Every quote and trade return here is simulated. [Hyperliquid perpetual metadata](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals) · [Nasdaq regular market hours](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule)

```sh
python -m lane_z.hip3_volume_breakout fetch
python -m lane_z.hip3_volume_breakout run --output data/agent-hip3-volume-breakout/report.json
```
