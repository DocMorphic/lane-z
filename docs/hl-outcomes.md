# Hyperliquid daily outcome complete sets

Hyperliquid lists USDC-quoted daily price outcomes on BTC, ETH, SOL and HYPE. Buying equal quantities of both sides of the same binary outcome costs the sum of the asks. The screen checks whether a notional $1 complete settlement payout per set could cover that cost. It uses the public outcome IDs and read-only order books. [Outcome asset IDs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids#outcomes) · [Public book API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#l2-book-snapshot)

On November 23, 2026, between 12:10 and 12:16 Berlin time, 36 public API polls sampled both sides of all four outcomes expiring November 24. Sweep displayed asks for 100 units of each side when the book timestamps differ by no more than 250 ms and each leg has at least 10 USDC of order value. Three consecutive twelve-poll batches yielded 48, 47 and 44 comparable pairs. The lowest estimated cost for a full set was 1.00343 USDC before any fees, for BTC in the middle batch. No pair crossed below the modeled payout. The six-minute snapshot checked one expiry. It did not generate paper fills or measure a latency-adjusted realized return.

```sh
python -m lane_z.hl_outcomes --capture --data data/outcomes-next/books.json \
  --output data/outcomes-next/report.json
```

[Public quote-pair results](../results/follow-up/hl-outcomes.json)
