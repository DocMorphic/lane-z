# Hedged paper trades

## Same-stock spread trade

The position buys one NVDA or TSLA equity perp and shorts the same underlying in the other venue, in equal share-equivalent quantities. That cancels first-order exposure to a common stock move when both legs fill at equal size. The price basis between contracts can still widen, the collateral is separate, and an asynchronous or partial fill creates exposure. This replay assumes USDT and Lighter collateral are both worth one U.S. dollar. It leaves stablecoin conversion costs, transfers, margin and liquidation outside its scope. [Binance equity perps](https://www.binance.com/en/learn/binance-futures-tradfi-perpetual-contracts) · [Lighter fee and account schedule](https://docs.lighter.xyz/trading/trading-fees)

At a 250 ms decision grid, subtract the mean basis of the previous 60 seconds from the current Binance–Lighter log midpoint basis. The mean requires at least 75% observed coverage. Buy the relative underperformer and short the relative outperformer when the absolute deviation exceeds the sum of both displayed spreads, both venues' round-trip fees, four slippage allowances, and 2 bps. This assumes some of the temporary deviation may revert. It does not imply convergence to a fixed payout or to identical reference prices.

- Use both public receipt clocks for the entry decision. Require a quote no older than 750 ms and a spread no wider than 8 bps on either venue. The gate opens on each new threshold crossing or direction change.
- Request up to $100 per leg, rounded down to the common 0.01 entry lot. Use each venue's own lot size for displayed partial fills. Both independently simulated orders use a 2 bp entry limit from the last observed touch.
- Match orders from the best quote visible on the venue clock at the modeled arrival. The base uses 500 ms per order leg. The replay assumes confirmation after a round trip. Equal quantities are held for 30 seconds from the later simulated match, rounded to the decision grid. If only one leg fills or amounts differ, attempt to close the filled positions immediately after the later confirmation instead. Closing orders can partially fill or retry. A retry cannot consume the same quote twice. The report marks any unresolved position or funding boundary crossing as incomplete.
- Charge Binance 4 bps and Lighter Standard 0 bps for every fill, including forced unwinds. Reserve another 0.5 bps per fill on each venue. Stress cases change one venue's delay to 900 ms, Binance fees to 5 bps, or slippage to 1 bp per fill. Signals stay fixed across cases. [Binance TradFi fees](https://www.binance.com/en/support/announcement/detail/e3424d3a781841eb8b507bc694c0ca3a)

Validation uses October 13–15, 2026. The holdout uses October 16 and 19–23. These dates are new to the project. Each window takes decisions from 15:02 until 15:58 UTC, with the remainder of the hour reserved for exits. The experiment charges no funding when both venues close within the hour. Archive receipt timestamps come from the data vendor. The replay captures neither confirmed live order latency nor market impact beyond available displayed size and the reserve.

The reported P&L includes forced unwinds, and residual exposure measures time with nonzero net share equivalents across venues. The base assumes simultaneous transmission and identical order delay. The asymmetric scenarios measure what happens when one venue is slower.

### Paper results

There were zero attempts in three validation windows and two attempts across six untouched test windows per symbol. Both test signals appeared on October 22. Both filled Binance only and were unwound. The base lost $0.1766 after $0.1593 in fees and $0.0199 in slippage reserve. Even with both venues set to 500 ms, price limits left the entries exposed for 1,000 ms each. There were no completed two-leg trades and no unresolved positions.

| Held-out execution | Hedge round trips | Forced unwinds | Paper net | Unhedged time p90 |
| --- | ---: | ---: | ---: | ---: |
| 500 ms per leg | 0 | 2 | −$0.1766 | 1,000 ms |
| Lighter at 900 ms | 0 | 2 | −$0.2208 | 2,000 ms |
| Binance at 900 ms | 0 | 2 | −$0.1928 | 3,800 ms |
| Binance at 5 bps | 0 | 2 | −$0.2165 | 1,000 ms |
| Reserve 1 bp per fill | 0 | 2 | −$0.1965 | 1,000 ms |

The sample shows the cost of missed legs, and it provides no filled-pair observations to estimate returns on the hedge itself. [Complete replay and 36 file hashes](../results/follow-up/hedged-perps.json)

## Funding carry

A longer hold can collect the difference between the venues' funding payments. At 15:30 UTC, the fixed screen uses the previous 24 hours of settled rates. When the difference exceeds 12 bps, it hypothetically shorts the venue where longs were paying more, buys the other venue, and measures the next 24 hours of funding at equal dollar notionals. The cost hurdle allows 8 bps for Binance round-trip fees, 2 bps for four fills' slippage, and 2 bps for combined spread. Actual spread and position P&L can vary. Lighter settles funding hourly. Its historical response's displayed hourly percent is combined with the payer direction. Binance rates are signed fractions per actual settled payment. [Lighter funding](https://docs.lighter.xyz/trading/funding) · [Lighter history API](https://github.com/elliottech/lighter-python/blob/main/docs/CandlestickApi.md#fundings) · [Binance funding history API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

The October validation period had one candidate across 58 symbol-days. On that TSLA day, a prior 12.37 bps spread flipped, and the following day would have paid −2.23 bps before costs, or −14.23 bps after the screen's 12 bps allowance. The November holdout had zero candidates across 38 symbol-days. This was a funding-only feasibility screen with no price-basis or fill simulation. [Funding screen results](../results/follow-up/funding-carry.json)

## Prediction markets and betting hedges

A complete set of the two tokens for the same Polymarket binary condition is backed by 1 pUSD per set. A sub-1 sum is an opportunity to investigate only if both sides can be acquired in sufficient size and the difference covers trading and settlement friction. Two quote snapshots can move independently before the second order arrives. [Position and token mechanics](https://docs.polymarket.com/concepts/positions-tokens) · [Fee mechanics](https://docs.polymarket.com/trading/fees)

At 13:39 UTC on November 21, 2026, the read-only screen took the 80 currently listed markets ranked by reported 24-hour volume. It kept 51 open standard binary conditions and found 45 with enough ask depth to buy ten tokens on both sides. The cheapest complete set cost **1.001 pUSD per 1 pUSD of backing before fees**. Six lacked sufficient ask depth or exceeded the ten-token minimum-size screen. This was one public snapshot and produced zero candidates even before costs. [Snapshot report](../results/follow-up/event-hedge-polymarket.json) · [Market discovery API](https://docs.polymarket.com/api-reference/markets/list-markets) · [Batch order books](https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body)

For opposite bets across venues, compare the payout in every settlement state. Matching titles can have different cutoff times, overtime rules, result sources, cancellation or refund provisions, or treatment of a player who did not participate. A cross-venue hedge can lose both legs when definitions disagree. Sportsbook hedges use the same payoff check with odds, stake limits, void rules, and fees. A combo pays based on multiple outcomes and can add correlated exposure. No cross-platform or sportsbook settlement pairs were replayed here. [Kalshi contract rules](https://help.kalshi.com/en/articles/13823822-market-rules) · [Kalshi sports and combo settlement](https://help.kalshi.com/en/articles/13823821-market-faqs)

## Reproduction

Use the hedge report's source URLs and filenames for the historical quotes. These commands replay the perps and capture a new prediction-market snapshot from public APIs.

```sh
python -m lane_z.hedge --data data/hedged --output data/check-hedged-perps.json
python -m lane_z.funding_screen --capture --snapshot data/funding/new.json \
  --output data/check-funding.json
python -m lane_z.event_hedge --capture --snapshot data/event-hedge/new.json \
  --output data/check-event-hedge.json
```
