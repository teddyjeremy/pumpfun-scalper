#!/usr/bin/env python3
"""Price quote-coin tapes in USD and backtest them with the same rules as SOL coins (no RPC).
USD spot = (vq / 10^dq) / (vt / 1e6) * quoteUSD(bt); trade USD = qamt/10^dq * quoteUSD(bt); mcap = spot * 1e9.
quoteUSD: stables = $1; others = GeckoTerminal 15-min close of the quote's top pool, linearly interpolated."""
import bisect, csv, glob, gzip, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; SCREEN = HERE.parent
sys.path[:0] = [str(SCREEN)]
import scalp_setups as S
import scalp_run as SR
META = json.loads((HERE / "quotes_meta.json").read_text()) if (HERE / "quotes_meta.json").exists() else {}
_PX = {}
PX_FLAG = {}
def quote_px_fn(m):
    if m in _PX: return _PX[m]
    g = META.get(m, {})
    if g.get("category") == "stable":
        fn = (lambda t: 1.0); why = None
    else:
        p = HERE / "quotes" / f"{m}.json"
        fn = None; why = "no_price_history"
        if p.exists():
            bars = json.load(open(p)).get("bars15m") or []
            if len(bars) >= 20:
                ts = [b[0] + 900 for b in bars]; cl = [float(b[4]) for b in bars]   # close at bar end
                def fn(t, ts=ts, cl=cl):
                    i = bisect.bisect_left(ts, t)
                    if i <= 0: return cl[0]
                    if i >= len(ts): return cl[-1]
                    w = (t - ts[i - 1]) / (ts[i] - ts[i - 1]); return cl[i - 1] * (1 - w) + cl[i] * w
                why = None
                if max(b[0] for b in bars) < 1795035600: fn = None; why = "stale_history"
    if fn is None and g.get("price_now") and g.get("category") in ("tokenized_stock", "other_token"):
        try:
            if float(g.get("reserve_usd") or 0) >= 100000:
                c = float(g["price_now"]); fn = (lambda t, c=c: c); why = None; PX_FLAG[m] = "const_price_now"
        except Exception: pass
    _PX[m] = (fn, why); return _PX[m]
def decimals(m):
    d = META.get(m, {}).get("decimals")
    return int(d) if d is not None else None
def price_tape(blob, qmint, creator=None):
    fn, why = quote_px_fn(qmint); dq = decimals(qmint)
    if fn is None or dq is None: return None, why or "no_decimals"
    tr = []
    for x in blob["trades"]:
        if x.get("vq") is None or not x.get("vt") or x.get("qmint") != qmint: continue
        q = fn(x["bt"])
        spot = (x["vq"] / 10 ** dq) / (x["vt"] / 1e6) * q
        tr.append({"t": x["t"], "bt": x["bt"], "slot": x["slot"], "side": x["side"], "usd": (x["qamt"] or 0) / 10 ** dq * q,
                   "spot": spot, "trader": x["trader"], "tok": x["tok"] / 1e6, "sol": 0.0, "create": x.get("create")})
    tr.sort(key=lambda r: (r["slot"], r["bt"]))
    return tr, None
FEE, GAS = S.FEE, S.GAS_USD
def net_at(mult, stake):
    return stake * (mult * (1 - FEE) ** 2 - 1) - GAS
