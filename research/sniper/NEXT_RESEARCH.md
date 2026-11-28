# Sniper Next Research Plan

**Priority:** Post-entry continuation and early curve deterioration  
**Status:** Research plan only. No strategy rules changed.

## Objective

Find whether information in the first seconds after a paired delayed entry can distinguish:
- continuation trades that eventually reach TP,
- sell-pressure deterioration that reaches SL,
- low-participation stalls that time out,
- delayed-recovery winners that initially look weak.

The goal is fewer, higher-quality trades, but no early exit or entry threshold will be adopted until independently validated.

## Step 0 — Freeze the baseline

- Keep the current baseline and all existing lanes unchanged.
- Record current main commit and source observation artifact IDs.
- Preserve Run15/Run16 raw and canonical tapes.
- Use the exact PR35 entry and exit signatures for each paired latency lane.
- Treat the three latency lanes as paired variants, not separate independent trades.

## Step 1 — Rebuild a canonical per-trade path table

For each unique mint and paired trade:
- source run, lane and delay;
- signal, decision, entry-fill, exit-trigger and exit-fill signatures;
- entry and exit slots, timestamps and event indices;
- initial virtual/real SOL and token reserves;
- counterfactual reserves after our $1 entry;
- observed external buys/sells applied in canonical transaction order;
- exact executable sell quote after each complete transaction;
- net P&L after fees and gas;
- quote age and time to exit.

Quality controls:
- no fills inside a transaction;
- use canonical block transaction order where available;
- reject or label reserve discontinuities;
- report unsupported/missing paths;
- confirm replay exit P&L matches PR35 for every included trade.

## Step 2 — Calculate early-path descriptors

At 0.5, 1, 2, 3, 5, 10, 15, 20 and 30 seconds after entry, where observations exist:
- executable P&L after full costs;
- maximum favorable excursion (MFE);
- maximum adverse excursion (MAE);
- first breakeven-crossing time;
- time spent above and below breakeven;
- drawdown from best executable quote;
- recovery from worst executable quote;
- quote persistence: one-off cross versus sustained positive path;
- virtual/real SOL and token reserve progression;
- external buy and sell SOL/token amounts;
- reserve-normalized buy/sell pressure;
- number of complete transactions and distinct traders;
- buy/sell event counts and transaction counts;
- creator sells and large sell transactions, with no assumption that absence implies safety.

Avoid broad re-running of buyer-count, wallet-history, HHI, generic buy/sell threshold or TP/SL grids already explored.

## Step 3 — Compare outcomes without leakage

Primary comparison:
- group by eventual TP, SL and time-cap outcomes;
- use unique mint as the independent unit;
- show per-session results separately;
- compare the same mint across latency variants without inflating sample size;
- report medians, distributions and individual paths, not only averages.

Specific questions:
1. Do eventual winners show sustained improvement before their eventual outcome is known?
2. Do time-cap trades show weak flow, low curve response, or simply a delayed response?
3. Can sell absorption distinguish a stall from a genuine reversal?
4. Are early negative paths predictive, or do they include delayed recoveries such as PoNZI?
5. Does entry delay change the path enough that one descriptor only works at one latency?

## Step 4 — Cross-session verification

- Derive any candidate descriptor on Run15 only and check it on Run16, then reverse the direction as a robustness check.
- Do not choose cutoffs by maximizing the combined Run15+Run16 result.
- With only two sessions and small time-cap samples, treat all cutoffs as exploratory.
- Identify the minimum additional fresh observation needed to test a frozen hypothesis.

## Step 5 — New edge discovery after existing-work audit

Only after Steps 1–4, investigate:
- launch-to-entry timing relative to curve state;
- curve response per SOL of external buy flow;
- sell absorption and recovery speed;
- remaining executable curve upside versus downside;
- transaction-sequence structure not captured by existing multi-buyer and positive-flow gates.

For each, first search the existing research scripts and reports. Do not repeat previously tested generic buyer-count, wallet cohort, HHI, pullback, buy/sell volume, or TP/SL sweeps.

## Step 6 — Candidate validation protocol

For any candidate:
1. Write the hypothesis and expected mechanism before running.
2. Specify one primary metric and guardrail metrics.
3. Freeze code and parameters.
4. Run on fresh independent observation data.
5. Reprocess with the same curve replay version.
6. Compare to unchanged baseline and matched controls.
7. Document both positive and negative outcomes.
8. Do not change rules mid-observation.

## Deliverables for the next research pass

- `early_continuation_paths.csv`: one row per unique mint/trade path and lane identifier.
- `early_continuation_checkpoints.csv`: one row per trade/checkpoint.
- `early_continuation_summary.md`: outcome and session comparisons.
- `early_continuation_integrity.json`: counts, missing paths, signature mappings, replay reconciliation.
- Updated experiment register with run IDs and artifact references.

## Decision after the next pass

Do not implement a rule merely because a feature separates TP and time-cap outcomes in this retrospective sample. First establish that it is available at decision time, not a consequence of the future exit label, and then validate it on a fresh observation.
