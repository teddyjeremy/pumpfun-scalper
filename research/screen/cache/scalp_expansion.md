# Pump.fun quick-flip scalp expansion — research notes (Mon Nov 23, 2026)

Research only: no trading, wallets, signing or keys. Code: `screen/scalp_setups.py` (setups, rug layer, exits, stats — reusable on
any full tape incl. the paper trader's via `from_paper_tape`), `screen/scalp_run.py` (grid), `screen/scalp_report.py` (Sun-tune /
Sat+Fri-test selection), `screen/scalp_rug.py` (rug study), `screen/scalp_fetch.py` (tape fetcher). Raw outputs:
`screen/cache/scalp_tapes/report.json`, `report_tables.md`, `rug_study.json`. Nothing was added to the frozen rulebooks.

## Bottom line

* **No setup is proven profitable out of sample under Jeremy's rules** ($5k–$10k entry cap, TP ≥ 1.5x, out by ≤120 s, +1 s fills,
  1.25% fee each way + $0.0285 gas). Across all 1,408 entry × exit combos, only 48% of the ones that were positive on Sunday stayed
  positive on Sat+Fri; the median dropped from +$0.023 to −$0.006 per $1. That is what "no real edge yet" looks like.
* **Volume is not the problem.** About 1,100–1,250 coins/day trade inside $5k–$10k between t+30 s and t+300 s, and **36–50% of them hit
  1.5x within 2 minutes** of their first band touch (median 15–23 s). The ceiling for 20–50 wins/day is far above target. The issue is
  picking them ahead of time: the losers (fee drag + fast dumps) cancel the 1.5x winners.
* **Best loss-controlled candidates** (chosen on Sunday only; tiny test samples, treat as *paper-test candidates*, not rules):
  1. **I+C "steady reclaim" + G fast-invalidation exit, TP 1.5x, max 120 s** — positive on all three backtest days
     (+$0.100 / +$0.136 / +$0.090 per $1), avg loss −$0.08 to −$0.21, worst −$0.31, but **only 9 / 5 / 4 sampled trades** and
     **negative on today's live tapes (−$0.145, 7 trades)**. Est. ~80–110 trades/day, ~45–65 wins/day if the sample extrapolates.
  2. **B breakout (L2 floors) + G exit, TP 1.7x, max 60 s** — smallest losses of anything (avg loss −$0.06 to −$0.11, worst −$0.19,
     none worse than −20%), +$0.053 Sun / +$0.203 Sat / −$0.007 Fri / −$0.062 live. Est. ~90–125 trades/day, ~25–50 wins/day.
  3. **K band-entry momentum** (the volume option) — ~400–450 est. trades/day and 130–190 wins/day, but expectancy is negative on
     the test days (−$0.04 / −$0.08) — it is basically the base rate minus costs.
* **Rug layer:** creator still holding > 8% of supply at entry is the only filter that cut rugs ≥ 3× more than winners on every
  day. The Sunday-chosen combo (creator > 8% OR create-slot bundle > 25% OR wash-like < 0.35 unique wallets per trade over 60 s)
  cut the rug rate 24%→18% (Sun), 34%→25% (Sat), 36%→30% (Fri), 23%→14% (live), but on Friday it cut 15 of 39 winners.

## Data and coverage (honest)

* The cached heavy-phase tapes were **not usable for scalping**: they are capped at 300 sampled txs/coin, cover only t+0–300 s, and
  stop fetching once a dip is found. Friday had only 4 coins with ≥100 priced trades.
* So I fetched **full, unsampled curve tapes t0−2 s…t0+480 s** with Helius `getTransactionsForAddress` (100 txs/page) at
  ≤2 calls/s through `lib/rpc.py` for a **stratified random sample of the active universe**. The universe = coins with Bitquery minute
  bars already cached (0 points spent), tiered by trades in the first 5 min. About 3,600 successful page calls in total (plus retries); 54 coins hit repeated 429s and were
  skipped. The pipeline was not touched; paper/ was only read.
* Sample: **Sun 206 coins, Sat 107, Fri 110**, out of ~2,750 / ~2,950 / ~2,800 active coins. Day counts are **weight-scaled estimates**
  (tier universe ÷ tapes fetched). Per-trade quality (win %, expectancy, losses) is the plain average of sampled trades. Fri's
  Bitquery pull used a higher floor (300 trades), so its lowest tier is under-represented.
* Extra check: **today's 241 live paper-trader tapes** (complete, but only kept for coins the paper trader flagged, so dip-biased;
  unweighted, no per-day projection).
