#!/usr/bin/env python3
"""Scalp setup families for Pump.fun launches (research only: no trading, no wallets, no keys).

Reusable on any full trade tape (list of dicts with t, bt, slot, side, usd, spot, trader),
e.g. screen/cache/scalp_tapes/<day>/<mint>.json.gz or the paper trader's live tapes
(adapt with `from_paper_tape`).

Families (entries only between t+30s and t+300s, one entry per coin per setup):
  A  dip      : spot >= D below the running high, activity floors, dip-not-crash guard.
  B  breakout : spot breaks a running high that is >= 10s old with a buy-volume surge.
  C  reclaim  : after a >= D dip to low L that has held >= 10s (higher low), spot reclaims
                the 50% retrace level of the dip with buys > sells in the last 10s.
Exits (dynamic, no hard stop): take-profit multiple, max hold, volume-fade (FADE_CFG of
screen/backtest.py), stall (if not +10% by S seconds), break-even trail, peak trail.

Costs: 1.25% curve fee each way, $0.0285 gas per round trip on a $1 stake.
Fills: entry and exit fill at the curve spot of the first trade >= 1s after the trigger
(if no trade within 2s, the curve state at trigger+1s, i.e. the last spot), matching the
paper trader's +1s fills. Our $1 own price impact (~0.02%) is ignored.
"""
from __future__ import annotations

import bisect
import gzip
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field

FEE = 0.0125
GAS_USD = 0.0285
STAKE = 1.0
FILL_DELAY = 1
ENTRY_MIN_T, ENTRY_MAX_T = 30, 300
FADE_CFG = {"vol_frac": 0.30, "min_trades_15": 3, "sell_buy_ratio": 2.0, "vol_window": 15, "flow_window": 30}


def breakeven_mult(stake: float = STAKE, gas: float = GAS_USD, fee: float = FEE) -> float:
    """Exit/entry price multiple needed to net $0 after fees and gas."""
    return (1.0 + gas / stake) / (1.0 - fee) ** 2


def net_pnl(entry_px: float, exit_px: float, stake: float = STAKE) -> float:
    tokens = stake * (1.0 - FEE) / entry_px
    return tokens * exit_px * (1.0 - FEE) - stake - GAS_USD * stake  # gas scales ~flat; stake=1 here


# ---------------------------------------------------------------- tape + features
def load_tape(path: str) -> dict:
    with gzip.open(path, "rt") as f:
        return json.load(f)


def from_paper_tape(blob: dict) -> dict:
    t0 = blob["t0"]
    tr = [{"t": x["bt"] - t0, "bt": x["bt"], "slot": x.get("slot"), "side": x["side"], "usd": x.get("volume_usd") or 0.0,
           "spot": x.get("spot_usd"), "trader": x.get("trader"), "tok": x.get("token_ui") or 0.0, "sol": x.get("sol_ui") or 0.0}
          for x in blob["trades"]]
    return {"mint": blob["mint"], "symbol": blob.get("symbol"), "t0": t0, "creator": blob.get("creator"), "trades": tr}


@dataclass
class Feat:
    """Per-trade causal features (everything known right after trade i lands)."""
    n: int
    bt: list = field(default_factory=list)
    t: list = field(default_factory=list)
    spot: list = field(default_factory=list)
    side_buy: list = field(default_factory=list)
    usd: list = field(default_factory=list)
    high: list = field(default_factory=list)
    t_high: list = field(default_factory=list)
    prev_high: list = field(default_factory=list)      # running high before trade i
    prev_high_age: list = field(default_factory=list)  # seconds since that high was set
    low_since_high: list = field(default_factory=list)
    t_low_since_high: list = field(default_factory=list)
    cum_vol: list = field(default_factory=list)
    cum_n: list = field(default_factory=list)
    cum_w: list = field(default_factory=list)
    top_seller_share: list = field(default_factory=list)  # top-1 seller share of sell $ since the high


