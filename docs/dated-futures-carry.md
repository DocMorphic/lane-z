# Bitcoin dated-futures carry paper test

This test buys BTC spot and shorts a fixed-expiry inverse BTC future on Deribit. The futures contract settles to a BTC index price at expiry and has no periodic perpetual funding. The premium at entry can pay for holding collateral, fees, and execution costs. Each leg can still execute at a different price, and the spot sale can differ from the delivery index. The BIS study derives the same underlying spot-futures trade and documents substantial variation in carry across periods. [BIS crypto carry study](https://www.bis.org/publ/work1087.htm) · [Inverse futures](https://support.deribit.com/hc/en-us/articles/31424938981533-Inverse-Futures)

## Frozen rules

- Evaluate Deribit's BTC inverse futures that expire on the last Friday of each calendar month. Use June 2023–December 2024 for development, calendar 2025 for the first holdout, and January–October 2026 for the separate recent holdout. Use only contracts listed by the signal time. Reset each cohort to 1,000 USDC. Assume USD and USDC parity during the position.
- Signal at 08:00 UTC, exactly 21 calendar days before delivery. Use the prior closed hour's last spot and futures trades. The fixed hurdle is 4% annualized on all sleeve capital, prorated for the 21-day position. Estimate the carry with the reference fills and the base charges under a flat settlement spot price. Trade only if that estimate exceeds the hurdle. The threshold is frozen for both holdouts and both execution scenarios.
- Reserve USDC in cash and short approximately 90% of sleeve capital in $10 contract increments. Set the spot BTC quantity at decision time to futures face value divided by the reference futures price, rounded down to a satoshi. The spot BTC supplies the inverse contract's collateral. Credit the inverse short payoff in BTC from entry to the official delivery price, subtract the BTC-denominated futures taker and delivery charges, and sell the remaining BTC for USDC. The fixed spot quantity makes any mismatch between the reference and actual futures entry price count in P&L. [Deribit margin modes](https://support.deribit.com/hc/en-us/articles/25944811317149-Margin-types-and-usage) · [Deribit spot collateral](https://support.deribit.com/hc/en-us/articles/31424969480093-Spot-Instruments)
- Use the 09:00 UTC hourly opening trade as the base proxy for both entry legs and the 09:00 UTC hourly opening trade at expiry for the spot exit. This allows one hour from the signal to entry and one hour for the expiry settlement. Stress uses the hourly high for the spot buy and the hourly low for the futures short and spot sale. Each three-order cycle charges 5 bps per trade plus an execution allowance of 5 bps per trade in the base case or 15 bps under stress. Charge another 2.5 bps on futures delivery. Model spot fees in USDC and futures charges in BTC. Price proxies give no guarantee of simultaneous market liquidity or fills. Require each execution hour to have at least ten times the modeled trade notional and positive volume. A selected position with insufficient tape is flagged as incomplete rather than dropped from the conclusion. [Deribit fee schedule](https://support.deribit.com/hc/en-us/articles/25944746248989-Fees) · [Expiry and settlement](https://support.deribit.com/hc/en-us/articles/29734325712413-Settlement)
- Compound closed positions within each cohort. Cash that remains idle earns zero. Compare actual closed-equity P&L with zero-return cash and a 4% annual cash alternative held throughout the entire calendar span of the cohort. Report exposure time, net dollars, coverage gaps, and the worst daily traded-range estimate of collateral cushion. Daily traded futures highs and lows provide a margin stress proxy. They cannot establish mark-price liquidation behavior. Custody and stablecoin stress, transfers, and taxes are outside these paper models.

## Results

The archive covered all 41 scheduled expirations. Every selected cycle had price and range data and closed under the paper rules. Each cohort began with 1,000 USDC.

| Period | Positions closed | Base net | Adverse execution net | 4% cash hurdle |
| --- | ---: | ---: | ---: | ---: |
| June 2023–December 2024 | 7 | +43.93 USDC | −29.83 USDC | +64.31 USDC |
| 2025 holdout | 1 | +0.04 USDC | −6.81 USDC | +40.00 USDC |
| January–October 2026 holdout | 0 | 0.00 USDC | 0.00 USDC | +35.54 USDC |

The sleeve invested for 147 calendar days in development, 21 in the first holdout, and zero in the recent holdout. The decision-time premium failed to survive even the hourly price range stress in 2025, and never cleared the hurdle in 2026. The fixed rule is retired on this evidence. [Complete paper report and source hashes](../results/follow-up/dated-carry.json)

```sh
python -m lane_z.dated_carry fetch --data data/dated-carry
python -m lane_z.dated_carry run --data data/dated-carry --output data/dated-carry-check.json
```
