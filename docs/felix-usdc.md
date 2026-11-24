# Felix USDC on Morpho paper replay

## Frozen protocol

Test the Morpho-listed Felix flagship native USDC vault on HyperEVM with one $2,000 paper lot and no tokens, leverage or collateral reuse. Select the newer V2 only if native-USDC vault code exists at all four fixed endpoints. Both archive providers found V2 bytecode absent on May 17 and July 19, 2026. The listed V1 at `0x8A862fD6c12f9ad34C9c2ff45AB2b6712e8CEa27` existed with matching bytecode and native USDC as its asset on all four dates. Use V1 alone without joining V2’s history. [Morpho Felix V1](https://app.morpho.org/hyperevm/vault/0x8A862fD6c12f9ad34C9c2ff45AB2b6712e8CEa27/felix-usdc) · [Morpho Felix V2](https://app.morpho.org/hyperevm/vault/0x51254785367d73A10a2Ea7d44B8e97b749BfbE8b/felix-usdc-v2)

Use the same three consecutive 63-day windows as the limUSD and HyperLend replays, with UTC closing checkpoints on May 17, July 19, September 20, and November 22, 2026. All capital holds one position to each window’s end. At each entry use the total amount of USDC left after reserve and entry haircut in the historical `previewDeposit` and carry its exact shares forward to the exit’s `previewRedeem`. Verify historical deposit capacity via `maxDeposit` and withdrawal capacity via idle cash and enabled withdrawal markets or an actual shareholder’s sufficient `maxWithdraw`. Vault interest and fees are embedded in the exact quote. Use two independent canonical archival reads. No custody, signers, deposits, transfers or redemptions are used in this test. [MetaMorpho implementation](https://github.com/morpho-org/metamorpho/blob/main/src/v1.1/MetaMorphoV1_1.sol)

Require all three windows to beat 4% annual compounding on the entire $2,000 with base costs and primary stress costs and to have deposit and redemption capacity. Apply a 1 USDC gas reserve in base and a 3 USDC reserve plus a 5 bp entry and 10 bp exit haircut in stress. Separately test a 100 bp loss added to stress. Report absolute P&L and excess versus cash. Retry or change providers on infrastructure errors while preserving the validated windows. If a deposit or capacity test does not pass, mark that window as ineligible. Do not assume the other vault would behave identically.

## Results

| Dates, UTC | No-friction P&L | Base P&L | Stress P&L | 4% cash P&L | Conservative exit liquidity |
| --- | ---: | ---: | ---: | ---: | ---: |
| May 17–July 19 | +20.155837 | +19.145759 | +14.100922 | +13.585136 | 2,768,828.460758 USDC |
| July 19–September 20 | +21.357985 | +20.347306 | +15.299468 | +13.585136 | 4,275,884.284276 USDC |
| September 20–November 22 | +16.115587 | +15.107530 | +10.072783 | +13.585136 | 2,652,273.291151 USDC |

**Decision.** The V1 position made positive paper P&L after modeled costs in all three windows. In the most recent window, the stressed result missed cash by 3.512353 USDC, failing the frozen rule. The additional 100 bp loss produced −6.060248, −4.873700 and −10.048066 USDC. V2 had enough history for one complete window in the same date grid. V1 historical results do not validate V2.

Two archival providers independently matched the exact 18-decimal share amounts and six-decimal USDC exit quotes. Both recorded a 10% fee on vault performance and a 0.000001 USDC `lostAssets` accounting balance. All paper vintages had deposit headroom and exit liquidity sufficient for the exact quoted amount. The cash lower bound sums vault-owned USDC claims limited by unborrowed supply in each unique withdrawal-queue market, then caps the total at actual USDC held by Morpho. The replay floors conversions from raw market state and excludes unaccrued interest. [MetaMorpho withdrawal logic](https://github.com/morpho-org/metamorpho/blob/main/src/v1.1/MetaMorphoV1_1.sol) · [Morpho Blue liquidity and share logic](https://github.com/morpho-org/morpho-blue/tree/main/src)

```sh
python -m lane_z.felix_usdc fetch
python -m lane_z.felix_usdc run --output data/agent-felix-usdc/report.json
```
