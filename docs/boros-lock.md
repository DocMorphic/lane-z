# Hyperliquid funding lock with Boros

## Screen fixed before reading entry rates on November 24, 2026

Test the economics of replacing floating funding on a fully funded Hyperliquid cash-and-carry position with an executable fixed-rate bid on a Boros contract referencing the same Hyperliquid market. The original carry tests retained floating-rate risk. This variant would buy spot, short the matching perpetual, and short a Boros Yield Unit (YU) so the swap receives a fixed rate and owes floating rates through maturity. Actual cash flows could leave funding oracle, USD₮0 versus USDC, basis, and execution mismatches. [Hyperliquid funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) · [Boros settlement](https://docs.pendle.finance/boros-docs/about-boros/rate-settlement)

Fix eligible Hyperliquid BTC, ETH, SOL and HYPE. Require a matching Boros funding contract, hourly settlement, and the USD₮0 collateral variant (token ID 3), to avoid assuming that unhedged BTC, ETH or HYPE collateral is cash. Use the last Friday UTC maturity in each month from January to November 2026. Freeze the entry observation at 00:00 UTC exactly 28 days before that maturity. Use the official archived `bestBid` on that hour, the rate available to a short-YU taker if the displayed order can fill. If several eligible markets exist within a vintage, rank by the lowest-cost modeled profit using only their entry marks. Missing rates do not establish executable economics. The November 27 maturity is pending until the maturity has passed, even though its entry indication is historical. [Archive schema](https://docs.pendle.finance/boros-dev/Backend/historical-data)

Start each paper vintage with 2,000 USDC-equivalent. The notional on each Hyperliquid leg is 800 USDC. Reserve 800 for spot, 800 for the matching short and 400 for margin and costs on Boros. Use no leverage beyond this split. Report the fixed rate times the 28-day remaining term times 800 as the pre-cost projection. Compare it with 4% annually compounded on the entire 2,000 USDC-equivalent for 28 days. The favorable screen first charges only the published Tier 0 Hyperliquid spot and perpetual round-trip taker fees of 23 bp on the 800 USDC common notional. A second case adds the listed Boros taker fee and approximate settlement fee over the term, 10 bp of adverse execution on each of the four Hyperliquid legs and 4 USDC for the Boros market entrance fee and gas/transfers. It uses current archive metadata for scheduled Boros fees rather than claiming historical fee accrual. Both cases assume par between USD₮0 and USDC, sufficient depth at the bid, immediate fills, matched settlement amounts, stable collateral and no basis loss. [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) · [Boros fees](https://docs.pendle.finance/boros-docs/boros-systems/fees)

A favorable projection is a screen for a complete paper replay. Require at least three consecutive matured vintages with positive cash excess under the cost-aware case before treating it as a recurring hypothesis. An executable candidate also needs archive depth at order arrival, settlement cash flows in both venues, conservative intra-period margin checks, exit basis, conversion and funding-oracle reconciliation. This screen cannot count a paper winner. The original limUSD forward protocol and position remain separate.

## Result

The best observed bid in each of the ten matured windows fell short of the 6.03 USDC cash hurdle even with only Hyperliquid’s four taker fees. Nine fell short before any fee. The publisher did not export a March maturity HYPE entry file, so that market is unobserved for the March vintage. The best 28-day observed indication was the January HYPE bid at 11.34% annualized. It projected 6.96 USDC gross, 5.12 after only the Hyperliquid fees, and −2.17 with the modeled Boros and execution costs. The November observation had not matured at the cutoff and showed the same cost problem.

| Maturity | Highest cost-aware candidate | Fixed bid APR | Modeled net, optimistic | Modeled net, with cost stress |
| --- | --- | ---: | ---: | ---: |
| January 30 | HYPE | 11.34% | +5.12 | −2.17 |
| February 27 | BTC | 7.99% | +3.07 | −4.23 |
| March 27 | BTC | 5.59% | +1.59 | −5.70 |
| April 24 | HYPE | 7.98% | +3.06 | −4.23 |
| May 29 | HYPE | 4.60% | +0.98 | −6.31 |
| June 26 | HYPE | 6.41% | +2.09 | −5.20 |
| July 31 | HYPE | 9.02% | +3.70 | −3.59 |
| August 28 | HYPE | 7.90% | +3.01 | −4.29 |
| September 25 | HYPE | 9.28% | +3.85 | −3.44 |
| October 30 | HYPE | 8.44% | +3.34 | −3.95 |
| November 27, pending | HYPE | 9.09% | +3.74 | −3.55 |

Under the stated funding and execution assumptions, locking the rate is insufficient at this capital split. A quoted bid provides a useful rejection screen here. No historical entry size, execution or actual settlement cash flow was simulated, and gains or losses from the spot–perpetual basis remain outside the screen. [Quote-level projections and source hashes](../results/follow-up/boros-lock.json)

```sh
python -m lane_z.boros_lock --data data/boros-yields --output data/boros-yields/recheck.json --fetch
```
