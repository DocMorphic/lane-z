# Hyperliquidity Provider paper allocation

## Protocol fixed before scoring on November 23, 2026

HLP is Hyperliquid’s protocol market-making and liquidation vault. It takes inventory, earns part of protocol trading fees, and supplies USDC in Earn. This tests a passive share of that operation, with all 2,000 paper USDC allocated for the window. There is no signal fitting, leverage at the depositor level, rotation among vaults, or wallet activity. Hyperliquid specifies a four-day deposit lock. [Protocol vaults](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/protocol-vaults)

Use the paired `accountValueHistory` and `pnlHistory` samples from Hyperliquid’s public HLP `vaultDetails` response. Cash flows change account value without themselves earning a depositor return. For each consecutive pair of samples, infer net flow as the change in account value less the change in reported cumulative P&L. Estimate the interval return as the change in P&L divided by starting account value plus half the inferred net flow. Geometrically link these returns and recalculate at both endpoints of the net-flow timing assumption. Those endpoint scenarios do not bound returns when offsetting intraperiod flows or P&L timing matter. Portfolio values are sampled, so this is an estimate of depositor return, with drawdown measured at sample times. Individual share prices and depositor fills are not available in this response. [Vault API](https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint#retrieve-details-for-a-vault) · [Portfolio graph sampling](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/portfolio-graphs) · [CFA Institute on flow-weighted return estimates](https://www.gipsstandards.org/wp-content/uploads/2021/03/gips_standards_handbook_for_asset_owners.pdf)

Test eight sequential windows from calendar Q1 2025 through November 22, 2026, with Q4 2026 partial. At quarter starts, use the recorded observation nearest midnight UTC (ties go to the earlier one), within seven days. Use the latest observation through November 22 UTC for the final endpoint. Adjacent windows share an endpoint with no overlap in compounded returns. The cash hurdle is 2,000 USDC compounded at 4% annual over the actual sample timestamps. These are retrospective windows, chosen before reading returns from the raw API history. Published descriptions of earlier HLP activity were already public.

Base reserves are 10 bp each for entering and redeeming, plus the documented 1 USDC Hyperliquid bridge withdrawal fee per window. Stress uses 25 bp on both sides, the same fixed charge, and a separate 50 bp final USDC redemption haircut. The proportional costs are scenario assumptions for execution and transfers. The vault snapshot must report zero leader commission for these scenarios. We use reported vault P&L, which reflects the underlying portfolio performance, and do not add a separate estimate for its earned rebates or transaction expenses. The reserves are debited on the full allocated capital, with the exit charge applied to closing equity. [Vault withdrawal behavior](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy) · [Bridge fee](https://hyperliquid.gitbook.io/hyperliquid-docs/onboarding/how-to-start-trading)

A candidate needs to beat 4% cash after stress in each observed 2026 window across all three flow-timing estimates for this screen to justify deeper ledger reconstruction. Report each 2025 result too. Qualification requires verifying material results against external ledger flows or share values and accounting for lockup, settlement liquidity and unsampled event drawdown. Drawdown stress is separate from steady-state execution costs. A positive cumulative vault P&L or trailing APR alone cannot qualify it.

## Results

Each test starts at 2,000 paper USDC. The table uses the midpoint net-flow timing estimate. The final row ends November 22 and uses observed marks from September 30. The four latest windows failed the predeclared requirement of beating cash under stress throughout.

| Window | Base paper P&L | Stressed paper P&L | 4% cash P&L | Stressed excess vs cash |
| --- | ---: | ---: | ---: | ---: |
| 2025 Q1 | +48.63 | +32.26 | +18.13 | +14.13 |
| 2025 Q2 | +43.89 | +27.56 | +19.65 | +7.91 |
| 2025 Q3 | +52.76 | +36.36 | +21.17 | +15.19 |
| 2025 Q4 | +156.78 | +139.55 | +21.17 | +118.38 |
| 2026 Q1 | +138.54 | +121.45 | +18.14 | +103.32 |
| 2026 Q2 | −13.27 | −29.14 | +18.13 | −47.27 |
| 2026 Q3 | +11.36 | −4.71 | +21.17 | −25.88 |
| 2026 Q4 through November 22 | +27.73 | +11.53 | +11.41 | +0.12 |

Shifting the assumed flow timing within each sample interval gave Q2 2026 stressed excess of −47.72 to −46.87 USDC and Q3 excess of −26.07 to −25.66. The latest excess ranged from −1.64 to +2.19. In a separate 24-hour accounting check, the 142 parent-vault deposit and withdrawal events summed to 1,113,022.07 USDC of net inflow. The P&L and account value sample implied 1,113,022.21 USDC for the same interval. This supports the recent flow treatment. The sparse quarterly estimates still lack event-time share prices and a full event ledger.

HLP's drawdown was sampled at roughly weekly to biweekly intervals for this screen. Those samples can miss losses around liquidations. Hyperliquid lists market-making and liquidation among HLP's functions. A 21Shares SEC filing documents losses to liquidity provider vaults during the JELLY event. The good 2025 and early 2026 returns are consistent with exposure to time-varying market-making and liquidation income. HLP's private trading rules and realized depositor drawdowns cannot be inferred from these aggregate marks. [Vault functions](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/protocol-vaults) · [21Shares filing](https://www.sec.gov/Archives/edgar/data/2090011/000121390026016539/ea0276878-s1a1_21shares.htm)

Reproduce all months from the frozen aggregate marks. The input contains no depositor-level rows. Replace `--snapshot` with `--raw data/hlp/vault-details.json --fetch` to capture a fresh public API snapshot. Downsampled marks can change when the venue regenerates the latest history.

```sh
python -m lane_z.hlp_vault --snapshot results/follow-up/hlp-vault-marks.json \
  --output data/hlp/reproduced.json
cmp data/hlp/reproduced.json results/follow-up/hlp-vault.json
```

[Paper results and timing sensitivity](../results/follow-up/hlp-vault.json) · [Frozen aggregate marks](../results/follow-up/hlp-vault-marks.json)
