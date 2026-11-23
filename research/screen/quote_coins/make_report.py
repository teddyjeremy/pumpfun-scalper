#!/usr/bin/env python3
import json, sys, collections, statistics as st
from pathlib import Path
HERE = Path(__file__).resolve().parent; SCREEN = HERE.parent
sys.path[:0] = [str(HERE), str(SCREEN)]
import report as R, qc_analyze as Q
OUT = SCREEN / "cache" / "quote_coins_report.md"
BT_DAYS = ["2026-11-22", "2026-11-21", "2026-11-20", "2026-11-19"]
def f3(x): return "–" if x is None else f"{x:+.3f}"
def pc(x): return "–" if x is None else f"{x*100:.0f}%"
def i0(x): return "–" if x is None else f"{x:,.0f}"
L = []
rows, perq, qday, grad = R.counts()
meta = Q.META
L.append("# Non-SOL-quoted Pump.fun coins: census + backtest (Nov 16–23, 2026, EAT)\n")
L.append("Research only (no trading, wallets or signing). Code: `screen/quote_coins/` (new files only; paper/, lib/parse.py and the running pipeline untouched). Generated " + __import__("time").strftime("%Y-%m-%d %H:%M EAT") + ".\n")
L.append("## How quote coins were identified\n")
L.append("* Pump.fun bonding-curve accounts (166 bytes) store the **quote mint at byte offset 83** (System Program `111…1` = SOL). "
         "Verified on AGI (SPCX) and on a 100-coin random sample.\n"
         "* Every launch whose create tx touched a non-WSOL mint (`extra_mints`) was checked with `getMultipleAccounts`; the quote vault always shows up there "
         "(100% of confirmed non-SOL curves had it). A ~3% random validation sample of launches with **no** extra mint was also checked: see the `val non-SOL` column (expect 0).\n"
         "* Nov 18–22 launch lists come from the pipeline's enumerate cache; Nov 16, 17 and today (00:00–~16:20 EAT) were enumerated here via Helius `getTransactionsForAddress` on the mint-authority PDA.\n"
         "* Quote tokens were identified with GeckoTerminal (`/tokens/multi`), USD price history = GeckoTerminal 15-min OHLCV of the quote's deepest pool (stables = $1).\n")
L.append("## 1. How many launches are non-SOL quoted\n")
L.append("| Day (EAT) | Launches | Non-SOL quoted | Share | Tokenized stocks | Stables | Pump memecoins as quote | Other tokens | val sample (non-SOL) | curve missing |")
L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for d, n, nq, cats, nv, vns, miss in rows:
    L.append(f"| {d} {R.DOW[d]} | {i0(n)} | {i0(nq)} | {pc(nq/n if n else None)} | {cats.get('tokenized_stock',0)} | {cats.get('stable',0)} | {cats.get('pump_memecoin',0)} | {cats.get('other_token',0)+cats.get('unknown',0)+cats.get(None,0)} | {nv} ({vns}) | {miss} |")
L.append("")
L.append(f"Distinct quote mints over the period: **{len(perq)}**. Top 25:\n")
L.append("| Quote | Name | Type | Launches (period) | " + " | ".join(R.DOW[d][:3] + " " + d[-2:] for d, *_ in rows) + " | Price now | USD history source |")
L.append("|---|---|---|---:|" + "---:|" * len(rows) + "---:|---|")
for m, n in perq.most_common(25):
    g = meta.get(m, {})
    qp = HERE / "quotes" / f"{m}.json"
    src = "pegged $1" if g.get("category") == "stable" else ((json.load(open(qp)).get("pool") or {}).get("pool_name") + " (GT 15m)" if qp.exists() and (json.load(open(qp)).get("pool") or {}).get("pool_name") else ("GT pool " + str(g.get("top_pool"))[:8] + "… (not pulled)" if g.get("top_pool") else "none"))
    L.append(f"| {g.get('symbol')} `{m[:6]}…` | {(g.get('name') or '')[:38]} | {g.get('category')} | {n} | " + " | ".join(str(qday[d].get(m, 0)) for d, *_ in rows) + f" | {g.get('price_now') and ('$%.4g' % float(g['price_now']))} | {src} |")
