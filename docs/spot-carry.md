# Hyperliquid spot–perpetual funding carry

## Hypothesis and venue

Hold equal units of Unit Bitcoin (`UBTC/USDC`, public symbol `@142`) and a short native `BTC` perpetual. Apply the same rule to Unit Ethereum (`UETH/USDC`, `@151`) and the native `ETH` perpetual in a second sleeve. Each perp short receives positive funding and pays negative funding. Spot and perpetual traded prices can diverge while the hedge is open, and the Unit assets add tokenization risk. Hyperliquid calculates the hourly funding payment using the oracle price, which the public funding history response omits. Use the corresponding four-hour spot open as the price proxy for funding cash flows, then stress that approximation separately from the observed rates. [Hyperliquid funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) · [Unit supported assets](https://docs.hyperunit.xyz/unit/about-unit/supported-assets/)

Schmeling, Schrimpf, and Todorov document wide variation in crypto futures carry and associate it with demand for leveraged exposure and limits to arbitrage capital. He, Manela, Ross, and von Wachter show how funding, frictions, and convergence uncertainty affect perpetual futures pricing. Their results motivate measuring funding and basis separately. Neither supplies estimates of this strategy's future P&L. [BIS crypto carry](https://www.bis.org/publications/working-paper-1087-crypto-carry) · [Fundamentals of perpetual futures](https://arxiv.org/abs/2212.06888)

## Frozen paper rules

- Start each chronological window with 2,000 USDC, split into two equal sleeves. In each sleeve, check funding on Monday at 00:00 UTC. Add the settled hourly funding rates from the preceding 28 days, excluding any event at the decision time. Open a hedge when the sum is at least 50 bps and the sleeve is flat. Keep the hedge if already open. Close when the sum is zero or negative. This persistence rule uses only known funding. Fifty basis points exceeds the base four-fill cost of about 31 bps per matched notional at unchanged prices, with room for adverse basis moves. No parameter is fitted.
- Execute at the next four-hour bar's open, a public traded-price proxy. Determine a common base-unit quantity at the decision from 45% of sleeve equity divided by the larger of the two most recently completed candle closes. Round down to the common 0.00001 BTC or 0.0001 ETH lot. The remainder of the sleeve funds the short's USDC margin. Keep the matched quantity fixed while open. Debit spot buy fees in USDC equivalent as if the corresponding base units had been grossed up for the trade. No leverage, borrow, lending yield, or margin sharing across sleeves is assumed. Close each remaining hedge at the window endpoint.
- Charge Tier 0 taker fees of 7 bps spot and 4.5 bps perpetual on actual modeled fill notionals at entry and exit, and add 2 bps to each leg per fill for execution. Use a 10 bp allowance per fill in the high-cost scenario. In that scenario, also price positive funding at the spot bar low less 50 bps and negative funding at the bar high plus 50 bps. The separate funding stress additionally reduces positive funding rates by 25% without reducing negative payments. [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)
- Count realized funding during open positions using the published settled rates, excluding positive payments at an entry or exit boundary and charging negative payments at either boundary. Calculate price P&L as the spot gain plus the same-unit perp short gain. Require initial isolated collateral to cover the short notional and check it against a conservative 5% maintenance threshold on every four-hour bar using the perp high and only negative funding in that bar. Flag a window with a breach as incomplete. The simulation assumes both legs execute at their cost-adjusted proxies. Candle data cannot establish fill synchronization, depth, or the liquidation path after a breach. [Hyperliquid margining reference](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining)
- Compare with zero-yield idle USDC, an illustrative 4% annual cash hurdle on total sleeve capital, and an always-on same-asset hedge with the same allocation and trading costs. Keep margin collateral in the denominator for every return. Funding credited to the short stays in the sleeve. Compare both non-overlapping held-out windows after all costs and report counts, drawdown, funding, basis change, execution costs, and margin safety separately. A favorable historical result requires positive base and high-cost P&L in both complete held-out windows.

## Chronological windows and data

Use Hyperliquid's public four-hour spot and perpetual candles and hourly funding history. Begin collection on December 1, 2025 for funding warmup. Run a development check January 1–April 1, 2026, the first holdout April 1–August 1, and the confirmation holdout August 1–November 22, with UTC boundaries and independent accounts at the start of each window. These dates and rules were specified before fetching historical prices or funding rates. Verify market identity, prices, candle boundaries, and hourly funding coverage without filling missing records silently. [Hyperliquid public market-data API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)

After the 2026 comparison, freeze a separate September 1–December 1, 2025 confirmation window with an August 1 warmup. This earlier data precedes the original source tape and was unused in the 2026 comparison. Reuse both policies, timing, size, cost and margin assumptions without retuning.

## Results

All amounts are USDC profit on 2,000 USDC of capital per window, including short collateral. Every cell below completed its positions without a simulated margin breach. Each window had two round trips per policy, one per asset.

| Window | Filter base | Filter high cost | Filter funding stress | Always-on base | Always-on high cost | Always-on funding stress | 4% cash hurdle |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Sep 1–Dec 1, 2025 confirmation | +16.83 | +14.02 | +9.03 | +16.83 | +14.02 | +9.03 | +19.65 |
| Jan 1–Apr 1, development | +4.23 | +1.55 | −1.05 | +6.99 | +4.25 | +1.32 | +19.44 |
| Apr 1–Aug 1, holdout | +4.45 | +1.57 | −0.36 | +10.52 | +7.50 | +2.85 | +26.39 |
| Aug 1–Nov 22, confirmation | +25.06 | +21.95 | +14.58 | +25.13 | +21.98 | +14.44 | +24.43 |

In the first 2026 holdout, the filtered hedge earned 6.94 in modeled funding and 0.19 on the change in basis. It paid 1.99 in fees and 0.69 in execution allowance. In the second holdout it earned 28.03 in funding, lost 0.31 on the basis, and paid 1.97 in fees and 0.69 in execution allowance. Its base return was 0.22% and 1.25% respectively, with four-hour marked drawdown of 0.08% and 0.20%. The always-on version earned 0.53% and 1.26% on the same committed capital. The filter cut exposure in the first holdout and reduced drawdown. It earned less than always-on carry in both base windows and lost a small amount under funding stress in the first holdout.

Both rules took the same pair of positions in the 2025 confirmation. They earned 18.88 in funding and 0.40 on the basis, then paid 1.82 in fees and 0.63 in execution allowance under the base assumptions. The always-on version had positive P&L in all three independent windows under the combined high-cost and funding stress. Both policies fell short of the illustrative 4% cash hurdle in the first 2026 holdout and the 2025 confirmation. They cleared that hurdle in the second 2026 base run by less than 0.70 USDC. The higher-cost runs also fell short there.

The study has six completed test hedges per rule across three windows. It leaves the filter's added value unresolved and does not measure live fills or future funding. A separate November 22 public order-book check could cover 450 USDC at its best level in each direction on all four markets, roughly 0.66 bps and 1.47 bps from the midpoint for the BTC and ETH spot pairs, and 0.08 bps and 0.29 bps for their perps. That single book observation supports using a larger 2 bp base allowance for the modeled position size. It cannot establish historical basis synchronization or execution costs. Both modeled shorts were open during Hyperliquid's October 10, 2025 deleveraging episode. The margin checks assess collateral on those shorts. They cannot determine the venue's selection of an account for forced deleveraging or how a forced exit would break the hedge. [Hyperliquid order-book API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#l2-book-snapshot) · [Deleveraging rules](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/auto-deleveraging) · [October 10 empirical reconstruction](https://arxiv.org/abs/2603.15963)

### October 2025 forced-exit sensitivity

As a counterfactual, close the always-on sleeves at the October 11 00:00 UTC bar-open proxies and hold idle USDC thereafter. Under the existing combined funding and execution stress, the truncated positions earn **4.05 USDC**. An extra 50 bp charge on just the two perp buybacks costs 4.30 USDC and leaves **−0.25 USDC**. A 100 bp charge costs 8.60 USDC and leaves **−4.55 USDC**. This scenario assumes a full synchronized close at the proxy time. Actual deleveraging can close the short without closing the spot leg. The calculation measures sensitivity to the supplied costs while the actual account selection, event fill prices and uncovered spot price risk remain unknown.

### Independent execution check

Archived top quotes were sampled for the twelve planned legs at the April 1 entry, August 1 exit, and August 1 entry of the always-on holdouts. At 500 and 900 ms after the scheduled times, eight legs had a usable quote no more than two seconds old and sufficient displayed size. The largest adverse difference from the bar open on those eight legs was 0.16 bps. At five seconds, eleven of twelve legs had a fresh quote and sufficient size, with a maximum adverse difference of 1.62 bps. The November 22 exit was outside these public archive samples. This independently checks the cost assumptions where quotes were available. Synchronized execution remains unverified. [Execution check](../results/follow-up/spot-carry-execution-check.json) · [Tardis Hyperliquid collection](https://docs.tardis.dev/historical-data-details/hyperliquid)

An independent decimal-arithmetic calculation matched the always-on cash flows of both assets in all three scenarios in all three held-out windows. All 66 input digests matched. [2026 replay](../results/follow-up/spot-carry-2026.json) · [2025 replay and forced-exit stress](../results/follow-up/spot-carry-2025.json)

## Reproduction

```sh
python -m lane_z.spot_carry --capture --data data/agent-spot-carry \
  --output data/agent-spot-carry/spot-carry-result.json
python -m lane_z.spot_carry --older-confirmation --capture --data data/agent-spot-carry/older \
  --output data/agent-spot-carry/older-confirmation.json
```

Omit `--capture` to replay the cached input files without network access. Each report lists SHA-256 values and row counts for its inputs, full round-trip ledgers and payoffs by asset and scenario. Raw data stays under the ignored local `data/` directory.
