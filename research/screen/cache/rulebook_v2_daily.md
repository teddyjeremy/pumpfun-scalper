# Rulebook v2 "daily-volume" — research draft (Mon Nov 23, 2026, ~22:00 EAT)

Research only. No trading, wallets, signing or network calls; built only from cached tapes and paper logs.
Code + raw outputs: `screen/v2/` (`extract.py` candidates, `search.py` walk-forward search, `prereg.py` fixed rules,
`wallet.py` wallet Monte Carlo; `cands.pkl`, `search_results_cn300.json`, `wallet_results.json`).
Costs on every trade: 1.25% fee each way + $0.0285 gas round trip; fills at the first trade ≥1 s after the trigger.

## Verdict (plain)

**Nothing I tested makes money at 10–50 trades/day out of sample.** Hitting the volume is easy. The filters below fire on
an estimated 210–370 coins/day. The edge is the missing part. The best near-miss loses about **$0.02–$0.065 per $1 trade**
on every day (pooled −$0.041, n=98). Even with zero gas it is still −$0.012, so bigger size alone won't fix it.
**Status: paper-test only. Do not put money on Lane B.**

Big finding: **the cached scalp tapes have survivorship bias.** The tape sample only included coins that *later* reached
250–300+ trades (Bitquery stage A counts trades over a 3 h window). So any entry made before a coin's 300th trade "knows" it
won't die. Without a guard, the walk-forward search kept picking `cumulative trades ≤ ~190`. That looked great on the
backtest days (+$0.10/trade out of sample on Fri and Sun) and **failed on the live tapes (−$0.07 to −$0.08)**. All honest
numbers below require **≥300 cumulative trades at entry**. Only then is the sample selection known at entry time. This
probably also inflates the earlier `scalp_expansion.md` results: IC_reclaim with the guard is −$0.035 to −$0.110 per trade
on every day.

## Rule list

### Standing guards (Jeremy's hard rules, always on)
1. Entry only at **t+30 s < t ≤ t+300 s**, spot mcap **$5k–$10k**.
2. **≥300 cumulative trades** at entry (survivorship-safe; also a liveness floor).
3. **Not a crash:** skip if last-10 s sells ≥ 2× the prior 10 s while sells > buys, or if last-10 s buy $ < 60% of sell $.
4. **v1.1:** if price is ≥10% below the high, skip when the top-1 seller is >50% of sell $ since the high.
   **v1.3:** skip if the creator sold since the high.
5. **Rug:** skip if the creator still holds >8% of supply.

### Lane B, volume quick-flip (best near-miss "steady + fresh", NOT profitable yet)
6. Steady curve: net buy inflow over both the last 30 s and 60 s, no single buy >35% of the last-30 s buy $.
7. ≥15 new unique buyers in the last 30 s, and unique wallets / trades over 60 s ≥ 0.5 (anti-wash).
8. Exits (dynamic, no hard stop): **TP 1.5x**. **Invalidation** if price breaks the 30 s swing low within 60 s.
   **Sell surge** if, after 5 s, the 10 s sell count is ≥2× buys and ≥3. **Time cap 90 s.** (Median hold 20–31 s.)
9. At most 2 entries per coin; the second only after the first has exited.

### Lane A, quality dip (existing v1.1 → v1.3, unchanged)
* The strict v0 dip with v1.1/v1.2/v1.3 skips. Passers were **0 / 0 / 0 / 1 / 21** on Wed–Sun, so it can't supply 10–50/day.
  Sunday's 21 were positive (TP1.7 avg +$0.41), but that was in-sample and with a 5-min hold, not ≤120 s.

### Bankroll / no-wipeout layer (apply to any lane)
10. One open position at a time.
11. Stake ladder: $1 to start, +20% each time the wallet grows 20% above the last step (down again if it shrinks).
    Stake ≤ 35% of wallet (floor $1), hard cap $20 (curve liquidity at $5–10k mcap).