SETUPS = {
    "reclaim (I+C recl25 L1) + G fast-inval, TP1.5, <=120s": ("I_C_recl25_L1", "G_tp1.5_h120_fade_inv"),
    "breakout (B L2) + G fast-inval, TP1.7, <=60s": ("B_brk_L2", "G_tp1.7_h60_fade_inv"),
    "band entry (K r1 L1) TP1.5 <=120s fade": ("K_band_r1_L1", "tp1.5_h120_fade"),
    "band entry (K r2 L2) TP1.7 <=120s stall20": ("K_band_r2_L2", "tp1.7_h120_stall20_fade"),
    "band entry (K r1 L0) TP1.5 <=120s fade": ("K_band_r1_L0", "tp1.5_h120_fade"),
}
def strict_v0(f, creator, band=(5000, 40000)):
    """Strict v0: first volume-live mid-tape (30<t<=300) touch of a >=30% dip from the running high, VA (15s >=8 trades,
    >=$200), cum >= $10k vol / 300 trades / 150 wallets, mcap in band. Annotates v1.1/v1.2/v1.3 skips."""
    for i in range(f.n):
        if not (30 < f.t[i] <= 300): continue
        if f.high[i] <= 0 or 1 - f.spot[i] / f.high[i] < 0.30: continue
        w = S.win(f, i, 15)
        if w["n"] < 8 or w["vol"] < 200: continue
        # first volume-live mid-tape 30% touch = the v0 entry; floors are checked THERE (no retry), as in prescreen.py
        mc = f.spot[i] * 1e9
        fails = []
        if f.cum_vol[i] < 10000: fails.append("low_vol")
        if f.cum_n[i] < 300: fails.append("low_trades")
        if f.cum_w[i] < 150: fails.append("low_wallets")
        if not (band[0] <= mc <= band[1]): fails.append("mcap")
        if fails: return {"fail": fails, "t": f.t[i], "mcap": mc}
        # dev sold since the high?
        dev_sold = False
        if creator:
            j = bisect.bisect_left(f.bt, f.t_high[i])
            dev_sold = any((not f.side_buy[k]) and f.trader[k] == creator for k in range(j, i + 1))
        return {"i": i, "t": f.t[i], "mcap": mc, "drop": 1 - f.spot[i] / f.high[i], "skip_v11": f.top_seller_share[i] > 0.5,
                "skip_v12": (f.bt[i] - f.t_high[i]) > 28, "skip_v13": dev_sold}
    return None
def run_coin(trades, creator, cover_t=None):
    """cover_t: last second covered by a truncated tape (None = full t0..t0+480). Results that would need data past
    the coverage are dropped (marked in out['dropped'])."""
    f = S.build_features(trades); f.creator = creator
    out = {"base": S.base_rate(f), "base17": S.base_rate(f, mult=1.7), "setups": {}, "v0": None, "n_tr": f.n, "dropped": []}
    if cover_t is not None:
        t_touch = next((f.t[i] for i in range(f.n) if 30 < f.t[i] <= 300 and 5000 <= f.spot[i] * 1e9 <= 10000), None)
        for k in ("base", "base17"):
            if out[k]["in_band"] and not out[k]["first_hit"] and (t_touch is None or t_touch + 123 > cover_t):
                out[k] = dict(out[k], in_band=False, unknown=True); out["dropped"].append(k)
    for name, (en, ex) in SETUPS.items():
        fam, p = SR.ENTRIES[en]
        r = S.run_coin(f, fam, p, {ex: SR.EXITS[ex]})
        if r and cover_t is not None and r["entry_t"] + r["ex"][ex]["hold_s"] > cover_t - 2:
            out["dropped"].append(name); r = None
        if r:
            e = r["ex"][ex]
            out["setups"][name] = {"entry_t": r["entry_t"], "entry_mcap": r["entry_mcap"], "mult": e["mult"], "exit": e["exit"],
                                   "hold": e["hold_s"], "net1": net_at(e["mult"], 1), "net5": net_at(e["mult"], 5), "mae": e["mae"], "mfe": e["mfe"],
                                   "rug_any": any(bool(v) for v in []), "t15": r.get("t_to_1.5")}
    v = strict_v0(f, creator)
    out["v0_fail"] = v.get("fail") if v and "fail" in v else None
    if v and "fail" in v: v = None
    if v:
        ent_px, ei, ebt = S.fill_px(f, f.bt[v["i"]], v["i"])
        v_ent = S.win(f, v["i"], 15)["vol"]
        for k, x in (("tp1.5_h120_fade", dict(tp=1.5, hold=120, fade=True)), ("tp1.7_h120_fade", dict(tp=1.7, hold=120, fade=True))):
            e = S.simulate_exit(f, ei, ebt, ent_px, v_ent, x)
            if cover_t is not None and (ebt - f.bt[0] + f.t[0]) + e["hold_s"] > cover_t - 2: v["incomplete"] = True
            v[k] = {"mult": e["mult"], "exit": e["exit"], "hold": e["hold_s"], "net1": net_at(e["mult"], 1), "net5": net_at(e["mult"], 5)}
        v["in_5_10k"] = 5000 <= ent_px * 1e9 <= 10000
        out["v0"] = v
    return out
