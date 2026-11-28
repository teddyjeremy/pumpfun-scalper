# Pump.fun Launch Sniper — Research Register

**Last updated:** 2026-11-28  
**Repository:** [teddyjeremy/pumpfun-scalper](https://github.com/teddyjeremy/pumpfun-scalper)  
**Research mode:** paper-only / offline replay. No production profitability has been established.

This document is the durable index of sniper experiments, observation runs, replay methods, engineering corrections, findings, and open questions. It deliberately separates the launch sniper from the legacy K/B quick-flip scalper. K/B rules and results are not sniper evidence.

## 1. Executive status

The sniper is still a research system, not a production-ready profitable strategy.

What we know with the strongest evidence:

1. The original first-non-creator-buy baseline is weak. In Run 16 it completed 295 trades, won 117, lost 178, and returned **-$20.462716** at the configured paper stake/cost assumptions.
2. Requiring multiple distinct non-creator buyers in the same signal transaction was a promising early cohort split. Across Runs 2 and 3, the exploratory multi-buyer cohort made **+$16.9331 over 124 trades**, while the single-buyer cohort lost **-$126.0368 over 1,751 trades**. These were broad counterfactual cohorts, not independently executed fills.
3. Later high-conviction, one-position and wallet-guard variants were promising in retrospective samples, but have not shown robust out-of-sample performance after realistic latency and curve execution.
4. Latency and transaction ordering materially change results. A same-slot or mid-transaction mark can create fictitious profits. We corrected those replay errors before trusting later results.
5. The positive post-signal flow gate improved Run 16 relative to wallet-lock-only in the curve replay, but the same improvement was not consistent in Run 15. It is a candidate for more validation, not a proven edge.
6. Exact curve replay shows that a large portion of baseline mark-to-market gains disappear once the hypothetical $1 buy, Pump fees, gas, reserve path, and delayed entry/exit are modelled.
7. Time-cap losses are mostly not profits lost solely to slow exits. In Run 16, most of the 13 unique flow-gated time-cap mints never had a positive executable curve quote at any observed point before the trigger.
8. Early post-entry follow-through is now a key research question: take-profit trades generally attracted materially more net buying and broader participation than time-cap trades, but the sample remains small and the observed features are not yet a validated filter.

**Current best research candidate:** multi-buyer signal + first-buyer wallet-loss guard + post-signal positive-flow gate, with delayed later-slot entry/exit. It is not promoted. It remained negative in Run 16 after exact curve replay and did not show a consistent incremental improvement in Run 15.

**Current live/paper strategy rules:** no new strategy rule has been promoted from this research. The original baseline and isolated shadow lanes remain separate. K/B remains untouched.

## 2. Baseline sniper definition

The first sniper implementation was added in PR #9 and is paper-only.

- 60-minute launch discovery plus a 10-minute observation tail.
- $1 fixed paper stake; $10 starting accounting wallet.
- Detect launch, ignore creator's initial buy, signal on the first non-creator buy, and enter only on a subsequent observed transaction (not retroactively on the signal print).
- One open baseline position at a time.
- Take profit: +20%.
- Stop loss: -12%.
- Maximum hold target: 30 seconds, including timer-driven exits when no further trade arrives.
- Paper costs and fill assumptions are recorded in the reports. A configured stop is not a guaranteed maximum loss because observed price can gap through it.
- PR #10 removed the wallet-balance stop so an observation continues for its full duration even after losses. This was important for collecting unbiased full-session data.

The sniper is distinct from the older K/B quick-flip scalper, whose separate rulebook is documented in `RULES.md`.

## 3. Observation run register

The following observation run numbers and statuses are verified from GitHub Actions. Earlier run IDs and exact per-run result tables are retained only where the linked PR research summaries provide them; do not infer missing numbers.

| Run | Actions run ID | Started (UTC) | Status | Notes |
|---:|---:|---|---|---|
| 2 | 45777883484 | 2026-11-26 07:46 | Success | Early baseline/multi-buyer evidence. |
| 3 | 45802306493 | 2026-11-26 10:32 | Success | Second multi-buyer cohort observation. |
| 6 | 45854523569 | 2026-11-26 15:59 | Success | Included in Runs 6–8 retrospective high-conviction quality analysis. |
| 7 | 45870046982 | 2026-11-26 17:42 | Success | Included in Runs 6–8 retrospective analysis. |
| 8 | 45944405900 | 2026-11-27 05:12 | Success | Included in Runs 6–8 retrospective analysis. |
| 9 | 45953649428 | 2026-11-27 06:43 | Success | Included in retrospective wallet-loss guard analysis. |
| 10 | 45970517353 | 2026-11-27 09:03 | Success | Captured for subsequent latency/wallet-guard replay work. |
| 11 | 45985327171 | 2026-11-27 10:49 | Success | Captured for subsequent latency/wallet-guard replay work. |
| 12 | 45997413929 | 2026-11-27 12:17 | Success | Used for timer-parity and latency replay validation. |
| 13 | 46014359297 | 2026-11-27 14:15 | Success | Used in the first curve-aware replay research. |
| 14 | 46038059133 | 2026-11-27 16:58 | Success | Revealed multi-event transaction-boundary and slot-order reconstruction issues. |
| 15 | 46055823769 | 2026-11-27 19:19 | Failure | Observation workflow ended unsuccessfully; its data was later reprocessed where available. Do not treat it as a successful complete observation. |
| 16 | 46116613167 | 2026-11-28 06:26 | Success | Latest complete observation in this register; 1,053 launches and 102,729 trade events. |

Runs 1, 4, and 5 are referenced by earlier research/PR descriptions, but their complete metadata and full run-level metrics have not yet been consolidated into this register. Run #5 was the stated source of the high-conviction three-transaction hypothesis. Add exact records only after retrieving their original artifacts.

## 4. Strategy and hypothesis experiments

### 4.1 Original first-buy baseline

**Test:** enter after the first non-creator buy and use the baseline one-position portfolio and +20% TP / -12% SL / 30-second hold.

**Run 16 result:**
- 295 closed trades.
- 117 wins / 178 losses.
- 39.66% win rate.
- Net P&L: **-$20.462716**.
- Average: approximately **-$0.0694 per trade**.
- Exit mix: 139 stop-loss, 112 take-profit, 2 ordinary time-cap, 42 timer exits.
- Stop-loss P&L: approximately -$41.9684.
- Take-profit P&L: approximately +$24.7743.
- Timer exits: approximately -$3.2686.
- Worst trade: approximately -$0.6236.
- Median observed stop-loss market move was about -20.64%, materially worse than the configured -12% threshold.

**Finding:** first non-creator buy alone is not a sufficient entry signal. The configured stop does not cap realized paper loss at -12%.

### 4.2 Multi-buyer signal transaction

**Test:** count distinct non-creator buyers within the same first-buy signal transaction signature; compare one buyer with at least two buyers.

**Runs 2 and 3 evidence:**

| Cohort | Trades | Wins | Losses | Net P&L |
|---|---:|---:|---:|---:|
| At least 2 distinct non-creator buyers | 124 | 68 | 56 | +$16.9331 |
| Single non-creator buyer | 1,751 | 713 | 1,038 | -$126.0368 |

Run 2 alone: multi-buyer 74 trades, 42W/32L, +$10.9350; single-buyer 941 trades, 385W/556L, -$72.7593.  
Run 3 alone: multi-buyer 50 trades, 26W/24L, +$5.9981; single-buyer 810 trades, 328W/482L, -$53.2775.

**Finding:** the signal transaction's buyer breadth was a strong exploratory discriminator in these two sessions. This is not proof of a live edge: the cohort replay is counterfactual and the observations are limited.

### 4.3 One-position multi-buyer portfolio

**Test:** apply chronological one-position capacity to the multi-buyer candidates rather than allowing independent per-mint shadow positions.

**Retrospective result:**
- Run 2: 57 selected trades, +$8.1361.
- Run 3: 44 selected trades, +$5.7025.

**Finding:** one-position capacity materially reduces the number of selected trades and aggregate profit, but preserves a positive result in those historical samples. It is a scheduling replay, not an execution guarantee.

### 4.4 Transaction-complete buyer/flow confirmation

**Test:** aggregate all decoded Pump trade events sharing one Solana transaction signature. Require at least two distinct non-creator buyers, positive net SOL buy flow, and no creator sell in the signal transaction. Enter only on a subsequent qualifying transaction using its final observed price.

**Finding:** this was introduced to avoid treating partial transaction events as complete signals. It is more faithful to atomic Solana transaction structure, but transaction-final reserve marks still are not guaranteed executable fills. No standalone robust profitability claim was established.

### 4.5 High-conviction three-transaction sequence

**Test:** positive-flow multi-buyer signal transaction, followed by a separate confirmation transaction (at least two buyers, at least 0.50 SOL buys, positive net flow, no creator sell, and at least 5% positive within-transaction price movement), followed by a third later positive-buy-flow transaction before entry. Conservative high/low event marks were used.

**Finding:** a more selective confirmation design was tested. It reduced the number of entries but still required execution-lag and curve-cost validation. It was based on Run #5 transaction-level findings and was kept separate from the baseline.

### 4.6 High-conviction V2/V3 and quality shadow

**Test:** compare high-conviction sequence variants, then add a V3 quality lane with:
- Maximum entry age of 10 seconds after launch.
- One entry per mint.
- 25% take profit.
- Existing 12% stop and 30-second maximum hold.

**Retrospective Runs 6–8 evidence used to motivate V3:**
- High-conviction entries later than 10 seconds: 9 trades, 3W/6L, about -$1.03.
- Five repeated high-conviction entries on already-traded mints were all losses.
- Paired replay with 10-second launch-age limit, one entry per mint, and 25% TP: 122 trades, about +$19.12, versus 130 trades and +$16.37 for the existing V2 lane.

**Run 16 validation warning:**
- High-conviction V2: 12 trades, 2W/10L, -$0.383149.
- High-conviction V3: 12 trades, 2W/10L, -$0.333365.

**Finding:** the Runs 6–8 retrospective result was not enough to establish an edge. Run 16's small fresh sample was negative for both V2 and V3. Do not promote V3 on the retrospective result.

### 4.7 First-loss signal-wallet guard

**Test:** for the one-position multi-buyer lane, identify the first buyer wallet in the signal transaction. After that wallet's first losing closed trade, block that wallet's subsequent signals for the rest of the session.

**Retrospective Runs 6–9 result used to motivate the shadow lane:**
- Retained 196 of 232 historical portfolio trades.
- 169 wins / 27 losses.
- +$49.65 versus +$47.64 for the original lane (180W/52L).

**Important limitation:** the initial result was a retrospective subset, not a complete sequential reallocation replay. It therefore needed a forward shadow lane and later latency testing.

### 4.8 Latency-aware wallet guard

**Test:** compare assumed action delays of 0.5s, 1.0s, and 1.5s. Require later-slot entry/exit marks; delay TP/SL/time-cap exits and avoid stale quote liquidation. The wallet-lock rule remained isolated.

**Finding:** results are sensitive to delay and to which transaction is selected as the fill mark. These are reserve-derived paper proxies, not actual transaction inclusion or executable fills. The lanes overlap and must never be added together.

### 4.9 Wallet-loss policy ablation

**Test:** offline paired replay of:
- No wallet lock.
- Lock after one loss.
- Lock after two losses.
- Five-minute cooldown.
- Ten-minute cooldown.

Each policy was compared at 0.5s, 1.0s and 1.5s delays.

**Finding:** policy effects must be compared on the same captured transaction stream and with sequential one-position state. A retrospective "remove losing wallets" subset can overstate the benefit. No wallet policy is promoted from this test alone.

### 4.10 Post-signal execution-flow gate

**Test:** compare wallet-lock-only with wallet-lock plus an execution transaction gate requiring at least one buy, positive net SOL flow, and no creator sell.

**Run 16 market-mark paired results:**

| Delay | No lock | Wallet lock | Wallet lock + flow gate |
|---|---:|---:|---:|
| 0.5s | -$1.575731 | -$1.431687 | -$1.225174 |
| 1.0s | -$1.472005 | -$1.404590 | -$0.803425 |
| 1.5s | -$1.163402 | -$1.163402 | -$0.744632 |

These are overlapping mark-based lanes, not independent samples.

**Finding:** the flow gate materially improved Run 16 relative to wallet-lock-only, especially at 1 second. It did not establish a positive strategy.

### 4.11 Feed latency and capture quality

**Test:** record websocket callback receive time and block-timestamp-to-callback latency, negative timestamp differences, and first/final virtual reserve snapshots.

**Finding:** feed and capture timing must be measured rather than assumed. Block timestamps have second-level resolution, so negative timestamp differences are not automatically proof of impossible ordering.

### 4.12 Execution-quality ablation

**Test:** hold the high-conviction signal and confirmation constant, compare flow-only with minimum execution buy-volume gates of 0.5 / 1.0 / 2.0 SOL and a 0.5 SOL + at least two execution buyers gate.

**Finding:** this isolates execution confirmation from signal selection. It is an offline paired comparison, not independent strategy samples. No gate was promoted without later latency/curve validation.

### 4.13 First-five-second microstructure and buyer concentration

**Tests:** first-five-second participation, buyer breadth, reserve-normalized flow, short-horizon price momentum, delayed later-slot forward marks, top-buyer share and buyer HHI.

**Finding:** the data now supports measuring whether participation is distributed or concentrated. This was diagnostic research only; no new threshold or entry decision was added. Missing forward observations remain missing rather than being called losses.

### 4.14 Exit-risk diagnostics

**Test:** measure sell transactions strictly between entry and the original exit-trigger transaction, normalize sell size by prior virtual SOL reserves, and record the first later-slot mark before the trigger.

**Run 16 context:** the stop-loss cohort accounted for approximately -$41.97, while take profits contributed approximately +$24.77. The configured -12% stop was frequently exceeded by the observed exit mark.

**Finding:** stop-loss trigger configuration is not a guaranteed realized loss bound. Exit trigger, delayed fill and curve liquidation need to be separated.

## 5. Execution integrity and curve-replay experiments

These engineering/replay tests are critical because they changed how earlier results must be interpreted.

### 5.1 Signal-to-fill diagnostics (PR #34)

Recorded decision, selected entry fill, exit trigger and selected exit fill signatures, plus timing/slot gaps and execution decay.

**Run 16 coverage:**
- 46 paired lane records across the tested lanes.
- 16 unique mints.
- 39 unique entry-decision signatures.
- 38 unique entry-fill signatures.
- 30 unique exit-trigger signatures.
- 30 unique exit-fill signatures.
- 12 mints were non-positive in every observed lane variant; 3 had mixed signs; 1 (VOW) was positive in every lane where it appeared.

**Finding:** decision-to-fill timing is material. A signal signature is not the entry fill, and an exit trigger is not the exit fill.

### 5.2 Exact Pump curve-aware execution replay (PR #25)

The curve replay models a hypothetical $1 buy and replays later observed buys/sells against counterfactual reserves using integer quote arithmetic, Pump exact-quote-in fee rounding, separate protocol/creator fee rounding, and the one-lamport buy adjustment.

Two assumptions were retained:
- Immediate-after-observation: optimistic lower bound.
- Next-later-slot: wait through all same-slot transactions, then fill after the first later-slot transaction; same treatment for exits.

It rejects missing signatures, same-slot decision exits, reserve discontinuities, formula mismatches, cashback modes and curve completion. Mayhem V3 support is included where the captured path remains on-curve.

**Finding:** this is materially more realistic than reserve-mark P&L, but it is still a counterfactual replay. It cannot guarantee on-chain inclusion, actual priority, slippage or transaction success.

### 5.3 Atomic transaction-boundary correction (PR #27)

**Problem:** a later-slot transaction can contain multiple Pump trade events. Filling after the first event would place a hypothetical order inside an atomic Solana transaction.

**Run 14 correction:**
- One Puds example's replay P&L changed from +$0.04 to -$0.44 when the final event in the six-event exit transaction was used.
- Corrected 500ms next-later-slot lane changed from 21W/5L, +$1.81 to 20W/6L, +$1.33.
- 1.0s and 1.5s summaries were unchanged in that observation.

**Finding:** transaction atomicity matters. Earlier optimistic replay results that used mid-transaction event fills must not be trusted.

### 5.4 Canonical slot-order reconstruction (PR #28)

**Problem:** websocket capture contains slot and signature but not transaction index within the slot. Reserve discontinuities made it impossible to reconstruct some intra-slot order safely.

**Test:** detect reserve discontinuities, fetch confirmed blocks for affected slots, decode Pump trade events and rebuild a separate canonical block-ordered tape. The original raw tape remains unchanged.

**Run 14 evidence:** 12 of 13 unique replay failures first showed a reserve discontinuity between different transactions in the same slot; one was cross-slot. This indicated missing canonical order, not necessarily missing trade events.

**Finding:** canonical transaction ordering is required before curve replay. The block-ordered tape is the replay source when reconstruction succeeds.

### 5.5 Paired latency and timer parity (PRs #21–22)

**Test:** paired replay of no wallet lock, wallet lock, and wallet lock + positive execution flow across 0.5s, 1.0s and 1.5s. Corrected timer handling to expire candidates before the next transaction and trigger max-hold independently of a same-mint trade.

**Finding:** replay must reproduce timer state transitions and delayed marks. A timer exit cannot depend on a new transaction arriving for that mint. The PR #22 parity fix was necessary before trusting paired comparisons.

### 5.6 Paired curve replay: Run 15 versus Run 16 (PR #35)

Run 15 and Run 16 were reprocessed with the canonical block-ordered tape and exact curve quote model. Assumptions: $1 stake, SOL/USD $71.69, $0.0285 round-trip gas.

**Curve-replay net P&L:**

| Session | Delay | Wallet lock only | Wallet lock + flow gate |
|---|---:|---:|---:|
| Run 15 | 0.5s | +$3.2210 | +$3.2189 |
| Run 15 | 1.0s | +$2.5195 | +$2.5539 |
| Run 15 | 1.5s | +$1.2269 | +$1.5017 |
| Run 16 | 0.5s | -$0.9728 | -$0.7914 |
| Run 16 | 1.0s | -$0.9451 | -$0.3886 |
| Run 16 | 1.5s | -$0.7592 | -$0.4534 |

Do not sum rows or columns: these are overlapping paired counterfactual lanes.

**Findings:**
- Run 15 was positive across these lanes, but net P&L was highly concentrated in the top three winners: about 79.6% at 0.5s, 95.9% at 1.0s, and 96.3% at 1.5s.
- Run 16 was negative in every lane.
- Flow gating barely changed Run 15 at 0.5s and 1.0s, and improved it more at 1.5s.
- Flow gating improved Run 16 more substantially, especially at 1.0s, but did not make it positive.
- One positive session and one negative session are insufficient to establish robustness.

### 5.7 Same-slot wallet-guard exit audit

Run 16 multi-buyer wallet-guard lane: 24 trades, 19W/5L, +$4.262707 in the original market-mark replay.

- All 24 entries were in the same Solana slot as the signal.
- Among 19 non-timer exits, 12 exit signatures were in the same slot as the exit trigger and 7 were later-slot.
- All 12 same-slot exits were winners, contributing +$3.747276.
- Five timer exits had no signature.

**Finding:** about 88% of nominal wallet-guard profit came from same-slot exits. These results are not verified executable and should not be promoted. Do not confuse timestamp equality with same-slot signature evidence.

## 6. Early post-entry path research (Runs 15–16)

Detailed follow-through report: [Run 15 / Run 16 post-entry analysis](SNIPER_POST_ENTRY_FOLLOWTHROUGH_2026-11-28.md). This includes checkpoint P&L, per-transaction exact curve quotes, persistence statistics and the delayed-winner counterexample.

A diagnostic CSV was generated for the flow-gated wallet-lock lane at 0.5s, 1.0s and 1.5s. It contains 68 lane records across the two sessions; these overlap across delays. The primary summary below uses the 500ms lane only.

**500ms lane, Run 15 + Run 16: 26 trade records**
- 10 take-profit trades.
- 4 stop-loss trades.
- 12 time-cap trades.

| Exit group | n | Median net SOL flow in first 3s | Median buys | Median sells | Median distinct traders | Median executable curve P&L quote at 3s |
|---|---:|---:|---:|---:|---:|---:|
| Take profit | 10 | +4.36 SOL | 10 | 3.5 | 9 | +$0.1200 |
| Stop loss | 4 | -2.54 SOL | 5.5 | 5.5 | 6 | -$0.1683 |
| Time cap | 12 | +0.16 SOL | 5.5 | 0.5 | 5 | -$0.0472 |

The immediate executable quote after entry was approximately -$0.0532 for each trade under this cost model. Take-profit trades generally recovered that initial deficit through stronger buying and broader participation. Time-cap trades generally did not, despite modest positive net flow.

**Interpretation:** the existing gate checks that a qualifying buy-flow transaction exists at entry. It does not ensure that meaningful buying participation continues after entry. A stall/weak-follow-through category may be important.

**Caution:** these are small groups (especially four stop-loss trades), selected by eventual exit outcome. The table is descriptive, not a causal or validated predictive filter. Do not use these medians as thresholds.

### Additional first-30-second follow-through findings (500ms flow-gated lane)

Using the 26 trade records in the 500ms wallet-lock + flow-gate lane across Runs 15 and 16:

| Horizon | Take-profit quotes positive | Stop-loss quotes positive | Time-cap quotes positive |
|---|---:|---:|---:|
| 1s | 3/10 | 0/4 | 1/12 |
| 2s | 7/10 | 0/4 | 1/12 |
| 3s | 8/10 | 0/4 | 1/12 |
| 5s | 8/10 | 0/4 | 1/12 |
| 15s | 9/10 | 0/4 | 1/12 |
| 30s | 10/10 | 0/4 | 1/12 |

For these same 26 records, Spearman correlation with final curve-replay P&L was approximately 0.76 for 3-second executable P&L and 0.76 for 3-second net SOL flow; 3-second buy count correlated about 0.44, distinct trader count about 0.41, and sell count about 0.03. This is exploratory and strongly affected by the small sample and session composition; it is not a validated predictor.

**Critical counterexample to a hard 3-second rule:** two eventual take-profit trades were still non-positive at 3 seconds. One Run 15 Gemini path did not first cross executable breakeven until about 25.78 seconds and ultimately finished positive. A hard 3-second loss/exit rule would have discarded a delayed winner. The data supports studying persistence and path shape, not applying a fixed early timeout.

Run-level composition was very different: Run 15's 500ms lane had 8 TP, 2 SL and 1 time-cap record; Run 16 had 2 TP, 2 SL and 11 time-cap records. This is another reason not to pool the two sessions into a single claimed win-rate or infer a universal threshold.

### Run 16 time-cap path (exact curve quote analysis)

Across the three flow-gated delay lanes:
- 29 time-cap lane records represented 13 unique mints (lane overlap).
- Only two unique mints ever reached positive executable curve P&L before the time-cap trigger: PoNZI and VOW.
- Eleven unique mints remained negative throughout their observed entry-to-trigger path.
- Best observed executable curve quote among those 11 was still negative (approximately -$0.0521 at the best edge; median best-path result about -$0.0378).
- At the 500ms lane, 1/11 was positive at trigger/exit; at 1.0s, 2/11; at 1.5s, 2/7.
- Trigger-to-fill delay deteriorated P&L modestly on average, but most losses were already present before the trigger.

PoNZI's best observed curve quote was about +$0.1278 around 25 seconds after entry; VOW's was about +$0.0764. These two examples show that delayed recovery and immediate follow-through can both occur. They are not enough to define a reliable rule.

**Finding:** shortening the time cap alone is unlikely to repair the main failure mode. The majority of these positions never reached positive executable curve value before the timer trigger.

## 7. Engineering/replay PR register

PRs #9–35 were merged into main. Their purpose and scope are summarised here.

| PR | Area | What it added or corrected |
|---:|---|---|
| 9 | Baseline sniper | Isolated paper-only launch sniper V1. |
| 10 | Observation integrity | Continue full session after losses; remove wallet-balance stop. |
| 11 | Shadow/timer | Independent per-mint shadow and timer-driven 30s exits. |
| 12 | Multi-buyer signal | Same-transaction distinct buyer cohort lane. |
| 13 | Portfolio | One-position chronological multi-buyer portfolio. |
| 14 | Transaction intelligence | Complete transaction summaries and positive-flow/no-creator-sell lane. |
| 15 | High conviction | Three-transaction confirmation/execution sequence. |
| 16 | Feed diagnostics | Callback latency and reserve snapshots. |
| 17 | Execution ablation | Paired execution buy-volume and buyer-count gates. |
| 18 | Quality shadow | 10s launch-age limit, one entry per mint, 25% TP. |
| 19 | Wallet guard | First-loss signal-wallet lock shadow. |
| 20 | Latency | 0.5/1.0/1.5s wallet-guard lanes. |
| 21 | Paired replay | No-lock vs wallet-lock vs positive-flow gate. |
| 22 | Replay correctness | Timer parity and candidate-expiry correction. |
| 23 | Wallet policies | No-lock, 1-loss, 2-loss, 5m and 10m cooldown ablation. |
| 24 | Microstructure | First-five-second participation, flow, forward marks and execution integrity. |
| 25 | Curve execution | Exact integer Pump curve quote replay. |
| 26 | Exit risk | Sell pressure between entry and exit trigger. |
| 27 | Atomicity | Use final event of a multi-event transaction for later-slot fills. |
| 28 | Canonical order | Confirmed-block transaction ordering for affected slots. |
| 29 | Buyer concentration | Top-buyer share and HHI diagnostics. |
| 30 | Workflow efficiency | Remove duplicate block reconstruction step. |
| 31 | Workflow reliability | Fix slot-reconstruction module invocation. |
| 32 | Reprocessing | Manually reprocess existing observation artifacts. |
| 33 | Workflow reliability | Fix curve-replay module invocation. |
| 34 | Fill diagnostics | Decision, entry fill, exit trigger and exit fill signatures. |
| 35 | Paired curve replay | Curve-aware paired wallet-lock and flow-gate lanes in artifact reprocessing. |

PR #35 was squash-merged at commit `14077feca20312775b1c7353d2ffb353f8996e0f` on 2026-11-28 08:15:55 UTC.

### PR #36 — paired time-cap path diagnostics (merged)

[PR #36](https://github.com/teddyjeremy/pumpfun-scalper/pull/36) adds observed transaction-price excursions before/after time-cap triggers. It is diagnostic only and explicitly treats extrema as market marks, not executable fills. It was squash-merged at commit `e7afa05c9525f54e67f865d2ca927f51b58ab1c7` after CI passed.

The first CI attempts exposed two issues:
- A floating-point assertion compared -9.999999999999998 to -10.0 using exact equality; the test now uses a tolerance.
- Post-trigger path selection originally used `slot >= trigger_slot`, which could include an earlier transaction in the same slot. The code now starts at the exact canonical trigger event index, and the regression fixture covers an earlier same-slot transaction.

Final PR #36 CI: **129 passed, 2 skipped** in both Python test and research replay workflows. Do not use observed extrema as executable P&L.

## 8. What we tested and should not repeat blindly

The legacy K/B research is separate, but it matters as a list of broad research families already explored in the repository. For the sniper, prior research has also covered the following families; do not launch another broad sweep without a specific unanswered question:

- First-buy versus multi-buyer signal cohort.
- Multi-buyer one-position scheduling.
- Transaction-complete buyer aggregation, net SOL flow and creator sells.
- Three-transaction high-conviction confirmation.
- Launch-age and repeat-mint limits.
- Execution buy-volume and execution-buyer count gates.
- Wallet first-loss lock and alternative cooldown policies.
- 0.5s / 1.0s / 1.5s latency variants.
- Positive post-signal flow gate.
- First-five-second buyer breadth, top-buyer share and HHI.
- Reserve-normalized buy/sell flow and short-horizon price movement.
- Sell pressure before exit trigger.
- Timer parity and stale timer mark handling.
- Same-slot entry/exit integrity.
- Multi-event transaction atomicity.
- Canonical intra-slot transaction ordering.
- Curve reserve continuity and exact quote math.
- Decision-to-fill and trigger-to-fill decay.
- Time-cap paths and early post-entry P&L/flow progression.

Do not conflate:
- K/B quick-flip scalper with launch sniper.
- Baseline with shadow lanes.
- Market-mark P&L with curve-quoted P&L.
- Immediate-after-observation with next-later-slot replay.
- Distinct mints with lane records.
- Overlapping latency lanes with independent samples.
- Signal transaction with actual entry-fill transaction.
- Exit trigger with actual selected exit-fill transaction.
- Observed intrapath price extrema with executable liquidation.

## 9. Open questions and next research plan

### Phase A — finish current diagnostics
1. Confirm PR #36 CI is green after the float-tolerance and same-slot-order fixes.
2. Review the generated Run 15/16 time-cap path report against the exact curve replay and ensure all lane paths map to the correct canonical signatures.
3. Keep observed price extrema separate from executable curve quote P&L.

### Phase B — investigate post-entry follow-through (next research priority)
The first comparison and event-by-event exact curve quote reconstruction are complete for the 500ms flow-gated lane. Final per-trade quotes match PR35 exactly. The next analysis is to calculate positive/negative quote persistence and flow acceleration from the same raw tapes, with trigger-to-fill periods separated from pre-trigger paths. Do not repeat the already completed horizon checkpoint or simple buyer-count/flow sweeps.

Use Run 15 and Run 16 tapes, not a fresh threshold sweep. Compare all flow-gated entries, including take-profit, stop-loss and time-cap outcomes, with unique mint/signature pairing and lane overlap controlled.

Measure:
- Executable curve P&L at 0.5, 1, 2, 3, 5, 10, 15, 20 and 30 seconds after actual delayed entry.
- Time below/above executable breakeven, first crossing and persistence (not just a one-tick crossing).
- Virtual and real SOL/token reserve progression.
- Buy and sell transaction counts, net SOL flow, trader breadth and event counts.
- Whether follow-through accelerates, fades, stalls or reverses.
- Creator sells and concentrated seller events only as explanatory context, not another broad wallet sweep.
- Maximum adverse/favorable executable quote excursions, with observed price marks labelled separately.

Key question: can early persistence of executable curve recovery distinguish winners from stalled entries without excluding delayed-recovery winners such as PoNZI?

Do not set a new threshold on the present sample. Any candidate must be specified before testing on a fresh observation.

### Phase C — out-of-sample validation
After the diagnostic is validated and merged:
1. Manually run a fresh full-duration paper observation using the current main branch.
2. Reprocess it with the same PR35 exact curve replay and the diagnostic reports.
3. Freeze candidate rules before looking at the new run's outcomes.
4. Compare baseline, wallet-lock-only and wallet-lock-plus-flow-gate as separate lanes.
5. Require repeatability across more than one independent session and inspect profit concentration, drawdown, worst trade, loss streak, entry count and execution assumptions.
6. Keep all trading disabled until a lane demonstrates a robust positive net result after curve fees, gas, latency and conservative fill selection.

### Phase D — only then consider strategy changes
No new TP/SL, timer, buyer-count, wallet-loss or flow threshold should be promoted yet. The next work is measurement and validation, not parameter optimisation.

## 10. Current readiness decision

| Area | Status |
|---|---|
| Paper observation and raw tape capture | Operational in successful Runs 6–16 |
| Independent shadow lanes | Implemented |
| Latency paired replay | Implemented; timer parity corrected |
| Transaction ordering | Canonical reconstruction implemented |
| Curve quote replay | Implemented; exact integer Pump quote model |
| Signal-to-fill diagnostics | Implemented |
| Time-cap path diagnostic | PR #36 fixes pushed; CI confirmation pending |
| Robust positive sniper edge | **Not established** |
| Production/live readiness | **Not approved** |

**Bottom line:** we have built a research-capable sniper and a much better execution audit, but we have not yet found a robust production edge. The next step is to finish PR #36, deepen the post-entry follow-through analysis, and validate any candidate on a fresh out-of-sample observation. Keep paper mode only.
