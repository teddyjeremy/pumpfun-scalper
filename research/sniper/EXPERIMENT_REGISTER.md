# Sniper Experiment Register

This register records research families and material findings. It is intentionally conservative: where a full artifact/run identifier or exhaustive test matrix is not present in the currently consolidated evidence, the entry says so rather than inventing it.

## Status key

- **Implemented:** code/report/replay exists.
- **Observed:** run or retrospective evidence exists.
- **Promising, unvalidated:** signal warrants further research but is not a rule.
- **Rejected / not promoted:** evidence is negative, inconsistent, compromised or too weak.
- **Infrastructure:** improves measurement or data integrity, not strategy performance.

## A. Sniper system and lane experiments

| PR | Experiment / change | Evidence or finding | Status / decision |
|---:|---|---|---|
| #9 | Sniper V1 baseline: first non-creator buy, subsequent transaction entry, one position, +20% TP / -12% SL / 30s cap | Establishes baseline and paper observation workflow; not a profitability claim | Implemented reference control |
| #10 | Remove $5 accounting-wallet kill switch | Fixed $1 notional continues through losses; wallet is metric only | Implemented operational correction |
| #11 | Independent per-mint shadow and timer exits | Captures signals independently of baseline busy state; timer quote age recorded | Implemented research lane |
| #12 | Multi-buyer signal transaction | Historical session showed multi-buyer cohort 42W/32L +$10.9350 vs single buyer 385W/556L -$72.7593 | Promising retrospective cohort; not execution validated |
| #13 | One-position multi-buyer portfolio | Two historical sessions combined 124 trades, 68W/56L +$16.9331; single buyer 1,751 trades, 713W/1,038L -$126.0368 | Promising historical lane; not promoted |
| #14 | Transaction-complete multi-buyer + positive net flow + no creator sell | Waits for a subsequent qualifying transaction; final transaction price and wallet cohort recorded | Implemented isolated lane |
| #15 | High-conviction three-transaction sequence | Requires signal, strong confirmation and later positive-flow execution transaction | Implemented research lane; later results weak |
| #16 | Feed latency and reserve snapshots | Captures callback/block timestamp latency and reserve progression | Infrastructure |
| #17 | Execution-quality ablation | Paired replay varies execution buy volume / buyer-count gates while holding signal logic constant | Implemented diagnostic; not a live fill model |
| #18 | High-conviction quality V3 | Retrospective Runs #6–#8 motivated <=10s launch age, one entry per mint and 25% TP; fresh validation required | Not promoted; Run16 V2/V3 each 2W/10L |
| #19 | First signal-wallet loss guard | Retrospective Runs #6–#9 subset looked positive; full sequential reallocation required | Implemented shadow; not sufficient proof |
| #20 | 0.5s / 1.0s / 1.5s wallet-guard latency lanes | Delayed later-slot reserve marks isolate latency | Infrastructure / counterfactual |
| #21 | Paired latency + post-signal flow gate | Compares no-lock, wallet-lock, wallet-lock + positive flow; P&Ls overlap | Implemented paired replay |
| #22 | Timer parity correction | Replay now matches runtime timer/candidate expiry semantics | Infrastructure / validation |
| #23 | Wallet-loss policy ablation | No lock, 1-loss, 2-loss, 5m and 10m cooldown variants across three delays | Implemented offline ablation; no policy promoted |
| #24 | Microstructure and execution-integrity research | First-five-second participation, breadth, reserve-normalized flow, momentum and signature/slot integrity | Implemented diagnostics |
| #25 | Curve-aware execution replay | Exact integer quote arithmetic, fee rounding, counterfactual reserve updates, immediate and next-slot models | Major execution realism upgrade |
| #26 | Exit-risk diagnostics | Sell transactions between entry and exit trigger; reserve-normalized sell sizes | Diagnostic only |
| #27 | Transaction atomicity correction | Run14 500ms next-slot result corrected from 21W/5L +$1.81 to 20W/6L +$1.33; one trade example +$0.04 to -$0.44 | Integrity fix |
| #28 | Canonical transaction-order reconstruction | Confirmed-block order repairs same-slot reserve discontinuities in affected windows | Infrastructure |
| #29 | Top-buyer share and buyer HHI | Adds concentration diagnostics to first-five-second microstructure report | Diagnostic only |
| #30 | Remove duplicate reconstruction step | Avoids repeated targeted Helius block requests | Workflow correction |
| #31–#33 | Reprocessing/module invocation fixes | Reprocessing and script invocation made reliable; see merged PR descriptions and workflow logs | Infrastructure |
| #34 | Signal-to-fill diagnostics | Maps decisions/triggers to selected fill signatures and measures timing/slot integrity | Diagnostic only |
| #35 | Persisted paired trade paths + curve-aware paired replay | Exact paired entry/exit signatures, trigger separated from fill, no second latency delay | Merged; research infrastructure |

## B. Named retrospective tests and strategy families

