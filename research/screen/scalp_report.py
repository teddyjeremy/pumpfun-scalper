#!/usr/bin/env python3
"""Pick quick-flip scalp setups on Sun Nov 22 (in-sample) and test them unchanged on Sat Nov 21 + Fri Nov 20.
Writes cache/scalp_tapes/report.json and prints markdown tables (scalp_expansion.md is assembled from these)."""
from __future__ import annotations
import sys, json, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scalp_setups as S
import scalp_run as R

MIN_WINS = 10  # est. wins/day needed in-sample to be a candidate (20 is the goal; band rule makes 20 rare)
IS, OOS = "2026-11-22", ["2026-11-21", "2026-11-20"]
DAYS = [IS] + OOS + [R.PAPER]
LBL = {R.PAPER: "Mon 23 live tapes (dip-biased, w=1)", "2026-11-22": "Sun 22 (tune)", "2026-11-21": "Sat 21 (test)", "2026-11-20": "Fri 20 (test)"}

HDR = ("| Day | Sampled trades | Trades/day (est.) | Wins/day | Losses/day | Win % | Exp. per $1 | Day P&L (est.) | Avg win | Avg loss | "
       "Worst | Lose >10% / >20% / >40% | Max DD (day) | Loss streak | Median hold | Reached 1.5x in 120s (median s) | Also v0 |\n"
       "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")


def fmt(s, day):
    r15 = f"{(s or {}).get('reach15', 0)*100:.0f}% ({(s or {}).get('reach15_med_s')}s)"
    if day == R.PAPER and s and s.get("n_sampled"):
        return (f"| {LBL[day]} | {s['n_sampled']} | n/a | n/a | n/a | {s['win_pct']*100:.0f}% | {s['exp']:+.3f} | n/a | {s['avg_win']:+.3f} | "
                f"{s['avg_loss']:+.3f} | {s['worst']:+.2f} | {s['p_lose10']*100:.0f}% / {s['p_lose20']*100:.0f}% / {s['p_lose40']*100:.0f}% | "
                f"{s['raw']['max_dd']:.2f} (seq.) | {s['raw']['loss_streak']} | {s['hold_med']}s | {r15} | – |")
    if not s or not s.get("n_sampled"):
        return f"| {LBL[day]} | 0 | 0 | 0 | 0 | – | – | – | – | – | – | – | – | – | – | – | – |"
    r15 = f"{s.get('reach15', 0)*100:.0f}% ({s.get('reach15_med_s')}s)"
    return (f"| {LBL[day]} | {s['n_sampled']} | {s['est_trades']:.0f} | {s['est_wins']:.0f} | {s['est_losses']:.0f} | {s['win_pct']*100:.0f}% | "
            f"{s['exp']:+.3f} | {s['est_total']:+.1f} | {s['avg_win']:+.3f} | {s['avg_loss']:+.3f} | {s['worst']:+.2f} | "
            f"{s['p_lose10']*100:.0f}% / {s['p_lose20']*100:.0f}% / {s['p_lose40']*100:.0f}% | {s['dd_med']:.2f} | {s['streak_med']} | {s['hold_med']}s | {r15} | {s['v0']} |")


def score(s):
    """Loss-adjusted score: expectancy per $1 of average loss, zero if the setup loses money or loses big often."""
    if not s or s.get("n_sampled", 0) < 6 or s["exp"] <= 0:
        return -9
    return s["exp"] / max(0.05, abs(s["avg_loss"])) - 1.0 * s["p_lose20"]


def oos_pooled(res, en, ex):
    tr = [t for d in OOS for t in res[(d, en)]]
    rows = [dict(net=t["ex"][ex]["net"], hold_s=t["ex"][ex]["hold_s"], v0=t["v0"]) for t in tr]
    return S.summarize(rows, [t["w"] / len(OOS) for t in tr])


def reentry(day, en, ex):
    coins, plan, tw = R.load_day(day)
    fam, p = R.ENTRIES[en]
    first = 0; second = []
    for c in coins:
        outs = S.run_coin_reentry(c["f"], fam, p, {ex: R.EXITS[ex]})
        if outs:
            first += 1
            r2 = outs[0].get("reentry", {}).get(ex)
            if r2:
                second.append((r2["ex"][ex]["net"], c["w"]))
    if not second:
        return {"first": first, "second": 0}
    n = len(second)
    return {"first": first, "second": n, "second_est_day": sum(w for _, w in second), "second_exp": sum(x for x, _ in second) / n,
            "second_win": sum(x > 0 for x, _ in second) / n, "second_worst": min(x for x, _ in second)}


