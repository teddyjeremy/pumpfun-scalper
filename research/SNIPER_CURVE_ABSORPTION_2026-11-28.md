# Sniper Curve Absorption and Sell Recovery — Run 15 / Run 16

**Date:** 2026-11-28  
**Scope:** paper-only counterfactual curve replay; descriptive, not a strategy rule.  
**Primary lane:** 500ms wallet-lock + positive-flow gate.  
**Sensitivity:** 1.0s and 1.5s variants are shown separately and overlap the primary mint paths.

## 1. Question

The earlier post-entry work found three broad path outcomes: strong follow-through, weak continuation/stall, and adverse-flow deterioration. This pass asks a narrower question:

- When a sell transaction moves the bonding curve down, how large is that sell relative to the available virtual SOL reserve?
- Does the curve recover to its pre-sell spot before the paired exit fill?
- Are time-cap paths losing mainly because of heavy selling, or because new buying is too weak to generate continuation?

This is not another buyer-count, wallet-history, HHI or broad buy/sell-threshold sweep.

## 2. Source and integrity

- Run 15 source Actions run: `46055823769`; original artifact: `pumpfun-launch-sniper-15`. The observation workflow was marked failed, so treat it as an incomplete/failed run, not a successful complete observation.
- Run 16 source Actions run: `46116613167`; original artifact: `pumpfun-launch-sniper-16`.
- Run 15 PR35 reprocessing run: `46127205889`; downloaded artifact ID: `15255998352`.
- Run 16 PR35 reprocessing run: `46126041140`; downloaded artifact ID: `15255836622`.
- Both reprocessed reports use the canonical block-ordered tape and the paired curve replay assumptions: $1 stake, SOL/USD 71.69, $0.0285 round-trip gas.
- The 500ms flow-gated lane has 26 records: Run 15 has 11 and Run 16 has 15. A direct mint-address check confirms **26 distinct mints**, with no repeated mint within or across these two sessions.
- All 26 selected paths were reconstructed transaction by transaction. Final P&L matched the PR35 curve replay exactly for every path (maximum absolute difference $0.000000).
- Each quote is taken after a complete signature/transaction. Inner events from one Solana signature are never treated as separate fill opportunities.

## 3. Primary 500ms results

### Per-trade path statistics

| Original paired exit label | Trades / unique mints | Median external buys | Median external sells | Median net external flow | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|
| Take profit | 10 / 10 | 10.0109 SOL | 1.3137 SOL | +6.8597 SOL | +$0.2399 | -$0.0517 |
| Stop loss | 4 / 4 | 2.7972 SOL | 7.1664 SOL | -$3.2049 | -$0.0451 | -$0.2364 |
| Time cap | 12 / 12 | 0.6718 SOL | 0.5130 SOL | +$0.4101 SOL | -$0.0335 | -$0.0522 |

MFE and MAE are the maximum/minimum counterfactual executable sell P&L observed after complete external transactions along the selected entry-to-exit-fill path. They are not guaranteed realizable fills at those exact moments.

### Adverse sell transaction behaviour

An *adverse sell transaction* here means a complete transaction containing a sell that lowers the counterfactual curve's marginal spot price. Sell pressure is gross SOL sold divided by the virtual SOL reserve immediately before that transaction. Recovery means a later complete transaction restores spot to at least its pre-sell level before the selected exit fill.

| Exit label | Adverse sell transactions | Median sell pressure / local virtual SOL reserve | Median spot move during sell transaction | Recovered before exit |
|---|---:|---:|---:|---:|
| Take profit | 58 | 0.849% | -1.692% | 69.0% |
| Stop loss | 26 | 2.526% | -4.991% | 23.1% |
| Time cap | 69 | 0.121% | -0.241% | 60.9% |

These 153 transaction observations are nested within 26 mint paths. They are not 153 independent trades.

### Session split

| Session | Exit label | Adverse sell transactions | Recovery rate |
|---|---|---:|---:|
| Run 15 | Take profit | 35 | 71.4% |
| Run 15 | Stop loss | 12 | 25.0% |
| Run 15 | Time cap | 9 | 77.8% |
| Run 16 | Take profit | 23 | 65.2% |
| Run 16 | Stop loss | 14 | 21.4% |
| Run 16 | Time cap | 60 | 58.3% |

Run 15 had 8 TP / 2 SL / 1 time-cap paths; Run 16 had 2 TP / 2 SL / 11 time-cap paths. The outcome mix is markedly different.

## 4. Paired latency sensitivity

The 1.0s and 1.5s flow-gated variants are shown as sensitivity checks only. They overlap the same underlying mints and market paths; do not pool their records.

| Delay | Exit label | Paths | Median external buy flow | Median net external flow | Median adverse sell pressure | Adverse sell recovery rate |
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

The most consistent descriptive separation is the scale of buying and net flow: time-cap paths have much smaller external buy flow and net flow at all three delays. Stop-loss paths show the highest median sell pressure in each delay variant. The transaction-level recovery rate is not monotonic by outcome: time-cap sell transactions often recover because their sell shocks are small.

## 5. Interpretation

1. **Stop-loss paths show sell-pressure deterioration.** Their median adverse sell transaction is much larger relative to local virtual SOL reserves, causes a larger spot decline, and recovers less often before exit.
2. **Time-cap paths look more like low-activity stalls than sell avalanches.** Their median net flow remains positive, but the total buying is much smaller than in take-profit paths. The curve does not receive enough sustained buying to overcome the entry-cost deficit.
3. **Take-profit paths show strong net buying and some sell absorption.** They experience larger sell events than time-cap paths, but recover more often than stop-loss paths.
4. **Recovery percentage alone is not a useful rule.** Time-cap paths have a relatively high sell-recovery rate because their adverse sells are often tiny. Recovery must be interpreted alongside reserve-normalized sell size and buying that follows.
5. **Do not infer a causal predictor from this table.** The outcome groups are defined by future paired exit labels, and the path extends through the selected delayed exit fill. Some transactions are post-trigger. This is descriptive path anatomy, not an entry-time signal.

## 6. What this adds to prior research

The repository already tested broad sell-pressure diagnostics (PR #26), first-five-second flow and reserve-normalized activity (PR #24), early buyer concentration (PR #29), and entry-to-exit post-entry curve progression. This pass adds a narrower measurement: **whether each adverse sell transaction is absorbed by later complete transactions before exit**, measured against the exact pre-sell spot and local virtual SOL reserve.

It does not repeat the old broad sell-volume or buyer-count sweeps and does not establish a new threshold.

## 7. Next decision

- Keep all strategy rules unchanged.
- Retain curve absorption/recovery as a descriptive feature for the next independent observation.
- In the next fresh run, freeze the current entry rules and record the same transaction-boundary metrics.
- Evaluate the new run separately from Runs 15 and 16; do not tune a sell-pressure or recovery threshold on this 26-mint sample.
- Any future candidate must be computed before its decision time and validated on a fresh holdout, with delayed-recovery winners retained as counterexamples.

## 8. Reproducibility implementation

PR #38 adds `scripts/sniper_curve_path_diagnostics.py` and invokes it during manual artifact reprocessing. It writes per-trade, per-transaction and adverse-sell recovery CSVs plus JSON/Markdown summaries. Its final per-trade P&L must reconcile to the PR35 paired curve report.
