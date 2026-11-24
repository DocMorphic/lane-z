# HyperLend native USDC lending paper test

## Frozen protocol

Test unlevered supply of native USDC to HyperLend’s core pool as an independent credit exposure. A supplier receives a rebasing hUSDC claim. The reserve’s normalized income index measures the increase in that claim. Use native USDC, no borrowing, collateral loop, extra incentive or assumption of funding income. This is floating borrower-paid lending income. [Core pool architecture](https://docs.hyperlend.finance/developer-documentation/core-pools) · [Native USDC reserve deployment](https://docs.hyperlend.finance/developer-documentation/contract-addresses)

Use the same three consecutive 63-day vintages and $2,000 funding size as Lane Z’s existing limUSD replay. Pin each end-of-day state on May 17, July 19, September 20 and November 22, 2026 on two independent archive providers. Preserve scaled hUSDC units from the entry-index calculation to maturity, and apply the exit index to those units. Verify the issuer-listed hUSDC address, USDC decimals, active or paused status, entry supply-cap headroom, observable hUSDC reserve deficit where the ABI provides it, and actual underlying USDC balance available at the exit. Record virtual reserve balance when present, while using the actual underlying balance for the token transfer check. Apply base 1 USDC gas reserve. In stress reserve 3 USDC gas, apply a 5 bp reduction to the invested amount and a 10 bp reduction to exit proceeds. Show a separate 100 bp loss stress. No rewards, borrow rates, UI APY or fees outside the model are credited. [HyperLend supply and withdrawal](https://docs.hyperlend.finance/how-to/how-to-hyperlend/how-to-lend-on-hyperlend) · [Pool contract](https://docs.hyperlend.finance/developer-documentation/core-pools/pool)

Compare against 4% annually compounded on the entire initial $2,000 across all 63 days. Require each vintage to outperform cash after both the base and primary stress costs, with available entry and exit capacity. Report gross interest, absolute P&L, and excess above cash independently. Historical hUSDC index accuracy and available liquidity do not establish future reserve solvency or an executable position.

## Results

| 2026 vintage | Gross interest (USDC) | Base paper P&L (USDC) | Stress paper P&L (USDC) | Full-capital cash P&L (USDC) |
| --- | ---: | ---: | ---: | ---: |
| May 17–July 19 | 21.219075 | +20.208465 | +15.160974 | +13.585136 |
| July 19–September 20 | 21.878610 | +20.867671 | +15.818533 | +13.585136 |
| September 20–November 22 | 18.124634 | +17.115572 | +12.075809 | +13.585136 |

**Decision.** The core reserve beat cash in every base case. The latest stress case fell 1.509327 USDC short, so native USDC lending failed the frozen rule. A further 100 bp exit loss made every vintage negative in absolute USDC. Returns vary with borrowed utilization and the realized funding of loan liquidity.

Both archive providers agreed on all four canonical blocks, exact reserve index, scaled and unscaled hUSDC supply, and USDC cash at the hUSDC contract. Issuer-listed and on-chain reserve addresses matched. The historical native balance agreed with the pool’s virtual balance. The reserve was active, unpaused and under its supply cap, with more than 3.96 million USDC available at every boundary. All modeled lots passed entry and exit checks. Reserve deficit was zero in May and 0.003314 USDC on each of the later observations. These values are snapshots at the requested blocks. [HyperLend core source](https://github.com/hyperlendx/hyperlend-core) · [HyperLend contract addresses](https://docs.hyperlend.finance/developer-documentation/contract-addresses)

```sh
python -m lane_z.hyperlend_usdc fetch
python -m lane_z.hyperlend_usdc run --output data/agent-hyperlend-usdc/report.json
```
