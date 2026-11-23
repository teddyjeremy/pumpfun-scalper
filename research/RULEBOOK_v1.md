# Pump.fun first-30-minute 2x scalp — draft rulebook v1

Study scope: 10 Pump.fun coins, first 30 minutes of life only, $1 buy simulations, 1.25% fee each way plus ~$0.0285 round-trip gas. Net is profit on a $1 stake (so a clean 2x is about +$0.93 to +$1.05 after fees, not +$1.00). All times Africa/Nairobi (UTC+3).

This v1 is scored on the **first volume-live touch** of each dip threshold, not the first print that merely crosses the percentage. Official first-touch figures (first print at threshold, even if volume is dead, then requiring volume-active for eligibility) are cited only where they differ.

## Definitions (checkable)

- **Running local high:** walk the trade tape in analyze order `(block_time, slot, create-tx first, signature)`. Peak updates on a new high in `price_sol` (quote units on custom-quote coins; PnL is always in USD).
- **Touch of T%:** first print whose price is ≥ T% below the current running local high (T in {25, 30, 35, 40}). After a touch is recorded for T, later deeper prints do not replace it for that T.
- **Volume-active (VA / live):** ≥ 8 trades AND ≥ $200 USD notional in the prior 15 seconds inclusive of the entry print.
- **First volume-live touch:** keep walking after a dead-volume print until the first touch of T that is volume-active. If the official first touch was already live, they coincide.
- **Opening-dump window:** 0 ≤ t+ ≤ 30 seconds (t+ = 30 s counts as opening only).
- **Mid-tape window:** 30 s < t+ ≤ 300 s.
- **Late:** first live touch at t+ > 300 s. Not a v1 entry.
- **Time stop:** last trade at or before entry + 300 s, capped at the 30-minute tape end.
- **Take-profit multiple:** exit at the first later print that is 1.7x / 1.8x / 2.0x the entry USD price, before the time stop.

**Sample caveats (apply to every evidence count below):** n = 10, not a random draw; mid-tape cells are n = 1–3; fills, slippage, and latency are not modelled, so opening-second prints are optimistic; snowball-v2 PumpSwap tape inside 30 minutes was time-stratified (~11.5% of ok signatures) and understates volume; two coins used a custom quote (Private Inu / VVV, Mihaomas / mihao) with USD inferred from same-tx hops; some pre-create SOL snapshots are missing after Helius 429s.

---

## R1 — Entry trigger

**Preferred entry (v1 setup).** Buy the first volume-live touch of a **30%** dip from the running local high if, and only if:

1. the touch is mid-tape (30 s < t+ ≤ 300 s), and
2. volume-active is true on that print.

A **35%** or **40%** first volume-live mid-tape touch is the same setup, just a deeper trigger. A **25%** mid-tape live touch is allowed but is supported by only one coin in this batch.

**How to check:** at each print, compute drop vs running high; require `drop ≥ T` and VA (`n_trades_15s ≥ 8` and `usd_15s ≥ 200`); require `30 < t_plus ≤ 300`; enter at that print’s USD price.

**Evidence — first volume-live, t+ ≤ 300 s:**

| Threshold | Mid-tape n | 1.7x | 1.8x | 2.0x | Avg net on 2x hits | Avg / med time to 2x | Avg DD before 2x | 5-min stop avg net |
|---|---:|---:|---:|---:|---:|---|---:|---:|
| 30% mid | 3 | 3/3 | 3/3 | 3/3 | +$0.929 | 582 s / 121 s | −10.5% (worst −27.9%) | +$0.619 (3/3 green) |
| 35% mid | 1 | 1/1 | 1/1 | 1/1 | +$0.931 | 121 s | 0.0% | +$0.202 |
| 40% mid | 2 | 2/2 | 2/2 | 2/2 | +$1.073 | 239 s | −26.5% (worst −43.5%) | +$2.342 (2/2; stop beat 2x on both) |
| 25% mid | 1 | 1/1 | 1/1 | 1/1 | +$1.028 | 71 s | −2.9% | +$0.617 |

Mid-tape names: 30% = Private Inu t+211 s, Corona t+188 s, Betty t+207 s (Betty’s official first 30% was a dead t+37 s print; the live-touch re-score is the one that counts here). 35% = Private Inu t+211 s. 40% = Mihaomas t+108 s, Biao t+43 s. 25% = Private Inu t+153 s.

Combined mid-tape 25–40% first-live setups: **7/7 hit 1.8x and 2.0x** across 5 coins, average 2x net +$0.985.

**Secondary, not size-full: opening-dump live touches (0 ≤ t+ ≤ 30 s).** Same VA rule, but the print usually sits inside a simultaneous dev / sniper / bundle dump and is the least fillable. Hit rates are also 100% in this sample, but time-to-2x is bimodal (3 s on snowball-v2 25% vs 992 s on Gifter 25%) and the 5-min stop can be almost flat (Gifter 25% +$0.08; snowball-v2 30% −$0.00 after giving back a 25 s 2x). Evidence: 25% 8/8, 30% 5/5, 35% 4/4, 40% 2/2 all hit 2x.