def build_features(trades: list[dict]) -> Feat:
    tr = [x for x in trades if x.get("spot")]
    f = Feat(n=len(tr))
    f.trader = [x.get("trader") for x in tr]; f.tok = [float(x.get("tok") or 0.0) for x in tr]
    f.sol = [float(x.get("sol") or 0.0) for x in tr]; f.slot = [x.get("slot") or 0 for x in tr]
    f.creator = None
    hi = 0.0; thi = 0; lo = math.inf; tlo = 0; cv = 0.0; wallets = set()
    sellers: dict = defaultdict(float); sell_tot = 0.0
    for i, x in enumerate(tr):
        s = float(x["spot"]); bt = int(x["bt"])
        f.prev_high.append(hi); f.prev_high_age.append(bt - thi if hi > 0 else 0)
        if s > hi:
            hi, thi = s, bt; lo, tlo = s, bt; sellers = defaultdict(float); sell_tot = 0.0
        if s < lo:
            lo, tlo = s, bt
        if x["side"] == "sell":
            sellers[x.get("trader")] += x.get("usd") or 0.0; sell_tot += x.get("usd") or 0.0
        cv += x.get("usd") or 0.0
        if x.get("trader"):
            wallets.add(x["trader"])
        f.bt.append(bt); f.t.append(int(x["t"])); f.spot.append(s); f.side_buy.append(x["side"] == "buy")
        f.usd.append(x.get("usd") or 0.0); f.high.append(hi); f.t_high.append(thi)
        f.low_since_high.append(lo); f.t_low_since_high.append(tlo)
        f.cum_vol.append(cv); f.cum_n.append(i + 1); f.cum_w.append(len(wallets))
        f.top_seller_share.append((max(sellers.values()) / sell_tot) if sell_tot > 0 else 0.0)
    # prefix sums for windows
    f._pb = [0.0]; f._ps = [0.0]; f._nb = [0]; f._ns = [0]; f._nw = [0]
    seen = set()
    for i, x in enumerate(tr):
        nw = 1 if (x.get("trader") and x["trader"] not in seen and x["side"] == "buy") else 0
        if x.get("trader"):
            seen.add(x["trader"])
        f._nw.append(f._nw[-1] + nw)
    for i in range(f.n):
        b = f.side_buy[i]; u = f.usd[i]
        f._pb.append(f._pb[-1] + (u if b else 0.0)); f._ps.append(f._ps[-1] + (0.0 if b else u))
        f._nb.append(f._nb[-1] + (1 if b else 0)); f._ns.append(f._ns[-1] + (0 if b else 1))
    return f


def win(f: Feat, i: int, w: int, lag: int = 0) -> dict:
    """Trades with bt in (bt_i - lag - w, bt_i - lag] (prefix-sum, O(log n))."""
    end_t = f.bt[i] - lag
    hi_idx = bisect.bisect_right(f.bt, end_t, 0, i + 1)
    lo_idx = bisect.bisect_right(f.bt, end_t - w, 0, hi_idx)
    bu = f._pb[hi_idx] - f._pb[lo_idx]; su = f._ps[hi_idx] - f._ps[lo_idx]
    nb = f._nb[hi_idx] - f._nb[lo_idx]; ns = f._ns[hi_idx] - f._ns[lo_idx]
    return {"buy_usd": bu, "sell_usd": su, "nb": nb, "ns": ns, "n": nb + ns, "vol": bu + su,
            "new_buyers": f._nw[hi_idx] - f._nw[lo_idx], "lo": lo_idx, "hi": hi_idx}


def steady_curve(f: Feat, i: int) -> bool:
    """Family I filter (proxy for bonding-curve real SOL rising steadily): net buy $ inflow > 0
    over both the last 30s and 60s, and no single buy > 35% of the last-30s buy $ (no one-trade spike)."""
    a = win(f, i, 30); b = win(f, i, 60)
    if a["buy_usd"] <= a["sell_usd"] or b["buy_usd"] <= b["sell_usd"]:
        return False
    big = max((f.usd[k] for k in range(a["lo"], a["hi"]) if f.side_buy[k]), default=0.0)
    return big <= 0.35 * a["buy_usd"]


