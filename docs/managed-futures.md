# Hyperliquid cross-asset managed-futures trend

This paper basket uses native BTC and ETH, the trade[XYZ] equity-index perpetual `xyz:XYZ100`, and its gold perpetual `xyz:GOLD`. Hurst, Ooi and Pedersen combined longer and shorter directional horizons, resized the positions monthly, and targeted 10% forecast annual portfolio volatility. This test uses 28- and 84-calendar-day directions because the joint listing history is short. Group risk is budgeted equally across crypto, equity and gold with no borrowed capital. [Hurst, Ooi and Pedersen 2017](https://fairmodel.econ.yale.edu/ec439/hurst.pdf) · [Moskowitz, Ooi and Pedersen 2012](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf) · [trade[XYZ] overview](https://docs.trade.xyz/) · [Prior contract verification](hip3-allocation.md)

## Frozen protocol

- Use three consecutive 63-day, fully covered vintages in 2026, May 15–July 17, July 17–September 18, and September 18–November 20. Measure from 16:00 UTC on each boundary. Start each vintage at 10,000 USDC without accrued idle yield, liquidate at the boundary, and charge both the exit and the next vintage's new entry. Dates overlap earlier work on components. The joint design and dates were set before revealing this basket's P&L, and successful results would need a fresh forward check.
- At 12:00 UTC on the start Friday and every 28 days thereafter, average the directions of the prior 28- and 84-day price changes. A zero vote means no position. Use the four-hour candle that ended before the decision and align observations by UTC calendar day. Estimate standalone annualized volatility from the last 28 such daily log returns. In each group combine signed inverse-volatility positions, using a 10% annual volatility floor. Scale that signed group's trailing volatility to a nominal budget of 10% divided by the square root of three, capped at 30% notional per group. Scale down all three groups together if forecast basket volatility exceeds 10%. Every desired rebalance targets at most 90% aggregate gross, with unused collateral in USDC. The two comparison portfolios take the same directions at equal notional or always hold long at equal notional. Both divide 30% gross evenly among the active assets in each group.
- Execute the target change four hours after the decision at the 16:00 UTC trade-bar open. These Friday fills are during regular U.S. equity hours, including after the November clock change. Sizes use the opening price proxy and the portfolio's marked equity at execution. Positions carry through weekends and holidays and settle every recorded funding hour. Fund each position separately to at least its modeled entry notional, with a 5% collateral buffer and no transfer between pockets until a scheduled rebalance. Charge only the net position change inside a vintage. Round to the venue's size step and require 1% or less of realized four-hour volume at each fill. Track gross drift between entries. Check isolated margin against the worst four-hour trade price plus adverse in-bar funding, with a 5% maintenance floor. Also show the minimum headroom under a hypothetical 30% jump against each short. [Nasdaq U.S. holiday and trading hours](https://www.nasdaq.com/holiday-trading-hours)
- Apply the published 4.5 bp native taker fee, 0.9 bp growth-mode rate on XYZ100, and 9 bp standard GOLD rate. Metadata records growth mode on XYZ100 with its latest fee-scale change on November 23, 2025. Stress all XYZ100 fills at the standard HIP-3 9 bp fee. Use 3 bps adverse per fill in base and 15 bps in combined stress. Report signed hourly funding. Since historical oracle prices are absent, use the venue's four-hour opening trade price for valuation. Stress funding credits with a 25% haircut and the four-hour low reduced another 0.5%, and stress debits with the four-hour high increased 0.5%. Use the terminal bar open with the same haircut and price allowance at liquidation. At a trading boundary take the less favorable funding outcome between the position before and after execution. [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) · [HIP-3 rules](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals) · [Hyperliquid funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)
- Qualification requires beating compounded 4% cash on the entire account under combined funding and execution stress in all three complete vintages. Entry and exit prices are four-hour proxies for trades and margin, without synchronized historical order books or account-level liquidation and ADL.

## Results

| 2026 vintage | Equal-risk trend base | Equal-risk trend stressed | Equal-notional trend stressed | Equal-notional always long stressed | 4% cash |
|---|---:|---:|---:|---:|---:|
| May 15–July 17 | −162.98 | **−203.03** | −356.27 | −1,067.05 | +67.93 |
| July 17–September 18 | −861.02 | **−909.05** | −993.36 | +1,195.94 | +67.93 |
| September 18–November 20 | −117.30 | **−148.57** | −24.04 | −2,014.24 | +67.93 |

Each entry is P&L in USDC on a fresh 10,000 USDC vintage. Linking the three stressed percentage returns gives **−12.26%** for equal-risk trend, **−13.35%** for equal-notional trend, and **−20.13%** for equal-notional always long. The compounded cash hurdle was **+2.05%** across the combined 189 days.

| Equal-risk trend under stress | Price P&L | Net funding | Fees | Execution | BTC price P&L | ETH price P&L | XYZ100 price P&L | GOLD price P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| May 15–July 17 | −156.03 | −5.98 | 13.58 | 27.43 | −448.40 | −38.99 | +46.12 | +285.25 |
| July 17–September 18 | −876.76 | +20.76 | 17.75 | 35.31 | −587.91 | +3.90 | −44.46 | −248.28 |
| September 18–November 20 | −65.44 | −43.66 | 13.10 | 26.37 | −141.38 | +12.21 | −70.20 | +133.93 |

Price P&L was negative in every stressed vintage before funding or execution costs. The basket opened long BTC in May before it fell and was short in August during its recovery. Gold shorts gained in the first and third vintages and lost during the middle rebound. Equal-risk sizing limited the drawdowns versus equal-notional trend in all three vintages, but total hedging gains were too small to cover the reversal losses. This paper test retires the frozen basket as a cash-hurdle candidate.

All modeled positions had complete isolated backing, exposure and funding histories. The combined-stress candidate's highest observed gross was 79.10% of equity and its largest marked drawdown was 10.74%. Its largest fill was 0.0811% of reported four-hour volume. Minimum modeled headroom was 346.63 USDC before the short squeeze sensitivity and 260.61 USDC across the separate 30% short-shock scenarios. The equal-weight benchmarks can drift above their 90% entry target between rebalances. The volume and shock tests leave actual executable quotes and exchange liquidation sequencing uncertain.

## Reproduction

Native BTC and ETH price and funding use the public [native trend sources](native-trend.md) and the funding archives from [spot carry](spot-carry.md). The joint bottleneck is the [HIP-3 allocation archive](hip3-allocation.md), which spans December 28, 2025 to November 20, 2026 at 16:00 UTC. Each HIP-3 market has 1,967 four-hour bars and 7,865 hourly settlements. The run consumed these market caches as public read-only inputs and records their hashes.

```bash
python -m lane_z.managed_futures
```

The command reads `data/agent-native-trend`, `data/agent-spot-carry`, and `data/agent-hip3-allocation`. It writes `data/agent-managed-futures/report.json` with all ledger components, fees, allocations, position changes, and risk summaries. The paths can be supplied explicitly via `--native`, `--carry`, `--hip3`, and `--output`.