* Fills: entry and exit at the curve spot of the first trade ≥ 1 s after the trigger (else the curve state at +1 s). This reproduces the
  paper trader's measured slippage. **Break-even multiple at $1 = 1.0547x** (fees + gas ≈ 5.5% before any slippage).

## Base rate (ceiling): $5k–$10k coins that reach 1.5x within 120 s

| Day | Band coins sampled | Est. band coins/day | First band entry hits 1.5x ≤120 s | Median time | Any band entry hits 1.5x | Same for 1.7x (first / any) |
|---|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 102 | 1249 | 36% (~409/day) | 23s | 54% | 26% / 43% |
| Sat 21 (test) | 44 | 1110 | 50% (~529/day) | 21s | 59% | 30% / 48% |
| Fri 20 (test) | 51 | 1158 | 43% (~506/day) | 15s | 55% | 37% / 49% |
| Mon 23 live tapes | 130 | n/a | 33% (43 coins) | 9s | 48% | 29% / 38% |

The "first band entry" is a +1 s fill the first time the coin trades at $5k–$10k (usually while pushing up through $5k). This is a
ceiling, not a strategy: it ignores the losers you would also have bought.

## Setup families tested (all: entry t+30…300 s, entry cap $5k–$10k, rug layer on, one trade per coin per setup)

| Family | Rule (exact, see `scalp_setups.py`) |
|---|---|
| A looser dip | spot ≥ 20/30% below a running high made after t+10 s (≤50% deep), floors L1 (cum vol ≥ $3k, ≥100 trades, ≥50 wallets) or L2 ($5k/150/80), ≥5 trades in last 15 s, v1.1 whale skip, not-crash guard |
| B breakout | spot breaks a running high ≥10 s old; 10 s buy $ ≥ 2× the prior-20 s avg per 10 s and ≥ $150 and ≥ 1.5× sell $ |
| C reclaim / higher low | dip ≥25/35% to low L that has held ≥10 s; buy the cross back above L + 50%·(H−L) with 10 s buys > sells, ≥4 trades |
| E confirmation | after a ≥20/30% dip: last 2–3 whole seconds each buy-dominated, price ≥3% off the low, still under the 50% retrace |
| H buy pressure | 10 s buy $ ≥ 1.5–2× sell $, ≥ $150, ≥4 brand-new buyer wallets in 10 s |
| I steady curve | filter on top of A/B/C/E/H/K: net buy inflow > 0 over both 30 s and 60 s, and no single buy > 35% of the 30 s buy $ |
| K band-entry momentum | (added) first moment in the $5k–$10k band with floors met, 10 s buy $ ≥ 1–2× sell $, not crashing |
| Not-crash guard (A/E/H/K) | skip if 10 s sell count ≥ 2× the prior 10 s while sell $ > buy $; require 10 s buy $ ≥ 60% of sell $ |
| Exits (all ≤120 s) | TP 1.5/1.6/1.7x × max hold 30/60/90/120 s with volume-fade; **G** = fade + exit on a new low under the dip low (≤60 s) or 10 s sells ≥ 2× buys; **Hx** = exit on first 10 s window with sell $ > buy $; stall (not +10% by 20 s); BE trail (+20%); **F** scale-out = half at 1.2/1.25x, half at 1.5/1.7x (extra sell gas charged) |
| J re-entry | a 2nd flip only after a winning 1st flip and a fresh signal; this almost never fires (0–1 per day in the sample), so it adds nothing |

Selection: picks were made on **Sun 22 only** (≥6 sampled trades, expectancy > 0, ≥10 est. wins/day, ≤20% of trades losing >20%),
ranked by a loss-adjusted score = expectancy ÷ |average loss| − share of trades losing >20%. Sat 21 and Fri 20 were then tested
unchanged. "Max DD (day)" = median max drawdown of a bootstrapped full day at $1/trade. "Loss streak" = median longest losing run.


### Balanced picks (chosen on Sun 22 only)