def not_crash(f: Feat, i: int) -> bool:
    """Dip, not crash: sells not accelerating (10s vs prior 10s) while out-selling buys,
    and buyers returning (last-10s buy $ >= 60% of sell $)."""
    a = win(f, i, 10); b = win(f, i, 10, lag=10)
    accel = a["ns"] >= 2 * max(1, b["ns"]) and a["sell_usd"] > a["buy_usd"]
    return (not accel) and a["buy_usd"] >= 0.6 * a["sell_usd"]


# ---------------------------------------------------------------- rug / dump features (causal, entry-time only)
def rug_feats(f: Feat, i: int) -> dict:
    """Everything here uses trades 0..i only (no lookahead).
    top1_drop    : biggest single seller's share of sell $ since the running high (v1.1 whale rule)
    bundle_drop  : share of sell $ since the high from 'bundle' wallets (bought within the first 2 slots of the tape)
    bundle_hold  : share of supply still held by bundle wallets
    creator_hold : creator's net bought tokens still held, share of supply; creator_sold30 = creator sold in last 30s
    top5_hold    : top-5 trader net balances (bought - sold) as share of supply (curve excluded)
    drain15      : net SOL out of the curve in the last 15s / net SOL in since launch
    uniq60       : unique wallets / trades over the last 60s (low = few wallets churning = wash)
    sell_accel   : sells in last 10s / max(1, sells in the 10s before)"""
    slot0 = f.slot[0] if f.n else 0
    bundle = {f.trader[k] for k in range(min(i + 1, f.n)) if f.slot[k] <= slot0 + 1 and f.side_buy[k]}
    bal: dict = defaultdict(float)
    net_sol = 0.0
    th = f.t_high[i]
    sell_since = 0.0; bundle_sell = 0.0
    cre = f.creator; cre_sold30 = False
    for k in range(i + 1):
        tr = f.trader[k]
        if f.side_buy[k]:
            bal[tr] += f.tok[k]; net_sol += f.sol[k]
        else:
            bal[tr] -= f.tok[k]; net_sol -= f.sol[k]
            if f.bt[k] >= th:
                sell_since += f.usd[k]
                if tr in bundle:
                    bundle_sell += f.usd[k]
            if cre and tr == cre and f.bt[k] >= f.bt[i] - 30:
                cre_sold30 = True
    pos = sorted((v for v in bal.values() if v > 0), reverse=True)
    sup = 1e9
    a = win(f, i, 15)
    lo60 = bisect.bisect_right(f.bt, f.bt[i] - 60, 0, i + 1)
    n60 = i + 1 - lo60
    u60 = len({f.trader[k] for k in range(lo60, i + 1)})
    s10 = win(f, i, 10)["ns"]; s10p = win(f, i, 10, lag=10)["ns"]
    sol_out15 = 0.0
    lo15 = bisect.bisect_right(f.bt, f.bt[i] - 15, 0, i + 1)
    for k in range(lo15, i + 1):
        sol_out15 += (-f.sol[k] if f.side_buy[k] else f.sol[k])
    return {
        "top1_drop": f.top_seller_share[i],
        "bundle_drop": bundle_sell / sell_since if sell_since > 0 else 0.0,
        "bundle_hold": sum(max(0.0, bal[w]) for w in bundle) / sup,
        "creator_hold": max(0.0, bal.get(cre, 0.0)) / sup if cre else 0.0,
        "creator_sold30": cre_sold30,
        "top5_hold": sum(pos[:5]) / sup,
        "drain15": (sol_out15 / net_sol) if net_sol > 0 else 0.0,
        "uniq60": (u60 / n60) if n60 else 1.0,
        "sell_accel": s10 / max(1, s10p),
    }


