# National Stock Exchange of India

## Research design

The official NSE capital-market sample mixes ordinary market records from April 10, 2024 with `NSETEST` records. Its regular-market order log ends at 09:15:12 IST, about 12 seconds after the open, while its trade log extends to 09:20:48. This truncated sample is unsuitable for measuring a normal session of millisecond fills. Contemporary millisecond cash-market results require complete order-book history or a licensed bid/ask capture. This study uses genuine NSE end-of-day Bhavcopy records for a separate intraday screen. The open and last trade in a daily file are price proxies, so this screen cannot measure latency, spread, order priority or fill rate. [NSE order/trade samples and subscriptions](https://betanseapi.nseindia.com/static/market-data/eod-historical-data-subscription) · [NSE real-time data](https://www.nseindia.com/static/market-data/real-time-data-subscription) · [Official capital-market reports](https://www.nseindia.in/all-reports/)

The fixed cash equity universe is RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, SBIN, ITC, LT, BHARTIARTL and AXISBANK. On each ordinary session, calculate each stock's close return against that day's official previous close. The next session's order targets the worst relative performer only if its close return is at most −1% and underperforms the universe median by at least 1.5 percentage points. Size whole shares using the signal day's closing price and ₹50,000 gross budget. Skip when the next official opening price plus the entry allowance exceeds 102% of the signal-day close. The planned exit is at the end of that same session and is modeled from the official last price. Compare the chosen name with the same-day equal-weight gross move across the universe. Special sessions are excluded.

Use June 1–August 31, 2026 for an earlier-period check and September 1–November 20 for the held-out period, classified by execution day. The rules are fixed before reading strategy performance. The primary execution allowance is 2 bps per side with a 5 bps-per-side stress. Fees use the current Zerodha resident intraday example, including capped brokerage, NSE transaction charges, SEBI and IPFT levies, GST, buy-side stamp duty and sell-side STT. No fill or brokerage quote is implied for a particular account. [Zerodha fee schedule](https://zerodha.com/charges/) · [NSE tax and levy rates](https://www.nseindia.in/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies)

## Additional period and sensitivity

Use January 1–May 29, 2026 as a separate historical period with the same universe, order rules, sizing and current fee assumptions. No threshold changes are permitted after the positive June–November screen. Inspect the September–November result by month and with the best trade removed. Add explicit execution allowances of 10 and 20 bps per side to show how an inaccurate open/last fill assumption can affect the result.

## Results on the September–November holdout

The 120-session June–November archive had ten valid cash equity rows per day. The holdout generated 29 setups. The opening limit passed on 28 under the primary cost assumption.

| Execution allowance per side | Paper round trips | Net INR | Net bps on filled entry notional |
| --- | ---: | ---: | ---: |
| 2 bps | 28 | ₹7,765.90 | 57.00 |
| 5 bps | 27 | ₹5,531.85 | 42.13 |
| 10 bps | 27 | ₹4,215.55 | 32.09 |
| 20 bps | 26 | ₹2,274.76 | 17.98 |

At 2 bps per side, the selected names gained about 71.68 bps gross on filled entry notional. The matched-day equal-weight universe gained 26.38 bps gross before costs. The paper rule finished positive in each of September, October, and November to date. Removing the three best trades leaves ₹3,175.10. ITC provided ₹4,077.69 across four trades, about 53% of held-out net P&L.

An equal ₹50,000 buy and sell costs approximately ₹21.07 and ₹32.07 under the modeled resident intraday fee schedule, a 10.63 bps round-trip fee hurdle before the execution allowance. Costs use continuous estimates. Real contract-note rounding and account-specific treatment can differ. The NSE retail API framework applies broker-side controls and static IP requirements to the self-hosted tech-savvy route. [NSE retail algorithmic trading circular](https://nsearchives.nseindia.com/content/circulars/INSP77559.pdf)

This is positive evidence for the slower daily-signal candidate. Confirming the opening and exit prices requires execution-time quotes, order priority, and actual receipt and routing measurements. The official example ZIP has 300,000 order and 300,000 trade records, including 2,826 and 1,386 `NSETEST` records respectively. The decoded regular order time window was 09:15:00.019424 to 09:15:12.026108 IST, with trades continuing to 09:20:48.729660. [NSE downloadable ZIP](https://archives.nseindia.com/web/sites/default/files/inline-files/Sample_CMsegment.zip) · [Order and trade layout](https://nsearchives.nseindia.com/web/mediaattachment/2026-05/NSE_Hist_Order_Trade_Data_1.18_20260518122214.pdf)

## Independent January–May result

The unchanged rule lost ₹12,793.47 on 56 round trips in the separate 98-session January–May archive at 2 bps per side. Gross P&L before fees and the execution allowance was −₹8,859.05. Every month in that earlier period was negative. Costs use the same current schedule as the June–November run.

| Period | Paper round trips at 2 bps per side | Net INR | Net bps on filled entry notional |
| --- | ---: | ---: | ---: |
| January–May independent check | 56 | −₹12,793.47 | −47.47 |
| June–August | 28 | ₹2,704.43 | 20.12 |
| September–November holdout | 28 | ₹7,765.90 | 57.00 |
| Pooled evaluated ranges | 112 | −₹2,323.14 | −4.30 |

The early losses outweighed the later gains for this fixed rule. Summary periods restart at the first standalone file, so the pooled result omits the trade opportunity across the separate January–May and June–November archive boundary. No Indian strategy qualified on this evidence.

## Reproduce

The [June–November report](../results/follow-up/nse-india.json) and [January–May report](../results/follow-up/nse-india-earlier.json) contain all input URLs and checksums, daily setups, costs and trade results. The calendar follows ordinary exchange sessions including the later January 15 closure. [Annual trading holiday circular](https://archives.nseindia.com/content/circulars/CMTR71775.pdf) · [January 15 closure circular](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf)

```sh
python -m lane_z.nse fetch
python -m lane_z.nse run --output data/nse-primary-check.json
python -m lane_z.nse fetch --earlier-check --data data/nse/bhav-early
python -m lane_z.nse run --earlier-check --data data/nse/bhav-early --output data/nse-earlier-check.json
```
