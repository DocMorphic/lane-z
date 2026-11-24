# Fresh Hyperliquid equity-perp paper replay

Three disjoint Nasdaq premarket decision windows on November 24, 2026 produced 18 paper entry attempts and zero fills at 500 ms per order leg. The faster 250 ms comparison closed one small loss. The regular-session test uses a separate tape. [Archived aggregates and quote checks](../results/follow-up/hip3-fresh-fast-2026-11-24.json) · [Nasdaq session hours](https://www.nasdaq.com/market-activity)

## Frozen paper rules

- The published five-second Binance-input rule kept its trained NVDA and TSLA weights, 250 ms decision grid, 100 USDC requested entry and original spread-and-cost gate. The replay charged 0.9 bp taker and 0.5 bp slippage reserve per filled side. We counted how many entries would have cleared that higher actual modeled cost gate. Current public exchange metadata reported Growth Mode enabled, deployer fee scale 1.0 and USDC collateral for both Trade[XYZ] targets. [Published study and parameters](results.md) · [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)
- A separate fixed, untrained 30-second rule observed only the same-stock Binance and Trade[XYZ] instruments. Require BBO quotes at most one second old, a signed five-second move of at least 4 bps in Binance, a flat or same-direction Hyperliquid move and at least 4 bps Binance outperformance in that direction. Require the same-signed current midpoint difference, centered on the preceding two minutes of observed basis, to exceed both 5 bps and the Hyperliquid spread plus round-trip execution costs and a 1 bp margin. The basis mean excludes the decision row and requires 80% coverage. Require a spread at most 2 bps, enter on the first qualifying row, and exclude the last 40 seconds before an hourly funding boundary.
- The simulator limits entries to 2 bps beyond the decision touch, uses public best-book depth at simulated arrival and reports partial, missed and unresolved trades. We also added 125 ms of leader-feed delay at 500 ms per order leg. No orders, account access or signed requests were used. [Primary study on lead–lag and trading costs](https://arxiv.org/abs/2108.09750) · [Hyperliquid WebSocket book feeds](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)

## Results

| Decision window UTC | Messages in window | Five-second finite forecasts, NVDA / TSLA | Five-second attempts at 500 ms | 30-second attempts at 500 ms | Completed trips at 500 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 09:24:02–09:26:22.500 | 44,168 | 77 / 76 | 1 | 0 | 0 |
| 09:27:00–09:30:01 | 56,182 | 69 / 47 | 0 | 3 | 0 |
| 09:30:53–09:40:14.250 | 187,395 | 327 / 303 | 0 | 14 | 0 |

Each symbol had 562, 724 and 2,245 grid decisions in the three windows. The first window's five-second forecast of −4.187 bps marginally cleared its original gate. The current cost reserve raised that gate to 4.686 bps. Its NVDA sell limit was 159.01819 from a decision bid of 159.05. The respective bids at 250, 500 and 900 ms arrival were 158.90, 158.80 and 158.84. The three second-window 30-second buy attempts had asks 2.52–3.15 bps above the decision touch at 250 ms and 6.52–8.20 bps higher at 500 ms. All exceeded the 2 bp cushion.

The third window's 30-second rule closed one partial NVDA fill at 250 ms. The 12.52 USDC entry lost 0.00351 USDC after costs. Its 500 ms replay with an added 125 ms of Binance delay closed one loss of 0.09119 USDC. Both policies ended flat in every case.

In the first two windows, median receipt-minus-venue quote ages for the targets ranged from 343 to 395 ms on Hyperliquid and 137 to 139 ms on Binance. Ninety-percentile ages ranged from 417 to 493 ms and 167 to 195 ms respectively. These clock observations include publication and transport. Paper order delay is a separate assumption. The three samples show thin qualifying opportunity and missed entry prices in Nasdaq premarket. The November 24 regular-session retest was scheduled separately for 14:35–15:05 UTC.

A retrospective widening of the entry cushion from 2 to 25 bps tested the cost of chasing the missed quotes. The five-second arm lost at every delay. The 30-second arm closed two trades at 250 ms for +0.013 USDC, and lost at 500 ms and 900 ms. The wider limit was selected after the primary outcomes. [Wider-limit diagnostic](../results/follow-up/hip3-fresh-fast-wide-limit-2026-11-24.json)

The replay CLI reruns both rules on a retained November 24 capture. The aggregate contains the data file digests, event and receipt clock diagnostics, posted limits and observed arrival prices.

```sh
python -m lane_z.equity_fast data/agent-equity-fast-today/premarket-01 \
  --start 2026-11-24T09:24:02+00:00 --end 2026-11-24T09:26:22.500+00:00 \
  --output data/agent-equity-fast-today/replicate-premarket-01.json
```
