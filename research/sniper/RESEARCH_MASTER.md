# Pump.fun Launch Sniper — Research Master

**Status:** Research / paper-only. No production profitability claim.  
**Last consolidated:** 2026-11-28  
**Repository:** `teddyjeremy/pumpfun-scalper`

## 1. Purpose and research discipline

The launch sniper is a separate research system from the legacy K/B scalper. Its purpose is to identify a small number of high-quality newly launched SOL-quoted Pump.fun tokens, enter only when the evidence supports continuation, and exit using assumptions that are as close as possible to actual curve execution.

Research decisions must be based on captured observations and reproducible replays. A reserve-derived mark, an optimistic immediate quote, a next-slot quote, and an actual on-chain fill are different things. Never describe a counterfactual replay as a live executable fill.

### Non-negotiable controls

- Keep baseline, shadow, multi-buyer, transaction-complete, high-conviction, wallet-guard, paired-latency and curve-replay lanes isolated.
- Do not add P&Ls from overlapping lanes or treat latency variants of the same mint as independent observations.
- Keep the K/B scalper's logic and results separate from the sniper.
- Preserve raw websocket tapes. Canonical block-ordered tapes are separate reconstructed research inputs.
- Use transaction signatures and mint identity together when joining data.
- Respect Solana transaction atomicity: no hypothetical fill between trade events in one signature.
- Distinguish signal, decision, trigger, observation, fill proxy and quote time.
- State stake, SOL/USD assumption, fees, gas, latency and fill model with every P&L comparison.
- Do not select thresholds on a dataset and then call the same dataset out-of-sample validation.
- A strategy improvement must be tested on a fresh observation with frozen rules.
- Paper-only research does not authorize signing, sending, or live transactions.

## 2. System history and architecture

### 2.1 Pre-sniper foundation

PRs #2–#8 built the Helius paper-observation and legacy K/B research foundation: Lane K, observer/tape capture, missed-opportunity diagnostics, exit comparisons, parallel entry-filter research, selective candidate tapes, causal wallet history and a sequential wallet-flow shadow replay. These are useful research infrastructure and historical context, but **their K/B P&Ls are not sniper P&Ls**. PR #1 was superseded by PR #2.

### 2.2 Sniper V1

PR #9 introduced a separate Pump.fun launch sniper paper workflow:
- Detect launches and observe their trade tapes.
- First non-creator buy is the signal.
- Entry uses a subsequent transaction, not a backdated fill in the signal transaction.
- One open baseline paper position at a time.
- SOL-quoted launches only in V1.
- $1 fixed paper stake; +20% take profit; -12% stop loss; 30-second maximum hold.
- Entry/exit slippage, per-side fees and round-trip gas are modelled.
- One-hour discovery plus ten-minute observation tail.

PR #10 removed the simulated $5 wallet kill switch. The observation should run to its scheduled end despite losses; $1 research notional remains fixed. The accounting wallet is a metric, not a funding constraint.

### 2.3 Research lanes added

PRs #11–#24 expanded isolated paper research:
- Independent per-mint shadow entries and timer exits.
- Multi-buyer signal cohort and one-position portfolio.
- Transaction-complete summaries and a separate one-position lane requiring multi-buyer positive net flow and no creator sell, with entry on a subsequent qualifying transaction.
- High-conviction three-transaction sequencing and conservative price marks.
- Feed callback/block timestamp latency and reserve snapshot capture.
- Paired execution-quality ablations.
- High-conviction quality shadow with launch-age and repeat-mint restrictions.
- First-buyer wallet loss guard.
- 0.5s, 1.0s and 1.5s latency-aware wallet-guard lanes.
- Paired latency replay with no-lock, wallet-lock, and wallet-lock-plus-positive-flow variants.
- Wallet-loss policy ablation.
- Microstructure and execution-integrity diagnostics.

These additions are research lanes and diagnostics. Their existence does not mean their rules were promoted into the baseline.

### 2.4 Execution and data-integrity improvements

PR #25 added curve-aware execution replay using integer Pump curve arithmetic, fee rounding and counterfactual reserve updates. It compares immediate-after-observation and next-later-slot assumptions and rejects unsupported/inconsistent cases.

PR #26 added exit-risk diagnostics.

PR #27 fixed transaction atomicity for later-slot fills. A documented Run #14 example changed from about +$0.04 to -$0.44 when a six-event exit transaction was correctly treated atomically; the corrected Run #14 500ms next-slot result changed from 21W/5L, +$1.81 to 20W/6L, +$1.33.

PR #28 reconstructs canonical transaction order from confirmed blocks for affected slots where websocket ordering caused reserve discontinuities. Raw tape remains unchanged.

