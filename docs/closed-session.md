# Hyperliquid closed-session dislocation test

Aperiodic reports that book imbalance, short-horizon reversals and deviations from the oracle were more predictive in closed sessions across 28 Trade[XYZ] stock perps. Its 15-minute model tended to enter after unusually large price moves, and its simulated fills had zero latency and omitted funding. Nagel’s equity-market research links short-term reversals to liquidity provision and finds that their returns vary with market stress. That motivates a separate paper test of a slower reversal on Hyperliquid. [Aperiodic study](https://blog.aperiodic.io/p/closed-market-stock-perps) · [Nagel, *Evaporating Liquidity*](https://www.nber.org/papers/w17653)

## Frozen rule

At each five-minute UTC grid boundary, measure the last 15 minutes of receipt-time midpoint returns in NVDA or TSLA and XYZ100. Both endpoints of each return need a valid two-sided quote that is no more than two seconds old. A target must move at least 50 bps and diverge from XYZ100 by at least 25 bps in the same direction. Enter against the target move when its current top-of-book imbalance leans toward the trade by at least 0.20 and its spread is at most 5 bps. The index return controls for broad moves alongside the target return. The public quote tape provides no oracle series, so this test asks a narrower question than the published model. The rule uses no fitted parameters.

Request a $100 simulated IOC through the shared replay and hold for 15 minutes. The 500 ms base models order arrival per leg, with 250 ms and 900 ms sensitivity. Charge 0.9 bps in fees and reserve 0.5 bps for slippage per filled leg. Use the exchange clock for executable quotes and the receipt clock for the signal. The funding guard excludes entries when the scheduled exit approaches the next hourly funding boundary. Any delayed exit that crosses funding is flagged as incomplete. Keep at least two minutes between the scheduled exit and the end of the input window to allow retries.

## Sample

The retrospective sample uses the four Tardis weekend dates already used elsewhere in Lane Z. Read February 1, March 1, August 1 and November 1, 2026, during 09:35–15:57 New York time, with the ending reserve specified above. The first forward window is November 22, 2026, from 00:02 through 04:57 UTC. The opening 15 minutes provide signal history and fills must finish within the window. Freeze the rules before calculating the retrospective or forward results.

```sh
python -m lane_z.closed_session --retrospective --data data/tardis \
  --output data/closed-session-retrospective.json
python -m lane_z.closed_session --forward --data data/overnight-combined \
  --output data/closed-session-2026-11-22.json
```

## Retrospective results

The funding and exit guards left 392 scheduled symbol-bars. Only 66 had the fresh target and index observations required for both returns. Of those, 57 came from March 1 and none came from November 1. No supported bar reached the fixed 50 bps shock threshold. The rule attempted zero orders and produced $0 paper P&L at all three modeled latencies. Sparse quote support and the absence of a qualifying dislocation leave the trade hypothesis untested on these dates.

[Quote-only retrospective report](../results/follow-up/closed-session-retrospective.json)

## Fixed oracle-premium rule

This independent rule uses the observed Hyperliquid oracle instead of the index comparison. On each five-minute timestamp, compute the target's log premium to the latest oracle observation received no more than five seconds earlier. Center the current premium on the median of the preceding twelve five-minute observations, with at least eight valid values. The current point is excluded from that median. Historical midpoint observations used for the trailing premium and the 15-minute lookback can be up to 30 seconds old. The current midpoint and touch must meet the replay's regular two-second freshness limit.

Require a 50 bp target move over fifteen minutes and a centered premium 30 bps or more away in that same direction. Enter against the move if displayed imbalance is at least 0.20 in the trade direction and spread is at most 5 bps. Use the same funding guard, $100 size, 15-minute hold, tier-0 growth fees and slippage reserve, and the same 250/500/900 ms simulations. The oracle channel does not supply a venue timestamp, so its observations use the local receipt clock throughout. Tardis publishes the archived `index_price` in its normalized derivative ticker. Neither the quote-only rule nor this oracle variant fits weights from the live data. [Hyperliquid public context format](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions) · [Tardis public archive](https://docs.tardis.dev/historical-data-details/hyperliquid)

Run the oracle variant on the same four retrospective dates and November 22 forward window. The first hour in each window provides premium history. Freeze the rule before computing its outcomes. The report lists both quote and oracle file hashes.

```sh
python -m lane_z.oracle_dislocation --retrospective --data data/tardis \
  --oracle-data data/overnight-oracle-archive --output data/oracle-retrospective.json
python -m lane_z.oracle_dislocation --forward --data data/overnight-combined \
  --oracle-data data/overnight-hyperliquid-context --output data/oracle-2026-11-22.json
```

The retrospective oracle run had enough observations on 75 of 392 scheduled symbol-bars. None reached the 50 bp current-move threshold. It made zero attempts at each delay. The two-symbol sample gives no filled trades with which to evaluate the oracle signal. [Oracle retrospective report](../results/follow-up/oracle-dislocation-retrospective.json)