**I_C_recl25_L1 + tp1.6_h30_fade** (loss-adjusted score on Sun 1.34; pooled Sat+Fri: 9 trades, exp +0.113, win 56%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 9 | 79 | 44 | 35 | 56% | +0.108 | +8.6 | +0.259 | -0.081 | -0.17 | 22% / 0% / 0% | -0.49 | 5 | 31s | 67% (72s) | 0 |
| Sat 21 (test) | 5 | 108 | 65 | 43 | 60% | +0.119 | +12.8 | +0.337 | -0.208 | -0.34 | 20% / 20% / 0% | -1.23 | 4 | 31s | 40% (53s) | 0 |
| Fri 20 (test) | 4 | 103 | 51 | 51 | 50% | +0.106 | +10.9 | +0.391 | -0.179 | -0.18 | 50% / 0% / 0% | -1.14 | 6 | 26s | 25% (13s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 7 | n/a | n/a | n/a | 43% | -0.093 | n/a | +0.081 | -0.223 | -0.39 | 57% / 29% / 0% | -0.88 (seq.) | 2 | 31s | 14% (71s) | – |

**B_brk_L2 + G_tp1.7_h60_fade_inv** (loss-adjusted score on Sun 0.78; pooled Sat+Fri: 8 trades, exp +0.098, win 38%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 13 | 126 | 39 | 87 | 31% | +0.053 | +6.6 | +0.322 | -0.067 | -0.14 | 8% / 0% / 0% | -0.92 | 11 | 10s | 23% (27s) | 0 |
| Sat 21 (test) | 4 | 92 | 46 | 46 | 50% | +0.203 | +18.6 | +0.467 | -0.062 | -0.08 | 0% / 0% / 0% | -0.38 | 6 | 34s | 25% (10s) | 0 |
| Fri 20 (test) | 4 | 103 | 26 | 77 | 25% | -0.007 | -0.7 | +0.296 | -0.108 | -0.15 | 50% / 0% / 0% | -2.20 | 12 | 5s | 50% (78s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 11 | n/a | n/a | n/a | 18% | -0.062 | n/a | +0.058 | -0.089 | -0.17 | 36% / 0% / 0% | -0.77 (seq.) | 8 | 12s | 9% (32s) | – |

**B_brk_L1 + G_tp1.7_h60_fade_inv** (loss-adjusted score on Sun 0.65; pooled Sat+Fri: 8 trades, exp +0.098, win 38%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 13 | 126 | 48 | 77 | 38% | +0.056 | +7.0 | +0.282 | -0.086 | -0.19 | 15% / 0% / 0% | -0.97 | 8 | 13s | 23% (27s) | 0 |
| Sat 21 (test) | 4 | 92 | 46 | 46 | 50% | +0.203 | +18.6 | +0.467 | -0.062 | -0.08 | 0% / 0% / 0% | -0.38 | 6 | 34s | 25% (10s) | 0 |
| Fri 20 (test) | 4 | 103 | 26 | 77 | 25% | -0.007 | -0.7 | +0.296 | -0.108 | -0.15 | 50% / 0% / 0% | -2.20 | 12 | 5s | 50% (78s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 11 | n/a | n/a | n/a | 18% | -0.062 | n/a | +0.058 | -0.089 | -0.17 | 36% / 0% / 0% | -0.77 (seq.) | 8 | 12s | 9% (32s) | – |

### Volume pick (most est. wins x expectancy on Sun 22)


**I_C_recl25_L1 + tp1.5_h120_nofade** (loss-adjusted score on Sun 1.33; pooled Sat+Fri: 9 trades, exp -0.133, win 33%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 9 | 79 | 53 | 26 | 67% | +0.281 | +22.3 | +0.518 | -0.194 | -0.30 | 33% / 11% / 0% | -0.74 | 3 | 91s | 67% (72s) | 0 |
| Sat 21 (test) | 5 | 108 | 43 | 65 | 40% | -0.075 | -8.1 | +0.513 | -0.467 | -0.67 | 60% / 60% / 40% | -10.72 | 8 | 121s | 40% (53s) | 0 |
| Fri 20 (test) | 4 | 103 | 26 | 77 | 25% | -0.205 | -21.0 | +0.423 | -0.414 | -0.58 | 50% / 50% / 50% | -21.62 | 12 | 121s | 25% (13s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 7 | n/a | n/a | n/a | 29% | -0.093 | n/a | +0.281 | -0.243 | -0.60 | 43% / 43% / 14% | -1.22 (seq.) | 5 | 121s | 14% (71s) | – |

### Best of each family (chosen on Sun 22)


**[A] A_dip20_L1 + G_tp1.6_h90_fade_inv_stall20** (loss-adjusted score on Sun -9.00; pooled Sat+Fri: 36 trades, exp -0.034, win 25%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 36 | 327 | 91 | 236 | 28% | -0.042 | -13.8 | +0.160 | -0.120 | -0.31 | 36% / 14% / 0% | -14.32 | 15 | 7s | 22% (49s) | 3 |
| Sat 21 (test) | 16 | 352 | 88 | 264 | 25% | -0.006 | -2.1 | +0.352 | -0.125 | -0.23 | 50% / 12% / 0% | -5.93 | 17 | 11s | 50% (33s) | 0 |
| Fri 20 (test) | 20 | 477 | 119 | 358 | 25% | -0.056 | -26.7 | +0.223 | -0.149 | -0.52 | 40% / 25% / 5% | -27.34 | 17 | 4s | 10% (30s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 29 | n/a | n/a | n/a | 24% | -0.011 | n/a | +0.298 | -0.110 | -0.25 | 41% / 10% / 0% | -0.80 (seq.) | 6 | 15s | 28% (49s) | – |

**[B] B_brk_L2 + G_tp1.7_h60_fade_inv** (loss-adjusted score on Sun 0.78; pooled Sat+Fri: 8 trades, exp +0.098, win 38%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 13 | 126 | 39 | 87 | 31% | +0.053 | +6.6 | +0.322 | -0.067 | -0.14 | 8% / 0% / 0% | -0.92 | 11 | 10s | 23% (27s) | 0 |
| Sat 21 (test) | 4 | 92 | 46 | 46 | 50% | +0.203 | +18.6 | +0.467 | -0.062 | -0.08 | 0% / 0% / 0% | -0.38 | 6 | 34s | 25% (10s) | 0 |
| Fri 20 (test) | 4 | 103 | 26 | 77 | 25% | -0.007 | -0.7 | +0.296 | -0.108 | -0.15 | 50% / 0% / 0% | -2.20 | 12 | 5s | 50% (78s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 11 | n/a | n/a | n/a | 18% | -0.062 | n/a | +0.058 | -0.089 | -0.17 | 36% / 0% / 0% | -0.77 (seq.) | 8 | 12s | 9% (32s) | – |

**[C] C_recl35_L1 + G_tp1.7_h90_fade_inv_stall20** (loss-adjusted score on Sun 0.49; pooled Sat+Fri: 9 trades, exp -0.039, win 11%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 9 | 78 | 44 | 35 | 56% | +0.066 | +5.2 | +0.208 | -0.110 | -0.26 | 11% / 11% / 0% | -0.84 | 5 | 22s | 33% (67s) | 0 |
| Sat 21 (test) | 6 | 134 | 22 | 112 | 17% | -0.036 | -4.8 | +0.456 | -0.134 | -0.23 | 67% / 17% / 0% | -6.25 | 19 | 21s | 33% (40s) | 0 |
| Fri 20 (test) | 3 | 62 | 0 | 62 | 0% | -0.046 | -2.9 | +0.000 | -0.046 | -0.09 | 0% / 0% / 0% | -2.87 | 62 | 22s | 33% (48s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 9 | n/a | n/a | n/a | 22% | -0.024 | n/a | +0.430 | -0.153 | -0.33 | 56% / 22% / 0% | -0.77 (seq.) | 4 | 21s | 33% (71s) | – |

**[E] E_conf20_k3_L1 + G_tp1.7_h120_fade_inv_be20** (loss-adjusted score on Sun -9.00; pooled Sat+Fri: 26 trades, exp -0.164, win 8%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 26 | 241 | 65 | 176 | 27% | -0.085 | -20.5 | +0.165 | -0.177 | -0.74 | 54% / 27% / 4% | -20.92 | 14 | 13s | 19% (44s) | 2 |
| Sat 21 (test) | 10 | 209 | 42 | 167 | 20% | -0.131 | -27.3 | +0.170 | -0.206 | -0.40 | 70% / 40% / 0% | -27.78 | 18 | 17s | 20% (68s) | 0 |
| Fri 20 (test) | 16 | 345 | 0 | 345 | 0% | -0.185 | -64.0 | +0.000 | -0.185 | -0.34 | 81% / 44% / 0% | -64.01 | 345 | 10s | 12% (84s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 14 | n/a | n/a | n/a | 29% | -0.087 | n/a | +0.177 | -0.192 | -0.42 | 43% / 21% / 14% | -1.21 (seq.) | 8 | 16s | 21% (50s) | – |

**[F(exit)] I_C_recl25_L1 + F_half1.2_rest1.5_h120_inv** (loss-adjusted score on Sun 0.72; pooled Sat+Fri: 9 trades, exp +0.052, win 56%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 9 | 79 | 53 | 26 | 67% | +0.068 | +5.4 | +0.148 | -0.094 | -0.19 | 11% / 0% / 0% | -0.45 | 4 | 26s | 67% (72s) | 0 |
| Sat 21 (test) | 5 | 108 | 65 | 43 | 60% | +0.059 | +6.3 | +0.245 | -0.221 | -0.33 | 40% / 20% / 0% | -1.45 | 4 | 21s | 40% (53s) | 0 |
| Fri 20 (test) | 4 | 103 | 51 | 51 | 50% | +0.044 | +4.5 | +0.253 | -0.165 | -0.19 | 50% / 0% / 0% | -1.18 | 6 | 22s | 25% (13s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 7 | n/a | n/a | n/a | 14% | -0.158 | n/a | +0.058 | -0.194 | -0.29 | 71% / 43% / 0% | -1.16 (seq.) | 6 | 15s | 14% (71s) | – |

**[G(exit)] I_C_recl25_L1 + G_tp1.5_h120_fade_inv** (loss-adjusted score on Sun 1.34; pooled Sat+Fri: 9 trades, exp +0.116, win 56%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 9 | 79 | 44 | 35 | 56% | +0.100 | +8.0 | +0.240 | -0.075 | -0.17 | 11% / 0% / 0% | -0.44 | 5 | 26s | 67% (72s) | 0 |
| Sat 21 (test) | 5 | 108 | 65 | 43 | 60% | +0.136 | +14.7 | +0.364 | -0.206 | -0.31 | 40% / 20% / 0% | -1.18 | 4 | 21s | 40% (53s) | 0 |
| Fri 20 (test) | 4 | 103 | 51 | 51 | 50% | +0.090 | +9.2 | +0.331 | -0.151 | -0.18 | 50% / 0% / 0% | -0.95 | 6 | 22s | 25% (13s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 7 | n/a | n/a | n/a | 14% | -0.145 | n/a | +0.073 | -0.182 | -0.28 | 71% / 43% / 0% | -1.09 (seq.) | 6 | 15s | 14% (71s) | – |

**[H] H_press2_nb4_L2 + tp1.7_h120_stall20_fade** (loss-adjusted score on Sun 0.01; pooled Sat+Fri: 37 trades, exp -0.061, win 30%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 42 | 431 | 174 | 257 | 40% | +0.049 | +20.9 | +0.412 | -0.199 | -0.78 | 40% / 24% / 10% | -3.84 | 10 | 23s | 40% (61s) | 2 |
| Sat 21 (test) | 18 | 405 | 112 | 292 | 28% | -0.041 | -16.5 | +0.384 | -0.204 | -0.59 | 50% / 28% / 17% | -18.88 | 15 | 56s | 50% (47s) | 0 |
| Fri 20 (test) | 19 | 409 | 129 | 280 | 32% | -0.081 | -33.1 | +0.344 | -0.277 | -0.70 | 53% / 37% / 16% | -33.28 | 13 | 21s | 32% (77s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 36 | n/a | n/a | n/a | 28% | -0.023 | n/a | +0.354 | -0.168 | -0.60 | 39% / 22% / 6% | -2.70 (seq.) | 9 | 21s | 28% (60s) | – |

**[Hx(exit)] I_C_recl25_L1 + Hx_tp1.5_h60_flip10_inv** (loss-adjusted score on Sun 1.23; pooled Sat+Fri: 9 trades, exp +0.057, win 44%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 9 | 79 | 53 | 26 | 67% | +0.062 | +4.9 | +0.105 | -0.025 | -0.05 | 0% / 0% / 0% | -0.12 | 3 | 15s | 67% (72s) | 0 |
| Sat 21 (test) | 5 | 108 | 65 | 43 | 60% | +0.069 | +7.4 | +0.215 | -0.151 | -0.21 | 20% / 20% / 0% | -0.90 | 4 | 15s | 40% (53s) | 0 |
| Fri 20 (test) | 4 | 103 | 26 | 77 | 25% | +0.042 | +4.4 | +0.423 | -0.084 | -0.11 | 25% / 0% / 0% | -1.15 | 12 | 14s | 25% (13s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 7 | n/a | n/a | n/a | 0% | -0.138 | n/a | +0.000 | -0.138 | -0.28 | 71% / 14% / 0% | -0.96 (seq.) | 7 | 11s | 14% (71s) | – |

**[I] I_C_recl25_L1 + tp1.6_h30_fade** (loss-adjusted score on Sun 1.34; pooled Sat+Fri: 9 trades, exp +0.113, win 56%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 9 | 79 | 44 | 35 | 56% | +0.108 | +8.6 | +0.259 | -0.081 | -0.17 | 22% / 0% / 0% | -0.49 | 5 | 31s | 67% (72s) | 0 |
| Sat 21 (test) | 5 | 108 | 65 | 43 | 60% | +0.119 | +12.8 | +0.337 | -0.208 | -0.34 | 20% / 20% / 0% | -1.23 | 4 | 31s | 40% (53s) | 0 |
| Fri 20 (test) | 4 | 103 | 51 | 51 | 50% | +0.106 | +10.9 | +0.391 | -0.179 | -0.18 | 50% / 0% / 0% | -1.14 | 6 | 26s | 25% (13s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 7 | n/a | n/a | n/a | 43% | -0.093 | n/a | +0.081 | -0.223 | -0.39 | 57% / 29% / 0% | -0.88 (seq.) | 2 | 31s | 14% (71s) | – |

**[K] K_band_r2_L2 + tp1.7_h120_stall20_fade** (loss-adjusted score on Sun -0.01; pooled Sat+Fri: 38 trades, exp -0.060, win 32%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 43 | 445 | 186 | 259 | 42% | +0.045 | +20.0 | +0.390 | -0.203 | -0.78 | 42% / 23% / 9% | -4.12 | 10 | 23s | 40% (61s) | 2 |
| Sat 21 (test) | 19 | 421 | 133 | 288 | 32% | -0.040 | -16.8 | +0.325 | -0.208 | -0.59 | 47% / 26% / 16% | -19.50 | 13 | 47s | 47% (47s) | 0 |
| Fri 20 (test) | 19 | 409 | 129 | 280 | 32% | -0.080 | -32.9 | +0.344 | -0.276 | -0.70 | 53% / 37% / 16% | -33.07 | 13 | 21s | 32% (77s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 37 | n/a | n/a | n/a | 30% | -0.008 | n/a | +0.380 | -0.172 | -0.60 | 38% / 22% / 8% | -2.20 (seq.) | 7 | 22s | 27% (60s) | – |

### Hindsight: best on the test days (NOT a valid pick, shown to size the overfitting gap)


**C_recl25_L1 + tp1.7_h60_fade** (loss-adjusted score on Sun -0.09; pooled Sat+Fri: 14 trades, exp +0.099, win 43%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 12 | 105 | 53 | 53 | 50% | +0.016 | +1.6 | +0.222 | -0.191 | -0.44 | 17% / 17% / 17% | -2.45 | 6 | 38s | 42% (67s) | 0 |
| Sat 21 (test) | 9 | 192 | 107 | 86 | 56% | +0.187 | +36.0 | +0.475 | -0.173 | -0.29 | 33% / 11% / 0% | -1.15 | 6 | 54s | 44% (53s) | 0 |
| Fri 20 (test) | 5 | 115 | 23 | 92 | 20% | -0.061 | -7.0 | +0.239 | -0.136 | -0.25 | 40% / 20% / 0% | -7.23 | 15 | 27s | 20% (48s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 15 | n/a | n/a | n/a | 33% | -0.085 | n/a | +0.213 | -0.234 | -0.66 | 53% / 27% / 13% | -1.29 (seq.) | 3 | 40s | 20% (71s) | – |

**C_recl25_L1 + tp1.6_h60_fade** (loss-adjusted score on Sun -9.00; pooled Sat+Fri: 14 trades, exp +0.083, win 43%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 12 | 105 | 53 | 53 | 50% | -0.009 | -0.9 | +0.173 | -0.191 | -0.44 | 17% / 17% / 17% | -2.96 | 6 | 37s | 42% (67s) | 0 |
| Sat 21 (test) | 9 | 192 | 107 | 86 | 56% | +0.162 | +31.2 | +0.430 | -0.173 | -0.29 | 33% / 11% / 0% | -1.15 | 6 | 50s | 44% (53s) | 0 |
| Fri 20 (test) | 5 | 115 | 23 | 92 | 20% | -0.061 | -7.0 | +0.239 | -0.136 | -0.25 | 40% / 20% / 0% | -7.23 | 15 | 27s | 20% (48s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 15 | n/a | n/a | n/a | 33% | -0.089 | n/a | +0.201 | -0.234 | -0.66 | 53% / 27% / 13% | -1.35 (seq.) | 3 | 40s | 20% (71s) | – |

**C_recl25_L1 + tp1.5_h60_fade** (loss-adjusted score on Sun -9.00; pooled Sat+Fri: 14 trades, exp +0.079, win 43%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 12 | 105 | 53 | 53 | 50% | -0.007 | -0.8 | +0.176 | -0.191 | -0.44 | 17% / 17% / 17% | -2.95 | 6 | 37s | 42% (67s) | 0 |
| Sat 21 (test) | 9 | 192 | 107 | 86 | 56% | +0.157 | +30.3 | +0.421 | -0.173 | -0.29 | 33% / 11% / 0% | -1.15 | 6 | 50s | 44% (53s) | 0 |
| Fri 20 (test) | 5 | 115 | 23 | 92 | 20% | -0.061 | -7.0 | +0.239 | -0.136 | -0.25 | 40% / 20% / 0% | -7.23 | 15 | 27s | 20% (48s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 15 | n/a | n/a | n/a | 33% | -0.089 | n/a | +0.201 | -0.234 | -0.66 | 53% / 27% / 13% | -1.35 (seq.) | 3 | 40s | 20% (71s) | – |

**C_recl25_L1 + tp1.7_h120_fade** (loss-adjusted score on Sun -9.00; pooled Sat+Fri: 14 trades, exp +0.077, win 36%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 12 | 105 | 53 | 53 | 50% | -0.018 | -1.9 | +0.214 | -0.249 | -0.44 | 25% / 25% / 25% | -4.14 | 6 | 38s | 42% (67s) | 0 |
| Sat 21 (test) | 9 | 192 | 86 | 107 | 44% | +0.153 | +29.4 | +0.622 | -0.222 | -0.33 | 44% / 33% / 0% | -1.93 | 7 | 54s | 44% (53s) | 0 |
| Fri 20 (test) | 5 | 115 | 23 | 92 | 20% | -0.061 | -7.0 | +0.239 | -0.136 | -0.25 | 40% / 20% / 0% | -7.23 | 15 | 27s | 20% (48s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 15 | n/a | n/a | n/a | 33% | -0.087 | n/a | +0.211 | -0.236 | -0.66 | 53% / 27% / 13% | -1.31 (seq.) | 3 | 40s | 20% (71s) | – |

**C_recl25_L1 + tp1.7_h90_fade** (loss-adjusted score on Sun -9.00; pooled Sat+Fri: 14 trades, exp +0.070, win 36%)

| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 12 | 105 | 53 | 53 | 50% | -0.025 | -2.7 | +0.199 | -0.249 | -0.44 | 25% / 25% / 25% | -4.48 | 6 | 38s | 42% (67s) | 0 |
| Sat 21 (test) | 9 | 192 | 86 | 107 | 44% | +0.142 | +27.4 | +0.580 | -0.208 | -0.29 | 44% / 33% / 0% | -1.81 | 7 | 54s | 44% (53s) | 0 |
| Fri 20 (test) | 5 | 115 | 23 | 92 | 20% | -0.061 | -7.0 | +0.239 | -0.136 | -0.25 | 40% / 20% / 0% | -7.23 | 15 | 27s | 20% (48s) | 0 |
| Mon 23 live tapes (dip-biased, w=1) | 15 | n/a | n/a | n/a | 33% | -0.087 | n/a | +0.211 | -0.236 | -0.66 | 53% / 27% / 13% | -1.31 (seq.) | 3 | 40s | 20% (71s) | – |

## Rug / dump layer (entry-time info only, no lookahead)

Rug label (for measurement only): within 120 s of the fill the price fell to ≤0.4x and was still <0.5x at +120 s, or trading
died (<3 trades in +60…120 s) below 0.7x. "Winner" = net > 0 with exit `G_tp1.5_h120_fade_inv`. Entries = the unique first entries of
every family (no rug layer).

| Filter (flag = skip entry) | Sun 22 (tune) | Sat 21 (test) | Fri 20 (test) | Mon 23 live tapes |
|---|---:|---:|---:|---:|
| `top1_drop > 0.5` | rugs 6/81, wins 5/94 | rugs 5/48, wins 0/42 | rugs 1/63, wins 5/39 | rugs 9/70, wins 5/71 |
| `top1_drop > 0.35` | rugs 10/81, wins 6/94 | rugs 9/48, wins 1/42 | rugs 9/63, wins 10/39 | rugs 14/70, wins 9/71 |
| `bundle_drop > 0.3` | rugs 16/81, wins 14/94 | rugs 23/48, wins 12/42 | rugs 22/63, wins 14/39 | rugs 32/70, wins 24/71 |
| `bundle_drop > 0.5` | rugs 6/81, wins 3/94 | rugs 9/48, wins 6/42 | rugs 7/63, wins 4/39 | rugs 31/70, wins 18/71 |
| `bundle_hold > 0.15` | rugs 25/81, wins 11/94 | rugs 13/48, wins 7/42 | rugs 16/63, wins 14/39 | rugs 33/70, wins 11/71 |
| `bundle_hold > 0.25` | rugs 11/81, wins 5/94 | rugs 3/48, wins 1/42 | rugs 13/63, wins 12/39 | rugs 31/70, wins 7/71 |
| `creator_hold > 0.03` | rugs 16/81, wins 7/94 | rugs 28/48, wins 7/42 | rugs 5/63, wins 4/39 | rugs 39/70, wins 18/71 |
| `creator_hold > 0.08` | rugs 12/81, wins 3/94 | rugs 17/48, wins 2/42 | rugs 5/63, wins 1/39 | rugs 39/70, wins 18/71 |
| `creator_sold30 == True` | rugs 20/81, wins 18/94 | rugs 6/48, wins 10/42 | rugs 26/63, wins 17/39 | rugs 3/70, wins 4/71 |
| `top5_hold > 0.25` | rugs 1/81, wins 0/94 | rugs 1/48, wins 1/42 | rugs 9/63, wins 11/39 | rugs 25/70, wins 7/71 |
| `top5_hold > 0.35` | rugs 1/81, wins 0/94 | rugs 0/48, wins 0/42 | rugs 0/63, wins 2/39 | rugs 6/70, wins 1/71 |
| `sell_accel > 2.0` | rugs 0/81, wins 5/94 | rugs 6/48, wins 3/42 | rugs 0/63, wins 2/39 | rugs 8/70, wins 4/71 |
| `sell_accel > 3.0` | rugs 0/81, wins 2/94 | rugs 3/48, wins 0/42 | rugs 0/63, wins 1/39 | rugs 5/70, wins 2/71 |
| `drain15 > 0.1` | rugs 22/81, wins 21/94 | rugs 4/48, wins 12/42 | rugs 11/63, wins 6/39 | rugs 8/70, wins 9/71 |
| `drain15 > 0.2` | rugs 11/81, wins 13/94 | rugs 1/48, wins 3/42 | rugs 3/63, wins 4/39 | rugs 4/70, wins 6/71 |
| `uniq60 < 0.35` | rugs 9/81, wins 0/94 | rugs 0/48, wins 0/42 | rugs 7/63, wins 5/39 | rugs 1/70, wins 1/71 |
| `uniq60 < 0.25` | rugs 8/81, wins 0/94 | rugs 0/48, wins 0/42 | rugs 5/63, wins 5/39 | rugs 1/70, wins 1/71 |

Chosen on Sunday (cuts rugs ≥ ~3× more than winners there): `creator_hold > 0.08` OR `bundle_hold > 0.25` OR `uniq60 < 0.35`.
Combined effect:

| Day | Entries | Rug rate before | Rug rate after | Rugs cut | Winners cut | Exp. before | Exp. after |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 (tune) | 344 → 282 | 24% | 18% | 31/81 | 8/94 | -0.054 | -0.031 |
| Sat 21 (test) | 142 → 117 | 34% | 25% | 19/48 | 3/42 | -0.051 | -0.019 |
| Fri 20 (test) | 175 → 133 | 36% | 30% | 23/63 | 15/39 | -0.052 | -0.080 |
| Mon 23 live tapes | 306 → 215 | 23% | 14% | 40/70 | 20/71 | -0.066 | -0.047 |

Read-across: **creator still holding >8% of supply** is the only rule that held up on all four days (Sat 17 rugs vs 2 winners,
Fri 5 vs 1, live 29 vs 18). Bundle-holding >25% held up Sun/Sat/live but not Fri (13 vs 12). Wash-like trading (<0.35 unique wallets per
trade over 60 s) worked only on Sunday. As Jeremy guessed, sell acceleration, creator selling in the last 30 s and curve-SOL drain do
**not** separate rugs from winners (they fire on winners as often). Top-1 seller share (v1.1) barely separates them either.

## Caveats

* **Samples are tiny for the good-looking setups** (4–13 trades per day). The estimated trades/day and wins/day are extrapolations
  with wide error bars (one sampled trade in a top tier can stand for 10–40 coins). Treat every "positive" number here as a
  paper-test hypothesis.
* Overfitting check: 1,408 combinations were scored on 206 Sunday coins; the in-sample winners kept only about half their edge.
* On today's live tapes (dip-biased), every pick was negative, which is a warning sign.
* The +1 s fill model is realistic for the free feed. The paper trader showed p90 entry slippage of ~20% when late; that tail is in the
  tapes but not stress-tested beyond +1 s. At $1 the fixed gas makes TP < ~1.06x pointless; at ≥1.5x targets fees are not the binding cost,
  **fast dumps are**.
* Without the $5k–$10k cap (my first pass, Sunday only), random quick flips in active coins lost about −$0.08/$1 and no broad
  condition turned positive, so this is not just a side effect of the cap.
* Before relying on anything above: run picks 1–2 live in the paper trader (shadow mode) for 1–2 days and fetch a bigger sample
  (another 300–500 coins/day once the pipeline is idle).
