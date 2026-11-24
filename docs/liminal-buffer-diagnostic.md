# limUSD queue timing sensitivity

Shorter request buffers raised the payout quote but weakened historical evidence that a full-size request could settle by cutoff. Seven days was the only tested buffer with a full-size peer payout at every deadline. It also missed 4% cash in one launch-adjacent window and had less protection against an additional payout haircut. This retrospective comparison leaves both frozen forward experiments unchanged.

## Historical service and downside

The queue tape covers the original verified limUSD redemption pipe from deployment through November 22, 2026 at 23:59:59 UTC. A full-size episode starts when its owner has zero pending shares and the original request is worth at least 2,050 USDC at the prior day's issuer mark. Measure time to fully redeem those original shares under two owner-level attribution rules. Under FIFO, each installment pays the earliest request first. Under LIFO, later requests are paid first, so the original shares can remain pending until the entire owner balance clears. The comparison does not assume which holder the manager processes first. [Verified queue tape and implementation](liminal-queued.md)

| Request lead | Completed by lead, FIFO | Completed by lead, LIFO | Late even under FIFO |
|---|---:|---:|---:|
| 3 days | 258 of 263 | 258 of 263 | 5 |
| 4 days | 263 of 263 | 262 of 263 | 0 |
| 5 days | 263 of 263 | 262 of 263 | 0 |
| 7 days | 263 of 263 | 263 of 263 | 0 |

All 263 episodes had at least seven calendar days of follow-up. Thirteen additional full-size episodes fell inside the final seven days of the tape. Six were still open at the cutoff. Neither the resolved nor the open recent episodes enters the common maturity denominator. The three-day misses are visible despite the issuer's documented one-to-three-day window.

## Daily anchor results

Use 150 successive 63-day paper windows from the first full UTC day after deployment, April 24, through November 22, 2026. Each begins and ends at a UTC daily mark. Request exactly 3, 4, 5 or 7 calendar days before the closing anchor. Reserve 3 USDC for gas, 5 bps on entry and 10 bps on the lowest issuer mark in the queue period. The instant control uses the same entry and its 30 bp exit fee. No interest accrues on unused cash. Comparison capital is 2,000 USDC and compounded 4% cash earns 13.59 USDC per window. [Issuer marks and matched instant control](liminal-allocation.md)

A capacity observation requires a single-request, receipt-verified peer that requested after the paper request anchor and was fully paid by cutoff. Both its initial mark and actual payout must be at least 2,050 USDC. Require payouts on two distinct UTC dates to test the loss of the busiest payout day. The first-24-hour check counts qualifying peers that submitted during the first day after the paper request. This tests same-size service precedent. Actual fills are observed throughput and do not reveal spare capacity after higher-priority users or the position of a simulated request.

| Request lead | Full-size peer | Two payout days | First-day peer | Median gain over instant if settled | Peer and above cash | Peer and above cash after extra 25 bp |
|---|---:|---:|---:|---:|---:|---:|
| 3 days | 119 / 150 | 0 / 150 | 93 / 150 | +4.75 USDC | 119 / 150 | 108 / 150 |
| 4 days | 138 / 150 | 28 / 150 | 99 / 150 | +4.51 USDC | 138 / 150 | 105 / 150 |
| 5 days | 144 / 150 | 69 / 150 | 99 / 150 | +3.62 USDC | 144 / 150 | 93 / 150 |
| 7 days | 150 / 150 | 132 / 150 | 99 / 150 | +3.21 USDC | 149 / 150 | 65 / 150 |

In July, the shortest buffer had peer evidence on 18 of 31 endpoints and the seven-day buffer had it on 31. In August those counts were 22 and 31. September through November improved to 74 of 83 at three days and 83 of 83 at seven. The five-day modeled payout exceeded the seven-day payout by a median 1.07 USDC on identical dates, conditional on settlement. The seven-day route missed the matched instant control on August 25 by 0.24 USDC.

Each successive window shares 62 days with the prior one. For a disjoint check, take fixed 63-day windows forward from April 24. The table reports quoted P&L before the extra 25 bp sensitivity. Each of these windows had peer evidence for each lead.

| Disjoint 2026 dates | 3 days | 4 days | 5 days | 7 days | 4% cash |
|---|---:|---:|---:|---:|---:|
| Apr 24–Jun 26 | +15.32 | +13.75 | +13.71 | +12.31 | +13.59 |
| Jun 26–Aug 28 | +21.84 | +19.49 | +19.48 | +16.62 | +13.59 |
| Aug 28–Oct 30 | +19.57 | +18.18 | +18.18 | +17.03 | +13.59 |

The extra 25 bp payout haircut erased the excess in all three disjoint seven-day windows. All valuations use the cached issuer's date-stamped NAV, with the share-price ratio verified against assets and shares on 214 days. The expanded dates lack the separate exact archive price checks of the three original vintages. Service results include confirmed requests and omit requests that reverted or never entered the queue.

## Reproduction

```sh
python -m lane_z.liminal_buffer_diagnostic
```

The script uses the existing ignored archive and public issuer snapshot without service calls. The full replay includes the 150 daily rows. The published aggregate includes the sample counts, input digests and non-overlapping window details. [Aggregate report](../results/follow-up/liminal-buffer-diagnostic.json) · [Issuer snapshot](../results/follow-up/liminal-issuer-marks.json) · [Frozen queue forward experiment](liminal-queue-forward.md)
