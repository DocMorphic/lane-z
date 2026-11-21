# Hyperliquid order-level paper smoke test

## Sample and verification

This diagnostic uses the public, no-key 0xArchive `xyz:TSLA` sample from November 10, 2026. The Parquet files supply order-level changes, order lifecycle events, three checkpoints, and trades with explicit `crossed`, order ID, side, price, and size. Replay only the interval bracketed by the first and final checkpoints, 15:01:35.730–15:03:35.739 UTC. Checkpoint order IDs, sides, prices, and sizes must reconcile exactly after applying ordered changes in each interval. Venue block times describe event order. The sample has no usable local receipt timestamps for this run. This is one small weekday segment for checking the simulator. [Sample manifest and checksums](https://0xarchive.io/.well-known/data-samples.json)

HyperCore publishes price and time priority. Paid ALO priority can reorder the queue tail at the same price for roughly 400 ms. This diagnostic uses zero-priority quotes and gives all same-price orders arriving inside that interval precedence. It credits no hypothetical fills during the interval. Block timestamps and sequence numbers give historical order for observed actions. They leave an intra-block ambiguity for a hypothetical insertion, so the replay joins after a completed block and ignores its fills. [Order-book rules](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/order-book) · [ALO priority](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/priority-fees) · [Order data formats](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/nodes/l1-data-schemas)

## Frozen policy

- Model market-data receipt at recorded venue time plus 500 ms. Use a 2-second maximum age from venue time. Allow at most one working order or open position. At a 2-second cadence, beginning one second after the first checkpoint and stopping eight seconds before the final one, alternate buy and sell by time slot and post a $100 join-the-touch ALO quote if flat. Round size down to 0.001 units. Skip orders below $10. Reject a placement if the original touch moved or the limit would immediately cross at the modeled arrival block.
- Replay outbound order, cancel and response latency at 250, 500, and 900 ms per message. Map arrival to the first recorded market block at or after the modeled time. Request cancellation one second after modeled placement acknowledgement. Allow fills through the cancellation arrival block before treating the order as gone.
- After the 400 ms priority window, count only actual aggressor volume supported by a paired public maker and taker print and by a size reduction on the same previously resting maker order. A print at the price of the simulated order can count only if it filled an order behind the simulated queue position. A print strictly through the limit can count in the direction that would have consumed the simulated order first. Cap credited size at the observed execution size and remaining simulated size. Record the simulated maker price at its resting limit. Cancellations and quote touches yield zero execution credit.
- Send one IOC exit once cancellation is acknowledged and at least two seconds have elapsed since the first fill. Use the visible opposite touch at that point with the existing 10 bps exit cap. On arrival use only displayed quantity at the recorded touch, and flag any remaining inventory. Charge Tier 0 growth-mode fees of 0.3 bps on maker entry and 0.9 bps on taker exit, with a 0.5 bps exit reserve. This short window sits entirely between funding times. [Trade[XYZ] fees](https://docs.trade.xyz/perpetuals/mechanics/fees/)

The smoke test estimates mechanical fills under explicit queue and clock assumptions. Even after exact checkpoint reconciliation, unobserved within-block details and the market response to a hypothetical order limit what can be inferred. The two-minute interval cannot establish a profitable strategy.

## Result

The 30,953 ordered changes across 1,354 blocks reproduced all order IDs, sizes, sides, and prices in the two later checkpoints. The replay paired 208 trades by venue timestamp and trade ID. Of those, 154 had a previously resting maker order whose recorded size fell by at least as much as its matched execution volume. Only that subset could support a hypothetical entry. The lifecycle also records 15,248 `canceled` events and 234 `filled` events. The replay gives cancellations zero fill credit.

| Modeled delay per message | Attempts | Orders rested | Maker fills | Closed positions | Net on closed positions | Remaining inventory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 250 ms | 50 | 6 | 1 | 1 | −$0.03020 | 0 |
| 500 ms | 15 | 3 | 1 | 0 | — | 0.198 TSLA |
| 900 ms | 15 | 1 | 1 | 0 | — | 0.205 TSLA |

Each modeled entry was 0.265 TSLA and backed by confirmed trades strictly through the resting limit. No same-price trade supported a simulated fill. At 500 ms the top-level IOC closed 0.067 TSLA, and at 900 ms it closed 0.060 TSLA. Both IOC prices were inside the cap. Both runs halted with inventory under the single-attempt, top-level exit rule, which omits deeper liquidity and retries. Their closed-position P&L has no completed trades to measure.

[Aggregate replay report](../results/follow-up/hyperliquid-tsla-l4-smoke.json)

## Reproduction

Download the referenced sample files and run the replay in the research Python environment. The replay checks all five files against the manifest for byte size, SHA-256, and row count. Keep the Parquet files under the ignored `data/` directory. [Sample manifest](https://0xarchive.io/.well-known/data-samples.json) · [Data license](https://0xarchive.io/policies/data-license)

```sh
sample_dir=data/agent-passive-l4
mkdir -p "$sample_dir"
curl -fsSL https://0xarchive.io/.well-known/data-samples.json -o "$sample_dir/manifest.json"
for file in l2_orderbook l4_checkpoints l4_orderbook l4_orders trades; do
  curl -fsSL "https://0xarchive.io/samples/evaluation/data/hip3/xyz-tsla/$file.parquet" \
    -o "$sample_dir/$file.parquet"
done
.venv/bin/python -m lane_z.l4_smoke --data "$sample_dir" --output "$sample_dir/run.json"
```
