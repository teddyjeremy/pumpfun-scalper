# Early Post-Entry Continuation — Run15 / Run16 Findings

**Research status:** Exploratory, retrospective; no strategy rule proposed.  
**Input:** PR35 reprocessed Run15 and Run16 artifacts; derived checkpoint table `early_path_tx.csv`.  
**Curve assumptions:** $1 stake, SOL/USD $71.69, $0.0285 round-trip gas.  
**Lane:** wallet-lock + positive-flow gate, 500ms primary comparison.  
**Important:** the exit-reason labels are from the paired latency replay. The checkpoint P&Ls are counterfactual executable Pump curve sell quotes after each complete observed transaction, not actual submitted fills.

## 1. Integrity and sample

- The 500ms lane contains 26 paired trade paths: 11 in Run15 and 15 in Run16.
- All 26 are distinct mints within their respective sessions.
- All 26 curve paths replayed; no skipped trade in these two flow-gated 500ms sets.
- The 500ms curve P&L sums reconcile to PR35: Run15 +$3.218909 and Run16 -$0.791356.
- The three latency lanes overlap and are paired variants. The 68 total rows across 0.5/1.0/1.5s are not 68 independent opportunities.
- Initial curve entry impact after fees/gas starts near -$0.0532 per $1 position. Early improvement must first overcome this entry deficit.

## 2. 500ms primary comparison

| Paired replay exit reason | Trades / unique mints | Positive curve quotes at 1s | at 3s | at 5s | at 10s | Median curve P&L at 3s |
|---|---:|---:|---:|---:|---:|---:|
| Take profit | 10 / 10 | 3/10 | 8/10 | 8/10 | 8/10 | +$0.1200 |
| Stop loss | 4 / 4 | 0/4 | 0/4 | 0/4 | 0/4 | -$0.1683 |
| Time cap | 12 / 12 | 1/12 | 1/12 | 1/12 | 1/12 | -$0.0472 |

Median path descriptors:

| Exit reason | Median net SOL flow, first 3s | Median distinct traders, first 3s | Median best curve P&L over path | Median worst curve P&L over path |
|---|---:|---:|---:|---:|
| Take profit | +4.3638 SOL | 9 | +$0.2399 | -$0.0517 |
| Stop loss | -2.5428 SOL | 6 | -$0.0451 | -$0.2364 |
| Time cap | +0.1573 SOL | 5 | -$0.0335 | -$0.0522 |

The distinction is not simply buy flow positive versus negative. Time-cap trades often had slightly positive net SOL flow, but the flow was much smaller and involved fewer traders. Their executable quote generally remained close to the initial cost deficit.

## 3. Session split matters

The apparent take-profit pattern is not identical in the two sessions.

- **Run15:** 8 take-profit paths, 2 stop-loss paths and 1 time-cap path. Median take-profit curve P&L was -$0.0390 at 1s, +$0.1336 at 3s and +$0.2254 at 5s. Median first-three-second net flow was +4.5969 SOL.
- **Run16:** only 2 take-profit paths, 2 stop-loss paths and 11 time-cap paths in this 500ms lane. The two take-profit paths were positive at the 1s checkpoint, but this is far too small to establish a stable cross-session predictor. The time-cap median at 3s was -$0.0472, with +0.1563 SOL median net flow and 5 median distinct traders.

The session mix changed dramatically: Run15's 500ms lane was mostly take-profit; Run16's was mostly time-cap. The combined median therefore hides a material regime/sample difference.

## 4. What this supports

1. **Post-entry continuation is worth investigating.** Eventual take-profit paths generally moved from the initial entry deficit into positive curve quotes by 3–5 seconds; most time-cap paths did not.
2. **Positive net flow alone is not enough.** The time-cap group often had positive net SOL flow, but its median flow and trader breadth were materially lower than the take-profit group.
3. **Stop-loss paths show a different deterioration pattern.** They had negative median net flow and progressively worse curve quotes.
4. **A fixed early-negative exit is unsafe.** Some eventual winners take longer to recover. The full path contains delayed-recovery examples, including PoNZI in the longer latency variants.
5. **The original paired exit label is not the same as curve-executable outcome.** For example, some trades labelled take-profit by the reserve-mark paired replay have only small or even negative curve P&L after execution costs. Always report both.

## 5. What this does not support

- It does not justify a 1s, 3s or 5s hard exit.
- It does not justify a minimum net-SOL-flow or trader-count threshold.
- It does not prove the first 3–5 seconds causally predict the eventual outcome; outcome groups are retrospective and selected by the original exit logic.
- It does not establish an out-of-sample strategy edge. Run16 has only two 500ms take-profit paths.
- It does not prove that a curve quote could be filled on-chain at that exact time.

## 6. Next analysis required

The next pass must use the full ordered transaction tape and reconstruct each path independently, not merely use the summary checkpoint CSV:

1. Reconcile every trade's signal, selected entry, exit trigger and selected exit signature.
2. Generate exact per-transaction curve quotes from entry through exit.
3. Calculate MFE, MAE, first breakeven crossing, time above breakeven, drawdown from MFE and recovery from MAE.
4. Measure buy/sell reserve response and sell absorption, not only aggregate SOL flow.
5. Compare the same mint across latency variants without multiplying the independent sample count.
6. Check whether any descriptor can be computed at the proposed decision time without future information.
7. Validate candidate descriptors across sessions and then on a fresh frozen observation.

**Decision:** retain early continuation as the leading research hypothesis. Do not modify the sniper's entry, exit, timer or flow-gate rules yet.


