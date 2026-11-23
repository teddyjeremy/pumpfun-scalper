#!/usr/bin/env python3
"""Evaluate rulebook v2 Lane B ("steady + fresh", TP1.5 / 90 s / invalidation) on the paper trader's
UNBIASED band tapes (every coin touching $5-10k by t+300, incl. coins that die).  Research only, offline.

Usage:
  python3 screen/v2/eval_band_tapes.py                       # all days under paper/logs/band_tapes
  python3 screen/v2/eval_band_tapes.py --days 2026-11-24 2026-11-25
  python3 screen/v2/eval_band_tapes.py --lag-aware            # entry/exit fills also wait for the receive lag of the trigger trade
  python3 screen/v2/eval_band_tapes.py --rule band            # guards only (band entry baseline)
  python3 screen/v2/eval_band_tapes.py --csv out.csv          # per-trade rows

Costs: 1.25% fee each way + $0.0285 gas per round trip ($1 stake); fill = first trade >= 1 s after the trigger
(scalp_setups.fill_px).  Per-trade groups:
  on_time       coin tape complete (no gap anywhere t0..t+600, early trades present) AND <=10% trades >5 s late
  complete_late coin tape complete, but the public feed lagged (live decisions would have been late; try --lag-aware)
  window_clean  coin has a gap somewhere, but early trades are present and no gap touches [entry-60 s, exit+5 s]
  gap_flagged   everything else (gap inside the trade window, early trades missing after late adoption without
                backfill, launched before the trader started, or a truncated tape)
"""
import argparse, bisect, collections, glob, gzip, json, math, os, statistics as st, sys
sys.path.insert(0, "/workspace/pumpfun/screen")
import scalp_setups as S

BASE = "/workspace/pumpfun/paper/logs/band_tapes"
EXIT = dict(tp=1.5, hold=90, fade=False, inval_low=True, inval_sec=60, inval_flow=True)   # X7_tp15_h90_inv
MAX_ENTRIES = 2
GROUPS = ("on_time", "complete_late", "window_clean", "gap_flagged")


def load(path):
    with gzip.open(path, "rt") as f:
        lines = f.read().splitlines()
    meta = json.loads(lines[0])["_meta"]
    rows = [json.loads(x) for x in lines[1:]]
    return meta, rows


def to_trades(meta, rows):
    t0 = meta["t0"]
    out = []
    for r in rows:
        if r.get("unpriced") or not r.get("mcap") or r.get("bt") is None:
            continue
        out.append({"t": r["bt"] - t0, "bt": r["bt"], "slot": r.get("slot"), "side": r["side"], "usd": r.get("usd") or 0.0,
                    "spot": r["mcap"] / 1e9, "trader": r.get("wallet"), "tok": r.get("tok") or 0.0, "sol": r.get("sol_eq") or 0.0,
                    "lag": r.get("lag")})
    out.sort(key=lambda x: (x["bt"], x["slot"] or 0))
    return out


def guards(f, i, mc, dip, cn):
    """Rulebook v2 standing guards 1-5."""
    if not (S.ENTRY_MIN_T < f.t[i] <= S.ENTRY_MAX_T and 5000 <= mc <= 10000):
        return False
    if cn < 300 or not S.not_crash(f, i):
        return False
    if dip >= 0.10 and f.top_seller_share[i] > 0.5:          # v1.1
        return False
    cre = f.creator
    if cre:
        th = f.t_high[i]
        lo = bisect.bisect_left(f.bt, th, 0, i + 1)
        if any(f.trader[k] == cre and not f.side_buy[k] for k in range(lo, i + 1)):   # v1.3 dev sold since high
            return False
    return True


def lane_b(f, i):
    if not S.steady_curve(f, i):
        return False
    w30 = S.win(f, i, 30)
    if w30["new_buyers"] < 15:
        return False
    lo60 = bisect.bisect_right(f.bt, f.bt[i] - 60, 0, i + 1)
    n60 = i + 1 - lo60
    return (len({f.trader[k] for k in range(lo60, i + 1)}) / n60 if n60 else 1.0) >= 0.5