12. Daily loss cap: stop for the day at −25% of the start-of-day wallet. Daily trade cap: 50.
13. After 3 losses in a row, skip the next 5 signals.
14. Stop completely if the wallet is < $1.20 (can't place $1 safely).
15. Kill switch: if the rolling last 100 on-time paper trades average < $0, the lane stays off.

## Walk-forward search (in-sample vs out-of-sample), survivorship-safe (≥300 trades)

Greedy search over 29 causal features × 7 exits, 1–3 conditions, objective = mean − 1 SE on the train days. Per $1 trade:

| Rule size | Train → test | Train avg (n) | **Test avg (n)** | Live Mon (n) |
|---|---|---|---:|---:|
| 1 cond | Sat+Sun → Fri | +0.04 / −0.04 (10/28) | **−0.074 (11)** | −0.091 (42) |
| 1 cond | Fri+Sun → Sat | +0.01 / +0.06 (9/20) | **−0.017 (7)** | −0.115 (27) |
| 1 cond | Fri+Sat → Sun | +0.07 / +0.05 (9/17) | **−0.059 (43)** | −0.043 (33) |
| 1 cond | Fri → Sat (chronological) | +0.048 (10) | **−0.196 (5)** | +0.013 (6) |
| 1 cond | Fri+Sat+Sun → Mon live | +0.01 / −0.08 / +0.06 | **−0.116 (29)** | — |
| 2 cond | Sat+Sun → Fri | +0.13 / +0.05 | **−0.178 (7)** | −0.022 (14) |
| 2 cond | Fri+Sun → Sat | +0.15 / +0.08 | **+0.042 (6)** | −0.123 (23) |
| 2 cond | Fri+Sat → Sun | +0.09 / +0.18 | **−0.128 (31)** | −0.030 (32) |
| 2 cond | Fri → Sat | +0.158 | **−0.010 (15)** | +0.024 (21) |
| 2 cond | all → Mon live | +0.01 / +0.02 / +0.08 | **−0.102 (26)** | — |
| 3 cond | Sat+Sun → Fri | +0.30 / +0.07 | **−0.102 (7)** | +0.010 (10) |
| 3 cond | Fri+Sun → Sat | +0.14 / +0.11 | **−0.024 (5)** | −0.071 (14) |
| 3 cond | Fri+Sat → Sun | +0.17 / +0.16 | **−0.120 (24)** | −0.023 (25) |
| 3 cond | Fri → Sat | +0.210 | **−0.034 (10)** | +0.098 (11) |
| 3 cond | all → Mon live | −0.01 / +0.05 / +0.11 | **−0.102 (15)** | — |

In-sample +$0.01…+$0.30 → out-of-sample −$0.20…+$0.04. That gap is overfitting: no stable edge. The selected conditions
also change from fold to fold.

Fixed (pre-registered, not fitted) rules, per $1 trade, sampled n | avg, Fri | Sat | Sun | Mon-live:

| Rule (with guards 1–5) | Exit | Fri | Sat | Sun | Mon live |
|---|---|---|---|---|---|
| Band entry only | TP1.5, 60 s, inval | 36 −0.079 | 30 +0.028 | 89 −0.113 | 76 −0.032 |
| IC_reclaim | TP1.5, 90 s, inval | 5 −0.102 | 12 −0.072 | 18 −0.096 | 31 −0.020 |
| B_breakout | TP1.5, 60 s, inval | 1 +0.527 | 3 −0.175 | 3 −0.106 | 6 +0.081 |
| Inflow + fresh buyers + uptrend | TP1.3, 120 s | 5 −0.207 | 17 −0.065 | 32 −0.061 | 21 −0.009 |
| **Steady + fresh (Lane B)** | **TP1.5, 90 s, inval** | **12 −0.063** | **20 −0.020** | **35 −0.065** | **31 −0.018** |
| Calm dip 15–40% | TP1.5, 60 s | 18 −0.050 | 17 +0.004 | 53 −0.084 | 46 −0.074 |

## Lane B per-day results (steady + fresh, TP1.5 / 90 s / invalidation)

| Day | Sampled trades | Est. trades/day | Win % | Avg $/trade ($1) | Day $ at the 50/day cap | Worst trade | Best | Max DD (sampled, in order) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Fri Nov 20 | 12 | ~254 | 33% | −0.063 | −3.13 | −0.78 | +0.47 | −1.37 |
| Sat Nov 21 | 20 | ~371 | 45% | −0.020 | −1.01 | −0.81 | +0.60 | −1.28 |
| Sun Nov 22 | 35 | ~211 | 26% | −0.065 | −3.27 | −0.81 | +0.61 | −2.29 |
| Mon Nov 23 live tapes* | 31 | n/a | 32% | −0.018 | −0.55 (at 31) | −0.47 | +0.55 | −1.52 |

*Live tapes exist only for coins the paper trader flagged with a ≥30% dip; only entries after that flag are used, so
selection is known at entry time.

Exit mix: about 60% sell-surge / invalidation exits, about 20% TP hits. Losses are small on average but have fat tails.
Rug-style drains still reach −0.8 even with invalidation exits, because the +1 s fill lands after the drain.

## $2 wallet, sizing-ladder simulation (Monte Carlo, 3,000 runs, 10–50 trades/day, gas fixed per trade)

Wipeout = wallet below $1.20 (can't place the $1 minimum).

| Scenario | Horizon | Bankroll rules | Risk of wipeout | Median wallet | Median max DD | Median worst day |
|---|---|---|---:|---:|---:|---:|
| Lane B as measured (−$0.041/trade) | 7 d | off | 99.7% | $1.08 | −57% | −$0.94 |
| Lane B as measured | 7 d | on | 95.8% | $1.08 | −57% | −$0.70 |
| Lane B as measured | 30 d | on | 100% | $1.07 | −58% | −$0.70 |
| Hypothetical +$0.05/trade edge | 7 d | off | 40.2% | — | −58% | — |
| Hypothetical +$0.05/trade edge | 7 d | on | 34.5% | — | −51% | — |
| Hypothetical +$0.10/trade edge | 7 d | off | 12.2% | — | −42% | — |
| Hypothetical +$0.10/trade edge | 7 d | on | 10.4% | — | −37% | — |

The hypothetical rows shift the measured trade distribution. Their wallet growth is not realistic (liquidity, slippage),
so only the wipeout odds matter. Even with a real +5% edge, a **$2 wallet trading $1 has about a 1-in-3 chance of being
wiped out** in the first dozen trades: one −80% drain plus a few small losses ends it. The bankroll rules cut that
only a little (40% → 35%). The fix for the wipeout risk is structural. Either the first stake is a much smaller share of
the wallet, or a bigger start. Smaller stakes don't work here, because $0.0285 gas is already 2.85% of a $1 trade.

## What would settle it

* **Unbiased full tapes:** every coin that touches $5–10k mcap, from t0 to t+425 s, for 3–5 days (~1,000+ band coins/day).
  The paper trader's websocket already sees them, so dumping tapes for all band coins would cost no extra RPC credits.
  That is a code change to the running paper trader, which I did not make.
* **Sample size:** per-trade spread is about $0.31, so a +$0.05/trade edge needs ≥150 out-of-sample trades to show at
  2 SE, and ~400 to be confident. Today we have 98 survivorship-safe trades for the best rule.
* Until then: keep Lane B as shadow-only in the paper trader, and Lane A (v1.1–v1.3) as the only rule with positive
  (in-sample) history.

## Unbiased-sample test (started Mon Nov 23, 23:35 EAT) — INTERIM, in progress

Status at 23:55 EAT: fetching full tapes (t0..t+430 s) for an outcome-blind stratified sample of ALL launches on
Nov 16–22 (code `screen/v2/unbiased/`: `select.py` plan, `fetch.py` Helius gTFA, `fetch_pub.py` public RPC, `extract.py`,
`fast.py`). Strata per day: H = SOL coins whose first 5 min show ≥15 trades and ≥$4.5k peak mcap (necessary for any entry,
taken in full on Nov 18–22, sampled 400 on Nov 16–17); L = random 900 of the other SOL launches (incl. dead coins);
NS = random 350 non-SOL-quoted launches. Each coin is weighted by stratum size / coins fetched, so the results are
estimates for the whole population, including coins that died. Hard rules enforced in every variant: entry only
t+30 s…t+300 s, every exit ≤120 s, not-a-crash, v1.1, v1.3, creator ≤8%. Numbers will be appended here.
