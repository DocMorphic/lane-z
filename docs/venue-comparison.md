# Fresh paper tests and venue comparison

This follow-up keeps the [original Binance-input model](../results/binance.json) frozen and tests two separate hypotheses using [pre-specified rules](forward-protocol.md). All results are paper fills against recorded top quotes. Equity perps are synthetic contracts with their own books and reference prices. SPY and IWM are cash ETF shares.

## Venue mechanics

| Venue | Market tested | Per-fill fee used | Main execution assumption |
| --- | --- | --- | --- |
| Hyperliquid / Trade[XYZ] | NVDA and TSLA equity perps | 0.9 bps | Growth-mode fee. Quote ages are measured on the captured feed, while order-to-match delay is modeled. [Fee schedule](https://docs.trade.xyz/perpetuals/mechanics/fees/) |
| Binance USDⓈ-M | NVDAUSDT and TSLAUSDT equity perps | 4 bps, stressed at 5 bps | The public regular TradFi promotion takes 20% off the 5-bps taker rate. The archive records receipt and exchange clocks. [Fee example](https://www.binance.com/en/support/faq/detail/360033544231) · [Promotion notice](https://www.binance.com/en/support/announcement/detail/e3424d3a781841eb8b507bc694c0ca3a) |
| Lighter Standard | NVDA and TSLA equity perps | 0 bps | Regular orders have zero protocol fees and a documented 300 ms taker delay. Use 500 ms to allow for transport and stress 900 ms. [Account types and fees](https://docs.lighter.xyz/trading/trading-fees) |
| NYSE Arca | SPY and IWM cash ETFs | $0.003 per share plus 0.5 bps reserve | Whole shares on one venue's BBO. Receipt adjusted by a modeled 125 ms with 250 ms sensitivity. The reserve is a research assumption for broker, routing and regulatory costs. [BBO product](https://www.nyse.com/data-products/catalog/taq-bbo) · [Exchange fee schedule](https://beta.nyse.com/publicdocs/nyse/markets/nyse-arca/NYSE_Arca_Marketplace_Fees.pdf) |

NYSE Arca samples cover an individual venue. Nasdaq-listed NVDA and TSLA cash equities need a direct Nasdaq feed or synchronized consolidated quotes for a same-name cash/perp comparison. The public Arca samples used here cover SPY on both July dates and IWM on July 1. [NYSE historical feeds](https://www.nyse.com/market-data/historical) · [Nasdaq TotalView specification](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf) · [Public Arca samples](https://ftp.nyse.com/Historical%20Data%20Samples/TAQ%20NYSE%20ARCA%20BBO/)

## Fresh frozen Binance signal

The November 1 Tardis weekend archive yielded 26 finite NVDA forecasts and zero TSLA forecasts over 89,760 eligible decision times per symbol. Zero forecasts cleared the frozen cost gate. At 500 ms execution delay, 0.9 bps fees and 0.5 bps slippage reserve per side, the replay made zero order attempts. The archive confirms sparse feature coverage on weekends. A separate check of current historical Hyperliquid depth found roughly 12.4 seconds between snapshots and rejected that data for millisecond fill simulation. [Tardis native quote data](https://docs.tardis.dev/historical-data-details/hyperliquid) · [CryptoHFTData archive](https://www.cryptohftdata.com/datasets/hyperliquid-orderbook-data)

The first live Saturday check captured 53,753 messages. Its 12-minute decision window had 95 finite NVDA and 58 finite TSLA forecasts out of 2,880 grid decisions per symbol. None cleared the entry gate at any tested latency. On this host the median age of the target quotes was 423–427 ms from Hyperliquid and 130–131 ms from Binance. Binance 90th-percentile ages were 352–446 ms. These are measured quote ages, separate from the simulated order delays. The limited weekend coverage leaves regular-session forward performance unverified.

## Reversal and continuation

A fixed 60-second reversal strategy bought a small rally after a ten-second liquidity shock when the local book favored buying. Binance and Lighter ran on six common recent weekday hour windows. Their matched samples allow a tighter comparison than the differing July cash ETF and weekend Hyperliquid periods.

A distinct continuation rule waited for a ten-second rally in both Binance and Lighter, then bought a small Lighter pullback while Binance held up. Six different weekday hours were held out for this rule. It crossed the spread on Lighter only.

| Test | Attempts | Round trips | Net USD | Net bps per filled entry notional |
| --- | ---: | ---: | ---: | ---: |
| Binance reversal, 500 ms | 311 | 268 | −$137.93 | −11.24 |
| Lighter reversal, 500 ms | 392 | 328 | −$32.08 | −2.17 |
| Lighter continuation, 500 ms | 140 | 105 | −$2.37 | −0.53 |
| Lighter continuation, 900 ms | 155 | 96 | −$10.65 | −2.51 |

### Where the P&L went

The midpoint column measures signed price movement between the simulated entry and exit matches. It weights each partial exit by its filled size. Spread cost is the difference between that midpoint result and the result at executable bid and ask prices. The fee and slippage columns are both deducted from the result after spread. All values are USD and use 500 ms per order leg.

| Test | Midpoint move | Spread cost | After spread | Modeled fees | Slippage reserve | Net |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Binance reversal | −$21.37 | $6.16 | −$27.54 | $98.13 | $12.27 | −$137.93 |
| Lighter reversal | +$0.61 | $17.93 | −$17.32 | $0.00 | $14.76 | −$32.08 |
| Lighter continuation | +$8.01 | $5.88 | +$2.13 | $0.00 | $4.50 | −$2.37 |
| NYSE Arca SPY queue pressure | −$3.08 | $24.37 | −$27.45 | $65.92 | $61.01 | −$154.38 |

Binance's reversal forecast went against subsequent price movement even at the midpoint. Fees accounted for most of the total loss. The Lighter reversal had almost flat midpoint P&L, and paid $17.93 across the spread at a zero-fee venue. Two of six continuation dates had positive 500 ms net P&L. These tests yielded no qualifying paper strategy.

The 500 ms continuation replay could absorb about 0.24 bps of extra slippage on each side before turning negative. Its paper trades used a 0.5 bps-per-side reserve. At 900 ms the result was already negative before the reserve.

On six matched full regular sessions in the Tardis archives, the same reversal lost $39.20 on 299 Hyperliquid round trips and $76.50 on 229 Binance round trips at 500 ms. That is −2.88 bps and −8.04 bps per filled entry notional, respectively. The dates were also used for the prior research, so this is a retrospective venue comparison. The short live Hyperliquid weekend window produced zero reversal attempts.

## NYSE Arca paper fills

The cash reversal made one IWM round trip, losing $0.20 at 500 ms. It made zero attempts when the modeled feed receipt delay changed from 125 to 250 ms. Both SPY dates yielded no reversal setup at the pre-specified 8 bps minimum shock.

The separate five-second queue-pressure diagnostic on July 2 SPY completed 819 round trips at 500 ms for −$154.38, or −2.53 bps on filled entry notional. It lost $27.45 after crossing spread and before costs. At 900 ms it lost $143.28 over 751 round trips. The cash result uses a single venue's top quote, no consolidated routing and one held-out cash date for that diagnostic. Quote Message 140 publishes the ask fields first and sizes in shares. [NYSE Pillar file specification](https://www.nyse.com/publicdocs/nyse/data/TAQ_Pillar_Products_Client_Spec_v2.3e.pdf)

## Reproduce

The [JSON reports](../results/follow-up/) include raw source SHA-256 checksums, per-date and per-symbol figures, and replay cost settings. The public data files stay local. For the CryptoHFTData arms, download each `url` from the input audit into the corresponding `file` name below the hours directory. Download both Arca channels per date from the input audit into the Arca directory.

```sh
python -m pip install -r requirements-research.txt
python -m unittest discover -s tests

python -m lane_z.reversion binance --data data/venue/hours --output data/check-binance.json
python -m lane_z.reversion lighter --data data/venue/hours --output data/check-lighter.json
python -m lane_z.continuation --data data/venue/hours --output data/check-continuation.json
python -m lane_z.reversion arca --data data/venue/arca --output data/check-arca.json
python -m lane_z.reversion arca --arca-queue-diagnostic --data data/venue/arca --output data/check-arca-queue.json

python -m lane_z.study fetch --regime binance
python -m lane_z.reversion hyperliquid --tardis-regular --data data/tardis --output data/check-hyperliquid-regular.json
python -m lane_z.reversion binance --tardis-regular --data data/tardis --output data/check-binance-regular.json
```

Forward replay uses published model parameters with no refit. For a shorter capture on a single UTC date, pass `--utc-window START END` and retain at least two additional captured minutes to process exits. Set `--delay-ms` and `--slippage-bps` to reproduce a reported execution scenario. The signal retains its published entry gate.
