"""SHADOW quick-flip strategies for the live paper trader (paper only: no wallets, no signing, no keys).

Signals and exit rules come from screen/scalp_setups.py, imported unchanged:
  IC_reclaim : I+C steady reclaim (C_recl25_L1 + steady=True) with G exit: TP 1.5x, hold 120s, fade, invalidation
  B_breakout : B_brk_L2 with G exit: TP 1.7x, hold 60s, fade, invalidation
Each runs twice: without the rug layer (rug={}) and with it (scalp_run.RUG). Hard rules come from the params:
entry t+30..300s (S.ENTRY_MIN_T/MAX_T), entry mcap $5k-$10k (BAND).

Execution is measured like v1.1: we decide at wall time D (when our periodic evaluation saw the signal), buy lands at
D+0.4/1/2s against the curve state we had received by then; exit triggers come from S.simulate_exit (run on the
tape with our +1s fill as entry), and each exit fills at trigger-receive +0.4/1/2s.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/workspace/pumpfun/screen")
import scalp_setups as S  # noqa: E402

BAND = dict(mcap_min=5000, mcap_max=10000)
L1 = dict(cum_vol=3000, cum_n=100, cum_w=50, live_n=5, **BAND)
L2 = dict(cum_vol=5000, cum_n=150, cum_w=80, live_n=5, **BAND)
RUG = {"creator_hold": (">", 0.08), "bundle_hold": (">", 0.25), "uniq60": ("<", 0.35)}  # = scalp_run.RUG
_INV = dict(inval_low=True, inval_sec=60, inval_flow=True)

STRATS = {
    "IC_reclaim": {"fam": "C", "p": dict(dip=0.25, dip_max=0.6, hold_low=10, retrace=0.5, live_n10=4, steady=True, **L1),
                   "exit": dict(tp=1.5, hold=120, fade=True, **_INV)},
    "B_breakout": {"fam": "B", "p": dict(surge=2.0, high_age=10, bs_ratio=1.5, min_buy10=150, **L2),
                   "exit": dict(tp=1.7, hold=60, fade=True, **_INV)},
}
TRACKS = {}
for _k, _v in STRATS.items():
    TRACKS[_k] = {**_v, "rug": {}}
    TRACKS[_k + "+rug"] = {**_v, "rug": RUG}
STAKES = (1.0, 3.0, 5.0)
FEE = S.FEE
GAS = S.GAS_USD  # flat per round trip
DELAYS = (0.4, 1.0, 2.0)

# capture the trigger passed to fill_px by simulate_exit (wrapper only records args; logic unchanged)
_orig_fill_px = S.fill_px
_last_fill_call = {}


def _fill_px_spy(f, trig_bt, after_i):
    _last_fill_call["trig_bt"], _last_fill_call["trig_i"] = trig_bt, after_i
    return _orig_fill_px(f, trig_bt, after_i)


S.fill_px = _fill_px_spy


def tape_of(c) -> tuple:
    tr = [x for x in c.trades if x.get("spot_usd")]
    tape = S.from_paper_tape({"t0": c.t0, "mint": c.mint, "symbol": c.symbol, "creator": c.creator, "trades": tr})
    f = S.build_features(tape["trades"])
    f.creator = c.creator
    return f, tr


def curve_buy(tr, usd):
    vs, vt = tr["vs"], tr["vt"]
    net_l = usd * (1 - FEE) / tr["sol_usd"] * 1e9
    tok = (vt - vs * vt / (vs + net_l)) / 1e6
    return tok, usd * (1 - FEE) / tok


def curve_sell(tr, tok):
    vs, vt = tr["vs"], tr["vt"]
    sol = (vs - vs * vt / (vt + tok * 1e6)) / 1e9
    return sol * tr["sol_usd"]


def state_at(trs, wall, min_idx):
    j = min_idx
    for i in range(min_idx, len(trs)):
        if trs[i]["recv"] <= wall:
            j = i
        else:
            break
    return j


class ScalpShadow:
    def __init__(self, trader, log_fn, eat_fn):
        self.T = trader
        self.log = log_fn
        self.eat = eat_fn

    # called from on_trade (cheap pre-filter); evaluation happens in tick()
    @staticmethod
    def mark(c, tr):
        t = tr["t_plus_sec"]
        mc = tr.get("mcap_usd") or 0
        if S.ENTRY_MIN_T < t <= S.ENTRY_MAX_T and 5000 <= mc <= 10000 and len(c.trades) >= 100 \
                and c.cum_vol >= 3000 and len(c.wallets) >= 50:
            if getattr(c, "scalp", None) is None:
                c.scalp = {"checked": {}, "pos": {}}
            if len(c.scalp["pos"]) < len(TRACKS):
                c.scalp["dirty"] = True

    def tick(self):
        t_now = time.time()
        for c in list(self.T.coins.values()):
            sc = getattr(c, "scalp", None)
            if not sc or not sc.get("dirty") or c.finalized:
                continue
            if time.time() - sc.get("last_eval", 0) < 0.25:
                continue
            sc["dirty"] = False
            sc["last_eval"] = time.time()
            try:
                f, trs = tape_of(c)
            except Exception:
                continue
            for name, tk in TRACKS.items():
                if name in sc["pos"]:
                    continue
                fn = S.SIGNALS[tk["fam"]]
                p = tk["p"]
                start = sc["checked"].get(name, 0)
                hit = None
                for i in range(start, f.n):
                    if not (S.ENTRY_MIN_T < f.t[i] <= S.ENTRY_MAX_T):
                        continue
                    if fn(f, i, p) and (not p.get("steady") or S.steady_curve(f, i)) and not S.rug_flag(f, i, tk["rug"]):
                        hit = i
                        break
                sc["checked"][name] = f.n
                if hit is None:
                    continue
                decide = time.time()
                rf = S.rug_feats(f, hit)
                st = trs[hit]
                pos = {
                    "track": name, "strategy": name.replace("+rug", ""), "rug_layer": int(bool(tk["rug"])),
                    "mint": c.mint, "symbol": c.symbol, "name": c.name, "launch_time_eat": self.T_eat(c.t0),
                    "sig_idx": hit, "sig_sig": st["signature"], "sig_t_plus": f.t[hit], "sig_bt": st["bt"],
                    "sig_recv": st["recv"], "sig_recv_eat": self.T_eat(st["recv"]), "decide": decide,
                    "detect_latency_s_vs_blocktime": round(st["recv"] - st["bt"], 3),
                    "decide_delay_ms": round((decide - st["recv"]) * 1000, 1),
                    "feed_lag_med10s_at_signal": self.T.feed_lag_now(),
                    "late": int(st["recv"] - st["bt"] > 5.0),
                    "sig_spot_usd": st["spot_usd"], "sig_mcap_usd": round(st["spot_usd"] * 1e9, 1),
                    "sig_dip_from_high": round(1 - f.spot[hit] / f.high[hit], 4) if f.high[hit] else None,
                    "cum_vol": round(f.cum_vol[hit], 1), "cum_trades": f.cum_n[hit], "cum_wallets": f.cum_w[hit],
                    "rug_creator_hold": round(rf["creator_hold"], 4), "rug_bundle_hold": round(rf["bundle_hold"], 4),
                    "rug_uniq60": round(rf["uniq60"], 4), "rug_top1_drop": round(rf["top1_drop"], 4),
                    "rug_drain15": round(rf["drain15"], 4), "rug_creator_sold30": int(bool(rf["creator_sold30"])),
                    "flag_creator_gt8": int(rf["creator_hold"] > 0.08), "flag_bundle_gt25": int(rf["bundle_hold"] > 0.25),
                    "flag_wash_uniq60_lt35": int(rf["uniq60"] < 0.35),
                    "rug_flag_any": int(S.rug_flag(f, hit, RUG)),
                    "feed_gap_before": int(c.gap),
                    "finalize_after": decide + 1.0 + tk["exit"]["hold"] + 10,
                }
                sc["pos"][name] = pos
                self.T.bump(f"scalp_signal:{name}", ts=st["bt"])
                self.log(f"SHADOW {name} {c.symbol} {c.mint[:8]} t+{f.t[hit]}s mcap ${pos['sig_mcap_usd']:,.0f} "
                           f"rugflag={pos['rug_flag_any']} decide+{pos['decide_delay_ms']:.0f}ms")

    def T_eat(self, ts):
        return self.eat(ts)

    @staticmethod
    def pending(c) -> bool:
        sc = getattr(c, "scalp", None)
        if not sc:
            return False
        return any(time.time() < p["finalize_after"] for p in sc["pos"].values())

    def finalize(self, c) -> list:
        sc = getattr(c, "scalp", None)
        out = []
        if not sc or not sc["pos"]:
            return out
        f, trs = tape_of(c)
        seq_to_fi = {x["seq"]: i for i, x in enumerate(trs)}
        for name, pos in sc["pos"].items():
            try:
                out.append(self._sim(c, f, trs, seq_to_fi, name, pos))
            except Exception as e:
                pos["error"] = f"{type(e).__name__}: {e}"
                out.append(pos)
        return out

    def _sim(self, c, f, trs, seq_to_fi, name, pos):
        tk = TRACKS[name]
        d = dict(pos)
        hit = pos["sig_idx"]
        sig_spot = pos["sig_spot_usd"]
        fills = {}
        for dl in DELAYS:
            j = state_at(trs, pos["decide"] + dl, hit)
            ft = trs[j]
            k = f"d{int(dl * 1000)}"
            fills[k] = j
            tok1, eff1 = curve_buy(ft, 1.0)
            d[f"entry_{k}_trades_between"] = j - hit
            d[f"entry_{k}_spot_move_pct"] = round((ft["spot_usd"] / sig_spot - 1) * 100, 3)
            d[f"entry_{k}_slip_pct"] = round((eff1 / sig_spot - 1) * 100, 3)
            d[f"entry_{k}_mcap"] = round(ft["spot_usd"] * 1e9, 1)
            d[f"entry_{k}_in_band"] = int(5000 <= ft["spot_usd"] * 1e9 <= 10000)
        # scalp_setups' own +1s block-time fill (reference)
        rpx, _ri, _rbt = S.fill_px(f, f.bt[hit], hit)
        d["ref_scalp_fill_slip_pct"] = round((rpx / sig_spot - 1) * 100, 3)
        # primary entry = our +1s fill
        ej = fills["d1000"]
        et = trs[ej]
        ent_px = et["spot_usd"]
        v_ent = S.win(f, hit, S.FADE_CFG["vol_window"])["vol"]
        x = dict(tk["exit"])
        if x.get("inval_low") is True:
            x = dict(x, inval_low=f.low_since_high[hit] * x.get("inval_buf", 1.0))
        d["inval_low_usd"] = x.get("inval_low")
        _last_fill_call.clear()
        res = S.simulate_exit(f, ej, f.bt[ej], ent_px, v_ent, x)
        trig_i = _last_fill_call.get("trig_i", ej)
        trig_bt = _last_fill_call.get("trig_bt")
        trig = trs[trig_i]
        d.update({"exit_reason": res["exit"], "hold_s_blocktime": res["hold_s"], "rule_mult": round(res["mult"], 4),
                  "rule_net_per1": round(res["net"], 4), "mfe_pct": round(res["mfe"] * 100, 2),
                  "mae_pct": round(res["mae"] * 100, 2), "trigger_bt": trig_bt,
                  "trigger_spot_usd": trig["spot_usd"]})
        # timed-out exits (hold/stall) trigger at a clock time, not a trade: wall = entry fill wall + offset
        if res["exit"] in ("hold", "stall") and trig_bt is not None:
            trig_wall = et["recv"] + (trig_bt - f.bt[ej])
        else:
            trig_wall = trig["recv"]
        d["trigger_wall_after_entry_s"] = round(trig_wall - et["recv"], 2)
        # realistic nets: buy at our +1s fill curve state, sell at trigger-receive + delay
        toks = {s: curve_buy(et, s)[0] for s in STAKES}
        for dl in DELAYS:
            k = f"d{int(dl * 1000)}"
            j = state_at(trs, trig_wall + dl, trig_i)
            xt = trs[j]
            gross1 = curve_sell(xt, toks[1.0])
            d[f"exit_{k}_slip_pct"] = round(((gross1 / toks[1.0]) / trig["spot_usd"] - 1) * 100, 3)
            d[f"exit_{k}_trades_between"] = j - trig_i
            for s in STAKES:
                g = curve_sell(xt, toks[s]) * (1 - FEE)
                d[f"net_{k}_per{int(s)}"] = round(g - s - GAS, 4)
            if dl == 1.0:
                d["hold_s_wall"] = round(xt["recv"] - et["recv"], 2)
        d["net_per1"] = d["net_d1000_per1"]
        d["net_per3"] = d["net_d1000_per3"]
        d["net_per5"] = d["net_d1000_per5"]
        d["net_pct_per3"] = round(d["net_per3"] / 3 * 100, 2)
        d["net_pct_per5"] = round(d["net_per5"] / 5 * 100, 2)
        d["feed_gap_during"] = int(c.gap)
        d.pop("finalize_after", None)
        return d
