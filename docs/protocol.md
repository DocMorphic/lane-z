# Study protocol

## Hypothesis

A shock in the liquid Trade[XYZ] XYZ100 index and the AAPL/MSFT perps may carry information that the NVDA or TSLA perp has yet to absorb. A large target-only move may be a temporary liquidity dislocation. An auditable model combines these effects with the target's own book. The test asks whether any residual movement is large enough to cross both spreads and pay fees after the order arrives.

Fit distinct models for the U.S. cash session and the weekend/holiday regime. The latter uses the same New York clock window as the weekday test to reduce time-of-day differences. An internally derived oracle during closed hours changes the economic regime, so neither cohort borrows labels from the other.

## Splits

All files are Tardis's free first-of-month `hyperliquid/book_ticker` captures. The universe is fixed to NVDA and TSLA as targets, with XYZ100, AAPL and MSFT as predictors. Each session admits decisions from 09:35 to 15:55 New York time and retains a two-minute exit buffer. Dates are New York session dates. For these hours the corresponding data is in the same UTC-date file.

| Regime | Train | Validation | Held out | Candidate holds |
| --- | --- | --- | --- | --- |
| Regular | Apr 1, May 1, Jun 1 | Jul 1 | Sep 1, Oct 1 | 1,000 / 5,000 / 15,000 ms |
| Internal | Jan 1 (holiday), Feb 1, Mar 1 | Aug 1 | Nov 1 | 5,000 / 15,000 / 60,000 ms |

All dates are in 2026. The independent unit is closer to a session than a 250 ms row because labels overlap and the two targets are correlated. Three held-out dates cannot support a reliable confidence interval or an annualized Sharpe. The internal regime mixes holiday and weekend development data.

## Features and target

Decision times fall on a 250 ms grid. Quotes are joined as of the Tardis capture timestamp. Exchange times provide a separate age check and an independent fill clock. Drop stale quotes beyond 2,000 ms, invalid or empty books, and older venue states received after newer ones. The train and forecast comparisons use common finite feature/label rows.

- Local model uses 250 ms, 1 s and 5 s log returns, top-size imbalance, normalized one-second sampled order-flow imbalance, spread, and five-second realized volatility.
- Cross model adds XYZ100 and peer basket returns and order-flow imbalance, leader/target gaps, peer/index agreement, and hinge terms on larger shocks and dislocations.
- Target is the log-midpoint move in bps from simulated entry arrival to scheduled exit arrival. Both endpoints come from the venue clock. Each round trip includes the delay on both legs. Ridge parameters and feature scaling use only the training dates. The objective is mean squared error plus 0.1 times the squared norm of the standardized weights. Standardized inputs are clipped at ±8.
- Baselines are a zero forecast, a no-trade portfolio, and a target-only ridge with the same rows, horizon, and execution rules. Forecast skill and executable P&L are measured separately.

## Selection and replay

An entry requires a finite score with magnitude above the observed full spread plus 2.3 bps of combined fees and modeled slippage, plus a validation margin of 0, 1 or 2 bps. Maximum entry spread is 5 bps. For each regime a single hold and margin pair is selected on validation by greatest total net P&L among complete positive runs with at least 25 round trips. The default selected policy is cash if none qualifies. In that case the middle hold with zero additional margin is still replayed as a reference. The 0.25-bp unguarded run is a fixed diagnostic for whether smaller forecasts clear execution costs.

- Base execution uses 250 ms order-to-match delay per leg, plus a modeled return delay before a new action. Treat the latest sampled venue BBO at order arrival as the fill proxy. Both targets use 0.001-unit lots and an approximately $100 entry. Crossing orders respect displayed size and direction-specific limits built from quotes observed at send time. Entry limits allow up to 2 bps from the observed touch, exits 10 bps.
- Paper IOC orders may miss or partially fill. Positions stay open until the remainder exits. Exit retries use fresh decision-time limits and cannot consume the same sampled venue quote twice. One pending or open position per symbol is allowed. Attempts inside the 30-second hourly funding guard are suppressed. Any actual crossing of a funding boundary or unresolved position flags the run as incomplete.
- Base assumed fees are 0.9 bps per side plus a 0.25-bps slippage reserve on turnover. Stress runs hold the forecast and threshold fixed while changing delay to 900 ms, fee to 9 bps per side, slippage to 1 bp per side, or entry size to $1,000. No priority fees are paid. The slippage reserve is debited in P&L and is separate from the order limit.
- The BBO is sampled and cannot verify the true quote or competing orders between updates. Market impact, exchange rejects other than modeled size and price checks, and account margin are outside this replay. Losses or gains from unfinished exits are outside realized P&L, so a run flagged incomplete cannot qualify. Closed P&L drawdown ignores unrealized losses.

A funding-adjusted, forward shadow replay across at least 30 fresh active sessions per candidate regime, with realized execution delays or more granular books and a positive lower day-block confidence bound after fees, would be required to establish a credible edge. The current study reports descriptive out-of-sample evidence.

## Data coverage adjustment and separate Binance experiment

The initial internal study produced zero common training rows. In the three development files, the 90th-percentile inter-arrival gap was 30–37 seconds for AAPL and 24–75 seconds for MSFT. The internal arm drops those two predictors and retains the same 2,000 ms freshness cap. Its reduced cross model pairs local features with XYZ100 returns, order flow, index/target gaps, and an index shock term. The peer feeds remain in the coverage audit. This adjustment uses the internal training dates and was committed before running that holdout.

The separate Binance arm shares dates and targets with the regular arm. Give the same-stock Binance USDT perp a chance to lead Trade[XYZ] using short returns, sampled top-of-book flow, return gaps, and a 60-second trailing basis adjustment. The offset estimate uses only earlier quotes and requires 75% coverage. Only Hyperliquid executes. The ablation uses the regular arm's full Hyperliquid feature set on common rows. This extension follows the result of the first regular arm, so it is an exploratory comparison on reused dates. Apply an extra 250 ms of delay to Binance receipt times as a sensitivity test for transporting third-party data to the execution host.
