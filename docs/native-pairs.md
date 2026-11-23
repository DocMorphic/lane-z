# Native perpetual relative-value pairs

## Fixed paper rule formed before scoring

Test whether correlated native assets converge after a large price-ratio deviation. Gatev, Goetzmann and Rouwenhorst test price relationships for stock pairs. A cryptocurrency pairs study found weaker performance and strong sensitivity to transaction costs and execution. This tests a simple spread state with lower turnover than ranking all assets every day. [Stock pairs research](https://doi.org/10.1093/rfs/hhj020) · [Cryptocurrency pairs evidence](https://ieeexplore.ieee.org/document/9200323/)

Fix ETH/BTC and SOL/BTC. At a midnight UTC decision, compute the log ratio at the last fully closed bar and the 90 earlier daily values. Require equal-window daily return correlation of 0.60 or higher and a centered lag-one log-ratio persistence estimate between zero and 0.90 inclusive. This is a rolling stability filter. It gives no guarantee of cointegration. Choose the eligible ratio farthest from its prior mean when the deviation is at least 2.5 standard deviations and less than 4. Enter toward convergence. Once entered, keep the reference mean and deviation scale fixed for the trade. Exit when the ratio crosses the mean, moves beyond 4 standard deviations in the adverse direction, or reaches seven days. Hold one pair at a time. Keep target signed notional at 25% of paper equity per leg when the pair is open, subject to the common minimum order rule. This controls initial net dollar exposure. Token-specific risk remains.

Every policy starts with 2,000 paper USDC, earns zero on unallocated capital and uses official signed hourly funding. Run the common four-hour fill proxy at 4.5 bp fees plus 5 bp reserve per changed order with an eight-hour delay, 15 bp reserve and 25% haircut to funding credits in stress. Compare four disjoint Dec 2025–Nov 22 2026 independent-account vintages after the 90-day formation. Reuse dates from the previous premium tests, label them as retrospective, and keep the spread rules fixed. Require after-cost 4% cash excess in all four stressed vintages before seeking an independent additional sample. [Execution and collateral method](perp-basis.md)

## Results

This rule generated one candidate in each of the first two periods and none in the last two. The first trade hit its spread stop. The second closed at the vintage boundary. Stressed price movement was already negative on both trades. Retire the fixed pair test because it yielded sparse trades and failed cash in every scored period.

| Window | Entries | 4% cash | Base net | Stressed net |
| --- | ---: | ---: | ---: | ---: |
| Dec 2025–Mar 2026 | 1 | +26.17 | −27.25 | −29.95 |
| Apr–Jun 2026 | 1 | +19.65 | −6.44 | −7.83 |
| Jul–Sep 2026 | 0 | +19.87 | 0.00 | 0.00 |
| Oct–Nov 22, 2026 | 0 | +11.21 | 0.00 | 0.00 |

[Paper results and source hashes](../results/follow-up/native-pairs.json)

Use the same public tape folders as the basis replay and install `requirements-research.txt`.

```sh
python -m lane_z.native_pairs --data data/perp-basis data/agent-native-trend \
  data/agent-spot-carry data/agent-spot-carry/older data/agent-selective-carry/late \
  --output data/native-pairs-reproduced.json
```