## 7. Transaction-level curve absorption and sell recovery

A second pass reconstructed each complete transaction between the paired 500ms flow-gated entry and exit signatures. The selected signature was resolved to its final tape event, every observed reserve transition was validated, and the final curve quote reconciled exactly to the PR35 curve replay (maximum absolute P&L difference: $0.0000000000).

**Path sample:** 26 unique mints (Run15: 11; Run16: 15), comprising 10 paired take-profit labels, 4 stop-loss labels and 12 time-cap labels. The 153 adverse sell transactions below are nested within these paths and are not independent observations.

| Original paired exit label | Trades | Median total external buys | Median total external sells | Median net external flow | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|
| Take profit | 10 | 10.0109 SOL | 1.3137 SOL | +6.8597 SOL | +$0.2399 | -$0.0517 |
| Stop loss | 4 | 2.7972 SOL | 7.1664 SOL | -3.2049 SOL | -$0.0451 | -$0.2364 |
| Time cap | 12 | 0.6718 SOL | 0.5130 SOL | +0.4101 SOL | -$0.0335 | -$0.0522 |

For adverse sell transactions (sell-containing transactions that lowered simulated spot price):

| Original paired exit label | Adverse sell transactions | Median sell / local virtual SOL reserve | Median spot move in sell transaction | Recovered pre-sell spot before exit |
|---|---:|---:|---:|---:|
| Take profit | 58 | 0.849% | -1.692% | 69.0% |
| Stop loss | 26 | 2.526% | -4.991% | 23.1% |
| Time cap | 69 | 0.121% | -0.241% | 60.9% |

Session split of sell recovery rates:
- Run15: take profit 71.4% (35 adverse sell transactions), stop loss 25.0% (12), time cap 77.8% (9).
- Run16: take profit 65.2% (23), stop loss 21.4% (14), time cap 58.3% (60).

### Interpretation

1. **Stop losses are predominantly sell-pressure deterioration.** Their adverse sell transactions are larger relative to local virtual SOL reserves, move spot further, and rarely recover before exit.
2. **Time caps are predominantly low-activity stalls in this sample.** Their adverse sells are small, and their median net flow is positive, but total external buying is far lower than the take-profit group. A lack of meaningful buy-side curve response is more consistent with these paths than a major sell shock.
3. **Take-profit paths combine stronger net buying with sell absorption.** They tolerate larger adverse sells than time-cap paths and more often recover to the pre-sell spot.
4. **A simple sell-count or sell-presence rule is unlikely to distinguish the groups.** The size of selling relative to reserves and the subsequent curve recovery matter more than the mere existence of a sell transaction.

These are retrospective descriptive differences. The exit labels are future outcomes, the transaction observations are nested within 26 mints, and the sessions have very different outcome mixes (Run15: 8 TP / 2 SL / 1 time cap; Run16: 2 TP / 2 SL / 11 time cap). No threshold or rule is approved.

### Reproducibility

The diagnostic implementation is being added in PR #38:
- `scripts/sniper_curve_path_diagnostics.py`
- `curve_path_trades.csv`
- `curve_path_transactions.csv`
- `curve_path_sell_recovery.csv`
- `curve_path_diagnostics.json` and `curve_path_diagnostics.md`

The reprocessing workflow is extended to generate these outputs for the wallet-lock-plus-flow-gate latency lanes. Each latency remains a paired sensitivity variant, not an independent sample.


### Latency-lane sensitivity (not pooled)

The same path reconstruction was also checked in the 1.0s and 1.5s wallet-lock-plus-flow-gate lanes. These lanes overlap the 500ms mint set and are paired timing variants, so the rows below are sensitivity checks only.

| Delay | Outcome label | Paths | Median external buy flow | Median external net flow | Median adverse sell pressure | Adverse sell recovery rate |
|---|---|---:|---:|---:|---:|---:|
| 0.5s | Take profit | 10 | 10.011 SOL | +6.860 SOL | 0.849% | 69.0% |
| 0.5s | Stop loss | 4 | 2.797 SOL | -3.205 SOL | 2.526% | 23.1% |
| 0.5s | Time cap | 12 | 0.672 SOL | +0.410 SOL | 0.121% | 60.9% |
| 1.0s | Take profit | 9 | 7.983 SOL | +6.711 SOL | 0.996% | 51.8% |
| 1.0s | Stop loss | 4 | 5.932 SOL | -4.890 SOL | 1.271% | 33.3% |
| 1.0s | Time cap | 12 | 0.627 SOL | +0.214 SOL | 0.273% | 73.8% |
| 1.5s | Take profit | 5 | 9.483 SOL | +6.768 SOL | 1.223% | 56.2% |
| 1.5s | Stop loss | 5 | 7.583 SOL | -2.611 SOL | 1.615% | 46.4% |
| 1.5s | Time cap | 7 | 0.408 SOL | +0.293 SOL | 0.545% | 71.4% |

The most consistent descriptive distinction is **buy-flow scale and net flow**, not a single recovery-rate cutoff: time-cap paths have much smaller external buying and low net flow at every delay. Stop-loss paths show the strongest sell pressure in each delay variant. Take-profit paths show the strongest median net buying.

The recovery rate itself is not monotonic by outcome: time-cap sell transactions often recover because the sell shocks are very small. This is why a recovery percentage alone would be a misleading strategy rule. The 1.5s sample is particularly small (17 paths) and must not be used to set a threshold.
