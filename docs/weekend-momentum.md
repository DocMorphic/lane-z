# Hyperliquid weekend momentum

This paper test asks whether a broad move across Hyperliquid equity perps persists long enough to trade after confirmation. It uses receipt-time best bid and offer for XYZ100, NVDA and TSLA. The strategy trades NVDA and TSLA and uses XYZ100 for confirmation.

## Frozen rule

Every 250 ms, measure log midpoint returns over 30 seconds. An eligible broad move requires XYZ100, NVDA and TSLA each to move at least 4 bps in the same direction. The target must also move at least 1 bp in that direction over the most recent 5 seconds, and its 30-second move must exceed its displayed spread plus 2.8 bps of assumed round-trip fees and slippage. Its spread must be at most 5 bps. The shared two-second quote-age limit applies at decision time for all three books and at order arrival for the target. Enter only when these conditions first hold or the qualifying direction changes.

The fixed target-only comparison uses the same grid rows with all required returns present and the same target thresholds. It derives direction from the target's 30-second move. Both outcomes are descriptive. The difference measures the effect of adding the two other contracts' confirmation to this fixed rule.

The paper order requests $100 and holds 30 seconds using the existing IOC limits, partial-fill logic, and hourly funding guard. Base order latency is 500 ms per leg, with 250 ms and 900 ms sensitivity. Each filled leg pays 0.9 bps in fees and reserves 0.5 bps for slippage. The original receipt clock determines what the strategy sees. Exchange timestamps determine the best displayed quote available at simulated arrival. The last two minutes are reserved for exits. The model approximates execution from top-of-book liquidity and omits market impact, queue interaction, and funding across any flagged unresolved boundary. Each symbol's position is simulated separately.

## Sample and reproduction

The fixed historical sample is February 1, March 1, August 1, and November 1, 2026. These are the four weekend dates available in the repository's Tardis archive at freeze time. Use 09:35–15:55 New York time on each date and retain a two-minute exit buffer. All four dates appeared in earlier Lane Z research. This new rule treats them as a chronological retrospective check without fitting or tuning. The first forward window starts at 19:05 UTC on November 21, after the 18:58 UTC strategy freeze. It resumes at 00:02 UTC on November 22. Replay only after the direct capture files have closed.

```sh
python -m lane_z.weekend_momentum --data data/tardis --retrospective \
  --output data/weekend-momentum-retrospective.json
python -m lane_z.weekend_momentum --data data/overnight-combined --date 2026-11-21 \
  --utc-window 19:05 23:59:59 --output data/weekend-momentum-2026-11-21.json
python -m lane_z.weekend_momentum --data data/overnight-combined --date 2026-11-22 \
  --utc-window 00:02 04:59 --output data/weekend-momentum-2026-11-22.json
```

## Retrospective results

| Assumed latency per leg | Broad-confirmed round trips | Broad-confirmed paper net | Target-only round trips | Target-only paper net |
|---|---:|---:|---:|---:|
| 250 ms | 2 | −$0.0418 | 39 | −$1.6916 |
| 500 ms | 2 | −$0.0418 | 39 | −$1.6200 |
| 900 ms | 2 | −$0.0418 | 38 | −$1.6539 |

The 500 ms replay had 11,215 common usable decision rows across both target symbols. The broad rule generated three onsets and completed two simulated trades, both on August 1. About $0.0142 of gross P&L fell short of $0.0560 in fees and reserved slippage. The comparison traded more often and lost more at every delay. Two round trips give little evidence about the effect of broad confirmation.
