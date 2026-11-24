# HyperEVM xToken paper comparison

## Frozen research protocol

Compare three distinct ways of accessing Liminal's managed Hyperliquid yield through xBTC, xHYPE and xLEND, alongside the already fixed limUSD experiment. Hold the share tokens directly with 2,000 paper USDC available on HyperEVM, without borrowing, bridge transfers, yield boosts, token incentives, or real transactions. The BTC and HYPE products describe single-asset delta-neutral strategies. xLEND represents a different money-market route. The manager's pooled return already includes underlying trades, funding and operating performance. Issuer-valued share marks do not independently verify strategy assets, positions or custody. [Issuer product description](https://docs.liminal.money/tokenized/intro) · [Issuer SDK](https://docs.liminal.money/tokenized/developers/sdk) · [Issuer risk disclosures](https://docs.liminal.money/tokenized/risks)

- Fix the same three consecutive 63-day windows as the earlier limUSD replay, based on UTC end-of-day marks on May 17, July 19, September 20 and November 22, 2026. Reinitialize the entire account each window. Require positive assets and supply with a complete daily series for each scored window. Check the stated marks against vault assets divided by share supply, using a 0.000001 USDC tolerance. Do not move a window, backfill missing marks, interpolate daily values, or use a reported trailing APY as a realized return.
- Use actual native HyperEVM USDC entry and instant redemption. Reuse the previously verified archive boundary blocks and require two archive providers to agree on historical addresses, state and quotes. Where deployments or usable histories differ, keep a documented missing or failed window. Historical conditions include the USDC asset at both pipes, deposit and redemption pauses, share and token restrictions, exact integer amount previews, per-transaction mint and withdrawal limits, room under the supply cap, and the funded USDC liquidity provider's balance and allowance. Require the contract preview and every eligibility check at both candidate entry and exit before treating a result as executable under the model. The synthetic holder may be checked against restrictions. It has no personal balances, approvals or claim to ownership. Check upgrade and privileged-control configuration and record shared issuer/payment sources. [xBTC addresses](https://docs.liminal.money/tokenized/developers/xbtc) · [xHYPE addresses](https://docs.liminal.money/tokenized/developers/xhype) · [Redemption interface](https://docs.liminal.money/tokenized/developers/interfaces/redemption-pipe)
- Use the higher documented precision where possible. Base charges 1 USDC total gas. Stress uses 3 USDC gas, a 5 basis point cash entry reserve and a further 10 basis point haircut on the instant payout, using the same full-capital convention as limUSD. Independently show a fixed 30 basis point exit as the published fee assumption and historical deployed fee and quote as the primary result, without charging the deployed fee twice. Stress a further 100 basis point loss on the exit in a separate sensitivity. At every vintage compare the full 2,000 USDC compounded at an effective 4% annual rate and prior limUSD's corresponding settled stressed P&L. [Issuer minting and redemption guide](https://docs.liminal.money/tokenized/flows) · [Prior limUSD paper audit](liminal-allocation.md)
- For concentration and downside report issuer daily NAV mark drawdown, distance from the cash hurdle after actual fees, reserve relative to the modeled exit and outstanding share NAV, per-contract governance or implementation evidence, and overlapping positions or operators where disclosed. A candidate requires complete after-cost contract evidence and positive stressed outperformance versus both cash and limUSD in every frozen window. Current estimates, early custody records, a common venue and unverified off-chain counterparties do not establish independent diversification or prospective results.

## Paper results

The protocol was frozen at 00:01:57 UTC on November 24, 2026 before retrieving the historical marks. Its SHA-256 was `93354adb8d7e5f5d5d8232874b04be4c42f1f64eee0eabafea493597c471d3a9`. Each candidate supplied all 190 required daily marks. dRPC and PublicNode agreed on the four archived endpoint blocks and matched contract state and integer previews. The largest difference between issuer and on-chain endpoint NAV per share was less than 0.000001 USDC. An independent Decimal calculation reconciled 18 quote cases, including mint and redemption floors, caps, available liquidity, and benchmark math.

### Stressed P&L on 2,000 paper USDC

| Route | Instant fee observed | May 17–July 19 | July 19–September 20 | September 20–November 22 |
| --- | ---: | ---: | ---: | ---: |
| 4% effective annual cash | — | +13.59 | +13.59 | +13.59 |
| limUSD | 30 bp | +14.00 | +16.28 | +17.64 |
| xBTC | 30 bp | −4.57 | +15.77 | +21.06 |
| xHYPE | 30 bp | +13.71 | +20.23 | +19.35 |
| xLEND | 0 bp | +15.86 | +16.06 | +15.48 |
| xLEND with hypothetical 30 bp | 30 bp | +9.82 | +10.01 | +9.43 |

xHYPE beat cash in all three windows and trailed limUSD by 0.30 USDC in the first. Its first cash cushion was 0.12 USDC, or about 0.60 basis points of the exit quote. xLEND beat cash in all three windows at its deployed fee and trailed limUSD by 0.21 and 2.17 USDC in the last two. The historical xLEND zero fee was confirmed from its contract at all four boundaries and two separate completed public redemptions. xBTC missed cash in the first window by 18.15 USDC. None passed the requirement to beat both benchmarks in every stressed window. [xLEND product mechanism](https://liminal.money/blog/xlend-tokenizing-hyperliquid-money-market-yield) · [xLEND May on-chain fee receipt](https://hyperevmscan.io/tx/0x8e18510c374fcab2d2463d329c96faa10546ad20ccdc80497a61f1b047fce865) · [xLEND October on-chain fee receipt](https://hyperevmscan.io/tx/0x236325006fa589eeefcf6e6b2dbf4dfd2e8038b9e0fd674231a3309204bcc971)

All three products had zero drawdown at the issuer's published daily marks over the test dates. An additional 100 bp exit loss drove eight of the nine stressed vintages below their starting capital or within one cent of it. The largest remaining profit was 0.83 USDC. Daily reported NAV is limited evidence of intraday market losses, reserve competition or asset impairments.

### Capacity and concentration

Both chain providers found native USDC at the deposit and redemption pipes, unpaused controls and sufficient caps, allowance and reserve for each 2,000 USDC paper vintage at both endpoints. The thinnest result was the xBTC opening snapshot on May 17, when spendable reserves of 2,184.92 USDC covered just 1.10 times the immediate modeled exit. Across the four historical endpoints, reserves covered 1.61–10.55% of xBTC NAV, 2.07–4.90% of xHYPE NAV and 4.93–5.09% of xLEND NAV. These are point-in-time limits for the sampled size and do not establish liquidity during overlapping withdrawals.

| Route | NAV at November 24 00:07:44 UTC | Spendable USDC | Reserve / NAV | Deposit / withdrawal caps in shares | Instant fee |
| --- | ---: | ---: | ---: | ---: | ---: |
| xBTC | 1,642,013.43 | 58,219.93 | 3.55% | 2,000,000 / 2,000,000 | 30 bp |
| xHYPE | 9,728,075.17 | 318,245.22 | 3.27% | 2,000,000 / 2,000,000 | 30 bp |
| xLEND | 717,822.56 | 10,137.22 | 1.41% | 5,000,000 / 1,000,000 | 0 bp |

The current checks use HyperEVM block 52,075,546. xLEND's supply fell 36.78% since the November 22 endpoint while NAV per share rose 0.012%. Its spendable reserve fell from 56,693.01 to 10,137.22 USDC, an 82.12% decline. The recorded decline in pool size mainly coincided with a lower share count. The remaining reserve covered 5.08 times the modeled instant exit at that block.

Each product used a distinct account as both liquidity provider and deposit strategist. Those accounts had no contract bytecode at the sampled blocks, so their signer policy and balance-sheet support remain unverified. Each product's timelock owned all four observed proxy admins for its share, deposit, redemption and NAV contracts. The redemption timelock reported a two-day base delay. Current manager configuration reported 100 bp annual management and 1,000 bp performance fees for the three xTokens. The study uses the marks and integer previews after realized fee accounting. Any later fee crystallization, custody loss or withdrawal pause can still change results. Liminal describes limUSD as a portfolio that can own the same xTokens, so a separate token holding can duplicate exposure through the same issuer and trading venue. Historical underlying holdings and third-party custody backing remain unverified. [Issuer fee disclosures](https://liminal.money/faq) · [Issuer limUSD composition](https://docs.liminal.money/tokenized/limusd) · [Liminal risk disclosures](https://docs.liminal.money/tokenized/risks) · [Current archived block](https://hyperevmscan.io/block/52075546)

### Replay

Save the public issuer metrics, then capture or reuse the recorded contract states. The first capture writes intermediate provider-verified readings so a public RPC interruption can resume. The limUSD benchmark archive in the repository supplies the four dated block references and exact benchmark costs.

```sh
mkdir -p data/agent-hyperliquid-xtokens
for token in xBTC xHYPE xLEND; do
  curl -fsS "https://api.liminal.money/sdk/tokenized/${token}/metrics?range=max" \
    -o "data/agent-hyperliquid-xtokens/${token}-metrics.json"
done
python -m lane_z.hyperliquid_xtokens --data data/agent-hyperliquid-xtokens \
  --capture --output data/agent-hyperliquid-xtokens/report.json
```