L.append("")
L.append("## 2. Graduation (curve `complete` flag as of now)\n")
L.append("| Day | Non-SOL graduated | Non-SOL rate | SOL graduated (est.) | SOL rate (est.) |")
L.append("|---|---:|---:|---:|---:|")
for d, (gq, nq, gs, ns) in grad.items():
    L.append(f"| {d} {R.DOW[d]} | {gq} / {nq} | {pc(gq/nq if nq else None)} | {i0(gs)} / {i0(ns)} | {pc(gs/ns if ns else None)} |")
ag = json.load(open(HERE / "active_grad.json")) if (HERE / "active_grad.json").exists() else {}
if ag:
    L.append("\n**Among ACTIVE coins** (≥150 trades in the first 5 min on Bitquery):\n")
    L.append("| Day | Non-SOL active | Non-SOL graduated | rate | SOL active | SOL graduated | rate |"); L.append("|---|---:|---:|---:|---:|---:|---:|")
    for d, x in ag.items():
        L.append(f"| {d} {R.DOW[d]} | {x['nonsol_active']} | {x['nonsol_grad']} | {pc(x['nonsol_grad']/x['nonsol_active'] if x['nonsol_active'] else None)} | {x['sol_active']} | {x['sol_grad']} | {pc(x['sol_grad']/x['sol_read'] if x['sol_read'] else None)} |")
L.append("\nSOL rate is weighted (SOL coins with an extra mint counted 1:1; the rest from the ~3% validation sample). Today's coins can still graduate.\n")
# ---------------- backtests
sol = json.load(open(HERE / "sol_ref.json")) if (HERE / "sol_ref.json").exists() else {}
L.append("## 3. Active universe, base rate and backtests\n")
L.append("Universe = launches with ≥150 trades in their first 5 minutes on Bitquery's cached minute bars (0 new points), the same definition as the SOL scalp sample; "
         "stratified random sample by 5-min trade tier (150–300 / 300–500 / 500–1000 / 1000+), day counts are tier-weighted estimates. Tapes: full, unsampled curve tapes t0−2 s…t0+480 s "
         "(Helius getTransactionsForAddress, 100 tx/page, ≤15 pages/coin). Prices: quote reserve ÷ token reserve × quote USD. Rules identical to SOL: entry t+30…300 s, fill at the first trade ≥1 s after the signal, "
         "TP ≥1.5x, out by ≤120 s, 1.25% fee each way + $0.0285 gas; per $5 = 5×(mult×0.9875²−1) − $0.0285.\n")
allc = {}; pooled = collections.defaultdict(lambda: ([], [])); pooled_base = []; catbase = collections.defaultdict(list); catset = collections.defaultdict(list)
L.append("### Base rate: coins trading at $5k–$10k (t+30…300 s) whose +1 s fill hits 1.5x within 120 s\n")
L.append("| Day | Non-SOL tapes used | Non-SOL active (est./day) | Non-SOL in band (est./day) | **Non-SOL first-touch hits 1.5x** | any touch | 1.7x | median s | SOL active (est./day) | SOL in band (est.) | **SOL first-touch 1.5x** | SOL any | SOL 1.7x |")
L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
res_by_day = {}
for d in BT_DAYS:
    if not (HERE / f"plan_{d}.json").exists(): continue
    coins, P, skipped, qcat = R.load_nonsol(d)
    res = R.eval_coins(coins); res_by_day[d] = (coins, P, skipped, qcat, res)
    b = R.base_summary(res["base"]); s = (sol.get(d) or {}).get("base") or {}
    sol_univ = None
    sp = SCREEN / "cache" / "scalp_tapes" / f"plan_{d}.json"
    if sp.exists(): sol_univ = sum(t["universe"] for t in json.load(open(sp))["tiers"])
    act = sum(t["universe"] for t in P["tiers"])
    L.append(f"| {d} {R.DOW[d]} | {len(coins)} (skipped {sum(skipped.values())}) | {act} | {i0((b or {}).get('band_est'))} | **{pc((b or {}).get('first_share'))}** ({(b or {}).get('band_sampled','–')} sampled) | {pc((b or {}).get('any_share'))} | {pc((b or {}).get('first17_share'))} | {(b or {}).get('med_s','–')} | {i0(sol_univ)} | {i0(s.get('band_est'))} | **{pc(s.get('first_share'))}** | {pc(s.get('any_share'))} | {pc(s.get('first17_share'))} |")
    for k, (trs, ws) in res["setups"].items():
        pooled[k][0].extend(trs); pooled[k][1].extend([1.0] * len(trs))
    pooled_base += res["base"]
    for c, (bb, b17, w) in zip(coins, res["base"]):
        catbase[Q.META.get(c["quote"], {}).get("category")].append((bb, b17, 1.0))