PR #29 added top-buyer share and buyer HHI to first-five-second microstructure diagnostics.

PR #30 removed a duplicate canonical reconstruction workflow step.

PRs #31–#35 fixed module invocation/reprocessing and expanded signal-to-fill and paired latency diagnostics. PR #35 added persisted per-lane paths and the research-only curve-aware paired replay, preserving exact delayed entry/exit signatures and separating exit trigger from fill. PR #35 merged on 2026-11-28 (merge commit `14077feca20312775b1c7353d2ffb353f8996e0f`).

## 3. Baseline strategy and assumptions

The baseline is a reference strategy, not the current proven winner.

| Parameter | Baseline |
|---|---|
| Discovery | 60 minutes |
| Observation tail | 10 minutes |
| Eligible quote | SOL-quoted launches |
| Signal | First non-creator buy |
| Entry | Subsequent observed transaction, not signal transaction |
| Portfolio | One open baseline position |
| Stake | Fixed $1 |
| Take profit | +20% |
| Stop loss | -12% |
| Maximum hold | 30 seconds |
| Execution model | Reserve-derived paper marks with configured slippage/fees/gas |

Important: the configured -12% stop is not a guaranteed maximum loss. A stop can gap between observed transactions. Timer exits can use stale reserve marks in the baseline model. These are limitations to measure, not to hide.

## 4. Main findings to date

### 4.1 Baseline is not a production-ready strategy

Run16 baseline:
- 295 closed trades; 117 wins and 178 losses.
- Win rate 39.66%.
- Net paper P&L -$20.462716.
- Average approximately -$0.0694 per trade.
- Stop-loss exits accounted for -$41.9684; take-profit exits +$24.7743; timer exits approximately -$3.2686.
- Worst trade approximately -$0.6236.
- Median stop-loss market move was around -20.64%, worse than the configured -12% threshold.

This is evidence that the original baseline entry/exit model is not adequate. It is not a live trading result.

### 4.2 Multi-buyer signal was a promising early hypothesis, not a final answer

Historical Runs #1/#2 of the sniper research (PR #12/#13 evidence; original run identifiers and artifact references should be maintained in the experiment register when available):
- Session 1: multi-buyer 74 trades, 42W/32L, +$10.9350; single-buyer 941 trades, 385W/556L, -$72.7593.
- Session 2: multi-buyer 50 trades, 26W/24L, +$5.9981; single-buyer 810 trades, 328W/482L, -$53.2775.
- Combined: multi-buyer 124 trades, 68W/56L, +$16.9331; single-buyer 1,751 trades, 713W/1,038L, -$126.0368.
- One-position scheduling reduced selected trades while retaining positive historical paper P&L.

This was exploratory and helped motivate separate lanes. It was not an execution-adjusted proof of profitability. Later research showed the importance of latency, curve impact and integrity.

### 4.3 High-conviction filters did not establish a durable edge

- High-conviction V2 and V3 in Run16 each recorded 12 trades, 2 wins/10 losses; V2 -$0.383149 and V3 -$0.333365 in their reported paper lanes.
- The earlier V3 historical motivation (10-second launch-age limit, one entry per mint and 25% TP) was based on retrospective Runs #6–#8 and must not be mistaken for fresh out-of-sample validation.
- Do not promote the high-conviction family based on its earlier in-sample improvement.

### 4.4 Wallet guard results require execution-integrity scrutiny

Run16 multi-buyer wallet-guard lane: 24 trades, 19W/5L, +$4.262707 on the original mark model. However, 12 of 19 non-timer exit signatures were in the same Solana slot as the exit trigger; these 12 same-slot exits contributed +$3.747276. Five timer exits had no signature. This profit is not verified as executable under the later-slot constraints. Do not promote this result.

### 4.5 Positive-flow gate was inconsistent across sessions

The gate requires at least one buy, positive net SOL flow, and no creator sell. It can wait for a later qualifying transaction rather than entering on a sell-only transaction.

Run15 and Run16 PR35 curve-replay results ($1 stake, SOL/USD $71.69, $0.0285 round-trip gas):

| Session | Delay | Wallet lock | Wallet lock + flow gate |
|---|---:|---:|---:|
| Run15 | 0.5s | +$3.2210 | +$3.2189 |
| Run15 | 1.0s | +$2.5195 | +$2.5539 |
| Run15 | 1.5s | +$1.2269 | +$1.5017 |
| Run16 | 0.5s | -$0.9728 | -$0.7914 |
| Run16 | 1.0s | -$0.9451 | -$0.3886 |
| Run16 | 1.5s | -$0.7592 | -$0.4534 |

