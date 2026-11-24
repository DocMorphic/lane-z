# limUSD seven-day standard-redemption challenger

Liminal's standard HyperEVM redemption escrows the user's shares and pays native USDC when a fulfillment manager processes the request. Deployed `RedemptionPipeV2` allows partial fulfillment and prices each installment at the share value at fulfillment. The fee for this route is zero. The issuer documents a one-to-three-day service window. Queue payout depends on operator processing and the provider's USDC. The deployed ABI exposes a four-argument `requestRedeem` with an explicit recipient, and escrowed shares have no user cancellation or instant conversion. [Issuer redemption terms](https://docs.liminal.money/tokenized/flows) · [limUSD contract addresses](https://docs.liminal.money/tokenized/developers/limusd) · [Verified implementation source and ABI](https://sourcify.dev/server/v2/contract/999/0x3fac21fdd343486280b04447aede477f689881c7?fields=abi,sources)

## Frozen challenger

- Keep the original instant-redemption forward experiment intact. This is a separate historical paper replay from $2,000 using the same three 63-day UTC closing-mark windows, May 17–July 19, July 19–September 20, and September 20–November 22, 2026. Nothing is signed or sent. The dates and issuer NAV were previously used for the instant model, so a positive result is retrospective.
- Request standard redemption exactly seven UTC days before each endpoint, with no yield on recovered cash during the queue period. Reserve 5 bp on entry and 3 USDC total for gas. The stress payoff assumes the lowest published share value from request day through cutoff, applies the same 10 bp payout haircut as instant stress, and charges no 30 bp instant-redemption fee. Compare with the original discounted instant-read benchmark and compounded 4% cash across all 63 days. Maintain full-size settlement tests, without a substitute instant withdrawal after shares have been escrowed.
- Read the standard request and fulfillment event topics from the official deployment history and match an episode by its indexed owner. Require a complete request and fulfillment sequence. Individual request delays are measured only when no overlapping request existed for that owner. Sum partial installments before declaring completion. Compare standard USDC transfers in the transaction receipt with each event. Count open episodes as censored. Logs measure successful submissions rather than mempool attempts that never confirmed or transactions that reverted.
- For each deadline require at least one unambiguous standard request within the same seven-day window that fully settled by cutoff and paid at least 2,050 USDC in audited transfers. The floor exceeds the modeled exit size and serves as a historical comparator for scale. Without a qualifying episode the vintage fails the capacity gate. A missed deadline means the paper route has no verified, fully liquid exit by that deadline.
- Report observed latency and payout capacity, real on-chain request gas where available, the fixed gas reserve, queue-delay/failure handling, and the stress sensitivity's cost advantage over instant redemption. The challenger would pass the historical screen only if every window passes the queue-capacity gate and beats 4% compounded cash under the fixed costs. Even a pass would need a fresh paper check and represent historical precedent rather than a live queue guarantee.

## Paper results

The queued challenger cleared all three retrospective windows. Its conservative payout uses the smaller of the issuer-mark model and the exact dual-archive quote at that window's lowest issuer mark. Values are USDC P&L on 2,000 USDC at entry. The weekly capacity counts are real third-party transactions.

| UTC vintage in 2026 | Request day | 4% cash | Instant stress | Queued stress if settled | Improvement over instant | Full $2,050+ queue payouts by cutoff |
|---|---|---:|---:|---:|---:|---:|
| May 17–July 19 | July 12 | +13.59 | +14.00 | **+17.20** | +3.20 | 4 |
| July 19–September 20 | September 13 | +13.59 | +16.28 | **+19.92** | +3.65 | 4 |
| September 20–November 22 | November 15 | +13.59 | +17.64 | **+20.34** | +2.70 | 6 |