| Family | What was tested | What we learned | Decision |
|---|---|---|---|
| Baseline first-buy | First non-creator buy, subsequent entry, standard TP/SL/time cap | Run16 baseline 295 trades, 117W/178L, -$20.462716; 39.66% wins | Baseline is a control, not a leading candidate |
| Single vs multi-buyer | Distinct non-creator buyers in first signal transaction | Early historical cohort separated sharply; later execution realism and session variation still matter | Continue as feature, not standalone strategy |
| Multi-buyer one-position | Chronological one-open-position scheduling | Reduced trade count and remained positive in early retrospective sessions | Not production validated |
| Transaction-complete gate | Multi-buyer, positive net flow, no creator sell; enter subsequent qualifying transaction | More transaction context, but reserve marks alone not execution proof | Keep isolated |
| High-conviction V2/V3 | Signal + confirmation + execution sequence; V3 adds launch-age/repeat-mint restrictions | Run16 each 2W/10L; negative net | Do not promote |
| Wallet loss guard | Lock signal wallet after first losing trade | Retrospective subset looked favourable; Run16 wallet guard apparent +$4.262707 compromised by same-slot exits | Do not promote without integrity-correct replay |
| Wallet policy ablation | No lock, one loss, two losses, cooldowns | Built to isolate policy effects; results must be compared under matched latency and execution | No universal policy established |
| Positive execution-flow gate | At least one buy, positive net SOL flow, no creator sell | Run16 curve P&L improved vs wallet lock alone but all lanes remained negative; Run15 improvement small/inconsistent | Promising entry-timing adjustment, insufficient alone |
| 0.5/1.0/1.5s latency | Paired delayed entry/exit variants | Latency matters; lane outcomes vary by session and overlap | Keep as sensitivity analysis, never aggregate |
| Immediate vs next-slot curve quote | Exact curve quotes at immediate observation and later-slot boundary | Run16 all lanes negative; Run15 small samples positive | Use as execution sensitivity, not fill guarantee |
| Same-slot integrity | Compare signal, entry, trigger and exit signature slots | 12 same-slot Run16 wallet-guard exits generated ~88% of reported lane profit | Same-slot exit marks cannot support promotion |
| Time-cap path | Exact executable curve quotes before timer trigger and at actual delayed exit | Run16 only 2 of 13 unique time-cap mints ever positive before trigger; most losses predated exit delay | Shorter timer alone unlikely to solve it |
| Early post-entry continuation | Early flow, trader breadth and executable curve progression by eventual outcome | Preliminary pattern: TP cohort stronger participation; time-cap group often weak/stalled; PoNZI delayed recovery warns against simplistic early stop | Next research priority; no thresholds |

## C. Historical K/B research — separate from sniper

These experiments belong to the legacy K/B scalper and must not be reported as sniper results.

- Entry pullback filter: 5–20% below recent high; high age 3–120 seconds.
- Participation filter: steady flow, at least 15 new buyers in 30 seconds, unique trader ratio >=0.45, and 10-second buy USD >= sell USD.
- Multi-window recovery and selective candidate tape capture.
- Wallet-history cohorts with matured-only causal scoring.
- Sequential wallet-flow shadow replay.
- Exit comparisons including confirmed sell-surge and price-confirmed sell-surge.
- Scalp expansion and broader parameter tuning showed weak generalization: only about 48% of positive tuning-day combinations remained positive on combined test days; rulebook v2 deteriorated out-of-sample; original Lane B near-miss lost roughly $0.02–$0.065 per $1 trade on test days.

These K/B findings are included to prevent repeating adjacent research, not to imply transferability to the sniper.

## D. Run-level evidence currently consolidated

### Run16
Source observation run: `46116613167`  
Source artifact: `pumpfun-launch-sniper-16`

- 1,053 launches detected; 875 SOL-quoted launches.
- 1,025 launches with tape; 102,729 trade events; 91,641 transaction summaries; 870 unique mints in transaction-summary research.
- Baseline: 295 trades, 117W/178L, -$20.462716.
- Independent shadow: 742 trades, 294W/448L, -$46.331678.
- Exit reasons: 139 SL, 112 TP, 2 time_cap, 42 time_cap_timer.
- Multi-buyer signal: 46 trades, 22W/24L, +$3.261227.
- Multi-buyer one-position: 37 trades, 15W/22L, +$1.399543.
- Multi-buyer wallet guard: 24 trades, 19W/5L, +$4.262707 on original mark model, but materially compromised by same-slot exit marks.
- Transaction lane: 35 trades, 14W/21L, +$1.09489.
- High-conviction V2: 12 trades, 2W/10L, -$0.383149.
- High-conviction V3: 12 trades, 2W/10L, -$0.333365.
- No open positions at shutdown.

### Run15 and Run16 PR35 curve replay
- Run15 and Run16 reprocessed artifacts were compared at $1 stake, $71.69/SOL and $0.0285 round-trip gas.
- See RESEARCH_MASTER.md for the separate lane P&Ls.
- Run15 and Run16 diverged materially. Do not pool them into one headline performance number.

### Missing evidence to fill
The consolidated history available for this register does not include a complete artifact/run-ID index for every historical session, nor every individual ablation row from PR #17/#23/#24. Before citing those exact experiments quantitatively, attach the corresponding source run ID, artifact name, file path, script version/commit and sample count. Do not reconstruct missing values from memory.

## E. Required fields for every new experiment

Every future register entry must include:
1. Hypothesis and reason it is not a duplicate.
2. Source observation run ID, artifact name and commit SHA.
3. Data files and tape version (raw websocket vs canonical block-ordered).
4. Exact lane and selection rules.
5. Unique mint count, trade count and paired/overlapping variants.
6. Stake, SOL/USD, fees, gas, latency and fill model.
7. Wins/losses, net P&L, average win/loss, drawdown and loss streak.
8. Missing/unsupported replay count and exclusion reasons.
9. In-sample vs holdout designation.
10. Decision: reject, investigate, retain as control, or validate fresh.