RUG_DEFAULT = {}  # filled by research: {"feature": (op, threshold)}; op ">" flags when value > threshold


def rug_flag(f: Feat, i: int, rules: dict) -> bool:
    if not rules:
        return False
    rf = rug_feats(f, i)
    for k, (op, th) in rules.items():
        v = rf[k]
        if (op == ">" and v > th) or (op == "<" and v < th) or (op == "==" and v == th):
            return True
    return False


# ---------------------------------------------------------------- entry setups
def sig_dip(f: Feat, i: int, p: dict) -> bool:
    t = f.t[i]
    if f.high[i] <= 0 or f.t_high[i] - (f.bt[i] - t) < 10:   # high must be made after t+10s (mid-tape)
        return False
    dip = 1.0 - f.spot[i] / f.high[i]
    if not (p["dip"] <= dip <= p.get("dip_max", 0.5)):
        return False
    mc = f.spot[i] * 1e9
    if not (p.get("mcap_min", 4000) <= mc <= p.get("mcap_max", 40000)):
        return False
    if f.cum_vol[i] < p["cum_vol"] or f.cum_n[i] < p["cum_n"] or f.cum_w[i] < p["cum_w"]:
        return False
    w15 = win(f, i, 15)
    if w15["n"] < p.get("live_n", 5):
        return False
    if p.get("whale_skip", True) and f.top_seller_share[i] > 0.5:
        return False
    if p.get("guard", True) and not not_crash(f, i):
        return False
    return True


def sig_breakout(f: Feat, i: int, p: dict) -> bool:
    if f.prev_high[i] <= 0 or f.spot[i] <= f.prev_high[i] * (1 + p.get("break_pct", 0.0)):
        return False
    if f.prev_high_age[i] < p.get("high_age", 10):
        return False
    mc = f.spot[i] * 1e9
    if not (p.get("mcap_min", 4000) <= mc <= p.get("mcap_max", 40000)):
        return False
    if f.cum_vol[i] < p["cum_vol"] or f.cum_n[i] < p["cum_n"] or f.cum_w[i] < p["cum_w"]:
        return False
    a = win(f, i, 10); b = win(f, i, 20, lag=10)
    base = b["buy_usd"] / 2.0  # avg buy $ per 10s over the prior 20s
    if a["buy_usd"] < p.get("surge", 2.0) * max(base, 1.0) or a["buy_usd"] < p.get("min_buy10", 150):
        return False
    if a["buy_usd"] < p.get("bs_ratio", 1.5) * a["sell_usd"]:
        return False
    return True


def sig_reclaim(f: Feat, i: int, p: dict) -> bool:
    H = f.high[i]; L = f.low_since_high[i]
    if H <= 0 or f.t_high[i] - (f.bt[i] - f.t[i]) < 10:
        return False
    depth = 1.0 - L / H
    if not (p["dip"] <= depth <= p.get("dip_max", 0.6)):
        return False
    if f.bt[i] - f.t_low_since_high[i] < p.get("hold_low", 10):   # low has held -> higher low
        return False
    level = L + p.get("retrace", 0.5) * (H - L)
    if f.spot[i] < level or (i > 0 and f.spot[i - 1] >= level and f.t_high[i - 1] == f.t_high[i]):
        return False  # only the reclaim cross
    mc = f.spot[i] * 1e9
    if not (p.get("mcap_min", 4000) <= mc <= p.get("mcap_max", 40000)):
        return False
    if f.cum_vol[i] < p["cum_vol"] or f.cum_n[i] < p["cum_n"] or f.cum_w[i] < p["cum_w"]:
        return False
    a = win(f, i, 10)
    if a["buy_usd"] <= a["sell_usd"] or a["n"] < p.get("live_n10", 4):
        return False
    return True


SIGNALS = {"A": sig_dip, "B": sig_breakout, "C": sig_reclaim}


