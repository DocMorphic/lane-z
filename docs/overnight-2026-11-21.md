# Hyperliquid overnight paper run

**November 21–22, 2026.** The overnight run found no qualifying Hyperliquid strategy. The frozen Binance-input model made five paper order attempts and filled none at 250, 500 or 900 ms. The Hyperliquid-only model made one small winning trade with the frozen entry gate. It stayed flat when the gate required the forecast to clear the costs charged in the replay. Each additional strategy that completed trades lost money.

## Capture and execution

The public feeds collected **2,433,924 quotes**, **32,288 Hyperliquid trade prints**, and **44,998 Hyperliquid oracle and mark updates** across their recording windows. Hyperliquid was the sole execution venue for these simulations. Binance supplied reference quotes for the frozen cross-venue model. No account, signing key, orders or money were involved.

The two principal decision windows were Saturday 11:42–23:58 UTC and Sunday 00:02–04:57 UTC. Hyperliquid NVDA's midpoint ranged from $127.935 to $131.525 and changed 30,948 times in those windows. Its TSLA midpoint ranged from $351.205 to $355.870 and changed 29,482 times. Median target quote age was 321–333 ms for Hyperliquid and 108 ms for Binance on the recorder. Those are observed feed ages. The 250, 500 and 900 ms order delays are additional modeled times.

The initial Hyperliquid disconnect required a recorder restart. The gap between observed quotes on either side ranged from 96 to 114 seconds across the seven streams, rounded to whole seconds. The updated recorder handled four later quote-connection closures automatically. A 61 ms backward movement of the local clock affected the Saturday Binance feeds. The replay excludes the entire 62 ms timestamp overlap, comprising 14 NVDA and eight TSLA rows. No trades, oracle messages, or Hyperliquid quote rows fell in that interval. The original captures are preserved. [Capture audit and digests](../results/overnight-2026-11-21/capture-audit.json)

## Frozen models

Each replay charges a 0.9 bp Hyperliquid growth-mode taker fee and a 0.5 bp slippage reserve per filled side. The frozen decision formula retains its original 0.25 bp reserve. The charged-cost sensitivity applies 0.5 bp per side at the decision as well, leaving the model weights and other gates intact. [Protocol](forward-protocol.md)

| Hyperliquid paper execution | Attempts at 500 ms | Round trips at 500 ms | Net at 250 ms | Net at 500 ms | Net at 900 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Binance inputs, frozen gate | 5 | 0 | $0.0000 | $0.0000 | $0.0000 |
| Binance inputs, charged-cost gate | 2 | 0 | $0.0000 | $0.0000 | $0.0000 |
| Hyperliquid-only weekend model, frozen gate | 1 | 1 | +$0.0115 | +$0.0143 | +$0.0051 |
| Hyperliquid-only weekend model, charged-cost gate | 0 | 0 | $0.0000 | $0.0000 | $0.0000 |

The Binance-input model scored 10,358 of 487,200 eligible symbol times. The Hyperliquid-only model scored 102,277. All five frozen Binance-input attempts were NVDA. At the 500 ms arrival the executable quote had moved adversely by 6.17–18.43 bps from the decision-time touch, outside the 2 bp limit. Displayed size was sufficient at those prices. The sole Hyperliquid-only fill was a TSLA short of about $19.85 at 500 ms. Its 2.802 bp forecast fell short of the 3.082 bp hurdle when the charged reserve was applied at entry. [Per-date and per-symbol replay](../results/overnight-2026-11-21/frozen-replays.json)

## Independent strategies

| Fixed strategy on Hyperliquid | Attempts at 500 ms | Round trips at 500 ms | Net at 500 ms | Net at 900 ms |
| --- | ---: | ---: | ---: | ---: |
| 60-second price-shock reversal | 29 | 27 | −$2.5861 | −$2.5979 |
| 15-second aggressive-flow absorption | 69 | 68 | −$2.4050 | −$2.3694 |
| 30-second momentum with broad confirmation | 5 | 5 | −$0.1400 | −$0.1545 |
| 30-second momentum with target-only signal | 156 | 154 | −$4.7986 | −$4.7346 |
| 15-minute dislocation with XYZ100 reference | 0 | 0 | $0.0000 | $0.0000 |
| 15-minute dislocation with oracle premium | 1 | 1 | −$0.0895 | −$0.0741 |

At 500 ms the 60-second reversal captured $2.33 of favorable midpoint movement on 27 fills. Spread, fees and the reserve cost $4.91. The absorption rule lost $0.04 on midpoint movement and paid a further $2.37 after adding spread, fees and reserves. The two 15-minute strategies used Sunday's window. The oracle variant had data on 51 of 78 scheduled symbol-bars. It found three large NVDA dislocations and entered once. This narrow sample provides one realized test of the rule and cannot establish the economics of the broader published Aperiodic signal. [All alternative and stress results](../results/overnight-2026-11-21/alternate-replays.json) · [Momentum protocol](weekend-momentum.md) · [Quarter-hour rules](closed-session.md)

The passive markout diagnostic found 21,770 observed trades at the prior maker-side touch over the two late windows, before applying a horizon-specific quote and funding filter. At 500 ms, the notional-weighted cost of a hypothetical taker exit after modeled fees and reserve ranged from −2.08 to −2.45 bps across the four date-symbol groups. These are public trade prints and hypothetical exits. Passive fill rates require order-level evidence like the separate TSLA sample. [Passive markouts](../results/overnight-2026-11-21/maker-markouts.json) · [Order-level test](l4-smoke.md)

## What the session establishes

The short-horizon signals either missed their tradeable prices or generated less favorable movement than they paid in round-trip costs. The two reported UTC dates are part of one weekend. A fresh U.S. regular session is still needed to evaluate whether the original Binance-input model transfers under its training regime. A broader set of Hyperliquid instruments and multiple untouched closed sessions would provide more observations of the rare quarter-hour setups. [Research and source assumptions](research.md) · [Earlier cross-venue results](venue-comparison.md) · [NSE India](nse-india.md) · [Hedging](hedges.md)

## Reproduction

Use the archived local tapes and their published hashes. The raw capture stays in the ignored `data/` directory. The public recorder can collect an independent tape. Keep quote, trade and oracle captures in separate output directories. For each date and delay, use the appropriate UTC decision window and pass either `results/binance.json` or `results/internal.json` to the same replay. Add `--gate-on-execution-costs` for the tighter cost threshold.

```sh
python -m lane_z.forward 2026-11-21 --data data/overnight-combined \
  --report results/binance.json --utc-window 11:42 23:58 --delay-ms 500 \
  --fee-bps 0.9 --slippage-bps 0.5 --output data/replay-overnight.json
```
