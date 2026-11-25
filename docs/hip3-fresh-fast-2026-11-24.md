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

## Regular-session clock recovery

The scheduled regular-session capture ran from 14:33 through 15:06 UTC. The replay detected a wall-clock rollback at 14:46:13 UTC. Its affected files reported backward steps of 54–68 ms. Preserve the original gzip files. Quarantine all receipts from 14:46:13 inclusive to 14:46:14 exclusive on every feed. Replay the portions before and after as independent segments with the original decision bounds, fresh signal warmup and an exit buffer inside each segment. Keep the results labeled as recovery from a clock interruption. Use a new 20-minute live tape captured with a monotonic, UTC-anchored clock as a separate regular-session check. All paper execution and signal parameters stay fixed. [Node.js clock reference](https://nodejs.org/api/perf_hooks.html#performancetimeorigin)

The quarantine excluded 485 receipts from the 707,249-row capture. The first eligible segment ran from 14:35 to 14:45:37.750 UTC and produced the following paper closes. All values are after fees and the 0.5 bp reserve per fill. [Original file hashes, quarantine audit and segment results](../results/follow-up/hip3-regular-2026-11-24.json)

| Delay per leg | Five-second closes | Five-second net | 30-second closes | 30-second net |
| --- | ---: | ---: | ---: | ---: |
| 250 ms | 11 | −0.0620 USDC | 10 | +0.2676 USDC |
| 500 ms | 4 | +0.2200 USDC | 3 | +0.0356 USDC |
| 900 ms | 3 | −0.3279 USDC | 5 | −0.8823 USDC |
| 500 ms with 125 ms of added Binance delay | 1 | +0.1588 USDC | 6 | +0.0411 USDC |

The second segment, from 14:46:14.500 to 15:05 UTC, had no usable decisions for either rule. Binance median quote age was 3.17 seconds on NVDA and 3.24 seconds on TSLA. The upper tenth of delays exceeded 16 seconds on both. That segment left the paper accounts flat. The small 500 ms gains came from one contiguous opening-period tape and reversed at the 900 ms order delay.

The monotonic-clock repeat captured 10 minutes and 21 seconds of usable decision time, from 16:20:51.500 to 16:31:12.500 UTC. The feeds stopped producing usable updates before the planned 20-minute capture ended. The seven files had no backward receipt times or negative quote ages. At 500 ms, the five-second model made three attempts and the 30-second rule made six. Every attempt missed, and both rules remained flat at all tested delays. The opening-period gains remain unconfirmed. [Monotonic follow-up report](../results/follow-up/hip3-regular-2026-11-24.json)

```sh
capture=data/agent-equity-fast-today/regular-01
parts=data/agent-equity-fast-today/regular-01-split
python scripts/split_quote_capture.py "$capture" "$parts" \
  2026-11-24T14:46:13+00:00 2026-11-24T14:46:14+00:00
for part in before after; do
  python -m lane_z.equity_fast "$parts/$part" --start 2026-11-24T14:35:00+00:00 \
    --end 2026-11-24T15:05:00+00:00 --output "$parts/$part/replay.json"
done
```

## Next-date confirmation

The November 25 regular-session follow-up uses a new 600-second capture with the same five-second and 30-second rules, fees, order limits, modeled delays and final 35-second exit buffer. Use the first common quote window in that capture. A result with no fills is flat for the sample. A positive result on a few fills remains exploratory.

```sh
followup=data/equity-fast/2026-11-25-regular
node scripts/capture.mjs "$followup" 600
python -m lane_z.equity_fast "$followup" --day 2026-11-25 --output "$followup/replay.json"
```

The first November 25 tape had zero usable decisions in the primary replay. More than 269,000 Binance rows in its scoring window had exchange timestamps later than their recorded receipt times. Read-only Apple and Google time probes both placed the host clock about 301 ms behind their clocks. Preserve the original tape and treat this replay as a clock diagnostic.

For the new 600-second tape, take read-only time probes immediately before the capture. Require the two estimates to agree within their reported combined uncertainty. Round their mean offset to the nearest millisecond and apply that fixed value to the common monotonic receipt clock for all feeds. Check the estimates again after capture and publish both measurements with the replay. The collector's `--clock-offset-ms` option applies this adjustment to new captures, with no changes to venue timestamps or the host clock. The strategy and fee assumptions remain frozen.

The first calibrated run recovered 1,461 usable five-second decisions per symbol. At 500 ms the 30-second rule made seven attempts and filled none. The host clock offset moved from about 303 ms to 69 ms, so that run remains a clock diagnostic. A further capture samples the recorder's own receipt clock and wall clock together at startup and shutdown. Compare the recorder's time directly against the independent UTC reference intervals at both ends. This shows whether a host wall adjustment also changed the time assigned to received quotes.
