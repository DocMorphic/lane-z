# NSE NIFTYBEES monthly trend paper test

## Frozen protocol

This paper test asks whether a long-or-cash trend filter on Nippon India ETF Nifty 50 BeES produces positive rupee P&L and improves the loss profile relative to holding the same fund. The ETF tracks Nifty 50 total returns before expenses. The monthly rule follows Faber’s published ten-month moving-average design. The original study covers other markets and periods. This test uses the Indian ETF’s own exchange prices and delivery-trading costs. [Fund manager’s ETF profile](https://etf.nipponindiaim.com/Funds/details/14) · [Faber’s paper](https://ssrn.com/abstract=962461)

After the last exchange session of each calendar month, compare the ETF close with the arithmetic mean of the ten most recent month-end closes, including that close. Hold the ETF when the close is strictly higher, and otherwise hold cash. Execute a required change at the next exchange session’s open price proxy. Hold unchanged positions without rebalancing. Start each test period with ₹100,000 and use the previous month’s signal to determine exposure on the first session. Use whole shares. Infer the number of units affordable after observing the opening reference and allowing for entry slippage and fees. At the final scheduled close in each period, price any remaining position at that close minus allowance and sell costs. This is a cash ETF simulation with no leverage, short selling, or funding payments.

Use January 2021 for price history warm-up, calendar 2022 for development, January 2023 through December 2024 as the first chronological holdout, and January 2025 through November 20, 2026 as confirmation. Trading dates follow published exchange records, including special sessions where the source supplies them. Freeze the instrument, periods, thresholds and expense scenarios before evaluating any strategy return. Evaluate cash and buy-and-hold independently on each identical date range. Primary strategy cash earns zero and a separate 6% annual cash hurdle provides a fixed opportunity-cost comparison. Also test interest on strategy cash at that same modeled rate. The 6% figure is a sensitivity, without using future realized interest rates.

The primary model charges 5 bps per side for spread, routing and adverse movement from the opening or terminal close reference. Stress both 15 and 30 bps per side. Apply current resident retail delivery fees, including zero brokerage, 0.00307% exchange fee, 0.0001% SEBI levy, 0.0000001% IPFT levy, and 18% GST on those levies. Include 0.015% purchase stamp duty, 0.001% sell-side equity-fund STT, and ₹15.34 per demat sale. Historical statutory rates may differ. Fund expenses are reflected in the ETF’s traded price. Personal income taxes are outside base P&L. A separate conservative sensitivity deducts 25% of positive realized lot gains without offsets or exemptions. [Zerodha fee schedule](https://zerodha.com/charges/) · [NSE equity-fund STT](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies)

Use exchange opens and closes as scenario prices. Daily bars do not establish spreads, market depth at order time, or realized order latency. Verify raw-price continuity with NSE prior-close fields and exchange corporate-action reports. The known December 2019 unit split precedes the sample. [NSE split circular](https://archives.nseindia.com/content/circulars/CML42873.pdf) · [NSE Bhavcopy](https://www.nseindia.in/all-reports) · [NSE public historical endpoint](https://www.nseindia.com/nse-mcp)

Compare profit, total return, annualized return, daily marked drawdown, number of switches, invested exposure and dependence on a single period. Absolute net profit, excess over the fixed cash hurdle, and excess over buy-and-hold answer separate questions. No outcome from development or the first holdout changes the rule before confirmation.

## Results

| Standalone period | Trend paper P&L at 5 bps per side | Buy-and-hold paper P&L at 5 bps per side | 6% annual cash hurdle | Trend round trips |
| --- | ---: | ---: | ---: | ---: |
| 2022 development | +₹409.23 | +₹4,080.17 | +₹5,928.16 | 2 |
| 2023–2024 holdout | +₹26,790.66 | +₹31,393.60 | +₹12,333.12 | 2 |
| Jan 2025–Nov 20, 2026 confirmation | −₹5,835.62 | +₹11,071.15 | +₹11,600.77 | 1 |

Trend P&L at 30 bps per side was +₹25,575.09 on the first holdout and −₹6,302.77 on confirmation. Even with modeled interest at 6% while in cash, the confirmation trend ended down ₹350.07. The 25% positive-lot gain sensitivity left first-holdout trend net +₹18,928.26 and confirmation unchanged at −₹5,835.62. The confirmation trade bought on May 2, 2025 and exited on April 1, 2026. Its loss came mostly from the price move. It paid ₹38.50 in fees and ₹96.89 in execution allowance. The filter spent about 49% of the confirmation window invested. Its daily closing drawdown of 14.80% was close to buy-and-hold’s 14.79%.

**Decision.** Retire this fixed timing rule as a profit candidate. The first holdout’s absolute gains fell short of the same ETF held continuously, and confirmation had negative net P&L at all execution allowances. Over confirmation buy-and-hold also fell short of the modeled 6% cash hurdle in absolute rupees. This is evidence about one specific timing rule over the frozen periods. It provides no positive confirmation of the rule’s timing edge.

## Data verification and reproduction

The study assembled 1,460 exchange sessions from 19 anonymous historical pages and 409 dated Bhavcopy ZIPs. The raw file archive fills gaps in the history endpoint, including the 2023 interval omitted from the queried pages and seven special weekend sessions absent from those pages. Every adjacent prior close in the assembled series matched exactly. The exchange’s venue-specific report lists December 2019 as the latest NIFTYBEES corporate action, before the warm-up sample. The replay records file SHA-256 hashes and the canonical price-series digest. [NSE corporate actions](https://www.nseindia.in/companies-listing/corporate-filings-actions?symbol=NIFTYBEES&tabIndex=equity)

```sh
python -m lane_z.nse_trend fetch
python -m lane_z.nse_trend run --output data/nse-trend-replay.json
```
