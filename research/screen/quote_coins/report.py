#!/usr/bin/env python3
"""Build screen/cache/quote_coins_report.md from class_*.csv, plan_*.json, tapes/ (no RPC)."""
import csv, glob, gzip, json, statistics as st, sys, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent; SCREEN = HERE.parent
sys.path[:0] = [str(HERE), str(SCREEN)]
import qc_analyze as Q
import scalp_setups as S
import scalp_run as SR
DAYS_ALL = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"]
DOW = {"2026-11-16": "Mon", "2026-11-17": "Tue", "2026-11-18": "Wed", "2026-11-19": "Thu", "2026-11-20": "Fri", "2026-11-21": "Sat", "2026-11-22": "Sun", "2026-11-23": "Mon (today, to ~16:20)"}
def fm(x, n=3, pct=False):
    if x is None: return "–"
    return f"{x*100:.0f}%" if pct else (f"{x:+.{n}f}" if isinstance(x, float) else str(x))
def counts():
    rows = []; perq = collections.Counter(); qday = collections.defaultdict(collections.Counter); grad = {}
    for d in DAYS_ALL:
        p = HERE / f"class_{d}.csv"
        if not p.exists(): continue
        cl = list(csv.DictReader(open(p)))
        n_total = None
        for src in (SCREEN / f"launches_{d}.csv", HERE / f"launches_{d}.csv"):
            if src.exists():
                n_total = len({r["mint"] for r in csv.DictReader(open(src)) if r.get("mint")}); break
        nq = [r for r in cl if r["quote_mint"] not in ("SOL", "", "SOL?short")]
        val = [r for r in cl if r["cand"] == "0"]
        val_nonsol = sum(1 for r in val if r["quote_mint"] not in ("SOL", "", "SOL?short"))
        cats = collections.Counter(Q.META.get(r["quote_mint"], {}).get("category", "unknown") for r in nq)
        for r in nq: perq[r["quote_mint"]] += 1; qday[d][r["quote_mint"]] += 1
        # graduation (curve complete flag now)
        g_nq = sum(1 for r in nq if r["complete"] == "1")
        sol_c = [r for r in cl if r["quote_mint"] == "SOL" and r["cand"] == "1"]
        sol_v = [r for r in val if r["quote_mint"] == "SOL"]
        n_noncand = n_total - sum(1 for r in cl if r["cand"] == "1") if n_total else None
        w = (n_noncand / len(val)) if val and n_noncand else 0
        g_sol = sum(1 for r in sol_c if r["complete"] == "1") + w * sum(1 for r in sol_v if r["complete"] == "1")
        n_sol = len(sol_c) + w * len(sol_v)
        grad[d] = (g_nq, len(nq), g_sol, n_sol)
        missing = sum(1 for r in cl if r["quote_mint"] == "" and r["cand"] == "1")
        rows.append((d, n_total, len(nq), cats, len(val), val_nonsol, missing))
    return rows, perq, qday, grad
def agg(trs, ws):
    if not trs: return None
    nets = [t["net1"] for t in trs]; W = sum(ws)
    wins = [t for t in trs if t["net1"] > 0]; losses = [t for t in trs if t["net1"] <= 0]
    return {"n": len(trs), "est_day": W, "wins_day": sum(w for t, w in zip(trs, ws) if t["net1"] > 0),
            "win": len(wins) / len(trs), "exp1": sum(n * w for n, w in zip(nets, ws)) / W, "exp1_plain": st.mean(nets),
            "exp5": sum(t["net5"] * w for t, w in zip(trs, ws)) / W, "avg_win": st.mean([t["net1"] for t in wins]) if wins else None,
            "avg_loss": st.mean([t["net1"] for t in losses]) if losses else None, "worst": min(nets),
            "lose20": sum(1 for n in nets if n < -0.2), "med_hold": st.median([t["hold"] for t in trs])}
def eval_coins(coins):
    """coins: list of dicts with f(trades), creator, w, quote."""
    res = {"setups": collections.defaultdict(lambda: ([], [])), "base": [], "v0": []}
    for c in coins:
        r = Q.run_coin(c["trades"], c["creator"], c.get("cover"))
        res.setdefault("dropped", collections.Counter()).update(r.get("dropped", []))
        for k, s in r["setups"].items():
            res["setups"][k][0].append(s); res["setups"][k][1].append(c["w"])
        res["base"].append((r["base"], r["base17"], c["w"]))
        if r["v0"] and not r["v0"].get("incomplete"): res["v0"].append((c, r["v0"]))
        elif r["v0"]: res.setdefault("dropped", collections.Counter())["v0"] += 1
    return res
def base_summary(base):
    band = [(b, b17, w) for b, b17, w in base if b["in_band"]]
    if not band: return None
    fh = [b for b, _, _ in band if b["first_hit"]]
    return {"band_sampled": len(band), "band_est": sum(w for *_, w in band), "first_share": len(fh) / len(band),
            "any_share": sum(1 for b, _, _ in band if b["any_hit"]) / len(band),
            "first17_share": sum(1 for _, b17, _ in band if b17["first_hit"]) / len(band),
            "med_s": st.median([b["first_hit_s"] for b in fh]) if fh else None, "hits_est": sum(w for b, _, w in band if b["first_hit"])}
def load_nonsol(day):
    P = json.load(open(HERE / f"plan_{day}.json"))
    have = collections.Counter(); coins = []; skipped = collections.Counter(); qcat = collections.Counter()
    for m, x in P["mints"].items():
        p = HERE / "tapes" / day / f"{m}.json.gz"
        if not p.exists(): continue
        b = json.load(gzip.open(p, "rt"))
        tr, why = Q.price_tape(b, x["quote"], x["creator"])
        if tr is None or len(tr) < 5:
            skipped[why or "few_trades"] += 1; continue
        have[x["tier"]] += 1
        coins.append({"mint": m, "trades": tr, "creator": x["creator"], "tier": x["tier"], "quote": x["quote"], "migrated": b.get("migrated_t"), "trunc": b.get("truncated"),
                      "cover": (b["trades"][-1]["t"] if b.get("truncated") and b["trades"] else None)})
    for c in coins:
        c["w"] = P["tiers"][c["tier"]]["universe"] / have[c["tier"]]
        qcat[Q.META.get(c["quote"], {}).get("category")] += 1
    return coins, P, skipped, qcat
def load_sol(day):
    coins, plan, tw = SR.load_day(day)
    out = []
    for c in coins:
        f = c["f"]
        tr = [{"t": f.t[i], "bt": f.bt[i], "slot": f.slot[i], "side": "buy" if f.side_buy[i] else "sell", "usd": f.usd[i], "spot": f.spot[i],
               "trader": f.trader[i], "tok": f.tok[i], "sol": f.sol[i]} for i in range(f.n)]
        out.append({"mint": c["mint"], "trades": tr, "creator": f.creator, "w": c["w"]})
    return out
if __name__ == "__main__":
    print(json.dumps({"counts": [(r[0], r[1], r[2]) for r in counts()[0]]}))
