# Pendle PT-limUSD paper quote screen

## Frozen protocol

PT-limUSD is a principal claim with native USDC accounting. Its purchase price comes from Pendle’s market, and its maturity redemption converts through SY and limUSD. A PT can pay below one USDC-equivalent when the current SY exchange rate is below the recorded PY watermark. The official HyperEVM market registry identifies the November 26, 2026 pool. Require at least three separate completed and independently evidenced PT-limUSD maturities to qualify this product family for an allocation. Other issuers and underlying assets are recorded as distinct candidates. [Pendle market](https://app.pendle.finance/trade/pools/0xfc93d1ada3277e6274c3905db0a2c81ac1252025/zap/in?chain=hyperevm) · [Pendle watermark mechanics](https://docs.pendle.finance/pendle-v2/ProtocolMechanics/NegativeYield)

Investigate prespecified snapshots 126, 63 and 28 days before maturity and at the November 23, 2026 UTC end-of-day block. At each, use 2,000 USDC initial paper capital and two gas allowances of 1 and 3 USDC. Use an amount-specific, read-only intrapool quote for the remaining USDC. Prefer the deployed Pendle static router and actual number of limUSD/SY units quoted by the deposit and wrapping contracts. If a hosted route is available, retain its quote, explicit fee and market impact for comparison. Exclude token rewards and any LP position. Capture archived balances, PT supply, fees, reserve liquidity, and exact pool and watermark state insofar as they can be read. Compare modeled end value against 4% compounded across the full term until maturity. Use current same-block SY exchange rate and watermark for a conditional redemption amount. Include the Liminal instant cash-out charge and stress a further 10 basis points of exit loss and a separate 100 basis point fall in the exchange rate. No quote or market mark qualifies as a realized full cash redemption. All research remains read-only. [Pendle static quote API](https://docs.pendle.finance/pendle-v2-dev/Contracts/RouterStatic/ApiReference/RateFunctions) · [Liminal redemption terms](https://docs.liminal.money/tokenized/flows)

## Results

| Quote date UTC | Days to maturity | PT from 1,999 USDC | Quoted Pendle swap fee, USDC equivalent | Conditional base P&L | Conditional stress P&L | 4% cash P&L |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| July 23, 2026 | 126 | 2,058.624675 | 1.278344 | +52.45 | +48.21 | +27.26 |
| September 24, 2026 | 63 | 2,031.181882 | 0.679986 | +25.09 | +21.05 | +13.59 |
| October 29, 2026 | 28 | 2,011.480613 | 0.275018 | +5.45 | +1.43 | +6.03 |
| November 23, 2026 EOD | 2.00 | 2,000.196553 | 0.046214 | −5.80 | −9.71 | +0.43 |

**Decision.** The official registry contained 50 HyperEVM markets and one PT-limUSD market. Its November 26 maturity was still pending. The dated quotes all have that same redemption date, so the product has zero independently realized maturity vintages. The latest annualized quote obscures a small gross dollar discount over the remaining two days. The 30 bp instant cash-out charge alone exceeds that discount. [Pendle HyperEVM market registry](https://api-v2.pendle.finance/core/v2/markets/all?chainId=999&limit=100&skip=0)

The November 23 close at canonical block 52,075,073 showed a SY exchange rate of 1.046404 USDC per SY and a stored watermark of 1.046403. The route had sufficient Liminal mint headroom and 299,877.151436 USDC of spendable instant redemption reserve at that block. Buying PT with 1,999 USDC would conditionally return 1,994.197346 USDC with the same redemption rate and 30 bp exit fee. A 100 bp reduction in backing exchange rate would lower that case to −25.74 USDC net P&L. This is an instant-route comparison. Liminal’s queued redemption is a separate process with later valuation and settlement. [Liminal minting and redeeming](https://docs.liminal.money/tokenized/flows)

An independent hosted quote shortly after the boundary priced 1,999 USDC at approximately 2,000.255 PT and an effective APY of about 12.2%. The alternative LP purchase quoted approximately 0.00085673 LP tokens at the same input size. The on-chain static LP quote at the checkpoint was 0.000856730480351759. An LP token holds both PT and SY and has different exposure, so its expected profit is outside this paper allocation.

At each archived checkpoint, two independent RPCs agreed on both headers and exact integer quotes. The static router’s token-input and SY-input routes gave the same PT result. Converting conditional maturity SY to USDC directly gave the same output as redeeming its limUSD shares through the Liminal pipe. Quoted swap fees, slippage, gas reserves, and the separate maturity assumption are distinguishable in the report. Historical endpoints establish point-in-time states and quote outputs. [Pendle HyperEVM deployments](https://docs.pendle.finance/pendle-v2-dev/Deployments)

```sh
python -m lane_z.pendle_pt fetch
python -m lane_z.pendle_pt run --output data/agent-pendle-pt/report.json
python -m lane_z.pendle_pt current-quote
```
