# limUSD paper allocation on HyperEVM

## Protocol fixed before scoring

Liminal's limUSD holds an externally managed basket of Hyperliquid strategies. The issuer describes spot–perpetual carry, staked-asset income and money-market yield. This tests an allocatable share of that trading system as a benchmark for Lane Z's own signals. It uses 2,000 paper USDC already on HyperEVM and makes no deposits or redemptions. The issuer's fee-adjusted share values reflect aggregated positions. They do not disclose each strategy's realized trading return. [Issuer product and underlying trades](https://docs.liminal.money/tokenized/limusd) · [Issuer fee schedule](https://docs.liminal.money/more/fees)

Use daily UTC end-of-day prices per share from the documented public metrics endpoint. Check the supplied price per share against total vault assets divided by total shares so that investor flows do not become returns. The issuer had published its current approximate share value and product terms. The protocol uses coverage dates only from the historical response before scoring. Fix three consecutive 63-day vintages using the marks at the ends of May 17, July 19, September 20 and November 22, 2026. Start each from 2,000 USDC and benchmark all 2,000 at 4% annually compounded over the full 63 days. Each vintage pays its own exit costs. [Issuer metrics schema](https://docs.liminal.money/tokenized/developers/sdk/metrics) · [Official HyperEVM contract addresses](https://docs.liminal.money/tokenized/developers/limusd)

The base instant-redemption case subtracts the issuer's 30 bp fee and reserves 1 USDC for entry and exit gas. The stress instant case charges the same actual 30 bp fee, reserves 5 bp on entry and 3 USDC total gas, and haircuts the assessed exit share value by 10 bp. This is an assumed adverse execution allowance in addition to the issuer's fund and redemption fees. The separate queued-redemption case requests redemption three days before the vintage endpoint, values the payout at the lowest mark between request and completion, credits no interest while waiting, and reserves 5 bp on entry and 3 USDC for gas. This case assumes the documented three-day service window and charges no instant fee. Report a separate 100 bp exit-loss stress without adding funding income a second time. [Issuer redemption fees, queue and capacity terms](https://docs.liminal.money/tokenized/flows) · [Redemption interface](https://docs.liminal.money/tokenized/developers/interfaces/redemption-pipe)

A screen pass requires after-cost excess over cash in all three instant stress windows. Qualification also requires independent on-chain share-value checks on the endpoints and credible evidence for $2,000 redemption capacity or a verified queue and amount treatment. A NAV-based share replay measures historical paper returns. It cannot prove future yield or liquidity, and the external manager bears risk across token, counterparty, leveraged basis, and protocol positions.

## Result

The limUSD allocation cleared the compounded 4% cash hurdle in three consecutive 63-day paper windows, including the instant redemption fee and the additional execution and gas reserves. All columns are P&L in USDC on 2,000 USDC of starting capital. Gas is set aside before calculating minted shares. Uninvested reserves earn zero.

| Window using UTC closing marks | Cash | Base instant | Stressed instant | Stress excess vs cash | Queued stress |
| --- | ---: | ---: | ---: | ---: | ---: |
| May 17–July 19, 2026 | +13.59 | +19.05 | +14.00 | +0.42 | +19.23 |
| July 19–September 20, 2026 | +13.59 | +21.33 | +16.28 | +2.69 | +22.80 |
| September 20–November 22, 2026 | +13.59 | +22.69 | +17.64 | +4.06 | +24.17 |

The stressed returns annualize to 4.12%, 4.81% and 5.22%. As a secondary endpoint check, the same policy cleared cash in 122 of 127 overlapping 63-day windows in the same interval. These overlapping windows share most of their days. The worst cash shortfall in that check was 0.62 USDC. An additional 100 bp exit loss made each of the three fixed vintages lose money outright. The cash edge is small relative to plausible losses from the strategy, stablecoin or contract risks. [Issuer risk disclosures](https://docs.liminal.money/tokenized/risks)

The issuer metrics contain 190 daily marks in the test range. All 190 reported prices per share match the supplied assets-to-shares ratios to within 0.000001 USDC. No drawdown appears in the sampled issuer marks. The on-chain share valuation is updated periodically and can miss the speed and depth of a loss in the underlying holdings. The paper allocation begins and ends with HyperEVM USDC. Cash conversion outside HyperEVM and personal taxes are outside the replay.

## Independent on-chain check

Two archive providers returned identical contract state and canonical block hashes at all four endpoint timestamps. In all cases, the issuer price differs from `NAVOracle.getNAV() / ShareManager.totalSupply()` by less than 0.0071 basis points, consistent with rounding the reported share mark to six decimals. The deposit and redemption pipes were unpaused, with deposit capacity, and the instant fee was 30 bps. The redemption contract takes USDC from its liquidity provider. The lowest spendable reserve observed at these four endpoints was 96,870.27 USDC, above the modeled redemption size. This test checks capacity at historical snapshots with read-only calls. Concurrent withdrawals and independent valuation of the manager’s underlying positions are outside the audit. [Hyperliquid archive RPC references](https://hyperliquid.gitbook.io/hyperliquid-docs/builder-tools/hyperevm-tools) · [Liminal contract addresses](https://docs.liminal.money/tokenized/developers/limusd) · [Deployed redemption logic](https://github.com/Lmnal/liminal-contracts/blob/80d3c9ff6400d73a24b721f79e89fab3f42df9d3/src/RedemptionPipe.sol)

| UTC date at 23:59:59 | HyperEVM block | On-chain share value, USDC | Spendable provider USDC |
| --- | ---: | ---: | ---: |
| May 17, 2026 | 35,387,475 | 1.002662619614 | 393,038.73 |
| July 19, 2026 | 40,920,592 | 1.015764711644 | 583,967.65 |
| September 20, 2026 | 46,453,895 | 1.030200156336 | 486,836.35 |
| November 22, 2026 | 51,987,233 | 1.045548236176 | 96,870.27 |

The independent audit also called `previewDeposit` with the actual modeled entry amount at each opening block and `previewRedeem` with those exact integer share units at the corresponding closing block. It confirmed stressed gains of 14.001578, 16.276091 and 17.641361 USDC after the additional modeled exit haircut. All archive-based gains exceeded the benchmark. The largest difference from the issuer-mark replay was under 0.002 USDC. [Archive states and block hashes](../results/follow-up/liminal-archive-state.json) · [Exact amounts and contract previews](../results/follow-up/liminal-archive-quotes.json)

## Reproduce

```sh
python -m lane_z.liminal_allocation --data results/follow-up/liminal-issuer-marks.json \
  --output data/liminal/reproduced.json
cmp data/liminal/reproduced.json results/follow-up/liminal-allocation.json
```

The report preserves the issuer snapshot digest. Add `--fetch` with a fresh `--data` path to fetch the current metrics response. The preserved issuer data include only publicly reported aggregate fund marks. The archive files encode large integer balances and token units as decimal strings to preserve precision. [Replay figures](../results/follow-up/liminal-allocation.json) · [Frozen issuer input](../results/follow-up/liminal-issuer-marks.json)