**Do not enter** a late first-live touch (t+ > 300 s) under v1. See R2.

---

## R2 — Filters / skip conditions

Apply all of these before an R1 entry.

**R2a. Skip if the print is not volume-active.** Dead-volume threshold prints are not entries, even if they later 2x.

- Evidence: earball never printed a volume-live 25–40% dip in 30 minutes (thin book, $5,024 volume, 179 trades). Official first 25% was t+703 s and dead, and 2x missed. **1/10 coins, skip.**
- Evidence: Mihaomas official first 25/30/35/40% was the same t+0 42.2% wick with VA false (7 trades / $893 — dollars pass, trade count fails). That wick 2x’d in 7 s, but it is not a live entry. First live 25/30/35% arrived at t+1–2 s (opening); first live 40% was mid-tape t+108 s and 2x’d. **1/10 coins, official first touch discarded; later live touches used.**

**R2b. Skip thin books.** Operational cut used in this batch: 30-minute USD volume well under ~$20k, or trade count in the low hundreds. earball is the only skip (above). The other 9 coins all did ≥ $53k and ≥ 1,961 trades and each produced at least one live ≤5 min 25% touch. **1 skip vs 9 producers.**

**R2c. Skip a first-live touch after t+300 s as a v1 entry.** Late paths 2x more often than not, but drawdowns are ugly and the 5-min stop is a poor backstop.

- Late 35% n=4 (snowball-v2 t+318 s, Corona t+341 s, Betty t+509 s, SANTA t+504 s): 2x **3/4**, avg DD before 2x −23.2% (worst −51.7%), 5-min stop green **1/4**, avg stop net +$0.20.
- Late 40% n=5 (PI t+512 s, snowball-v2 t+327 s, Corona t+341 s, Betty t+517 s, SANTA t+849 s): 2x **3/5**, avg DD −35.9% (worst −54.1%), 5-min stop avg net +$0.09, **2/5 red**.

**R2d. Do not skip solely because sniper or bundle share is high.** All 9 coins that produced a live dip had sniper supply between 29% and 75%, and several had bundle share 18–54%. They still 2x’d on live dips (snowball-v2 75.2% snipers with a 2% dev hold; Gifter 29.2% snipers + 26.2% bundles + a 26.6% dev dump). **9/9 live-dip coins.**

**R2e. Do not skip solely because the creator wallet is fresh or the dev bought a large stash.** Gifter: 8.0-minute fresh wallet, 9.88 SOL / 26.6% buy, 100% dumped at t+13–15 s, and every official opening live dip still 2x’d (slowly). Biao: 8 SOL / 22.6% dumped in 9 s, then ran. Corona: dev bought 10% and held, also ran. Treat opening-dump fill risk under R1 secondary, not as a blanket skip. **3/10 coins as exhibits; no counterexample that a large dev buy alone killed a later live mid-tape 2x in this sample.**

**R2f. Custom-quote coins must be scored in USD.** If `sol_amount` is zero, infer USD from same-tx USDC/SOL hops and use `price_usd` for PnL. Do not read raw quote units as SOL (Mihaomas sniper notional 251,922 is mihao ≈ $2,276, not 251k SOL). **2/10 coins (Private Inu, Mihaomas).**

**R2g. If PumpSwap trades fall inside the 30-minute window and the pool is too hot to fully fetch, cap and time-stratify the sample and flag volume as understated.** Do not treat sampled volume as complete. **Exhibit: snowball-v2 (109,863 window sigs → 6,663 fetched, ~11.5%).**

**R2h. Do not relabel an opening-dump print as mid-tape.** t+ = 30 s is opening only. snowball-v2 30% is the boundary case (t+30 s, live, 2x in 25 s, 5-min stop flat).

---

## R3 — Exit / take-profit

**Default on the preferred mid-tape 30% (and 35%) setup:** take **1.7x**, and if still in, **1.8x**. Do not sit for 2x by default.

**How to check:** first later USD print ≥ 1.7 × entry, then ≥ 1.8 × entry; flatten the remainder at 1.8x unless R3b applies.

**Evidence:**

- Mid-tape 30% n=3: 1.7x **3/3** in avg 49 s (max 83 s), avg net +$0.695, avg DD −1.2%; 1.8x **3/3** in avg 57 s, avg net +$0.801; 2.0x **3/3** but avg 582 s (median 121 s) because Corona took 1,545 s. Waiting for 2x on Corona tied up capital ~25 minutes for about +$0.13 more than 1.8x; the 5-min stop on that path was already +$0.52 at 1.59x.
- Mid-tape 35% n=1 (Private Inu): 1.7x in 5 s (+$0.66), 1.8x in 13 s (+$0.90), 2x in 121 s (+$0.93). Almost all of the 2x net is already in at 1.8x.
- Mid-tape 25% n=1 (Private Inu): 1.7x in 62 s, 1.8x in 63 s, 2x in 71 s, DD −2.9%. 2x is close enough to take if 1.8x already printed.

