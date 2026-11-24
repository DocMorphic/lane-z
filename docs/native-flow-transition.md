# Hyperliquid short-horizon flow continuation

Freeze a paper test of whether a one-sided best-quote liquidity change and aggressive trading persist after the initial move. The underlying research describes short-interval price impact contemporaneously. This tests the later move after the observable event and entry delay. [Cont, Kukanov and Stoikov](https://academic.oup.com/jfec/article-abstract/12/1/47/816163) · [Hyperliquid market streams](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)

Use native BTC, ETH and SOL perpetuals. Capture the public `bbo` and `trades` streams with the local millisecond receipt clock. After 20 seconds of warmup, score two consecutive 60-second segments on a one-second grid. A signal requires at least six prints in the past three seconds, at least 70% of traded notional on one taker side, a midpoint move of at least 1 bp in that direction, and signed best-quote order-flow imbalance of at least half the average displayed one-side size. Skip signals with a spread above 2 bp. The order-flow statistic adds signed changes at the bid and ask, including best-price changes. All signal inputs must have been received by the decision.

Cross the touch with at most $200 per entry, rounded down to venue size increments and bounded by the observed touch size. Use the first observed quote after a 750 ms entry delay, hold ten seconds, and use the first quote after a 750 ms exit delay. Stress doubles the delays to 1,500 ms and adds 0.5 bp adverse execution per fill. Allow at most another one second for observing the quote. Charge Hyperliquid's Tier 0 taker fee of 4.5 bp per fill. A stale or absent exit makes the segment incomplete. Hold one position per contract at a time and leave time to close before either segment ends. The seconds-long holding period avoids hourly funding boundaries. Paper fills use the displayed touch and do not establish executable depth. [Hyperliquid fee schedule](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)

## Fresh paper replay on November 24, 2026

The public capture ran from 09:24:51.958 through 09:27:16.962 UTC. It recorded 4,167 BBO updates and 799 trade records. After deduplication and timing validation, 724 trades remained. The candidate generated ten signals in the first 60 seconds after warmup and twelve in the next. Median BBO receipt-minus-venue time ranged from 359 to 364 ms across the three contracts. Completed entries arrived 755–831 ms after the base signal and 1,505–1,629 ms after the stressed signal. The costs include the observed bid/ask spread and the published 9 bp round trip.

| 60-second UTC segment start | Costs | Completed $200-cap trades | Unresolved exits | Net on closed trades | Net bps on closed trade entries |
|---|---|---:|---:|---:|---:|
| 09:25:11.958 | Base | 1 | 2 | −0.097 USDC | −4.84 |
| 09:26:11.958 | Base | 4 | 0 | −0.737 USDC | −9.22 |
| 09:25:11.958 | Stress | 3 | 1 | −0.519 USDC | −8.66 |
| 09:26:11.958 | Stress | 2 | 1 | −0.411 USDC | −10.29 |

The unresolved exits occurred when the recorded top level was smaller than the held position. A second replay sized at **25 USDC** with the unchanged signal and delays. This size check was chosen after observing the exit limitation. It left no unresolved exits and closed four trades in each 60-second segment at both cost assumptions. Base returns on entry notional were **−11.20 and −9.22 bps**. Stressed returns were **−13.10 and −10.64 bps**. The strongest gross stressed midpoint move on the completed small trades was **+4.84 bps**, below round-trip fees before spread and the adverse execution reserve.

The frozen taker rule failed this fresh test. The sample covers two minutes of one market session and the three contracts can move together. Exchange response and fill priority remain unmeasured. [200 USDC reference report](../results/follow-up/native-flow-transition.json) · [25 USDC sizing check](../results/follow-up/native-flow-transition-25.json)

## Reproduction

```sh
node scripts/capture_native_flow.mjs data/agent-native-flow/new.jsonl 145
python -m lane_z.native_flow_transition data/agent-native-flow/new.jsonl
python -m lane_z.native_flow_transition data/agent-native-flow/new.jsonl --trade-cap 25
```

Use `data/agent-native-flow/live.jsonl` if the original ignored raw capture exists. The capture records public market data without signing orders or storing trader identifiers.
