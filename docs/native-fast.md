# Hyperliquid 30-second flow reversal

Large trade bursts can remove resting liquidity and push the midpoint away from the level it would reach with a deeper book. This paper rule waits for that move and then requires public trades to turn against it. The trade feed identifies aggressor direction. Attribution to forced liquidations remains unknown. Cont, Kukanov, and Stoikov find that short-term order-flow imbalance relates to price changes and that the price response depends on available depth. Actual exchange experiments also show that stale quotes and uncertain fills can inflate hypothetical high-frequency returns. [Order-flow research](https://arxiv.org/abs/1011.6402) · [Live execution study](https://doi.org/10.1080/14697688.2025.2515933)

## Frozen test

- Use Hyperliquid native BTC, ETH, SOL, HYPE, DOGE and SUI perpetuals. Sample decisions on a 250 ms local receipt-time grid. An impulse requires a 3-second midpoint move of at least 10 bps in absolute terms and at least four trade prints in the same 3 seconds, with net signed dollar flow of at least 60% in the direction of the move. Require fresh snapshots no more than 1,000 ms old at both midpoint marks. Discard negative receipt lags and trade prints more than 1,000 ms old. Duplicate exchange trades count once.
- An impulse starts a four-second observation window. To enter a reversal, require the midpoint to move at least 2.5 bps against the most extreme midpoint observed after the impulse. In the last second the net dollar flow against the impulse must be at least 25% of gross traded volume, on at least two trades. Permit entry only after the impulse grid time. The matched continuation diagnostic takes the impulse direction at the impulse grid time. Use only midpoints received by the decision time.
- Use 100 USDC maximum per symbol and one position per symbol, no leverage or overlapping same-symbol entry. Round orders down to the exchange lot increment. Decisions must observe a spread of at most 5 bps. At each entry arrival, use the most recent eligible venue-time L2 snapshot, falling back to the first eligible snapshot observed no more than 1,000 ms after arrival if the last is stale. Walk bids or asks and cap entry price movement from the decision touch to 5 bps. A partial fill must meet the 10 USDC minimum. After 30 seconds from entry, cross the opposite L2 with a second execution delay, using at most the displayed depth per snapshot and retrying only at distinct snapshots for an incomplete exit. Reject candidates whose execution can cross an hourly funding checkpoint or extend past the known capture window. Count any unresolved exit separately, with no positive result allowed on a window containing one.
- Report 250, 500 and 900 ms delays separately, at the Tier 0 native fee of 4.5 bp per taker leg. Apply 1 bp additional adverse execution cost per side and run a 900 ms stress at 2 bp per side. Report completed trades, attempts, P&L, costs and per-market concentrations. Fresh windows are determined by the public capture schedule and are exploratory forward tests. Promotion requires positive stressed results in at least three distinct windows on at least two UTC dates and evidence across multiple symbols. [WebSocket contracts](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions) · [Native fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)

## Best-quote follow-up

The initial 180-second window produced only 14–15 usable full-book snapshots per market. Freeze a separate `bbo` feed replay with the same timing, signals, prices and costs. This version fills only against the displayed best price and size. Run fresh 180- and 300-second captures in order. The order-flow and price thresholds are unchanged.

## November 24 paper readout

| UTC capture | Feed | Valid book snapshots | Decision rows with both fresh midpoint marks | Completed reversal or continuation trades |
| --- | --- | ---: | ---: | ---: |
| 09:24:53–09:27:53 | L2 | 88 | 0 | 0 |
| 09:28:30–09:33:30 | L2 | 156 | 0 | 0 |
| 09:29:52–09:32:51 | Best quotes | 8,619 | 3,766 | 0 |

The initial full-depth feed lacked the cadence for this signal. Best quotes provided sufficient price-pair coverage to evaluate 3,766 market decisions. The largest observed absolute three-second midpoint move was 8.47 bps, below the 10 bp impulse threshold. Neither rule attempted entry. The first best-quote sample remained at zero paper P&L across all delay settings. It represents about three minutes of one morning. Best-quote fills use displayed liquidity and model a counterfactual order. [Paper report and capture hashes](../results/follow-up/native-fast-2026-11-24.json)

```sh
mkdir -p data/native-fast
curl -fsS https://api.hyperliquid.xyz/info -H 'Content-Type: application/json' \
  --data '{"type":"meta"}' -o data/native-fast/meta.json
node scripts/capture-native.mjs data/native-fast/next.jsonl.gz 300 --bbo
python -m lane_z.native_fast data/native-fast/next.jsonl.gz \
  --meta data/native-fast/meta.json --output data/native-fast/next.json
```
