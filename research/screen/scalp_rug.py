#!/usr/bin/env python3
"""Which entry-time features separate rugs from real coins among $5k-$10k scalp entries (no lookahead)."""
from __future__ import annotations
import sys, json, bisect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scalp_setups as S
import scalp_run as R

REF_EXIT = "G_tp1.5_h120_fade_inv"


def label(f, t):
    """rug = within 120s of the fill price fell to <= 0.4x and was still < 0.5x at +120s,
    or trading died (< 3 trades in +60..120s) with price < 0.7x."""
    e_bt = t["entry_bt"]; px = t["entry_px"]
    j0 = bisect.bisect_right(f.bt, e_bt)
    j1 = bisect.bisect_right(f.bt, e_bt + 120)
    seg = f.spot[j0:j1]
    last = f.spot[j1 - 1] if j1 > 0 else px
    mn = min(seg) if seg else px
    n_late = j1 - bisect.bisect_right(f.bt, e_bt + 60)
    rug = (mn <= 0.4 * px and last < 0.5 * px) or (n_late < 3 and last < 0.7 * px)
    return rug


def collect(day):
    coins, _, _ = (R.load_paper() if day == R.PAPER else R.load_day(day))
    out = {}
    for c in coins:
        for en, (fam, p) in R.ENTRIES.items():
            p = {k: v for k, v in p.items() if k != "rug"}
            r = S.run_coin(c["f"], fam, p, {REF_EXIT: R.EXITS[REF_EXIT]})
            if not r:
                continue
            key = (c["mint"], r["sig_i"])
            if key in out:
                out[key]["fams"].add(en.split("_")[0]); continue
            out[key] = {"mint": c["mint"], "sym": c["symbol"], "w": c["w"], "rf": r["rf"], "rug": label(c["f"], r),
                        "net": r["ex"][REF_EXIT]["net"], "win": r["ex"][REF_EXIT]["net"] > 0, "fams": {en.split("_")[0]}}
    return list(out.values())


CANDS = [
    ("top1_drop", ">", 0.5), ("top1_drop", ">", 0.35),
    ("bundle_drop", ">", 0.3), ("bundle_drop", ">", 0.5),
    ("bundle_hold", ">", 0.15), ("bundle_hold", ">", 0.25),
    ("creator_hold", ">", 0.03), ("creator_hold", ">", 0.08), ("creator_sold30", "==", True),
    ("top5_hold", ">", 0.25), ("top5_hold", ">", 0.35),
    ("sell_accel", ">", 2.0), ("sell_accel", ">", 3.0),
    ("drain15", ">", 0.10), ("drain15", ">", 0.20),
    ("uniq60", "<", 0.35), ("uniq60", "<", 0.25),
]


def flag(rf, k, op, th):
    v = rf[k]
    return (op == ">" and v > th) or (op == "<" and v < th) or (op == "==" and v == th)


def table(rows, rules):
    rugs = [r for r in rows if r["rug"]]; good = [r for r in rows if r["win"]]
    out = []
    for k, op, th in rules:
        rr = sum(1 for r in rugs if flag(r["rf"], k, op, th)); gg = sum(1 for r in good if flag(r["rf"], k, op, th))
        out.append({"rule": f"{k} {op} {th}", "rugs_cut": rr, "rugs": len(rugs), "wins_cut": gg, "wins": len(good),
                    "cut_total": sum(1 for r in rows if flag(r["rf"], k, op, th))})
    return out


# Chosen on Sunday only: cut rugs at >= ~3x the rate they cut winners (base rug:winner ratio 1.5), >= 9 rugs cut.
CHOSEN = [("creator_hold", ">", 0.08), ("bundle_hold", ">", 0.25), ("uniq60", "<", 0.35)]


def combined(rows, rules=CHOSEN):
    keep = [r for r in rows if not any(flag(r["rf"], *ru) for ru in rules)]
    n, nk = len(rows), len(keep)
    rugs, rk = sum(r["rug"] for r in rows), sum(r["rug"] for r in keep)
    wins, wk = sum(r["win"] for r in rows), sum(r["win"] for r in keep)
    return {"entries": n, "kept": nk, "rug_rate_before": rugs / max(1, n), "rug_rate_after": rk / max(1, nk),
            "rugs_cut": rugs - rk, "rugs": rugs, "wins_cut": wins - wk, "wins": wins,
            "exp_before": sum(r["net"] for r in rows) / max(1, n), "exp_after": sum(r["net"] for r in keep) / max(1, nk)}


def main():
    days = ["2026-11-22", "2026-11-21", "2026-11-20", R.PAPER]
    data = {}
    for d in days:
        try:
            data[d] = collect(d)
        except FileNotFoundError:
            data[d] = []
    res = {}
    for d, rows in data.items():
        n = len(rows); rugs = sum(r["rug"] for r in rows); wins = sum(r["win"] for r in rows)
        res[d] = {"entries": n, "rugs": rugs, "wins": wins, "rules": table(rows, CANDS), "combined": combined(rows)}
        print("COMBINED", d, json.dumps(res[d]["combined"]))
        print(f"\n{d}: unique entries={n} rugs={rugs} ({rugs/max(1,n):.0%}) winners(ref exit)={wins}")
        for t in res[d]["rules"]:
            print(f"  {t['rule']:26} rugs cut {t['rugs_cut']}/{t['rugs']}  winners cut {t['wins_cut']}/{t['wins']}  all cut {t['cut_total']}")
    # feature medians rugs vs non-rugs (Sunday)
    for d in days:
        rows = data[d]
        if not rows:
            continue
        print("\nmedians", d)
        for k in rows[0]["rf"]:
            def med(xs):
                xs = sorted(float(x) for x in xs); return xs[len(xs) // 2] if xs else None
            print(f"  {k:14} rug={med(r['rf'][k] for r in rows if r['rug'])} win={med(r['rf'][k] for r in rows if r['win'])} other={med(r['rf'][k] for r in rows if not r['rug'] and not r['win'])}")
    json.dump({"res": res, "rows": {d: [{**r, "fams": sorted(r["fams"])} for r in v] for d, v in data.items()}},
              open(R.TAPES / "rug_study.json", "w"), default=str)


if __name__ == "__main__":
    main()