The 30 bp fee savings exceeded the foregone share appreciation during the seven-day buffer under the fixed assumptions. The model's excess over cash was **3.61**, **6.34**, and **6.75 USDC**. An extra 100 bp loss on the queued payout reduced P&L to **−2.99**, **−0.30**, and **+0.12 USDC**, all below cash. The queue locks any actual escrowed shares while the manager controls fulfillment. A delayed or unavailable payout can exhaust the modeled buffer. The same full-window cash comparison applies if no payout is established by the deadline.

The two archive endpoints matched the exact `previewDeposit` share units for the 1,996.0015 USDC allocation and the `convertToAssets` integer payouts on July 12, September 13 and November 15 at 23:59:59 UTC. The no-fee quotes, after the 10 bp payout haircut, gave +17.197354, +19.924149 and +20.346960 USDC. The maximum difference from the issuer-mark computation was 0.00828 USDC. These no-fee calls value the paper shares. Actual operator fulfillment accrues the protocol's performance fee before converting and can occur at a different share price. [Original instant comparison and exact entry archive](liminal-allocation.md)

## Actual queue service and gas

The proxy was deployed on April 23, 2026 at 16:54:00 UTC. From its creation block 33,253,579 through November 22 at 23:59:59 UTC, block 51,987,233, the archive contained 731 standard request events and 709 fulfillment events. Each fulfillment matched an actual native USDC transfer in the corresponding execution receipt. The route paid **7.10 million USDC** over the scan. The reconstructed remaining standard share escrow exactly matched the contract's terminal limUSD balance on both RPCs. The providers verified the creation and cutoff block hashes and implementation, and the proxy emitted no later implementation upgrade. [HyperEVM archive references](https://hyperliquid.gitbook.io/hyperliquid-docs/builder-tools/hyperevm-tools) · [Public contract explorer](https://hyperevmscan.io/address/0x2e8c1256356e261150120a384d95dc0d0d00ae29)

Among the 651 completed episodes with exactly one request, the median completion time was **37.01 hours**, the 95th percentile was **69.76 hours**, and the longest was **94.03 hours**. Fourteen exceeded three days and none of the completed episodes exceeded seven. The 264 completed single-request episodes of at least 2,050 USDC had a 95th percentile of **68.48 hours**. Eighteen owner episodes remained open at the final cutoff, including 16 single-request episodes, all under three days old. The timing statistics describe this observed history with pending requests censored.

| Seven-day service window ending | Single-request episodes observed | Fully paid by cutoff | Open at cutoff | Fully paid peer volume, USDC |
|---|---:|---:|---:|---:|
| July 19 | 27 | 19 | 8 | 118,365.59 |
| September 20 | 23 | 13 | 10 | 526,456.47 |
| November 22 | 28 | 12 | 16 | 40,158.11 |

Requests can enter at any time during each week, so some had less than seven days by cutoff. The peer payouts demonstrate historical throughput for the modeled amount. Paper shares never entered the queue, so their position, processing time and cash settlement remain unobserved. In a matched forward paper study, keep the exact shared-entry share units, paper payout quote and independently observed peer-capacity gate as distinct measurements.

Actual gas for 731 confirmed request transactions was valued at the last completed HYPE/USDC four-hour price. Median request gas was **0.00166 USDC**, the 95th percentile was **0.02004 USDC**, and the highest observed was **0.66641 USDC**. The replay preserves a **3 USDC** all-in reserve for activation, minting, request and possible retry costs. The gas observations exclude attempts that reverted or never confirmed. Manager fulfillment pays the recipient directly, without a holder claim transaction.

## Reproduction

```bash
python -m lane_z.liminal_queued --capture
```

The command uses public read-only calls and the published issuer marks and opening-quote archives. It caches 375 contiguous contract-log segments and 801 distinct transaction receipts, including 475 fetched from a separate archive on this run. It stores local raw events and receipts in the ignored `data/agent-liminal-queued` directory and writes an aggregate `report.json` with input-batch hashes and exact sample quotes. Published summaries omit holder addresses and individual transaction details. Remove `--capture` to replay existing caches without network calls. The original forward protocol remains distinct. [Frozen forward protocol](liminal-forward.md)
