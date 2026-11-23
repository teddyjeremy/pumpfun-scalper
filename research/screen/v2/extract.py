#!/usr/bin/env python3
"""v2 daily-volume research: candidate entries (1 per second, t+30..300s, mcap $5k-$10k) from cached tapes,
with causal features and outcomes of several dynamic exits. Research only; reads cached tapes, no network."""
import sys, os, glob, gzip, json, bisect, pickle
from multiprocessing import Pool
sys.path.insert(0, "/workspace/pumpfun/screen")
import scalp_setups as S

EXITS = {
 "X1_tp15_h120": dict(tp=1.5, hold=120, fade=True, inval_low=True, inval_sec=60, inval_flow=True),
 "X2_tp15_h60": dict(tp=1.5, hold=60, fade=True, inval_low=True, inval_sec=60, inval_flow=True),
 "X3_tp13_h120": dict(tp=1.3, hold=120, fade=True, inval_low=True, inval_sec=60, inval_flow=True),
 "X5_tp15_pure": dict(tp=1.5, hold=120, fade=False),
 "X6_tp15_flow_be": dict(tp=1.5, hold=120, fade=False, inval_flow=True, be=0.2),
 "X7_tp15_h90_inv": dict(tp=1.5, hold=90, fade=False, inval_low=True, inval_sec=60, inval_flow=True),
}
SCALE = {"first": dict(tp=1.3, hold=120, fade=True, inval_low=True, inval_flow=True),
         "rest": dict(tp=2.0, hold=120, fade=True, inval_low=True, inval_flow=True, trail=(0.3, 0.2))}

def spot_at(f, bt, i):
    k = bisect.bisect_right(f.bt, bt, 0, i + 1) - 1
    return f.spot[k] if k >= 0 else f.spot[0]