**R3b. Mid-tape 40% (n=2):** 1.7x / 1.8x / 2x all hit, but the 5-min time stop **beat 2x on 2/2** (avg stop net +$2.34 / 3.46x vs 2x avg +$1.07). After a mid-tape 40% fill, take a 1.8x partial and leave a runner into the 5-min mark rather than a hard 2x flatten. Evidence: Mihaomas 40% 2x +$1.17 in 242 s vs stop 3.70x +$2.58; Biao 40% 2x +$0.98 in 236 s vs stop 3.21x +$2.10 (this path also drew down −43.5%).

**R3c. Opening entries (if taken):** if 1.7x / 1.8x print quickly, take them; do not hold for 2x. Exhibit: Gifter 40% 1.7x in 22 s, 1.8x in 26 s, 2x in 495 s. If 1.7x has not printed by the time stop, exit under R4 (Gifter 25/30/35% needed 493–975 s even to reach 1.7x; 5-min stops were only +$0.08 to +$0.30).

---

## R4 — Time stop

**Rule:** if R3 has not already flattened the position, exit at the last print at or before **entry + 300 seconds**, capped at the 30-minute window. This is a hard clock, not a trailing stop.

**Evidence on first volume-live touches with t+ ≤ 300 s:**

| Threshold | n | 5-min stop green | Avg stop net | Avg stop multiple | Notes |
|---|---:|---:|---:|---:|---|
| 25% | 9 | 9/9 | +$2.541 | 3.66x | Pulled up by opening verticals (snowball-v2 6.64x, SANTA 5.98x, Corona 5.81x). Gifter only +$0.08. Stop beat 2x on 6/9; 2x beat stop on 3/9. |
| 30% | 8 | 7/8 | +$1.174 | 2.26x | The one miss/flat: snowball-v2 opening 30% 1.05x −$0.00 — a 2x that printed in 25 s was given back by t+300. |
| 35% | 5 | 5/5 | +$1.729 | 2.83x | Stop beat 2x on 3/5. |
| 40% | 4 | 4/4 | +$2.487 | 3.60x | Stop beat 2x on 3/4 (both mid-tape 40% plus casual cow opening). |

**Implications:**

- If 2x (or 1.8x) already printed, R3 should already have you out. The snowball-v2 30% giveback is what the time stop looks like when you *ignore* R3. **1/8 of 30% live ≤5 min entries.**
- The 5-min stop is not a substitute for R3 on mid-tape 30/35% (2x / 1.8x usually beats or is close to the stop, and mid 35% stop was only +$0.20).
- On mid-tape 40%, the time stop is the *better* full exit in this sample (R3b).
- Late first-lives are why the cap exists: late 35% stop 1/4 green; late 40% avg +$0.09 and 2/5 red.

---

## R5 — Stop-loss suggestion (from observed drawdowns)

**Primary risk cap is R4 (the 5-min clock), not a tight tick stop.**

**Suggested protective stop, if used:** flatten if USD price trades **−35% through the entry print**, even inside the 5-minute window.

**How to check:** `price_usd / entry_price_usd − 1 ≤ −0.35`.

**Evidence (drawdown *before* a 2x hit, first volume-live):**

- Mid-tape 25/30/35% (n=5 setups): DDs were −2.9%, −2.4%, 0.0%, −1.1%, −27.9% (Corona 30%). A −35% stop would have kept all five 2x winners.
- Mid-tape 40% (n=2): Mihaomas −9.5% (kept), Biao −43.5% (would have been stopped — and that trade then reached 2x and a 3.21x time stop). **1 false stop in this sample.**
- Opening live ≤5 min 25–40% 2x hits: worst DD before 2x was casual cow 25% −30.4%, then Gifter 25% −26.4%. A −35% stop would have kept every opening ≤5 min 2x in this batch.
- Late first-lives often need more room than −35% (snowball-v2 35% −51.7%, Private Inu 40% −54.1%, snowball-v2 40% −43.1%). Another reason late is not a v1 entry.

**Do not tighten the stop to −20%.** That would have stopped casual cow 25% (−30.4%), Gifter 25% (−26.4%), Corona mid 30% (−27.9%), and Biao mid 40% (−43.5%) — four paths that still 2x’d.

---

## R6 — What v1 is not claiming

- 100% 2x hit rates in this batch are **not** a forecast. Mid-tape n is 1–3 per threshold; opening 100% includes unfillable first-second prints; the 10 coins were selected, not sampled at random.
- High sniper/bundle share is not a green light, only “not an automatic skip.”
- A 5-min time stop that beats 2x on a vertical opener is leftover trend, not the scalp’s edge.
- Graduation / PumpSwap after t+30 minutes is out of scope.

## Provenance

Re-score rows: `/workspace/pumpfun/live_touch_rescore.csv` and `live_touch_rescore.json`. Per-coin tapes and official first-touch sims: `/workspace/pumpfun/coins/<mint>/`. Dataset: `/workspace/pumpfun/dataset.csv`. Script: `/workspace/pumpfun/rescore_live.py`.
