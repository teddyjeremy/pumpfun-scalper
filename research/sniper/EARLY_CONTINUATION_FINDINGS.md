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
