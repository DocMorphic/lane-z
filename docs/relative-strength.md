# Equity perpetual relative-strength paper test

Jegadeesh and Titman found cross-sectional momentum in U.S. cash equities over horizons of months. Nagel documents that short-horizon reversals can reflect compensation for supplying liquidity. A seven-day signal in the younger equity-perpetual market is an independent hypothesis with an uncertain sign. The primary test goes with relative strength. The inverse portfolio is a predeclared directional diagnostic that pays the same two-sided costs. [Jegadeesh and Titman](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1993.tb04702.x) · [Nagel](https://www.nber.org/papers/w17653)

## Frozen protocol

- The fixed Trade[XYZ] universe is AAPL, AMD, AMZN, AVGO, GOOGL, META, MSFT, NFLX, NVDA and TSLA. These large-cap names were fixed before requesting their prices. The universe is not a historical index and cannot measure survivorship relative to all listed perps. A symbol qualifies at the decision time with both price endpoints present, a full set of 24 trailing hourly bars, and median reported hourly turnover of at least $20,000 using each close times contract volume. Require at least eight eligible symbols. Report which assets qualified and all data outages.
- At 16:00 UTC on Monday through Thursday, rank the seven-day close-to-close returns using only bars completed by the decision. Break ties by symbol. Long the three leaders and short the three laggards. Each leg targets $200 and uses the completed decision close for sizing, rounded down to the exchange lot. The reverse diagnostic takes the opposite position. An all-long equal-weight basket of the same six contracts matches the position gross and serves as a market-exposure comparison. Idle exchange collateral earns zero in the baseline. A hypothetical 4% annualized cash alternative is also reported as a reference.
- Simulate a fresh entry at the next hourly candle's first trade, ordinarily from 17:00 UTC, and a full exit 24 hours later. Exit before opening a new portfolio. Initial paper collateral is $1,500 with $1,200 targeted gross and no compounding or cross-date position netting. Candle opens are execution-price proxies with no executable quote or fill assurance. Fail the run on a selected contract with missing entry, exit or holding-period funding data. Check the hourly path to report marked drawdown. Liquidation, capacity and the precise ages of candle-opening trades are outside the test.
- The base first-trade proxy allowance is 5 basis points per fill. Charge the Tier 0 growth-mode taker fee of 0.9 basis points per fill when metadata shows growth mode unchanged since before the sample, and otherwise charge 9 basis points. Stress all fills at 15 basis points plus the standard 9 basis point taker fee. A separate timing stress executes both entry and exit one hour later with the same decisions. Historical public hourly funding rates are applied to signed positions. The previous hourly perp close proxies the historical settlement oracle price. Debit boundary funding when it costs the strategy and credit funding receipts only at interior hours, since the candle does not determine ordering near hourly settlement. [Trade[XYZ] fees](https://docs.trade.xyz/perpetuals/mechanics/fees/) · [Hyperliquid funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) · [Historical funding API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals#retrieve-historical-funding-rates) · [Candle API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#candle-snapshot)
- Decision dates in UTC are May 18 through August 28, 2026 for development, August 31 through October 9 for test, and October 12 through November 20 for confirmation. Assess the primary rule separately in each window. Do not choose signal lengths, costs, dates or instruments on either evaluation window. A positive candidate needs net positive P&L in both evaluation windows under baseline and fee/slippage stress. Report total returns, drawdown, net weekly variation and performance concentration. At most six weeks per evaluation window limits confidence even when a criterion passes.

The price endpoints and trailing liquidity history require positive reported volume. The feed can carry a previous price into no-trade hours. Those points contribute to interim marking and the funding-price proxy, with no simulated execution at a zero-volume open. AVGO may join after its listing once it has the required history. The number of eligible markets is checked at each decision.

Before inspecting P&L, a check of independently archived top quotes found four of 24 intended $200 legs lacked full size at the touch. A separate $25-per-leg sizing sensitivity carries over the original 9 bp fee and 15 bp execution-allowance stress with $187.50 of paper collateral. This sensitivity changes size and lot rounding. The original benchmark and rule remain fixed.

The quote check used the archived documents for the scheduled entry and exit legs at 17:00 UTC on September 1 and October 1. It looked up the latest exchange-timestamped best quote at a 500 ms modeled arrival, allowing at most two seconds of quote age. All 24 observations were fresh. The median adverse touch move from the candle open was 0.445 basis points and the maximum was 4.552 basis points. All 24 sampled $25 quantities fit the displayed top sizes. This check covers the sampled times and sizes. It does not establish fills throughout the replay. [Tardis sample documentation](https://docs.tardis.dev/historical-data-details/hyperliquid)

## Reproduction

The market data requests use the public Hyperliquid info endpoint. The downloader waits between requests to share the service limit and keeps existing inputs when rerun. The report records each source request and cached SHA-256 hash, the lot-sized signed positions, price P&L, funding, fees, modeled execution allowance and weekly results. Raw data stays under the ignored `data/` directory.

```sh
python -m lane_z.relative_strength --data data/agent-relative-strength --fetch
python -m lane_z.relative_strength --data data/agent-relative-strength --output data/agent-relative-strength/report.json
```

## Results

**The continuation hypothesis lost in both held-out periods.** The replay ran 59 development, 24 test, and 24 confirmation cycles. May 25 was idle because seven symbols met the signal-time data and liquidity requirements. Growth mode was enabled for the ten names when the data was downloaded. The metadata did not give a prior effective date, so the baseline charged the frozen fallback of 9 basis points per leg, plus the 5 basis point execution allowance. The cost and timing stresses increased the trade allowance or moved execution an hour later.

| Portfolio and cost assumption | Development net | Test net | Confirmation net |
| --- | ---: | ---: | ---: |
| Relative strength, $200/leg baseline | −$546.51 | −$142.34 | −$64.95 |
| Relative strength, $200/leg wider allowance | −$687.82 | −$199.86 | −$122.10 |
| Relative strength, $200/leg later execution | −$527.81 | −$122.12 | −$63.94 |
| Relative strength, $25/leg wider allowance | −$85.23 | −$24.59 | −$14.31 |
| Inverted ranking, $200/leg baseline | +$149.04 | −$19.23 | −$95.58 |
| Same selected contracts all long, $200/leg baseline | −$239.90 | −$54.78 | −$327.73 |

The baseline strategy lost $62.35 on price movement alone in test. Its $13.44 favorable price move in confirmation and $1.61 in funding receipts were insufficient to cover $80.01 in fees and execution reserves. It lost money in five of six test weeks and four of six confirmation weeks. Estimated marked drawdown was $146.78 in test and $116.84 in confirmation on $1,500 of starting paper collateral. A hypothetical 4% annual cash yield would have produced $6.58 in each held-out window.

The inversion's development gain weakened under the wider cost assumption and both held-out windows lost money. Holding the same trades fixed and applying the lowest growth taker fee across every date still leaves the continuation at −$95.74 in test and −$18.66 in confirmation. The inversion would show +$27.37 in test and −$49.29 in confirmation. There is no basis in these results to adopt either direction. A separate arithmetic check reconstructed the ranking, rounded positions and net P&L for all 7,704 simulated legs using the raw cached candles and funding rates. The results do not establish live prices, fills, incident handling, or portfolio capacity.

[Aggregate results and source audit](../results/follow-up/relative-strength.json)