def coin(args):
    path, day, weight, src = args
    try:
        b = S.load_tape(path)
        sel_bt = None; track_end = None
        if src == "paper":
            if b.get("quote_mint", "SOL") != "SOL": return []
            dec = b.get("decision") or {}
            if not dec.get("signal_block_time"): return []
            sel_bt = int(dec["signal_block_time"]); track_end = sel_bt + 310
            b = S.from_paper_tape(b)
        f = S.build_features(b["trades"]); f.creator = b.get("creator")
    except Exception as e:
        return []
    if f.n < 20: return []
    rows = []; last_bt = None; band_seen = 0
    for i in range(f.n):
        t = f.t[i]
        if not (S.ENTRY_MIN_T < t <= S.ENTRY_MAX_T): continue
        if i + 1 < f.n and f.bt[i + 1] == f.bt[i]: continue      # last trade of the second
        mc = f.spot[i] * 1e9
        if not (5000 <= mc <= 10000):
            continue
        band_seen += 1
        if src == "paper" and (f.bt[i] < sel_bt or f.bt[i] + 123 > track_end): continue  # causal selection + full 120s tracked
        H = f.high[i]; L = f.low_since_high[i]
        lo30 = bisect.bisect_left(f.bt, f.bt[i] - 30, 0, i + 1)
        swing = min(f.spot[lo30:i + 1])
        w10 = S.win(f, i, 10); w10p = S.win(f, i, 10, lag=10); w30 = S.win(f, i, 30); w60 = S.win(f, i, 60); w15 = S.win(f, i, 15)
        big30 = max((f.usd[k] for k in range(w30["lo"], w30["hi"]) if f.side_buy[k]), default=0.0)
        rf = S.rug_feats(f, i)
        cre = f.creator; th = f.t_high[i]
        dev_drop = any(f.trader[k] == cre and not f.side_buy[k] for k in range(bisect.bisect_left(f.bt, th, 0, i + 1), i + 1)) if cre else False
        r = dict(day=day, src=src, mint=b["mint"], w=weight, i=i, t=t, bt=f.bt[i], mc=mc, band_k=band_seen,
                 dip=1 - f.spot[i] / H if H else 0, depth=1 - L / H if H else 0,
                 secs_high=f.bt[i] - th, low_age=f.bt[i] - f.t_low_since_high[i],
                 recl=(f.spot[i] - L) / (H - L) if H > L else 1.0, high_t=th - (f.bt[i] - t),
                 ret10=f.spot[i] / spot_at(f, f.bt[i] - 10, i) - 1, ret30=f.spot[i] / spot_at(f, f.bt[i] - 30, i) - 1,
                 ret60=f.spot[i] / spot_at(f, f.bt[i] - 60, i) - 1,
                 swing_dd=1 - swing / f.spot[i],
                 b10=w10["buy_usd"], s10=w10["sell_usd"], nb10=w10["nb"], ns10=w10["ns"], nw10=w10["new_buyers"],
                 b10p=w10p["buy_usd"], s10p=w10p["sell_usd"], ns10p=w10p["ns"],
                 b30=w30["buy_usd"], s30=w30["sell_usd"], n30=w30["n"], nw30=w30["new_buyers"],
                 b60=w60["buy_usd"], s60=w60["sell_usd"], n15=w15["n"], v15=w15["vol"],
                 big30=big30 / w30["buy_usd"] if w30["buy_usd"] else 1.0,
                 cv=f.cum_vol[i], cn=f.cum_n[i], cw=f.cum_w[i], top1=f.top_seller_share[i],
                 brk=int(f.spot[i] > f.prev_high[i] > 0), phage=f.prev_high_age[i],
                 steady=int(S.steady_curve(f, i)), notcrash=int(S.not_crash(f, i)), dev_drop=int(dev_drop), **{"rf_" + k: float(v) for k, v in rf.items()})
        ent_px, ei, ebt = S.fill_px(f, f.bt[i], i)
        v_ent = w15["vol"]
        r["ent_px"] = ent_px; r["ebt"] = ebt; r["slip"] = ent_px / f.spot[i] - 1
        ex = {}
        for k, x in EXITS.items():
            x = dict(x)
            if x.get("inval_low") is True: x["inval_low"] = swing
            e = S.simulate_exit(f, ei, ebt, ent_px, v_ent, x)
            ex[k] = (e["net"], e["hold_s"], e["exit"], e["mfe"], e["mae"])
        sc = {"first": dict(SCALE["first"]), "rest": dict(SCALE["rest"])}
        e = S.scale_out(f, ei, ebt, ent_px, v_ent, {"first": dict(SCALE["first"], inval_low=swing), "rest": dict(SCALE["rest"], inval_low=swing)}, swing)
        ex["X4_half13_trail"] = (e["net"], e["hold_s"], e["exit"], e["mfe"], e["mae"])
        r["ex"] = ex
        rows.append(r)
    return rows

def jobs():
    J = []
    base = "/workspace/pumpfun/screen/cache/scalp_tapes"
    for day in ("2026-11-20", "2026-11-21", "2026-11-22"):
        plan = json.load(open(f"{base}/plan_{day}.json"))
        tw = {k: t["weight"] for k, t in enumerate(plan["tiers"])}
        for p in glob.glob(f"{base}/{day}/*.json.gz"):
            m = os.path.basename(p)[:-8]
            tier = plan["mints"].get(m, {}).get("tier")
            J.append((p, day, tw.get(tier, 1.0), "bt"))
    for p in glob.glob("/workspace/pumpfun/paper/logs/tapes/*.json.gz"):
        J.append((p, "2026-11-23", 1.0, "paper"))
    return J

if __name__ == "__main__":
    J = jobs(); out = []
    with Pool(3) as pool:
        for k, rows in enumerate(pool.imap_unordered(coin, J, chunksize=4)):
            out.extend(rows)
            if k % 100 == 0: print(k, len(J), len(out), flush=True)
    pickle.dump(out, open("/workspace/pumpfun/screen/v2/cands.pkl", "wb"))
    print("done", len(out))