def sig_confirm(f: Feat, i: int, p: dict) -> bool:
    """E: after a >= D dip to low L, the last k whole seconds were each buy-dominated (buy $ > sell $,
    at least one trade each) and spot is >= 3% off the low but still below the 50% retrace."""
    H = f.high[i]; L = f.low_since_high[i]
    if H <= 0 or f.t_high[i] - (f.bt[i] - f.t[i]) < 10:
        return False
    depth = 1.0 - L / H
    if not (p["dip"] <= depth <= p.get("dip_max", 0.5)):
        return False
    if not (L * 1.03 <= f.spot[i] <= L + 0.5 * (H - L)):
        return False
    mc = f.spot[i] * 1e9
    if not (p.get("mcap_min", 3000) <= mc <= p.get("mcap_max", 40000)):
        return False
    if f.cum_vol[i] < p["cum_vol"] or f.cum_n[i] < p["cum_n"] or f.cum_w[i] < p["cum_w"]:
        return False
    if p.get("whale_skip", True) and f.top_seller_share[i] > 0.5:
        return False
    for k in range(p.get("k", 2)):
        w = win(f, i, 1, lag=k)
        if w["n"] == 0 or w["buy_usd"] <= w["sell_usd"]:
            return False
    return not_crash(f, i)


def sig_pressure(f: Feat, i: int, p: dict) -> bool:
    """H: 10s buy $ >= R x sell $, >= nb new unique buyers in 10s, not right at a fresh spike top."""
    mc = f.spot[i] * 1e9
    if not (p.get("mcap_min", 3000) <= mc <= p.get("mcap_max", 40000)):
        return False
    if f.cum_vol[i] < p["cum_vol"] or f.cum_n[i] < p["cum_n"] or f.cum_w[i] < p["cum_w"]:
        return False
    a = win(f, i, 10)
    if a["buy_usd"] < p.get("ratio", 2.0) * max(a["sell_usd"], 1.0) or a["buy_usd"] < p.get("min_buy10", 150):
        return False
    if a["new_buyers"] < p.get("new_buyers", 4):
        return False
    return not_crash(f, i)


def sig_band(f: Feat, i: int, p: dict) -> bool:
    """K: band-entry momentum - the coin trades inside the $5k-$10k band with activity floors met,
    10s buy $ >= ratio x sell $, and no crash in progress (usually the first push through ~$5k)."""
    mc = f.spot[i] * 1e9
    if not (p.get("mcap_min", 5000) <= mc <= p.get("mcap_max", 10000)):
        return False
    if f.cum_vol[i] < p["cum_vol"] or f.cum_n[i] < p["cum_n"] or f.cum_w[i] < p["cum_w"]:
        return False
    a = win(f, i, 10)
    if a["n"] < p.get("live_n10", 4) or a["buy_usd"] < p.get("ratio", 1.0) * max(a["sell_usd"], 1.0):
        return False
    return not_crash(f, i)


SIGNALS.update({"E": sig_confirm, "H": sig_pressure, "K": sig_band})


def first_signal(f: Feat, fam: str, p: dict, after_bt: int | None = None) -> int | None:
    fn = SIGNALS[fam]
    for i in range(f.n):
        if not (ENTRY_MIN_T < f.t[i] <= ENTRY_MAX_T):
            continue
        if after_bt is not None and f.bt[i] <= after_bt:
            continue
        if fn(f, i, p) and (not p.get("steady") or steady_curve(f, i)) and not rug_flag(f, i, p.get("rug") or {}):
            return i
    return None


# ---------------------------------------------------------------- fills + exits
def fill_px(f: Feat, trig_bt: int, after_i: int) -> tuple[float, int, int]:
    """Spot at the first trade >= trig_bt+1 (within 2s); else the curve state at trig_bt+1."""
    target = trig_bt + FILL_DELAY
    j = bisect.bisect_left(f.bt, target, after_i + 1)
    if j < f.n and f.bt[j] <= target + 2:
        return f.spot[j], j, f.bt[j]
    k = bisect.bisect_right(f.bt, target) - 1
    k = max(k, after_i)
    return f.spot[k], k, target


