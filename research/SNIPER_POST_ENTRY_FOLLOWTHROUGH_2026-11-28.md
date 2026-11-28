# Sniper Post-Entry Follow-Through — Run 15 / Run 16

**Date:** 2026-11-28  
**Source:** paired curve-path diagnostic export, `early_path_tx.csv`  
**Primary comparison:** 500ms wallet-lock + positive-flow gate lane  
**Scope:** paper-only counterfactual curve quotes and observed transaction flow. Not live execution.

## 1. Why this analysis

The previous research showed that a positive-flow gate at entry was not enough to make every trade profitable. This analysis asks a narrower question:

> After a delayed entry is selected, does meaningful buying continue strongly enough to recover the initial curve-execution deficit?

The primary lane was chosen because it gives one consistent delay and one consistent wallet/flow policy. The other delay lanes overlap the same mints and are not independent samples.

## 2. Sample and outcomes

The 500ms lane contains 26 trade records across two sessions:

| Session | Take profit | Stop loss | Time cap | Total |
|---|---:|---:|---:|---:|
| Run 15 | 8 | 2 | 1 | 11 |
| Run 16 | 2 | 2 | 11 | 15 |
| **Combined** | **10** | **4** | **12** | **26** |

These are trade records, not 26 guaranteed unique mints. A mint can appear more than once after a prior position closes. The 500ms lane is separate from the 1s and 1.5s lanes.

The initial executable curve mark after entry was approximately **-$0.0532** per $1 position under the replay's fee and gas assumptions. This entry deficit is why merely observing positive net flow is not enough.

## 3. Early executable curve P&L

| Checkpoint | TP positive | SL positive | Time-cap positive |
|---|---:|---:|---:|
| 1 second | 3/10 | 0/4 | 1/12 |
| 2 seconds | 7/10 | 0/4 | 1/12 |
| 3 seconds | 8/10 | 0/4 | 1/12 |
| 5 seconds | 8/10 | 0/4 | 1/12 |
| 10 seconds | 8/10 | 0/4 | 1/12 |
| 15 seconds | 9/10 | 0/4 | 1/12 |

Median executable curve P&L at key early checkpoints:

| Outcome | 1s | 3s | 5s | 10s | 15s |
|---|---:|---:|---:|---:|---:|
| Take profit (n=10) | -$0.0219 | +$0.1200 | +$0.1707 | +$0.1707 | +$0.2254 |
| Stop loss (n=4) | -$0.1122 | -$0.1683 | -$0.1966 | -$0.1966 | -$0.1966 |
| Time cap (n=12) | -$0.0465 | -$0.0472 | -$0.0453 | -$0.0442 | -$0.0447 |

The take-profit cohort's median curve quote moves from below zero at one second to positive by two to three seconds. The time-cap cohort remains close to the initial entry deficit. Stop-loss trades deteriorate more severely.

These are outcome-group descriptions. They do not show that the exit outcome could have been known at entry.

## 4. Flow and participation in the first three seconds

| Outcome | Median net SOL flow | Median buys | Median sells | Median distinct traders |
|---|---:|---:|---:|---:|
| Take profit (n=10) | +4.36 SOL | 10 | 3.5 | 9 |
| Stop loss (n=4) | -2.54 SOL | 5.5 | 5.5 | 6 |
| Time cap (n=12) | +0.16 SOL | 5.5 | 0.5 | 5 |

The time-cap cohort's net flow is positive at the median, but very small compared with the take-profit cohort. This is an important result: the gate's Boolean condition (at least one buy, positive net SOL flow, no creator sell) can pass even when the magnitude and breadth of subsequent buying are weak.

The time-cap group also does not show high median sell counts in this 3-second window. Its problem is better described as weak follow-through / low participation than as an immediate sell avalanche.

## 5. Session split: why pooling is dangerous

Run 15 and Run 16 had very different outcome mixes.

| Session / outcome | n | Median 3s curve P&L | Median 3s net SOL flow | Median 3s distinct traders |
|---|---:|---:|---:|---:|
| Run 15 TP | 8 | +$0.1336 | +4.60 | 8 |
| Run 15 SL | 2 | -$0.1553 | -2.26 | 6 |
| Run 15 time cap | 1 | -$0.0472 | +0.16 | 4 |
| Run 16 TP | 2 | +$0.0595 | +2.55 | 22.5 |
| Run 16 SL | 2 | -$0.2457 | -5.29 | 12.5 |
| Run 16 time cap | 11 | -$0.0472 | +0.16 | 5 |

Run 15 contributed most of the take-profit sample; Run 16 contributed most of the time-cap sample. The data should therefore be read as two contrasting sessions, not as one stationary market population.

## 6. Early follow-through association with final result

Across the 26 records, Spearman rank correlations with final curve-path P&L were:

| First 3-second feature | Spearman correlation |
|---|---:|
| Executable curve P&L at 3s | 0.758 |
| Net SOL flow at 3s | 0.757 |
| Buy count at 3s | 0.443 |
| Distinct traders at 3s | 0.409 |
| Sell count at 3s | 0.026 |

The 3-second curve P&L and net SOL flow are strongly associated with the eventual result in this small sample. Buy count and distinct trader count show weaker associations. Sell count alone has almost no rank association here.

**This is not an out-of-sample prediction result.** The sample is only 26 records, groups are defined by eventual exit outcome, and the same underlying transaction flow drives several of these variables. The correlation is useful for deciding what to investigate, not for selecting a cutoff.

## 7. Full event-by-event executable curve path

I extended the checkpoint analysis by replaying the exact integer Pump curve state after every complete external transaction between the paired entry fill and selected exit fill. Events within one Solana signature were applied as one atomic transaction before taking a quote. The final calculated P&L matched the PR35 curve replay for all 26 records exactly (maximum absolute difference **$0.000000**).

| Outcome | Trades with any positive executable quote | Median share of transaction checkpoints positive | Median longest consecutive positive transaction run | Median best quote on path |
|---|---:|---:|---:|---:|
| Take profit | 10/10 | 64.5% | 11 transactions | +$0.2399 |
| Stop loss | 0/4 | 0% | 0 | -$0.0451 |
| Time cap | 1/12 | 0% | 0 | -$0.0335 |

Eleven of the twelve time-cap paths never became positive at any observed transaction checkpoint, including the delayed selected exit fill. The one exception was VOW: 20 of its 26 transaction checkpoints were positive and its best quote was +$0.0919. The best path among the other eleven time-cap trades was still negative at approximately -$0.0051.

All four stop-loss paths remained negative throughout their observed entry-to-exit paths. All ten take-profit paths reached positive executable quote territory, although some experienced substantial negative excursions first.

This event-by-event result is stronger than checking only the 3-second checkpoint, but it is still a small outcome-selected sample. It does not mean the bot could know the future exit category from the early path.

## 8. Critical counterexample: early red does not always mean a bad trade

Two of the ten eventual take-profit trades were still below executable breakeven at three seconds.

One Run 15 Gemini trade did not first cross executable breakeven until approximately **25.78 seconds**, yet it ultimately closed as a take-profit winner. The median first-positive time across take-profit trades was about **1.31 seconds**, but the range extended to approximately 25.78 seconds.

Therefore:
- A hard three-second stop would have discarded delayed winners.
- A single positive quote is not necessarily persistent momentum.
- A negative quote at three seconds is not enough evidence to reject a position.
- We need to study duration and persistence of recovery, not one checkpoint.

## 9. Current interpretation

The evidence supports three descriptive path types:

1. **Follow-through:** buying is sufficiently large and broad to overcome the initial curve-cost deficit; executable P&L improves.
2. **Weak continuation / stall:** net flow may remain slightly positive, but participation and buying magnitude are insufficient to overcome the entry deficit; P&L remains negative around the time-cap path.
3. **Adverse flow:** selling pressure and reserve movement push executable P&L materially lower.

The entry gate currently distinguishes positive-flow transactions from non-positive-flow transactions. It does not measure whether the market has enough post-entry demand to pay for the curve impact and costs.

## 10. Next tests — no threshold chosen yet

Use the already captured Run 15 and Run 16 tapes to deepen the measurement before collecting a new sample.

1. **Persistence:** calculate consecutive time spent below and above executable breakeven; record first crossing, last crossing, number of recrossings and longest positive run.
2. **Acceleration / fade:** measure net SOL flow and reserve changes over rolling 1s, 2s, 3s and 5s windows, comparing changes in flow rate rather than only cumulative flow.
3. **Curve efficiency:** compare net SOL flow with the executable curve P&L response. A large buy may produce less marginal benefit if the reserve/price path is already unfavourable.
4. **Breadth quality:** compare unique buyers, repeated buys by the same wallets, and whether participation is broadening or narrowing. Do not repeat the old broad wallet-intelligence sweeps.
5. **Counterexamples:** explicitly retain delayed winners such as the 25.78-second Gemini recovery. Any proposed early rejection rule must be tested against these.
6. **Session robustness:** report Run 15 and Run 16 separately, then use a fresh observation as a locked out-of-sample validation.
7. **No optimization yet:** do not select a 1s/2s/3s threshold from these 26 records. First establish whether a stable persistence/acceleration feature exists across sessions.

## 11. Decision

**No strategy rule changes.** The next research focus is post-entry demand persistence and curve efficiency. The flow gate remains a research candidate, not a production rule. All results remain paper-only and counterfactual.