def main():
    res, meta = R.evaluate(days=DAYS)
    rows = []
    for en in R.ENTRIES:
        for ex in R.EXITS:
            s = {d: R.table(res, d, en, ex) for d in DAYS}
            s["oos"] = oos_pooled(res, en, ex)
            rows.append({"en": en, "ex": ex, "s": s, "score": score(s[IS])})
    elig = [r for r in rows if r["s"][IS].get("n_sampled", 0) >= 6 and r["s"][IS]["exp"] > 0 and r["s"][IS]["est_wins"] >= MIN_WINS]
    bal = sorted([r for r in elig if r["s"][IS]["p_lose20"] <= 0.20], key=lambda r: -r["score"])
    # keep picks diverse: at most one exit per entry
    picks, seen = [], set()
    for r in bal:
        if r["en"] in seen:
            continue
        picks.append(r); seen.add(r["en"])
        if len(picks) == 3:
            break
    vol = sorted(elig, key=lambda r: -(r["s"][IS]["est_wins"] * r["s"][IS]["exp"]))[:1]
    fam_best = {}
    def fam_key(r):
        return "I" if r["en"].startswith("I_") else r["en"].split("_")[0]
    for r in rows:  # every family gets its best Sunday row (score, then expectancy), even if negative
        s0 = r["s"][IS]
        if s0.get("n_sampled", 0) < 6:
            continue
        k = (r["score"], s0["exp"])
        fk = fam_key(r)
        if fk not in fam_best or k > (fam_best[fk]["score"], fam_best[fk]["s"][IS]["exp"]):
            fam_best[fk] = r
    # F (scale-out), G (invalidation exit) and Hx (flow-flip exit) are exit families: best entry for each on Sunday
    for pre, fk in (("F_", "F(exit)"), ("G_", "G(exit)"), ("Hx_", "Hx(exit)")):
        cand_x = [r for r in rows if r["ex"].startswith(pre) and r["s"][IS].get("n_sampled", 0) >= 6]
        if cand_x:
            fam_best[fk] = max(cand_x, key=lambda r: (r["score"], r["s"][IS]["exp"]))
    # in-sample vs OOS gap over all eligible combos
    gap = {"n_combos": len(rows), "n_eligible_IS": len(elig),
           "share_eligible_oos_positive": (sum(1 for r in elig if r["s"]["oos"].get("exp", -1) > 0) / len(elig)) if elig else None,
           "median_IS_exp": sorted(r["s"][IS]["exp"] for r in elig)[len(elig) // 2] if elig else None,
           "median_OOS_exp": sorted(r["s"]["oos"].get("exp", 0) for r in elig)[len(elig) // 2] if elig else None}
    # whole-grid OOS-honest view: top 10 by OOS expectancy among setups with >=20 OOS wins/day (NOT a valid pick, shown for honesty)
    oos_top = sorted([r for r in rows if r["s"]["oos"].get("n_sampled", 0) >= 10 and r["s"]["oos"]["est_wins"] >= 20],
                     key=lambda r: -r["s"]["oos"]["exp"])[:10]
    rent = {f"{r['en']}|{r['ex']}": {d: reentry(d, r["en"], r["ex"]) for d in [IS] + OOS} for r in picks}
    out = {"meta": meta, "be_mult": S.breakeven_mult(), "picks": picks, "vol": vol, "fam_best": fam_best, "gap": gap,
           "oos_top": oos_top, "reentry": rent,
           "entries": {k: [v[0], v[1]] for k, v in R.ENTRIES.items()}, "exits": R.EXITS}
    out["base_rates"] = {d: R.base_rates(d) for d in DAYS}
    print("BASE RATES", json.dumps(out["base_rates"], default=str))
    (R.TAPES / "report.json").write_text(json.dumps(out, default=str, indent=1))
    # baseline tables per family default exit
    print("break-even multiple at $1:", round(S.breakeven_mult(), 4))
    print(json.dumps(gap))
    md = []
    for title, lst in (("Balanced picks (chosen on Sun 22 only)", picks), ("Volume pick (most est. wins x expectancy on Sun 22)", vol),
                       ("Best of each family (chosen on Sun 22)", [dict(r, fam=k) for k, r in sorted(fam_best.items())]),
                       ("Hindsight: best on the test days (NOT a valid pick, shown to size the overfitting gap)", oos_top[:5])):
        md.append(f"\n### {title}\n")
        if not lst:
            md.append("_None met the in-sample bar (n >= 6, expectancy > 0, est. wins >= %d/day)._\n" % MIN_WINS)
        for r in lst:
            fam = f"[{r['fam']}] " if r.get("fam") else ""
            oo = r["s"]["oos"]
            md.append(f"\n**{fam}{r['en']} + {r['ex']}** (loss-adjusted score on Sun {r['score']:.2f}; pooled Sat+Fri: "
                      f"{oo.get('n_sampled', 0)} trades, exp {oo.get('exp', 0):+.3f}, win {oo.get('win_pct', 0)*100:.0f}%)\n")
            md.append(HDR)
            for d in DAYS:
                md.append(fmt(r["s"][d], d))
    (R.TAPES / "report_tables.md").write_text("\n".join(md))
    print("\n".join(md))
    print("\nREENTRY", json.dumps(rent, default=str))
    return out


if __name__ == "__main__":
    main()
