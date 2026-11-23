# Strict v0 screen — validation on the 10 reference coins

Scored from existing `coins/<mint>/trades.csv` (full analyze tapes) with the **same rules as `screen/prescreen.py`**: 250 successful 5-minute curve-signature gate; first volume-live mid-tape (30s < t+ ≤ 300s) 30/35/40% dip; at that print, cumulative volume ≥ $10k, trades ≥ 300, unique wallets ≥ 150, mcap $5k–$40k.

The cheap pre-screen pages **only the bonding curve**, so the official result is the curve-only 5-minute tape. A second column shows what happens if PumpSwap prints after an early migration are included (they are not, today).

All times Africa/Nairobi (UTC+3). Machine-readable copy: `validation_reference10.json`.

## Result

**Official strict v0 (gate + curve-only 5-minute filters): 3 / 10 pass**
(Corona, Mihaomas, Gifter)

If the pre-screen also saw PumpSwap in the first 5 minutes: **5 / 10** would pass (the same three, plus Private Inu and snowball-v2).

The screen is not randomly broken — the trigger and filters fire correctly on the three curve mid-tape books that meet the cuts. It does, however, systematically miss early graduates, and it is tighter than the original 10-coin rulebook sample on Betty and Biao.

## Coin-by-coin

| Coin | Launch (EAT) | Migrate | Gate (≥250 ok 5m curve sigs) | Strict v0 (curve 5m) | Why | If PumpSwap included in 5m |
|---|---|---:|---|---|---|---|
| **Corona** | Nov 22 07:41:25 | t+2703s (after 30m) | PASS (1132) | **PASS** | Mid 30% live at t+188s, mcap $11,129, vol $17,616, 581 trades, 239 wallets | PASS (same print, still curve) |
| **Mihaomas** | Nov 22 07:29:17 | t+2323s | PASS (865) | **PASS** | Mid 30% live at t+87s, mcap $6,560, vol $12,087, 389 trades, 163 wallets | PASS (same) |
| **Gifter** | Nov 22 11:10:08 | t+2342s | PASS (635) | **PASS** | Mid 30% live at t+130s, mcap $5,274, vol $12,569, 389 trades, 160 wallets. Mcap is just $274 above the $5k floor. | PASS (same) |
| **Private Inu** | **Nov 21 23:20:08** | **t+180s** | PASS (2144) | **FAIL** (`mid_25_only`) | On the curve, the only mid-tape live dip in 5 minutes is 25% at t+153s (mcap $32.5k). The rulebook’s mid 30% exhibit is t+211s — **after migration, on PumpSwap**. | **PASS** mid 30% t+211s, mcap $35,482, vol $91,305, 2534 trades, 841 wallets, venue=pumpswap |
| **snowball-v2** | Nov 22 07:34:46 | **t+43s** | PASS (727) | **FAIL** (`opening_30plus_only`) | Curve only has an opening live 30% at t+30s (the rulebook opening-dump case). Mid 30% at t+79s is **PumpSwap**. | **PASS** mid 30% t+79s, mcap $27,423, 803 trades, 302 wallets, venue=pumpswap |
| **Betty** | Nov 22 17:04:48 | none in 30m | PASS (284, barely) | **FAIL** (`low_vol:6313, low_trades:157, low_wallets:73, mcap_low:3691`) | The mid 30% live trigger **is found** at t+207s (a rulebook 2x exhibit), but at that print the book is still thin and mcap is $3.7k. All four size/mcap cuts fail. | FAIL (same; never left the curve) |
| **Biao** | Nov 22 07:05:36 | t+3618s | PASS (784) | **FAIL** (`low_wallets:130`) | Mid 30% live at t+34s has mcap $6,372, vol $14,692, 321 trades — only wallets (130) miss the 150 cut. Even the rulebook mid 40% at t+43s still has only 143 wallets. Narrow miss. | FAIL (same; still on the curve) |
| **casual cow** | Nov 22 09:43:17 | t+1785s | PASS (333) | **FAIL** (`mid_25_only`) | Opening live 30/35/40% (t+8–14s); first mid-tape live dip is only 25% at t+182s, mcap $3,865. Not a v1 mid-30 setup. | FAIL (same; migration is after 5m) |
| **SANTA** | Nov 22 09:12:22 | t+278s | PASS (1259) | **FAIL** (`no_mid_live_30`) | Only an opening live 25% at t+22s in the first 5 minutes. Rulebook 35/40% touches were late (t+504 / t+849). Correct v1 skip (R2c). | FAIL (`no_mid_live_30` even with 443 PumpSwap prints before t+300) |
| **earball** | Nov 22 03:27:05 | t+41046s | **FAIL** (127) | **FAIL** (`dead` by gate; also `no_mid_live_30`) | Thin book, as in rulebook R2b. Would have been recorded `dead_by_sig_count` and would never have had trade txs fetched. | FAIL (same) |

## What this means

1. **Curve-only pre-screen is blind after early graduation.** Private Inu and snowball-v2 are two of the rulebook’s mid-tape 2x names, and both of those entries are PumpSwap prints after the curve completed (t+180s and t+43s). This is the main reason the official pass list is 3/10 rather than 5/10. A follow-on change worth considering: once the curve completes inside the 5-minute window, also page the PumpSwap pair through t+300s (still capped at 300 txs).

2. **The $5k mcap / 150 wallet / $10k vol / 300 trade cuts are tighter than the 10-coin study.** Betty’s mid 30% (a clean later 2x in the rulebook) fails every cut at the touch. Biao fails only the wallet floor, and still fails it at the 40% print (143 < 150). Gifter only clears mcap by $274. These are consistent applications of v0, not implementation bugs — but they will shrink the sample versus “coins that looked like the original mid-tape setups.”

3. **Three fails are consistent with the rulebook skip rules:** earball (thin), SANTA (late-only 35/40%), casual (no mid-tape live 30%).

4. **Calendar:** 9 of 10 launched on Nov 22 EAT (03:27–17:04). Only Private Inu is in the Nov 21 window, at 23:20 — outside the time-stratified slice (00:00, 06:00, 12:00, 18:00). A Nov 21-only slice cannot reproduce the study set. Nov 22 00:00–21:20 EAT is the window that actually contains the reference coins.

5. **The screen’s trigger machinery works** when the book stays on the curve and the cuts are met (Corona, Mihaomas, Gifter). The 0 passers on the Nov 21 slice so far are therefore not evidence of a scoring bug; that slice simply does not include these mid-tape books.

## Update — PumpSwap coverage fix (22 Nov 2026 22:05 EAT)

The pre-screen now pages the PumpSwap pair through t+300s (cap 300, time-stratified) when the curve completes or the pair appears inside that window. Strict v0 cuts are unchanged.

Re-scored the 10 reference coins from their analyze tapes with this coverage rule:

**Official strict v0: 5 / 10 pass** — Corona, Private Inu (PumpSwap mid 30% at t+211s), snowball-v2 (PumpSwap mid 30% at t+79s), Mihaomas, Gifter.

Still fail (criteria, not coverage): Betty (thin/low mcap at the mid 30% touch), Biao (130 wallets < 150), casual (mid 25% only), SANTA (no mid live 30% in 5m), earball (gate + no mid live 30%).