def simulate_exit(f: Feat, ent_i: int, ent_bt: int, ent_px: float, v_ent: float, x: dict) -> dict:
    """x: tp (mult or None), hold (s), fade (bool), stall (s or None: exit if peak < +10% by then),
    be (arm frac or None: after +arm, exit at break-even), trail (arm, pullback) or None."""
    tp = x.get("tp"); hold = x.get("hold", 120); fade = x.get("fade", True)
    inval_low = x.get("inval_low")      # G: price under this level (the dip low) within inval_sec -> exit
    inval_sec = x.get("inval_sec", 60)
    inval_flow = x.get("inval_flow")    # G: after >=5s, 10s sell count >= 2x buy count -> exit
    flip10 = x.get("flip10")            # H: after >=10s, first 10s window with sell $ > buy $ -> exit
    stall = x.get("stall"); be = x.get("be"); trail = x.get("trail")
    be_px = ent_px * breakeven_mult()
    peak = ent_px; trough = ent_px
    trig_bt = None; why = None; trig_i = ent_i
    deadline = ent_bt + hold
    j = ent_i + 1
    while j < f.n and f.bt[j] <= ent_bt:
        j += 1
    stall_done = stall is None
    while j < f.n:
        bt = f.bt[j]
        if bt > deadline:
            break
        if not stall_done and bt > ent_bt + stall:
            stall_done = True
            if peak < ent_px * 1.10:
                trig_bt, why, trig_i = ent_bt + stall, "stall", j - 1
                break
        s = f.spot[j]
        peak = max(peak, s); trough = min(trough, s)
        if tp and s >= ent_px * tp:
            trig_bt, why, trig_i = bt, "tp", j; break
        if trail and peak >= ent_px * (1 + trail[0]) and s <= peak * (1 - trail[1]):
            trig_bt, why, trig_i = bt, "trail", j; break
        if be and peak >= ent_px * (1 + be) and s <= be_px:
            trig_bt, why, trig_i = bt, "be", j; break
        dt0 = bt - ent_bt
        if inval_low and dt0 <= inval_sec and s < inval_low:
            trig_bt, why, trig_i = bt, "inval_low", j; break
        if inval_flow and dt0 >= 5:
            w = win(f, j, 10)
            if w["ns"] >= 2 * max(1, w["nb"]) and w["ns"] >= 3:
                trig_bt, why, trig_i = bt, "inval_flow", j; break
        if flip10 and dt0 >= 10:
            w = win(f, j, 10)
            if w["sell_usd"] > w["buy_usd"]:
                trig_bt, why, trig_i = bt, "flip10", j; break
        if fade:
            dt = bt - ent_bt
            r = None
            if dt >= FADE_CFG["vol_window"]:
                w = win(f, j, FADE_CFG["vol_window"])
                if (v_ent > 0 and w["vol"] < FADE_CFG["vol_frac"] * v_ent) or w["n"] < FADE_CFG["min_trades_15"]:
                    r = "fade"
            if r is None and dt >= FADE_CFG["flow_window"]:
                w = win(f, j, FADE_CFG["flow_window"])
                if w["sell_usd"] > 0 and w["sell_usd"] >= FADE_CFG["sell_buy_ratio"] * w["buy_usd"]:
                    r = "fade"
            if r:
                trig_bt, why, trig_i = bt, r, j; break
        j += 1
    if trig_bt is None:
        if not stall_done and ent_bt + stall < deadline and peak < ent_px * 1.10:
            trig_bt, why = ent_bt + stall, "stall"
        else:
            trig_bt, why = deadline, "hold"
        trig_i = max(ent_i, bisect.bisect_right(f.bt, trig_bt) - 1)
    px, k, xbt = fill_px(f, trig_bt, trig_i)
    # path extremes up to the actual sell
    lo_i = ent_i + 1
    seg = f.spot[lo_i:k + 1] or [ent_px]
    return {"exit": why, "exit_px": px, "mult": px / ent_px, "net": net_pnl(ent_px, px), "hold_s": xbt - ent_bt,
            "mfe": max(seg) / ent_px - 1, "mae": min(seg) / ent_px - 1}


