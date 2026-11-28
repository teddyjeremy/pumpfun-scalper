# Sniper Strategy Status

**As of:** 2026-11-28  
**Scope:** Pump.fun launch sniper only. K/B scalper is separate.

## Current status

<em>There is no strategy currently proven production-ready.</em> Keep the existing baseline as a reference control and keep all research variants isolated.

## Strategy / lane disposition

| Strategy or lane | Evidence | Current disposition |
|---|---|---|
| Baseline first non-creator buy | Run16 295 trades, 117W/178L, -$20.462716 | Retain as control; do not promote |
| Independent broad shadow | Run16 742 trades, 294W/448L, -$46.331678 | Diagnostic baseline only |
| Multi-buyer signal | Strong early retrospective cohort; execution realism not established | Retain as research feature |
| Multi-buyer one-position | Early two-session retrospective positive | Continue only as isolated comparator |
| Transaction-complete flow lane | Better transaction aggregation; mark-based results not enough | Research only |
| High-conviction V2 | Run16 2W/10L, -$0.383149 | Not promoted |
| High-conviction V3 | Run16 2W/10L, -$0.333365 | Not promoted |
| Wallet-loss guard | Run16 +$4.262707 original marks; same-slot exit integrity problem | Do not promote |
| Wallet lock + positive-flow gate | Improved Run16 vs lock alone, but every curve lane still negative; Run15 uplift inconsistent | Promising ablation only |
| 0.5/1.0/1.5s latency lanes | Different results by session; overlapping trades | Sensitivity analysis only |
| Curve-aware replay | More realistic execution-cost and reserve-impact analysis; not guaranteed inclusion | Required research evaluation standard |
| Early continuation / post-entry flow | Descriptive outcome separation in small sample | Highest-priority investigation; no rule yet |

## Leading research hypotheses (not approved rules)

1. **Post-entry continuation quality:** after the entry gate passes, sustained buying participation and curve progression may distinguish eventual TP trades from stalls.
2. **Entry timing within the signal sequence:** waiting for a qualifying later transaction may avoid entries during sell-only or weak transactions.
3. **Curve absorption capacity:** the amount of buy flow needed to move the curve, and the ability to absorb sells without losing price, may be more informative than raw buyer counts.
4. **Launch maturity and remaining curve path:** the same nominal buy signal may have different payoff depending on where the curve is and how much executable upside remains.

Each hypothesis must be checked against prior research and tested on independent sessions before any threshold is proposed.

## Do not promote based on

- Positive mark-to-market P&L without curve execution costs.
- Same-slot exit marks.
- One profitable session.
- A tiny number of unique mints.
- Overlapping latency lanes treated as independent trades.
- A threshold selected and evaluated on the same sample.
- An early positive quote that is not persistent.
- A replay that silently drops unsupported transactions.

## Candidate promotion gate

A candidate must pass all of the following:
- Pre-registered hypothesis and metrics.
- Reproducible transaction-boundary replay on canonical ordered tape.
- Exact curve math, fees, gas and stated SOL/USD assumption.
- Separate immediate and next-slot assumptions where supported.
- Clear unique mint and signature counts; paired variants not double-counted.
- Fresh holdout observation with frozen rules.
- Positive net after modeled costs in multiple independent sessions.
- Acceptable max drawdown, worst trade and maximum losing streak.
- Comparison against unchanged baseline and relevant control.
- Regression tests and documentation.

Passing this gate permits further paper validation, not automatic live deployment.
