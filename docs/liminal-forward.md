# Forward limUSD paper experiment

The prospective experiment began on November 23, 2026 at 21:44:29 UTC. The initial entry uses HyperEVM block 52,066,808 and 1,907.486964469928746871 simulated limUSD shares. This 2,000 USDC paper position carries forward the costs from the stressed historical test. The rolling rules were fixed on the same day, before the first exit. Every data request is read-only. [Historical paper audit](liminal-allocation.md)

## Fixed rules

1. Open each 63-day period from 2,000 paper USDC. Reserve 3 USDC for gas and 5 basis points for entry cost, leaving 1,996.0015 USDC for the `previewDeposit` quote. Hold that exact integer share amount. Each period starts with fresh simulated capital, and a rollover pays the costs again.
2. At each daily checkpoint, choose the first block at or after the target timestamp once both provider heads are at least eight blocks ahead. Confirm the predecessor and checkpoint block hashes with dRPC and PublicNode and require agreement on the identical contract inputs and quotes. Record the block time, retrieval time, and classification. Reads within five minutes of the selected block are contemporaneous. Later reads of the same predetermined block are archival recoveries. This captures outages without changing the tested dates. [HyperEVM RPC guidance](https://hyperliquid.gitbook.io/hyperliquid-docs/builder-tools/hyperevm-tools)
3. At the 63-day checkpoint, verify the instant redemption quote for the entire share balance. Confirm both contract assets, the current instant redemption fee, share size limits, pause and restriction status, the liquidity provider’s native USDC balance, and its allowance to the redemption pipe. The quoted amount already incorporates the contract fee. Apply another 10 basis points of modeled exit cost and report a separate additional 100 basis point loss scenario. Compare net USDC with the full 2,000 USDC compounded at 4% annually over actual elapsed time. In the first 63-day period, cash earns 13.59 USDC. [limUSD contract references](https://docs.liminal.money/tokenized/developers/limusd) · [Issuer withdrawal terms](https://docs.liminal.money/tokenized/flows)
4. Require three consecutive completed, non-overlapping windows with a full verified exit and positive cash excess. Daily price marks provide progress only. At a verified exit, start the next paper position at the same checkpoint using fresh simulated capital, if entry and instant exit capacity checks pass. A missed hurdle, failed full-size exit, or gap in re-entry breaks the streak. A liquidity failure stays in the ledger. Keep checking that position for recovery and then open the next independently funded paper period. Report delayed recovery separately from the failed checkpoint.

The first terminal checkpoint is January 25, 2027 at 22:44 Berlin time. The earliest three-window readout is May 31, 2027, assuming uninterrupted rollover. The historical limUSD paper profit was sensitive to extra costs, and the issuer can change fees and reserves. The paper model records the actual instant fee and reserve at each checkpoint. Its snapshots do not assess independent fair value of the fund or show a holder-specific executed transaction.

## First forward observation

The November 24 daily checkpoint was captured 40 seconds after its target. Both RPC providers agreed on the canonical block, the 30 bp instant fee, the full-position quote and a spendable reserve of 41,620.20 USDC. After the modeled exit cost, an immediate paper exit would have returned 1,988.18 USDC. Compounded cash on the starting capital was worth 2,000.21 USDC. This interim mark leaves the scored streak at zero. [First daily mark and recheck](../results/forward/liminal-first-mark-2026-11-24.json)

## Run and resume

Use Python 3.11 or newer on macOS or Linux. Initialize once from the recorded paper entry, which contains the initial block, exact share count, and matched provider quote. Initialization rechecks both sources. [Original paper entry](../results/forward/liminal-entry-2026-11-23.json) `watch` writes durable state and checks each new checkpoint. `status` reads it immediately. `tick` advances one due checkpoint when both providers agree.

```sh
python -m lane_z.liminal_forward --state data/liminal/forward-2026-11-23/state.json \
  init results/forward/liminal-entry-2026-11-23.json
python -m lane_z.liminal_forward --state data/liminal/forward-2026-11-23/state.json watch
python -m lane_z.liminal_forward --state data/liminal/forward-2026-11-23/state.json status
```

The watcher retrieves missed fixed checkpoints in order from both providers’ archives and labels them as recovered observations. A terminal provider disagreement or unavailable archive keeps the result unresolved for retry. An unavailable nonterminal daily mark may be recorded as missing after one day. The strategy score and streak survive restart. A manual restart uses `watch` against the existing state file.

The public recovery snapshot preserves the original entry, the November 24–26 contemporaneous observations, and the November 27 archival recovery. A resume from the earlier snapshot queried both providers and reproduced the November 27 canonical block, fee, full-position quote, spendable reserve, and modeled cash comparison. On a host that lost its local state, use the snapshot as `state.json` and start `watch`. Subsequent missed checkpoints acquire their own archival recovery labels. [Recovery snapshot](../results/forward/liminal-forward-recovery.json)

On the experiment host, user-level macOS LaunchAgents supervise both watchers. Each uses the project as `WorkingDirectory`, the absolute virtual-environment Python and state paths in `ProgramArguments`, and `KeepAlive` with `SuccessfulExit` set to false. It restarts after failure or when the user logs in again. A successful terminal exit stops the watcher for that login session. The host has to be awake and logged in for contemporaneous marks. Outages use the same archival recovery rules.

```sh
launchctl print "gui/$(id -u)/local.lane-z.liminal-forward"
launchctl print "gui/$(id -u)/local.lane-z.liminal-queue-forward"
```

The services append to `watch.log` alongside each state file. Read the service PID from `launchctl print` after a restart.
