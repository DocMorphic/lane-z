# Market maker behavior and the paper test

## What competitors disclose

| Participant or source | Public strategy or constraint | Observable consequence to test |
| --- | --- | --- |
| Hummingbot | Its open-source quoting strategy supports bid and ask offsets, periodic quote replacement, refresh tolerances, and asymmetric order quantities to manage inventory. | Track quote additions and removals at unchanged prices, alongside signed trade volume. [Configuration](https://hummingbot.org/strategies/v1-strategies/strategy-configs/) · [Implementation](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy/pure_market_making/pure_market_making.pyx) |
| Flow Traders | Its annual report describes continuous two-sided pricing across markets and generally hedging positions immediately. | Include the same underlying on another venue. A move confined to one book may have different follow-through from a broader repricing. This is a testable inference from the disclosed activity. [Annual report, market and liquidity risk](https://www.flowtraders.com/media/2s2pdl01/flow-traders-annual-report-2025.pdf) |
| Wintermute | Its public description names integrated traditional and crypto market signals, distributed low-latency infrastructure, and quantitative arbitrage. | Timestamp each venue independently at the collector and test how much any lead survives realistic arrival and execution delays. [Algorithmic trading](https://www.wintermute.com/algorithmic-trading) |
| Hyperliquid HLP | The protocol vault runs several market-making strategies, performs liquidations, and receives a share of trading fees. | Liquidation flow and changes in displayed depth can coincide. The Lighter tape can test how signed aggressive flow and depth changes interact. [Protocol vaults](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/protocol-vaults) |
| NYSE and NYSE Arca | NYSE DMMs have market and auction obligations. NYSE Arca runs electronic price/time priority and applies separate obligations to lead market makers. | Treat auctions, continuous quoting, and queue priority as venue-specific. The Arca data used here is one venue's BBO during continuous trading. [NYSE equity market models](https://www.nyse.com/trade/equities) · [DMM obligations](https://www.nyse.com/publicdocs/nyse/markets/nyse/designated_market_makers.pdf) |

An aggregate best quote shows prices and displayed sizes. It can show the net effect of orders entering and leaving the book. Several explanations can fit the same change, including fills, cancellations, inventory management, and repricing after an external move. The data used in this test provides no firm-level identities, private hedge positions, hidden orders, or our priority in a passive queue.

## Research behind the signals

- **Inventory and adverse selection.** Avellaneda and Stoikov derive an inventory-sensitive reservation price and quote placement policy. Cartea, Donnelly, and Jaimungal test order book imbalance as information about trade arrivals and price changes and use it to manage adverse selection. This motivates testing asymmetric replenishment and taking fewer trades when the book deteriorates. [Avellaneda and Stoikov](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf) · [Cartea, Donnelly, and Jaimungal](https://ora.ox.ac.uk/bookmarks/uuid%3A006addde-3a03-4d75-89c1-04b59026e1c0)
- **Depth and horizon.** Cont, Kukanov, and Stoikov relate short-interval price changes to order flow imbalance and show that impact grows as depth falls. Gould and Bonart find that queue imbalance predicts the next midpoint move more reliably in large-tick stocks in their sample. Both motivate scaling flow to displayed depth and measuring the remaining move after execution delay. [Cont, Kukanov, and Stoikov](https://arxiv.org/abs/1011.6402) · [Gould and Bonart](https://arxiv.org/abs/1512.03492)

## Frozen experiment

Paper trade NVDA and TSLA equity perps on Lighter Standard with the same-stock Binance USDⓈ-M contract as a reference. Score every 250 ms. Predict the five-second midpoint return from simulated match to scheduled exit, with 500 ms per order leg and exit scheduling rounded up to the grid. Lighter documents 0% standard fees and a 300 ms order delay. The 500 ms scenario adds a 200 ms allowance for transport and processing. The stress scenarios use 900 ms per leg or 1 bp slippage per side. Archive receipt clocks drive observations, and exchange event clocks drive simulated fills. [Lighter fee and latency schedule](https://docs.lighter.xyz/trading/trading-fees) · [Trade schema](https://www.cryptohftdata.com/docs/rest-trades) · [Lighter archive coverage and clocks](https://www.cryptohftdata.com/exchanges/lighter)

The four policies use common feature availability. Each ridge is fitted per symbol on the same labeled training rows with fixed 0.1 regularization.

1. **Local** uses Lighter returns, spread, volatility, book imbalance, and order flow imbalance.
2. **Flow** adds the last second of trade aggressor imbalance and total aggressive size relative to displayed depth. Only reports observed by the decision timestamp count. A buyer-maker flag marks a sell aggressor.
3. **Book and leader** adds same-price bid and ask size increases and decreases over four 250 ms steps, scaled by current displayed depth. It also incorporates spread change, the interaction of signed flow with replenishment, Binance returns and book pressure, and the one-second relative move. Size changes are net observations at the sampled quote and can miss changes between grid points.
4. **Adaptive** uses the same forecast and stands aside when total displayed best-quote size drops below 25% of its value one second earlier, or when the spread at least doubles and exceeds 2.5 bps.

Every policy requires a Lighter quote no older than 750 ms, a spread of at most 5 bps, and a predicted signed move exceeding the displayed spread, modeled fees and slippage for both legs, and 0.25 bp. Each $100 attempted entry crosses the available quote only within a 2 bp limit. The exit uses the existing position-reduction rules. The stress runs keep the base entry signal and apply the changed execution settings. Limits, stale quote checks, partial fills, and the funding guard stay active.

Each date uses 15:02–15:58 UTC for decisions, with the final two minutes of the file reserved for exits. The calendar split crosses the U.S. daylight-saving change and shifts the held-out window one hour earlier relative to New York local time. All eight dates are disjoint from earlier NVDA/TSLA paper replays.

| Period | 2026 dates |
| --- | --- |
| Train | Oct 26, 27, 28 |
| Validation | Oct 29, 30 |
| Test | Nov 2, 3, 6 |

No policy is selected from the test. The adaptive policy qualifies only with at least 25 completed round trips in both validation and test, positive and complete validation base P&L, positive test base P&L on at least two dates, and positive and complete totals under each test execution scenario. The qualification is a research filter and three held-out hours per symbol provide limited evidence about other market regimes.

## Results

The archive provided 53,659 comparable validation labels and 80,520 held-out labels. All four policies made zero attempts in either period. The adaptive rule flagged 15,920 held-out decision rows as thin or widening. No forecast reached the cost gate with or without that rule, so this run cannot measure whether avoiding those rows improves executed P&L.

| Forecast | Test RMSE (bps) | Test correlation to later midpoint return |
| --- | ---: | ---: |
| Constant zero | 4.027109 | — |
| Local | 4.026962 | 0.01801 |
| Flow | 4.027012 | 0.01771 |
| Book and leader | 4.027415 | 0.01763 |

The richer forecast improved correlation in validation (0.02241 versus 0.00696 for local), and the gain disappeared on the holdout. Aggressor flow, same-price depth changes, and external quotes were plausible signals. Together they produced no executable edge in this sample. The exercise left the paper portfolio flat. [Full metrics and 48 source hashes](../results/follow-up/maker-response.json)

## Reproduction

Install `requirements-research.txt`. The report records the URL and SHA-256 of every input. Download them into `data/makers/` under the report's `file` names, then run the following command.

```sh
python -m lane_z.makers --data data/makers --output data/maker-response.json
```