def scale_out(f, ei, ebt, ent_px, v_ent, x, low):
    """F: half at a small TP, half on the runner exit; extra sell tx gas ($0.01425) charged."""
    a = dict(x["first"]); b = dict(x["rest"])
    for d in (a, b):
        if d.get("inval_low") is True:
            d["inval_low"] = low
    ra = simulate_exit(f, ei, ebt, ent_px, v_ent, a)
    # the runner keeps all dynamic exits but cannot end before the first half
    rb = simulate_exit(f, ei, ebt, ent_px, v_ent, b)
    net = 0.5 * ra["net"] + 0.5 * rb["net"] - GAS_USD / 2
    return {"exit": f"{ra['exit']}+{rb['exit']}", "exit_px": None, "mult": 0.5 * ra["mult"] + 0.5 * rb["mult"], "net": net,
            "hold_s": max(ra["hold_s"], rb["hold_s"]), "mfe": max(ra["mfe"], rb["mfe"]), "mae": min(ra["mae"], rb["mae"])}


def run_coin(f: Feat, fam: str, p: dict, exits: dict[str, dict], after_bt: int | None = None) -> dict | None:
    i = first_signal(f, fam, p, after_bt)
    if i is None:
        return None
    ent_px, ei, ebt = fill_px(f, f.bt[i], i)
    v_ent = win(f, i, FADE_CFG["vol_window"])["vol"]
    out = {"rf": rug_feats(f, i), "sig_i": i, "sig_t": f.t[i], "sig_spot": f.spot[i], "entry_px": ent_px, "entry_bt": ebt,
           "entry_t": f.t[i] + (ebt - f.bt[i]), "entry_mcap": ent_px * 1e9,
           "entry_slip": ent_px / f.spot[i] - 1, "dip": 1 - f.spot[i] / f.high[i] if f.high[i] else 0.0, "ex": {}}
    # did price reach 1.5x / 1.6x / 1.7x of the fill within 120s (ignoring exits), and how fast
    for m in (1.5, 1.6, 1.7):
        out[f"t_to_{m:g}"] = None
    j = ei + 1
    while j < f.n and f.bt[j] <= ebt + 120:
        if f.bt[j] >= ebt:
            for m in (1.5, 1.6, 1.7):
                if out[f"t_to_{m:g}"] is None and f.spot[j] >= ent_px * m:
                    out[f"t_to_{m:g}"] = f.bt[j] - ebt
        j += 1
    low = f.low_since_high[i]
    for k, x in exits.items():
        if "first" in x:
            out["ex"][k] = scale_out(f, ei, ebt, ent_px, v_ent, x, low)
            continue
        if x.get("inval_low") is True:
            x = dict(x, inval_low=low * x.get("inval_buf", 1.0))
        out["ex"][k] = simulate_exit(f, ei, ebt, ent_px, v_ent, x)
    return out


def base_rate(f: Feat, mc_lo: float = 5000, mc_hi: float = 10000, mult: float = 1.5, horizon: int = 120) -> dict:
    """Ceiling check for a coin: does it trade at $mc_lo-$mc_hi in t+30..300s, and does ANY such
    +1s fill (or the FIRST one) see spot >= mult x fill within `horizon` seconds?"""
    first = None; anyhit = False; seen = False; t_first_hit = None
    for i in range(f.n):
        if not (ENTRY_MIN_T < f.t[i] <= ENTRY_MAX_T):
            continue
        mc = f.spot[i] * 1e9
        if not (mc_lo <= mc <= mc_hi):
            continue
        px, ei, ebt = fill_px(f, f.bt[i], i)
        j = ei + 1; hit = None
        while j < f.n and f.bt[j] <= ebt + horizon:
            if f.bt[j] >= ebt and f.spot[j] >= px * mult:
                hit = f.bt[j] - ebt; break
            j += 1
        if not seen:
            seen = True; first = hit
        if hit is not None:
            anyhit = True
            break
    return {"in_band": seen, "first_hit": first is not None, "first_hit_s": first, "any_hit": anyhit}