L.append("")
pb = R.base_summary(pooled_base)
if pb: L.append(f"Pooled non-SOL (unweighted, all sampled days): {pb['band_sampled']} band coins, first-touch 1.5x hit rate **{pc(pb['first_share'])}**, any-touch {pc(pb['any_share'])}, 1.7x {pc(pb['first17_share'])}, median {pb['med_s']} s.\n")
L.append("By quote type (pooled, unweighted):\n")
L.append("| Quote type | Band coins | First-touch 1.5x | Any touch | 1.7x |"); L.append("|---|---:|---:|---:|---:|")
for k, v in catbase.items():
    bs = R.base_summary(v)
    if bs: L.append(f"| {k} | {bs['band_sampled']} | {pc(bs['first_share'])} | {pc(bs['any_share'])} | {pc(bs['first17_share'])} |")
L.append("")
L.append("### Quick-flip setups (same code and parameters as screen/scalp_setups.py / scalp_run.py, rug layer on)\n")
L.append("| Setup | Day | Non-SOL trades sampled | est. trades/day | est. wins/day | Win % | Exp per $1 (weighted) | per $1 plain | **Exp per $5** | Avg win | Avg loss | Worst | < −20% | Median hold s | SOL exp per $1 (same code) | SOL per $5 |")
L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for k in Q.SETUPS:
    for d, (coins, P, skipped, qcat, res) in res_by_day.items():
        trs, ws = res["setups"].get(k, ([], []))
        a = R.agg(trs, ws); s = ((sol.get(d) or {}).get("setups") or {}).get(k) or {}
        if a:
            L.append(f"| {k} | {d[-5:]} | {a['n']} | {i0(a['est_day'])} | {i0(a['wins_day'])} | {pc(a['win'])} | {f3(a['exp1'])} | {f3(a['exp1_plain'])} | **{f3(a['exp5'])}** | {f3(a['avg_win'])} | {f3(a['avg_loss'])} | {f3(a['worst'])} | {a['lose20']} | {a['med_hold']} | {f3(s.get('exp1'))} | {f3(s.get('exp5'))} |")
        else:
            L.append(f"| {k} | {d[-5:]} | 0 | – | – | – | – | – | – | – | – | – | – | – | {f3(s.get('exp1'))} | {f3(s.get('exp5'))} |")
    a = R.agg(*pooled[k])
    if a: L.append(f"| **{k}** | **pooled** | {a['n']} | | | {pc(a['win'])} | | {f3(a['exp1_plain'])} | **{f3(a['exp5'])}** | {f3(a['avg_win'])} | {f3(a['avg_loss'])} | {f3(a['worst'])} | {a['lose20']} | {a['med_hold']} | | |")
L.append("")
L.append("### Strict v0 / v1.1 (annotated v1.2, v1.3) on non-SOL coins\n")
L.append("| Day | v0 signals (sampled) | est./day | v1.1 keeps | v1.2 keeps | v1.3 keeps | entries in $5–10k | TP1.5/120 s fade: net per $1 each | TP1.7: net per $1 each |")
L.append("|---|---:|---:|---:|---:|---:|---:|---|---|")
for d, (coins, P, skipped, qcat, res) in res_by_day.items():
    v = res["v0"]
    L.append(f"| {d} {R.DOW[d]} | {len(v)} | {i0(sum(c['w'] for c, _ in v))} | {sum(1 for _, x in v if not x['skip_v11'])} | {sum(1 for _, x in v if not x['skip_v11'] and not x['skip_v12'])} | {sum(1 for _, x in v if not x['skip_v11'] and not x['skip_v12'] and not x['skip_v13'])} | {sum(1 for _, x in v if x['in_5_10k'])} | "
             + ", ".join(f"{x['tp1.5_h120_fade']['net1']:+.2f}" for _, x in v) + " | " + ", ".join(f"{x['tp1.7_h120_fade']['net1']:+.2f}" for _, x in v) + " |")
