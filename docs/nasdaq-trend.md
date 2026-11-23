# Nasdaq QQQ monthly trend paper test

## Frozen protocol

The strategy holds Invesco QQQ or cash following Faber’s published ten-month simple moving average rule. QQQ is the tradable Nasdaq-listed ETF. The test uses its own market prices and distributions. Build a forward total-return wealth series by compounding `(close + cash distribution on ex-date) / previous close`, with splits expressed on a consistent share basis. After a completed month, hold QQQ if that month’s total-return value is strictly above the mean of the ten completed values including the current month. Otherwise hold cash. No parameter is fitted. [Faber’s published rule](https://mebfaber.com/2017/12/13/episode-86-quantitative-approach-tactical-asset-allocation/) · [Invesco QQQ](https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html)

Freeze four disjoint vintage windows. Use calendar 2017 for indicator warm-up. The intervals are January 2018–December 2019, January 2020–December 2021, January 2022–December 2023, and January 2024–November 20, 2026. Begin each window with $100,000 and inherit the signal available at its start. No window changes the rule for any later one. Use the price at the first regular-session open after a completed monthly signal, followed by an extra trading session of lag in stress. At the end of each window use the last regular-session closing price to liquidate. Use whole ETF shares and no borrowing or short selling.

Use 1 bp per side as a combined half-spread and adverse-execution reserve in the base case. Apply 5 and 15 bps per side in stress. Use zero brokerage commission and a conservative 0.3 bp sell-side regulatory fee allowance throughout, separate from the execution reserve. That allowance models the scale of current fees and provides a consistent stress against changing historical rates. Gross marked cash earns zero in the main case. Test 4% compounded annual yield on the strategy’s idle cash separately. Compare zero-interest cash, 4% compounded cash on the full initial capital, and a passive holding of QQQ under the same reserve, distribution and terminal-mark treatment. Work at published daily open and close reference prices. These inputs cannot measure realized spread or latency.

Cash distributions are earned on shares held from the prior close into the ex-date and added to cash on payable date. Include going ex-dividend receivables in valuation and terminal economic P&L. Reinvest paid distributions when a position is invested, at the next session’s open after cash payment under each policy. Time spent invested, trade count, annualized return, mark-to-market maximum drawdown, and net P&L will be compared. Treat positive absolute P&L, an edge over passive QQQ, and a drawdown benefit separately. Favor a standalone strategy for continued study only if it beats full cash in the latest vintage with base and stressed costs and offers repeatable improvement versus passive buy-and-hold in at least two of the four vintages, through higher return or a drawdown reduction of at least 5 percentage points without reducing annualized return by more than 2 percentage points. Personal taxes are outside this paper model.

## Results

Each period starts with $100,000. The table shows after-cost P&L using the next regular open and 1 bp execution reserve per side, plus the sell-side fee. Drawdown is measured from daily marked account value.

| Vintage | Trend net P&L | Passive QQQ net P&L | 4% compounded cash | Trend max drawdown | Passive max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2018–2019 | +$22,100.12 | +$38,052.93 | +$8,130.97 | 13.78% | 22.77% |
| 2020–2021 | +$61,503.59 | +$87,484.88 | +$8,142.58 | 28.54% | 28.54% |
| 2022–2023 | +$27,826.49 | +$4,022.59 | +$8,096.14 | 15.05% | 34.77% |
| 2024–Nov. 20, 2026 | +$19,267.26 | +$52,080.58 | +$11,971.21 | 19.91% | 22.69% |

Trend earned positive P&L and cleared the cash comparison in all four base cases. It still cleared cash in every vintage with 15 bps each way and one extra session of delay. Under that combined stress, the latest vintage earned $14,256.49 versus $11,971.21 in cash and $51,371.92 in passive QQQ. Trend annualized at 6.30% versus passive QQQ’s 15.65% in the latest base case. Crediting strategy cash at the same modeled 4% annual rate brought latest base P&L to $20,523.60.

**Decision.** The rule failed the standalone strategy criterion. The 2022–2023 vintage met both return and drawdown criteria. In 2018–2019, the 8.99-point drawdown reduction came with a 7.02-point annualized return reduction. In 2020–2021, the exit came after the largest drawdown and missed part of the rebound. In the latest vintage, three entries and 91.26% invested exposure delivered just 2.78 percentage points of drawdown relief. This supports describing the rule as a regime-dependent risk filter. It produced positive historical paper P&L without a repeatable advantage over passive exposure.

## Data and reproduction

Nasdaq published 2,486 QQQ trading sessions between January 3, 2017 and November 20, 2026. Invesco reported 40 distributions with matching Nasdaq ex-dates and payment dates. One December 2018 amount differed by $0.00001 per share. The test uses Invesco’s amount. Invesco also independently matched the final session’s $638.84 opening and $608.45 closing price. QQQ’s publicly documented split in March 2000 predates the sample. The strategy uses the actual ETF’s trading series rather than the index, issuer NAV or a backward-adjusted price as a fill. [Nasdaq price history](https://www.nasdaq.com/market-activity/etf/qqq/historical) · [Nasdaq distributions](https://www.nasdaq.com/market-activity/etf/qqq/dividend-history) · [Invesco distributions and market prices](https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html) · [Nasdaq split history](https://www.nasdaq.com/article/ndx-large-cap-growth)

A separate public feed matched all 2,486 session dates and closing prices to the cent and showed no in-sample splits. Its opening price differed from Nasdaq’s by more than one cent on 619 sessions, with a maximum difference of $0.35. Replaying with its opening prices moved base trend P&L by a maximum of $123.08 across the four vintages and preserved the decision. A separate decimal ledger independently reproduced every trade date and quantity and all 60 net results. The report records hashes for each primary payload. [Yahoo Finance historical quotes](https://finance.yahoo.com/quote/QQQ/history/) · [FINRA sell-side fee context](https://www.finra.org/sites/default/files/2024-11/NOF-IMM-EFF-FINRA-2024-019.pdf)

```sh
python -m lane_z.nasdaq_trend fetch
python -m lane_z.nasdaq_trend run --output data/agent-nasdaq/report.json
```
