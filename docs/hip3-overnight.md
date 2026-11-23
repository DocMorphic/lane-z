# Hyperliquid equity index overnight paper test

## Frozen protocol

Test whether the entire U.S. off-hours window produces after-cost paper profit on `xyz:XYZ100`. The original New York Fed research links a narrow overnight drift to liquidity providers carrying inventory acquired near the prior U.S. close. Their later analysis found the 02:00–03:00 ET effect had faded in E-mini Nasdaq-100 and other index futures. The primary rule tests a broader holding window on a separately traded custom U.S. index. The narrow 02:00–03:00 ET interval is a prespecified diagnostic of the published effect. [Original research](https://www.newyorkfed.org/research/staff_reports/sr917) · [July 2026 update](https://libertystreeteconomics.newyorkfed.org/2026/07/the-disappearing-overnight-drift/) · [Trade[XYZ] contract](https://docs.trade.xyz/perpetuals/specifications-and-schedules/specification-index.md)

Enter a long at 18:00 New York time following a normal U.S. equity session and exit at 09:00 the following calendar morning before the regular open. Both dates must be U.S. trading sessions, with entries limited to Monday through Thursday. The after-close start avoids the 17:00–18:00 ET external reference break. Use an exchange calendar and `America/New_York` conversion for daylight saving time. Use hourly candle opens as executable-price proxies. Begin each vintage with $10,000-equivalent USDC. Size to no more than 98% of available equity at the adverse buy reference and fee, with the remainder as a friction buffer. Assume USDC at par and no interest on venue balances. Test May 4–July 6, July 6–September 8, and September 8–November 20, 2026. Trade only if both daily periods fall within the vintage and stop all positions by the final regular close. No data-dependent parameter or trade selection. [Nasdaq trading calendar](https://www.nasdaqtrader.com/Trader.aspx?id=Calendar) · [Trade[XYZ] reference hours and holidays](https://docs.trade.xyz/perpetuals/specifications-and-schedules/holiday-closures.md)

Report prices-only returns, a fee-only optimistic case, and a base case at the published trade[XYZ] Growth Mode base taker charge of 0.9 bp per side plus 2 bps per side for adverse execution. Use current venue metadata and historical indications of Growth Mode for the cost model, without assuming that a current parameter proves the full history. Stress 7.5 bps execution reserve per side with entry one hour later and exit one hour earlier. Also test standard fee mode at 9 bps per side with base timing, for sensitivity to uncertain past fees. Charge published hourly funding rates, with each event applied to the position carried into that hour. Use the preceding hourly traded close as the historical oracle-price proxy. Apply a 10% signed funding adverse case with the timing stress. Use same-asset daytime 10:00–16:00 ET on each eligible exit session, continuous long exposure over the full vintage, and full capital compounded at 4% as benchmarks. Change neither test schedule nor assumptions after evaluation. Require the broader overnight effect to beat cash in all three base and stressed vintages before it qualifies for follow-up. [Trade[XYZ] fees](https://docs.trade.xyz/perpetuals/mechanics/fees.md) · [Trade[XYZ] hourly funding](https://docs.trade.xyz/perpetuals/mechanics/funding.md)

## Paper results

| 2026 vintage | Nights | Gross price-only | Overnight base | Overnight stress | Daytime base | Continuous base | 4% compounded cash |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| May 4–July 6 | 33 | −$91.52 | −$309.74 | −$556.76 | −$338.28 | +$617.56 | +$67.88 |
| July 6–September 8 | 36 | −$31.43 | −$259.38 | −$544.80 | −$189.74 | −$143.48 | +$68.96 |
| September 8–November 20 | 43 | −$216.54 | −$492.96 | −$945.42 | −$1,812.91 | −$1,566.55 | +$78.74 |

**Decision.** Retire the fixed overnight rule. The broad price-only return was negative in all three vintages, and financing and execution costs widened the losses. The narrow European-open diagnostic averaged +3.51, +1.94, and +3.25 bps gross per eligible window. Modeled costs averaged 5.73–5.75 bps on account equity, leaving the narrow window negative in every vintage. The largest base trading size was 0.51% of the corresponding full hourly traded quantity. Actual availability at the fill time remains unknown.

## Data and reproduction

The test used 4,818 contiguous hourly candles and matched funding-rate records. Of these, 1,204 groups matched the respective venue’s separate 4-hour candle opens and closes exactly. An independent decimal ledger confirmed dates and quantities, modeled funding and net P&L for all 24 scenarios. U.S. session dates from Nasdaq’s traded QQQ history matched trades generated from the holiday calendar. Growth Mode metadata was enabled at capture and reported a last fee-scale change of November 23, 2025. The standard-fee sensitivity also produced losses in every vintage, from −$800.17 to −$1,120.32. [Hyperliquid historical endpoints](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals) · [Nasdaq price history](https://www.nasdaq.com/market-activity/etf/qqq/historical)

```sh
python -m lane_z.nasdaq_trend fetch
python -m lane_z.hip3_allocation fetch
python -m lane_z.hip3_overnight fetch
python -m lane_z.hip3_overnight run --output data/agent-hip3-overnight/report.json
```
