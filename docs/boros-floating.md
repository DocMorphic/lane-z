# Forecast Hyperliquid funding on a single Boros contract

## Protocol fixed before scoring on November 24, 2026

The spot–perpetual rate lock failed at the fully funded capital split because the indicated rate applied to only 40% of full capital and needed to cover both Hyperliquid legs. A separate directional strategy takes rate risk without taking an underlying-token position. It holds USD₮0 collateral and buys or sells the single Boros contract that settles the corresponding Hyperliquid funding rate. It can lose capital as implied or realized funding diverges from its forecast. [Rate settlement](https://docs.pendle.finance/boros-docs/about-boros/rate-settlement) · [Margin model](https://docs.pendle.finance/boros-docs/boros-systems/margin-and-liquidations)

Fix the spot-lock screen's four native assets and USD₮0 Boros venues. Use last-Friday maturities January through October 2026 and start each paper window 28 days earlier at 00:00 UTC. Compute the median of four disjoint weekly averages of Hyperliquid's settled hourly funding APRs from the 28-day interval ending two hours before that decision. Require all events to have actually settled by the decision time. Check both directions against that timestamp's best bid and ask. Fix the candidate with the largest projected post-stress cash excess and trade only when it is positive. Each vintage starts with 2,000 cash-equivalent USDC and uses the same 2,000 notional, backed by the full account. The comparator compounds at 4% annual on 2,000 USDC over 28 days, including the hour of modeled delay.

Use the Boros archive's next-hour top-of-book as the delayed execution proxy. Treat the opening settlement epoch as complete and include the events with nominal settlement hours strictly later than that hour, through maturity. The selected order must still clear the entry-time projection hurdle at the proxy. Apply the publication's signed fixed and floating cash-flow formulas. Charge the venue's listed time-scaled opening and hourly settlement fees. Base charges 1 USDC equivalent for the market entrance and 1 for gas and transfers. Stress reserves 4 total USDC equivalent, moves the execution rate 100 bp annualized against the position, haircuts final USD₮0 recovery by 25 bp, and stresses the adverse mark by 10 percentage points of annualized APR for the margin check. There are no exit fills at contractual maturity. [Archive fields](https://docs.pendle.finance/boros-dev/Backend/historical-data) · [Fees](https://docs.pendle.finance/boros-dev/Mechanics/Fees) · [Trade and floating-payment accounting](https://docs.pendle.finance/boros-dev/Mechanics/Settlement)

Require complete hourly on-chain settlement events and archived market marks through the live position, including its terminal event. Prices at the sampled touches are best-case proxies for available depth. Require positive stress cash excess in at least three consecutive matured vintages before validating the full 2,000-unit amount against historical order books and recording an independent prospective rule. Funding archives overlap earlier studies. With the rules fixed before this replay, these results diagnose the rate model but remain retrospective.

## Result

Nine of ten forecast vintages produced no qualifying entry. The one delayed-entry signal paid floating SOL funding in the February 27 to March 27 maturity window. The past weekly medians forecast −17.93% annual floating APR. Across 671 observed settlement hours, realized floating funding averaged −7.60%. The strategy received 11.64 USDC on its floating payments, but paid 5.77 USDC for the fixed leg before base costs. It earned 3.63 USDC in base and lost 4.90 USDC in stress, against compounded cash of 6.03 USDC. Without the extra 25 bp collateral haircut, the stressed margin over initial capital was still only 0.10 USDC.

Archived hourly marks showed no modeled margin breach. In stress the minimum headroom was 1,967.70 USDC. The forecast overestimated how negative funding would stay. The fixed rule failed cash in every observed vintage and was retired. No full-size execution or real orders were evaluated. [Forecast decisions, maturity cash flows, and input hashes](../results/follow-up/boros-floating.json)

```sh
python -m lane_z.boros_lock --fetch --data data/boros-yields --output data/boros-yields/lock.json
python -m lane_z.boros_floating --data data/boros-yields --fetch \
  --funding data/agent-spot-carry data/agent-spot-carry/older \
  data/agent-native-trend data/agent-selective-carry/late \
  --output data/boros-yields/floating.json
```

The funding tapes use the official Hyperliquid funding-history API and can be restored from the capture commands in the earlier [spot carry](spot-carry.md), [native trend](native-trend.md) and [selective carry](selective-carry.md) studies. The report identifies every file it used.