The flow gate improved all three Run16 comparisons, especially 1.0s, but all remained negative. Run15 changes were small at 0.5/1.0s and larger at 1.5s. This is not a stable cross-session edge. Lanes overlap and must not be summed.

### 4.6 Curve-aware replay changed what counts as a credible result

Run16 PR34 curve replay (before PR35 paired reprocessing) showed:
- 500ms: 16 trades, 3W/13L, immediate -$0.985615; next-slot -$0.865247.
- 1000ms: 16 trades, 2W/14L, immediate -$0.956836; next-slot -$0.769310.
- 1500ms: 14 trades, 2W/12L, immediate -$0.742958; next-slot -$0.757812.

Run15 next-slot results were positive in its smaller sample: 500ms 11 trades, 7W/4L, +$2.9402; 1000ms 11 trades, 8W/3L, +$2.9783; 1500ms 8 trades, 6W/2L, +$1.4094.

The opposite session outcomes show regime/sample sensitivity. These are counterfactual curve quotes, not guaranteed included fills.

### 4.7 Transaction integrity is material

- Same-slot exit marks can materially inflate apparent wallet-guard performance.
- Multi-event transactions must be treated atomically.
- Websocket event order is not canonical transaction order within a slot; PR #28 adds targeted confirmed-block reconstruction.
- Joins must use mint plus signature, not signature alone.
- PR #35 preserves exact selected entry/exit signatures and does not add a second latency delay.

### 4.8 Time-cap trades usually failed before exit latency became the main problem

Run16 flow-gated time-cap investigation:
- 29 lane records across 0.5/1.0/1.5s; only 13 unique mints.
- Only two unique mints (PoNZI and VOW) ever reached positive executable curve P&L before the time-cap trigger.
- The other 11 unique mints stayed negative through their observed entry-to-trigger paths.
- Mean trigger-to-actual-exit deterioration was about $0.0020 at 500ms, $0.0028 at 1s and $0.0059 at 1.5s.
- Exit wait median was 2.31s, 7.11s and 10.38s respectively.
- Thus exit latency is a secondary cost; most of the loss was already present before the timer triggered.
- Run15 had only one 500ms and one 1s time-cap record, too few for robust comparison.

This indicates weak entry/post-entry curve behaviour is a more important problem than simply shortening the timer.

### 4.9 Early post-entry participation is a promising research direction, not yet a validated rule

A preliminary Run15/Run16 500ms comparison suggested take-profit trades had stronger early net buying and broader participation than time-cap trades. The time-cap group could have mildly positive net flow but relatively little activity. This suggests entry confirmation is not enough; continuation after entry may matter.

However, the sample is small and outcome-labelled comparisons are descriptive. Do not turn the observed values into thresholds. Delayed recovery examples such as PoNZI warn against simplistic early-negative exits.

## 5. What has not been proven

- No strategy is yet demonstrated profitable across enough independent fresh sessions after realistic execution constraints.
- No current paper replay guarantees an on-chain fill or inclusion.
- Positive flow is not sufficient by itself.
- Multi-buyer count is not sufficient by itself.
- Wallet guard is not safe to promote from the original same-slot mark results.
- A timer change alone is unlikely to repair poor entries.
- The number of trades and P&L across overlapping latency lanes cannot be combined as independent samples.
- A positive session does not establish production readiness.

## 6. Current decision

**Keep the baseline and all research lanes unchanged. Do not promote a new entry or exit rule yet.**

The highest-value next work is:
1. Complete the experiment register with run IDs, artifacts, lane definitions, sample counts, execution model and evidence status.
2. Reconstruct all flow-gated paired trade paths from Run15/Run16 at the transaction boundary.
3. Compare post-entry curve progression across TP, SL and time-cap outcomes, while controlling for unique mint and paired signature.
4. Evaluate candidate early-continuation descriptors on one session and verify them on another; then freeze them for a fresh observation.
5. Investigate new launch/curve edges only after checking the existing research inventory to avoid duplicating old sweeps.

## 7. Promotion standard

A candidate is not eligible for promotion until it:
- Has a written hypothesis and predeclared metrics.
- Is tested on multiple independent sessions, including a fresh holdout.
- Uses the canonical ordered tape and exact curve math where supported.
- Reports missing/unsupported replay paths rather than silently dropping or labelling them losses.
- Accounts for latency, fees, gas, slippage and reserve impact.
- Shows positive net performance with acceptable drawdown and loss streak.
- Has enough independent mint observations to make the result meaningful.
- Is compared against the unchanged baseline and relevant control lane.
- Has a reproducible report and regression tests.

---