L.append("")
for d, (coins, P, skipped, qcat, res) in res_by_day.items():
    mig = [c for c in coins if c.get("migrated") is not None]
    L.append(f"* {d}: sampled tapes by quote type {dict(qcat)}; skipped {dict(skipped)}; graduated inside 480 s: {len(mig)}; truncated tapes (>1,500 txs before t+480): {sum(1 for c in coins if c.get('trunc'))}; results dropped because they needed data past a truncated tape's end: {dict(res.get('dropped', {}))}.")
L.append("""
## 4. Verdict: are non-SOL-quoted coins better or worse scalp targets?

* **Big blind spot, now closed for research:** 12–27% of all launches each day are non-SOL quoted (Wed Nov 18 peak 27%, mostly TTWO-quoted (Take-Two, Backpack tokenized stock)). Our SOL-only decoder saw them as $0 volume, so the strict screen never judged them. On these tapes, strict v0 fires on an estimated ~30–170 non-SOL coins/day (51 sampled signals in 4 days), while the SOL screen found 22 in the whole week.
* **Not better as v0/v1.x targets:** the 51 sampled v0 signals averaged −$0.069 per $1 (TP 1.5x / ≤120 s / fade), 20 of 51 green. v1.1 (top-seller >50%) removed none of them. The v1.2 cut (≤28 s from the high) kept 24, which averaged +$0.025 (12 of 24 green). That's break-even, not an edge.
* **Base rate is the same or slightly higher:** 40–51% of non-SOL coins in the $5–10k band hit 1.5x within 2 min from the first touch (pooled 46%), against 36–50% for SOL coins. They get there more slowly (median 26–46 s vs 15–23 s). But there are ~10x fewer of them: ~190–650 band coins/day vs ~1,100–1,250 SOL.
* **Quick-flip setups:** the SOL favourites don't carry over. Steady reclaim was −$0.07 per $1 pooled (20 trades) and breakout −$0.05 (22 trades). Plain band entry (K, TP 1.5x) was the only family slightly positive: +$0.02 weighted on Sun/Sat/Fri, −$0.04 on Thu, pooled +$0.04 per $1 / +$0.31 per $5. Its losses are not minimal, though: 29 of 139 trades lost more than 20% and the worst lost 75%. On SOL coins the same rule lost about −$0.10 on 2 of 3 days.
* **Fewer graduations:** among active coins, 5–15% of non-SOL coins graduated vs 14–27% of SOL coins (Thu–Sun). Over all launches the non-SOL rate is also lower (table 2).
* **Bottom line:** non-SOL coins are *different*, not better. They make a worthwhile extra pool for a band-entry test in the paper trader, since their volume and wallet counts look farmed (about $40/trade, about 2 trades/wallet on v0 hits). They don't rescue the v0/v1.x rulebook.

## 5. Caveats

* Small samples: 187 priced tapes over 4 days (~40–50/day, tier-weighted). Setup rows have 3–40 trades/day. Treat every expectancy as ±$0.05–0.10 noise.
* 59 of the busiest tapes hit the 15-page cap before t+480 s. Any result that needed data past a tape's end was dropped and is listed per day above; that slightly favours early hits in the base rate.
* USD prices come from GeckoTerminal 15-minute closes of the quote's deepest pool, interpolated. Memecoin quotes can move within a 2-minute flip; the $5–10k band edges are ±a few % for them. 4 sampled coins were skipped (no usable quote price history). hSOL and a few others use today's price as a constant.
* Real execution on a non-SOL coin needs a SOL→quote swap in and quote→SOL out (or holding the quote). That's ~0.1–0.5% extra each way on deep stock/stable pools and more on memecoin quotes. It is **not** included above; at 0.3% each way it costs ~$0.006 per $1.
* Nov 16, 17 and 23 are counted and graduation-checked only. There are no Bitquery bars for those days and Nov 18's prescreen is unfinished, so no tapes were backtested for them. Today's coins can still graduate.
* Classification only checks launches whose create tx touched a non-WSOL mint. The random validation sample of the rest found 0 non-SOL curves in ~6,900 checked.
""")
OUT.write_text("\n".join(L) + "\n")
print(OUT)
