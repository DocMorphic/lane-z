# Forward and cross-venue protocol

The October holdout qualified neither the Binance model nor its Hyperliquid-only ablation. The positive 250 ms diagnostic turned negative with an added 125 ms of Binance feed delay. Keep the published 5,000 ms weights, spread gate, and 0 bps additional margin frozen.

## Fresh Binance signal

- Use received and exchange timestamps recorded independently for each feed, with no stale quote substituted as a fresh observation. The reference execution delay is 500 ms per leg, with 250 ms and 900 ms sensitivity. Debit 0.9 bps per side plus 0.5 bps per side slippage reserve. These are paper latency assumptions. Timing of actual order arrival was not measured.
- The first-of-month November 1 archive and the November 21 live capture are new to the Binance arm. Both are weekend samples outside its original training regime. Report them as transfer diagnostics with eligible forecast rows, attempts and fills. A zero-trade weekend result is evidence of abstention or insufficient coverage, according to the measured coverage.
- A regular-session forward test requires dense Hyperliquid quotes across the target and peer markets. The inspected CryptoHFTData Hyperliquid order book is snapshots only at roughly 12-second intervals. Reject it for millisecond fills or for generating dense five-second features.
- Keep independently sourced archive data and live captures distinct. Sampled exchange BBO and research feed receipt times are proxies for the venue state and this machine's future inputs.

## Alternative strategy

Test a same-venue, long-only liquidity-shock reversal. There is no trained model or cross-venue lead. At 250 ms decision intervals, require the target to be down at least 8 bps versus 10 seconds ago, up at least 0.5 bps versus one second ago, and have more displayed size on the bid than ask. The ten-second drop must also exceed twice the displayed spread plus round-trip fees and slippage. Restrict the initial spread to at most 5 bps. Trade only on the transition into eligibility, so the same event cannot send repeated entries on each grid row. Submit IOC limits with the existing sampled-depth mechanics, suppress entries around funding, then hold 60 seconds. All listed venues use the same signal and order price caps. The stock arm uses whole shares and buys before it sells so the comparison needs no borrow assumption.

Use $500 requested entry notional on all venues to make whole-share fills possible. Evaluate a common 500 ms order delay with 0.5 bps slippage per side, then stress 900 ms. Binance USDT equity-perp regular-user fees use 4 bps per side during the TradFi promotion and 5 bps as a stress. Hyperliquid growth-mode fees use 0.9 bps per side. Lighter Standard charges zero explicit fees and has a documented 300 ms taker delay before transport, so the common 500 ms scenario is retained. NYSE Arca uses its standard $0.003 per share exchange fee on each side plus a 0.5 bps per-side reserve for additional routing, broker and regulatory costs. NYSE Arca event-only quotes assume a modeled 125 ms market-data receipt delay, with a 250 ms sensitivity. Compare fills and actual entered notional alongside the net bps so zero-fill cases cannot appear equivalent to a profitable run.

The November 4, 11, 13, 18, 19, and 20 15:00–16:00 UTC Binance and Lighter archives are the common cross-venue weekday sample. The NYSE Arca sample uses July 1 and 2 official BBO files. The Hyperliquid weekend arm uses collected November 21 quotes. Different dates, sessions and structural costs limit cross-group comparisons. CryptoHFTData sampled Hyperliquid liquidity and Coinbase or other instruments cannot substitute for a matched venue replay. Parameters were set before reading P&L from these tapes, and any subsequent rule changes need additional held-out dates.

The two public Arca sample channels did not contain NVDA or TSLA. Their symbol-mapping records did contain SPY and IWM, so the cash arm uses those two liquid ETFs and explicitly compares different instruments. This availability adjustment was made before observing any cash-arm returns.

## Separate Lighter continuation check

The Binance and Lighter cash-hour reversal arms were negative before explicit fees. A different hypothesis is that a small pullback after a confirmed move continues in the direction of the move when an independently priced same-stock perp still confirms it. Try this on Lighter Standard, which has lower explicit taker cost and a mandatory matching delay.

For each NVDA and TSLA, go long on the first qualifying grid row when both Lighter and Binance are up at least 8 bps over ten seconds, the last one second is flat or positive on Binance and down 0.5–3 bps on Lighter, Lighter bid size exceeds ask size, and Lighter spread is at most 4 bps. Hold for 60 seconds with the same order caps and funding exclusions. Use the 500 ms and 900 ms Lighter assumptions plus a 1 bp per-side slippage stress. Keep Binance as a signal source. Use six dates withheld from the reversal arm, November 5, 9, 10, 12, 16 and 17, from 15:02 UTC through the hour with a two-minute exit buffer. Freeze the rule before reading any return on those dates.

Arca symbol maps show SPY on both dates and IWM only on July 1. The report uses all three of those market-days and reports the cash P&L by symbol and date.

The Arca sample's SPY reference price exceeds the original $500 request. The cash arm uses a $1,000 maximum request so it can buy at least one share of SPY. The perpetual arms keep $500. This sample-based sizing correction precedes inspection of the Arca replay outputs.