def coin_trades(meta, rows, rule, lag_aware):
    tr = to_trades(meta, rows)
    if len(tr) < 20:
        return []
    f = S.build_features(tr); f.creator = meta.get("creator")
    lags = [x["lag"] for x in tr if x.get("spot")]
    out = []; nxt = -1; k = 0
    for i in range(f.n):
        if f.bt[i] < nxt:
            continue
        if not (S.ENTRY_MIN_T < f.t[i] <= S.ENTRY_MAX_T):
            continue
        if i + 1 < f.n and f.bt[i + 1] == f.bt[i]:
            continue      # decide on the last trade of each second
        mc = f.spot[i] * 1e9
        H = f.high[i]; dip = 1 - f.spot[i] / H if H else 0
        if not guards(f, i, mc, dip, f.cum_n[i]):
            continue
        if S.rug_feats(f, i)["creator_hold"] > 0.08:
            continue
        if rule == "laneb" and not lane_b(f, i):
            continue
        lo30 = bisect.bisect_left(f.bt, f.bt[i] - 30, 0, i + 1)
        swing = min(f.spot[lo30:i + 1])
        delay = 0
        if lag_aware and lags[i] is not None:
            delay = max(0, math.ceil(lags[i]))     # signal only actionable once the trigger trade was received
        ent_px, ei, ebt = S.fill_px(f, f.bt[i] + delay, i)
        x = dict(EXIT); x["inval_low"] = swing
        e = S.simulate_exit(f, ei, ebt, ent_px, S.win(f, i, 15)["vol"], x)
        net = e["net"]
        if lag_aware and delay:   # exit decision also seen late: re-price the exit `delay` s later
            xbt = ebt + e["hold_s"]
            px2, _, _ = S.fill_px(f, xbt + delay - S.FILL_DELAY, max(ei, bisect.bisect_right(f.bt, xbt) - 1))
            net = S.net_pnl(ent_px, px2)
        out.append({"mint": meta["mint"], "symbol": meta.get("symbol"), "quote": meta.get("quote_symbol"),
                    "on_time": meta.get("on_time", 0), "group": grp(meta, f.t[i] - 60, f.t[i] + (ebt - f.bt[i]) + e["hold_s"] + 5), "flags": ";".join(meta.get("flags") or []),
                    "entry_bt": ebt, "entry_t": f.t[i], "mcap": round(mc), "net": net, "hold_s": e["hold_s"],
                    "exit": e["exit"], "mfe": e["mfe"], "mae": e["mae"]})
        k += 1
        nxt = ebt + e["hold_s"] + 1
        if k >= MAX_ENTRIES:
            break
    return out


def grp(meta, a=None, b=None):
    if meta.get("on_time"):
        return "on_time"
    if meta.get("complete"):
        return "complete_late"
    if a is not None and meta.get("early_trades_ok") and meta.get("end_reason") not in ("shutdown", "tail_full") \
            and "gaps_tplus" in meta and not any(x < b and y > a for x, y in meta["gaps_tplus"]):
        return "window_clean"
    return "gap_flagged"


def max_dd(xs):
    c = p = dd = 0.0
    for x in xs:
        c += x; p = max(p, c); dd = min(dd, c - p)
    return dd


def report(label, T, hours):
    if not T:
        print(f"  {label:12s} trades 0"); return
    T = sorted(T, key=lambda r: r["entry_bt"])
    n = [r["net"] for r in T]
    per_day = len(T) / hours * 24 if hours > 0 else float("nan")
    ex = collections.Counter(r["exit"] for r in T)
    print(f"  {label:12s} trades {len(T):4d}  (~{per_day:5.0f}/day over {hours:.1f} h)  win {sum(x > 0 for x in n) / len(n):4.0%}"
          f"  avg ${st.mean(n):+.4f}  med ${st.median(n):+.4f}  total ${sum(n):+.2f}  worst ${min(n):+.3f}  best ${max(n):+.3f}"
          f"  maxDD ${max_dd(n):+.2f}  se ${st.pstdev(n) / math.sqrt(len(n)):.3f}  exits {dict(ex)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--rule", choices=["laneb", "band"], default="laneb")
    ap.add_argument("--lag-aware", action="store_true")
    ap.add_argument("--csv")
    a = ap.parse_args()
    days = a.days or sorted(os.path.basename(d) for d in glob.glob(f"{a.base}/20*"))
    allrows = []
    print(f"rule={a.rule} exit=TP1.5/90s/inval  fees 1.25%x2 + $0.0285 gas  lag_aware={a.lag_aware}")
    for d in days:
        files = sorted(glob.glob(f"{a.base}/{d}/*.jsonl.gz"))
        T = []; coins = collections.Counter(); launches = []
        for p in files:
            try:
                meta, rows = load(p)
            except Exception:
                continue
            g = grp(meta)
            coins[g] += 1; coins["quote:" + str(meta.get("quote_symbol"))] += 1
            launches.append(meta["t0"])
            T += coin_trades(meta, rows, a.rule, a.lag_aware)
        hours = (max(launches) - min(launches)) / 3600 if len(launches) > 1 else 0.0
        print(f"\n{d}: band tapes {len(files)}  {dict(coins)}  launch span {hours:.1f} h")
        report("ALL", T, hours)
        for g in GROUPS:
            report(g, [r for r in T if r["group"] == g], hours)
        nonsol = [r for r in T if r["quote"] not in ("SOL", None)]
        if nonsol:
            report("non-SOL", nonsol, hours)
        for r in T:
            r["day"] = d
        allrows += T
    if len(days) > 1:
        print("\nPOOLED")
        for g in GROUPS:
            report(g, [r for r in allrows if r["group"] == g], float("nan"))
    if a.csv and allrows:
        import csv
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(allrows[0].keys())); w.writeheader(); w.writerows(allrows)
        print("wrote", a.csv)


if __name__ == "__main__":
    main()