def run_coin_reentry(f: Feat, fam: str, p: dict, exits: dict[str, dict], max_trades: int = 2) -> list[dict]:
    """J: per exit rule, a second flip only if the first was a win and a fresh signal appears after its exit."""
    first = run_coin(f, fam, p, exits)
    if not first:
        return []
    outs = [first]
    seconds = {}
    for k in exits:
        e = first["ex"][k]
        if e["net"] > 0:
            r = run_coin(f, fam, p, {k: exits[k]}, after_bt=first["entry_bt"] + e["hold_s"])
            if r:
                seconds[k] = r
    first["reentry"] = seconds
    return outs


# ---------------------------------------------------------------- stats
def seq_stats(nets: list[float]) -> dict:
    cum = 0.0; peak = 0.0; dd = 0.0; streak = 0; best = 0
    for x in nets:
        cum += x; peak = max(peak, cum); dd = min(dd, cum - peak)
        streak = streak + 1 if x <= 0 else 0; best = max(best, streak)
    return {"total": cum, "max_dd": dd, "loss_streak": best}


def summarize(rows: list[dict], weights: list[float], rng_seed: int = 1, boots: int = 300) -> dict:
    """rows: chronological trades with 'net','hold_s','v0'; weights: coins/day represented by each sampled coin
    (tier universe / tapes fetched in that tier).

    Per-trade quality (win %, expectancy, avg win/loss, loss shares, profit factor) is the plain average over the
    sampled trades (robust to a few heavy tier weights). Day counts (trades/wins/losses per day) are weight-scaled
    estimates; day P&L = expectancy x estimated trades. Max DD / longest loss streak are bootstrap medians of a
    simulated day (resample sampled trades to the estimated daily count, random order)."""
    if not rows:
        return {"n_sampled": 0, "est_trades": 0.0, "est_wins": 0.0}
    nets = [r["net"] for r in rows]
    n = len(nets)
    W = sum(weights)
    wins = [x for x in nets if x > 0]; losses = [x for x in nets if x <= 0]
    win_rate = len(wins) / n
    exp = sum(nets) / n
    rng = random.Random(rng_seed)
    n_day = max(1, int(round(W)))
    dds, sts = [], []
    for _ in range(boots):
        seq = rng.choices(nets, k=n_day)
        st = seq_stats(seq); dds.append(st["max_dd"]); sts.append(st["loss_streak"])
    dds.sort(); sts.sort()
    holds = sorted(r["hold_s"] for r in rows)
    gw = sum(wins); gl = -sum(losses)
    return {
        "n_sampled": n, "n_wins_sampled": len(wins), "est_trades": W, "est_wins": W * win_rate, "est_losses": W * (1 - win_rate),
        "win_pct": win_rate, "exp": exp, "exp_w": sum(x * w for x, w in zip(nets, weights)) / W, "est_total": exp * W,
        "avg_win": (gw / len(wins)) if wins else 0.0, "avg_loss": (-gl / len(losses)) if losses else 0.0,
        "worst": min(nets), "p_lose10": sum(x <= -0.10 for x in nets) / n, "p_lose20": sum(x <= -0.20 for x in nets) / n,
        "p_lose40": sum(x <= -0.40 for x in nets) / n, "pf": (gw / gl) if gl > 0 else float("inf"),
        "dd_med": dds[len(dds) // 2], "streak_med": sts[len(sts) // 2], "hold_med": holds[len(holds) // 2],
        "v0": sum(1 for r in rows if r.get("v0")), "raw": seq_stats(nets),
    }
