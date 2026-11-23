#!/usr/bin/env python3
"""Rulebook v1 backtest on strict v0 passers only. $1 size, 1.25% each way, ~$0.0285 RT gas.

Prefers the 30-minute analyze.py tape (coins/<mint>/trades.csv). If that is missing,
falls back to the 5-minute pre-screen tape and flags tape_source=prescreen_5m.

Exit variants
-------------
Baselines keep a 5-minute time cap at TP 1.7x / 1.8x with no stop, -35% SL, and -50% SL.
Dynamic exits are no-hard-stop rules, compared against plain no-stop and -50%:
  (1) volume-fade: trailing 15s volume < 30% of the 15s volume ending at entry,
      or <3 trades in 15s, or sell volume >= 2x buy volume in 30s;
  (2) stall: at 60/90/120s, sell if price never reached +10% or +20%, or is flat
      in [-10%, +10%];
  (3) break-even trail: once +20% or +30% is reached, exit if price falls back to
      after-fee break-even;
  (4) momentum trail: once +50% is reached, exit on a 25% pullback from the peak
      instead of a fixed TP;
  (5) combos: 1+2+3 with fixed TP, and 1+2+3 with the (4) trail replacing TP.
Thresholds are stated, not fit. Ranking them on a single-digit trade sample overfits.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.constants import DEFAULT_PRIORITY_SOL, DEFAULT_BASE_FEE_SOL, CURVE_FEE_BPS
from lib.study import simulate_trade, volume_active_at
from screen.common import CACHE, DATE_EAT, SCREEN, eat_str, hour_window, atomic_write_json
from screen.prescreen import first_live_touches, SIG_OK_GATE, entry_setup_metrics

ROOT = Path("/workspace/pumpfun")
COINS = ROOT / "coins"
CSV_OUT = SCREEN / f"backtest_{DATE_EAT}.csv"
PRE_DIR = CACHE / "prescreen"
SL = -0.35  # legacy default; sl_path takes an explicit sl
SL35 = -0.35
SL50 = -0.50
GAS_NOTE = 0.0285

# Volume-fade / combo notes (stated thresholds, not fit on this sample).
FADE_CFG = {
    "vol_frac": 0.30,
    "min_trades_15": 3,
    "sell_buy_ratio": 2.0,
    "vol_window": 15,
    "flow_window": 30,
}
FADE_NOTE = (
    "Volume-fade: trailing 15s volume < 30% of the 15s volume ending at entry, "
    "or <3 trades in that 15s, or sell volume >= 2x buy volume in the trailing 30s. "
    "Evaluated from 15s (volume/thin) or 30s (sell flow) after entry. "
    "Thresholds were taken from the stated rule, not fit. "
    "With only a handful of simulated trades, any ranking of these exits can overfit."
)
COMBO_STALL = {"sec": 90, "modes": ["never10", "flat"]}
COMBO_NOTE = (
    "Combo stall is checked at 90s: sell if the post-entry peak never reached +10% "
    "or the last price at 90s sits in -10% to +10%. "
    "Break-even trail arms at +20% and exits at after-fee break-even "
    "(~+5.5% given 1.25% each way plus ~$0.0285 gas). "
    "Momentum trail arms at +50% and exits on a 25% pullback from the running peak, "
    "replacing the fixed take-profit so larger runners can work."
)
SMALL_LOSS = -0.25  # baseline loser "converted to a small loss" if variant net >= this and better

BASE_FIELDS = [
    "mint", "name", "symbol", "slice_window", "launch_ts", "launch_time_eat", "entry_ts",
    "tape_source", "migrated_within_30m", "migrate_sec",
    "custom_quote", "is_token_2022",
    "entry_threshold", "entry_t_plus", "entry_drop", "entry_mcap",
    "entry_price_usd", "entry_sig",
    "tp17_hit", "tp17_net", "tp17_secs", "tp17_dd",
    "tp18_hit", "tp18_net", "tp18_secs", "tp18_dd",
    "timestop_net", "timestop_mult", "timestop_dd", "timestop_secs",
    "rule17_no_sl_exit", "rule17_no_sl_net",
    "rule18_no_sl_exit", "rule18_no_sl_net",
    "rule17_sl_exit", "rule17_sl_net", "rule17_sl_secs",
    "rule18_sl_exit", "rule18_sl_net", "rule18_sl_secs",
    "rule17_sl50_exit", "rule17_sl50_net", "rule17_sl50_secs",
    "rule18_sl50_exit", "rule18_sl50_net", "rule18_sl50_secs",
    "max_dd_5m_pct", "max_dd_30m_pct",
    "rugged_or_died", "end_mult_30m", "n_trades_tape",
    "entry_cum_vol", "entry_cum_trades", "entry_cum_wallets", "entry_wallets_obs",
    "entry_cum_trades_obs", "entry_trades_per_wallet",
    "entry_high_t_plus", "entry_secs_from_high",
    "entry_15s_vol", "entry_15s_trades", "entry_15s_buys", "entry_15s_buy_vol", "entry_15s_sell_vol", "entry_15s_buy_sell_ratio",
    "entry_10s_vol", "entry_10s_trades", "entry_10s_buys", "entry_10s_buy_vol", "entry_10s_sell_vol", "entry_10s_buy_sell_ratio",
    "entry_drop_sells", "entry_drop_sell_vol", "entry_top1_seller_share", "entry_top2_seller_share", "entry_dev_sold_in_drop",
    "error",
]


def _dynamic_specs() -> list[dict]:
    """No-hard-stop dynamic exits. Each item: key, label, kwargs, plot, compare_to."""
    specs: list[dict] = []
    def add(key, label, kwargs, plot=False, compare_to="rule17_no_sl_net"):
        specs.append({
            "key": key,
            "label": label,
            "kwargs": kwargs,
            "plot": plot,
            "compare_to": compare_to,
        })

    for tp, tag in ((1.7, "17"), (1.8, "18")):
        add(
            f"fade_{tag}",
            f"TP {tp:g}x, volume-fade, no SL, 5-min cap",
            dict(tp_mult=tp, sl=None, time_stop_sec=300, fade=True),
            plot=True,
            compare_to=f"rule{tag}_no_sl_net",
        )

    mode_tag = {"never10": "n10", "never20": "n20", "flat": "flat"}
    mode_lab = {
        "never10": "never +10%",
        "never20": "never +20%",
        "flat": "flat −10% to +10%",
    }
    for sec in (60, 90, 120):
        for mode in ("never10", "never20", "flat"):
            for tp, tag in ((1.7, "17"), (1.8, "18")):
                add(
                    f"stall{sec}_{mode_tag[mode]}_{tag}",
                    f"TP {tp:g}x, stall@{sec}s if {mode_lab[mode]}, no SL, 5-min cap",
                    dict(tp_mult=tp, sl=None, time_stop_sec=300, stall={"sec": sec, "modes": [mode]}),
                    plot=(sec == 90 and mode in ("never10", "flat") and tag == "17"),
                    compare_to=f"rule{tag}_no_sl_net",
                )

    for arm, atag in ((0.20, "20"), (0.30, "30")):
        for tp, tag in ((1.7, "17"), (1.8, "18")):
            add(
                f"be{atag}_{tag}",
                f"TP {tp:g}x, BE trail after +{int(arm*100)}%, no SL, 5-min cap",
                dict(tp_mult=tp, sl=None, time_stop_sec=300, be_arm=arm),
                plot=(atag == "20" and tag == "17"),
                compare_to=f"rule{tag}_no_sl_net",
            )

    add(
        "mom50_25",
        "Momentum trail: arm +50%, exit 25% off peak (no fixed TP), 5-min cap if never +50%",
        dict(tp_mult=None, sl=None, time_stop_sec=300, mom_arm=0.50, mom_pullback=0.25),
        plot=True,
        compare_to="rule17_no_sl_net",
    )
    for tp, tag in ((1.7, "17"), (1.8, "18")):
        add(
            f"c123_{tag}",
            f"Combo 1+2+3: fade + stall@90s (never +10% or flat) + BE trail after +20%, TP {tp:g}x, no SL, 5-min cap",
            dict(tp_mult=tp, sl=None, time_stop_sec=300, fade=True, stall=COMBO_STALL, be_arm=0.20),
            plot=True,
            compare_to=f"rule{tag}_no_sl_net",
        )
    add(
        "c1234",
        "Combo 1+2+3+4: fade + stall@90s (never +10% or flat) + BE trail after +20% + mom trail (arm +50%, 25% off peak, no fixed TP), 5-min cap",
        dict(tp_mult=None, sl=None, time_stop_sec=300, fade=True, stall=COMBO_STALL, be_arm=0.20, mom_arm=0.50, mom_pullback=0.25),
        plot=True,
        compare_to="rule17_no_sl_net",
    )
    return specs


DYNAMIC_SPECS = _dynamic_specs()

BASELINE_VARIANTS = [
    ("tp17_no_sl", "rule17_no_sl_net", "TP 1.7x, no SL, 5-min time stop", True, None),
    ("tp18_no_sl", "rule18_no_sl_net", "TP 1.8x, no SL, 5-min time stop", True, None),
    ("tp17_sl35", "rule17_sl_net", "TP 1.7x, -35% SL, 5-min time stop", False, "rule17_no_sl_net"),
    ("tp18_sl35", "rule18_sl_net", "TP 1.8x, -35% SL, 5-min time stop", False, "rule18_no_sl_net"),
    ("tp17_sl50", "rule17_sl50_net", "TP 1.7x, -50% SL, 5-min time stop", True, "rule17_no_sl_net"),
    ("tp18_sl50", "rule18_sl50_net", "TP 1.8x, -50% SL, 5-min time stop", True, "rule18_no_sl_net"),
]

FIELDS = list(BASE_FIELDS)
for _sp in DYNAMIC_SPECS:
    for _f in (f"{_sp['key']}_exit", f"{_sp['key']}_net", f"{_sp['key']}_secs"):
        if _f not in FIELDS:
            FIELDS.append(_f)


def _px_usd(t):
    if t.get("price_usd"):
        try:
            return float(t["price_usd"])
        except (TypeError, ValueError):
            pass
    ps, su = t.get("price_sol"), t.get("sol_usd")
    try:
        if ps and su:
            return float(ps) * float(su)
    except (TypeError, ValueError):
        return None
    return None


def load_analyze_trades(mint: str) -> tuple[list[dict], dict]:
    d = COINS / mint
    summary = {}
    sp = d / "summary.json"
    if sp.exists():
        try:
            summary = json.loads(sp.read_text())
        except Exception:
            summary = {}
    tp = d / "trades.csv"
    if not tp.exists():
        return [], summary
    rows = list(csv.DictReader(tp.open()))
    out = []
    for r in rows:
        t = dict(r)
        for k in ("block_time", "slot", "t_plus_sec"):
            if t.get(k) not in (None, ""):
                try:
                    t[k] = int(float(t[k]))
                except ValueError:
                    pass
        for k in ("price_sol", "price_usd", "mcap_usd", "sol_usd", "volume_usd", "sol_ui"):
            if t.get(k) not in (None, ""):
                try:
                    t[k] = float(t[k])
                except ValueError:
                    pass
        out.append(t)
    out.sort(key=lambda x: (x.get("block_time") or 0, x.get("slot") or 0, x.get("signature") or ""))
    return out, summary


def load_prescreen_trades(mint: str) -> list[dict]:
    p = PRE_DIR / mint / "trades.json"
    if not p.exists():
        return []
    try:
        blob = json.loads(p.read_text())
        return blob.get("trades") or []
    except Exception:
        return []


def qualifying_entry(trades: list[dict]) -> tuple[dict | None, str]:
    """First mid-tape (t+30s-300s) volume-live 30/35/40% touch — rulebook v1."""
    opening, mid = first_live_touches(trades)
    for T, name in ((0.30, "30"), (0.35, "35"), (0.40, "40")):
        rec = mid.get(T)
        if rec:
            tr = dict(rec["trade"])
            tr["_drop"] = rec["drop"]
            tr["_threshold"] = name
            return tr, name
    return None, ""


def _side(t: dict) -> str:
    return (t.get("side") or "").lower()


def _vol(t: dict) -> float:
    try:
        return float(t.get("volume_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def _tplus(t: dict) -> int | None:
    v = t.get("t_plus_sec")
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _entry_window_stats(trades: list[dict], entry_t: int, width: int = 15) -> tuple[float, int]:
    win = [t for t in trades if entry_t - width < (_tplus(t) if _tplus(t) is not None else -10**9) <= entry_t]
    return sum(_vol(t) for t in win), len(win)


def _trailing(trades: list[dict], tnow: int, width: int) -> list[dict]:
    lo = tnow - width
    out = []
    for t in trades:
        tp = _tplus(t)
        if tp is None:
            continue
        if lo < tp <= tnow:
            out.append(t)
    return out


def after_fee_be_px(entry_px: float, gas_usd: float) -> float:
    """Price at which a $1 long is break-even after 1.25% each way plus round-trip gas."""
    fee = CURVE_FEE_BPS / 10000.0
    return entry_px * (1.0 + (gas_usd or 0.0)) / ((1.0 - fee) ** 2)


def fade_hit(trades: list[dict], entry_t: int, tnow: int, v_ent: float) -> str | None:
    """Return a volume_fade_* reason if the stated fade rule is on at tnow, else None."""
    dt = tnow - entry_t
    reasons = []
    if dt >= FADE_CFG["vol_window"]:
        win = _trailing(trades, tnow, FADE_CFG["vol_window"])
        vol = sum(_vol(x) for x in win)
        if v_ent > 0 and vol < FADE_CFG["vol_frac"] * v_ent:
            reasons.append("vol")
        if len(win) < FADE_CFG["min_trades_15"]:
            reasons.append("thin")
    if dt >= FADE_CFG["flow_window"]:
        win30 = _trailing(trades, tnow, FADE_CFG["flow_window"])
        buy = sum(_vol(x) for x in win30 if _side(x) == "buy")
        sell = sum(_vol(x) for x in win30 if _side(x) == "sell")
        if sell > 0 and sell >= FADE_CFG["sell_buy_ratio"] * buy:
            reasons.append("sellflow")
    if not reasons:
        return None
    return "volume_fade_" + "+".join(reasons)


def post_entry_flow(trades: list[dict], entry: dict, horizon: int = 300, bucket: int = 15) -> dict:
    """Per-bucket volume / trade count / buy-sell flow after entry, plus first fade print."""
    entry_t = int(entry.get("t_plus_sec") or 0)
    entry_px = _px_usd(entry)
    v_ent, n_ent = _entry_window_stats(trades, entry_t, FADE_CFG["vol_window"])
    buckets = []
    for start in range(0, horizon, bucket):
        lo, hi = entry_t + start, entry_t + start + bucket
        left = entry_t if start == 0 else lo
        win = [t for t in trades if left < (_tplus(t) if _tplus(t) is not None else -10**9) <= hi]
        buys = [t for t in win if _side(t) == "buy"]
        sells = [t for t in win if _side(t) == "sell"]
        vb = sum(_vol(t) for t in buys)
        vs = sum(_vol(t) for t in sells)
        last = next((t for t in reversed(win) if _px_usd(t)), None)
        last_px = _px_usd(last) if last else None
        buckets.append({
            "lo": start,
            "hi": start + bucket,
            "n": len(win),
            "n_buy": len(buys),
            "n_sell": len(sells),
            "vol": sum(_vol(t) for t in win),
            "buy_vol": vb,
            "sell_vol": vs,
            "mult": (last_px / entry_px) if (last_px and entry_px) else None,
        })
    first_fade = None
    after = [t for t in trades if (_tplus(t) is not None and _tplus(t) > entry_t)]
    for t in after:
        tn = _tplus(t)
        if tn is None or tn > entry_t + horizon:
            break
        why = fade_hit(trades, entry_t, tn, v_ent)
        if why:
            px = _px_usd(t)
            first_fade = {
                "t_plus": tn,
                "secs_after_entry": tn - entry_t,
                "reason": why,
                "mult": (px / entry_px) if (px and entry_px) else None,
            }
            break
    return {
        "entry_t_plus": entry_t,
        "entry_px": entry_px,
        "entry_15s_vol": v_ent,
        "entry_15s_trades": n_ent,
        "buckets": buckets,
        "first_fade": first_fade,
    }


def format_flow_table(flow: dict, symbol: str) -> str:
    lines = [
        f"Post-entry 15s flow for {symbol} (entry t+{flow.get('entry_t_plus')}s, "
        f"entry-15s volume ${flow.get('entry_15s_vol', 0):.0f} over {flow.get('entry_15s_trades')} trades):",
        "",
        "| Window | Trades | Buys | Sells | Vol $ | Buy $ | Sell $ | Sell/Buy | Last multiple |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b in flow.get("buckets") or []:
        sb = ""
        if b["buy_vol"] > 0:
            sb = f"{b['sell_vol'] / b['buy_vol']:.2f}"
        elif b["sell_vol"] > 0:
            sb = "inf"
        else:
            sb = "—"
        mult = "" if b.get("mult") is None else f"{b['mult']:.2f}x"
        lines.append(
            f"| {b['lo']:>3}–{b['hi']:<3}s | {b['n']} | {b['n_buy']} | {b['n_sell']} | "
            f"{b['vol']:.0f} | {b['buy_vol']:.0f} | {b['sell_vol']:.0f} | {sb} | {mult} |"
        )
    fd = flow.get("first_fade")
    if fd:
        m = "" if fd.get("mult") is None else f" at {fd['mult']:.2f}x"
        lines += ["", f"First volume-fade print: +{fd['secs_after_entry']}s ({fd['reason']}){m}."]
    else:
        lines += ["", "No volume-fade print in the 5 minutes after entry."]
    return "\n".join(lines)


def exit_path(
    trades: list[dict],
    entry: dict,
    tp_mult: float | None = None,
    sl: float | None = None,
    time_stop_sec: int = 300,
    fade: bool = False,
    stall: dict | None = None,
    be_arm: float | None = None,
    mom_arm: float | None = None,
    mom_pullback: float | None = None,
) -> dict:
    """Simulate one path. Priority: mom trail, fixed TP, hard SL, BE trail, volume-fade, stall, time cap."""
    entry_px = _px_usd(entry)
    entry_t = int(entry.get("t_plus_sec") or 0)
    stop_t = entry_t + int(time_stop_sec)
    if not entry_px:
        return {"exit": "no_entry_price", "net": None, "secs": None, "dd": None, "mult": None}

    fee = CURVE_FEE_BPS / 10000.0
    sol_usd = float(entry.get("sol_usd") or 0) or 0.0
    gas_usd = (DEFAULT_PRIORITY_SOL + DEFAULT_BASE_FEE_SOL) * 2 * (sol_usd or 0)
    tokens = (1.0 * (1.0 - fee)) / entry_px
    be_px = after_fee_be_px(entry_px, gas_usd)
    v_ent, _n_ent = _entry_window_stats(trades, entry_t, FADE_CFG["vol_window"])

    def later_than_entry(t):
        return (
            (t.get("block_time") or 0, t.get("slot") or 0, t.get("signature") or "")
            > (entry.get("block_time") or 0, entry.get("slot") or 0, entry.get("signature") or "")
        )

    after = [t for t in trades if later_than_entry(t) and _px_usd(t)]

    min_px = entry_px
    peak_px = entry_px
    be_armed = False
    mom_armed = False
    stall_checked = False
    stall_sec = int(stall["sec"]) if stall else None
    stall_t = (entry_t + stall_sec) if stall_sec is not None else None
    last_before_stall = None
    peak_before_stall = entry_px
    chosen = None
    reason = None

    def mark(t, why):
        nonlocal chosen, reason
        chosen, reason = t, why

    def stall_reason(max_px_win, last_px) -> str | None:
        if not stall or not last_px:
            return None
        modes = stall.get("modes") or [stall.get("mode")]
        modes = [m for m in modes if m]
        hit = []
        mx = max_px_win / entry_px
        cur = last_px / entry_px
        if "never10" in modes and mx < 1.10:
            hit.append("never10")
        if "never20" in modes and mx < 1.20:
            hit.append("never20")
        if "flat" in modes and 0.90 <= cur <= 1.10:
            hit.append("flat")
        if not hit:
            return None
        return f"stall_{stall_sec}s_" + "+".join(hit)

    def try_stall(now_t, now_trade) -> bool:
        nonlocal stall_checked
        if not stall or stall_checked or now_t < stall_t:
            return False
        if now_t == stall_t:
            max_px_win = max(peak_before_stall, _px_usd(now_trade) or 0)
            last_px = _px_usd(now_trade)
            t_exit = now_trade
        else:
            max_px_win = peak_before_stall
            if last_before_stall:
                last_px = _px_usd(last_before_stall)
                t_exit = last_before_stall
            else:
                last_px = _px_usd(now_trade)
                t_exit = now_trade
        stall_checked = True
        why = stall_reason(max_px_win, last_px)
        if why:
            mark(t_exit, why)
            return True
        return False

    for t in after:
        tp = _tplus(t)
        if tp is None:
            continue
        if stall and not stall_checked and tp > stall_t:
            if try_stall(tp, t):
                break
        if tp > stop_t:
            break
        px = _px_usd(t)
        if not px:
            continue
        if px < min_px:
            min_px = px
        if px > peak_px:
            peak_px = px
        if stall and tp <= stall_t:
            last_before_stall = t
            if px > peak_before_stall:
                peak_before_stall = px

        if be_arm is not None and peak_px >= entry_px * (1.0 + be_arm):
            be_armed = True
        if mom_arm is not None and peak_px >= entry_px * (1.0 + mom_arm):
            mom_armed = True

        if mom_armed and mom_pullback is not None and px <= peak_px * (1.0 - mom_pullback):
            mark(t, f"mom_trail_{int(round(mom_arm * 100))}_{int(round(mom_pullback * 100))}")
            break
        if tp_mult is not None and px >= entry_px * tp_mult:
            mark(t, f"take_profit_{tp_mult:g}x")
            break
        if sl is not None and px <= entry_px * (1 + sl):
            mark(t, f"stop_loss_{int(round(abs(sl) * 100))}")
            break
        if be_armed and px <= be_px:
            mark(t, f"be_trail_{int(round(be_arm * 100))}")
            break
        if fade:
            fr = fade_hit(trades, entry_t, tp, v_ent)
            if fr:
                mark(t, fr)
                break
        if stall and not stall_checked and tp >= stall_t:
            if try_stall(tp, t):
                break

    if chosen is None:
        window = [t for t in after if (_tplus(t) or 0) <= stop_t]
        if window:
            tag = "time_stop_5m" if int(time_stop_sec) == 300 else f"time_stop_{int(time_stop_sec)}s"
            mark(window[-1], tag)
            px = _px_usd(chosen)
            if px and px < min_px:
                min_px = px
        else:
            return {"exit": "no_later_trade", "net": None, "secs": None, "dd": 0.0, "mult": None}

    px = _px_usd(chosen)
    net = tokens * px * (1.0 - fee) - 1.0 - gas_usd if px else None
    return {
        "exit": reason,
        "net": net,
        "secs": (int(chosen.get("t_plus_sec") or 0) - entry_t),
        "dd": min(0.0, (min_px / entry_px - 1) * 100),
        "mult": (px / entry_px) if px else None,
    }


def sl_path(trades: list[dict], entry: dict, tp_mult: float, sl: float | None = None,
            time_stop_sec: int = 300) -> dict:
    """Legacy wrapper: fixed TP, optional hard stop, time cap (default 5 minutes)."""
    return exit_path(trades, entry, tp_mult=tp_mult, sl=sl, time_stop_sec=time_stop_sec)


def max_dd(trades, entry, horizon_t):
    entry_px = _px_usd(entry)
    if not entry_px:
        return None
    after = [
        t for t in trades
        if (t.get("t_plus_sec") or -1) >= (entry.get("t_plus_sec") or 0)
        and (t.get("t_plus_sec") or 10**9) <= horizon_t
        and _px_usd(t)
    ]
    if not after:
        return 0.0
    mn = min(_px_usd(t) for t in after)
    return min(0.0, (mn / entry_px - 1) * 100)


def end_mult(trades, entry, horizon_t=1800):
    entry_px = _px_usd(entry)
    if not entry_px:
        return None
    window = [t for t in trades if (t.get("t_plus_sec") or -1) <= horizon_t and _px_usd(t)]
    if not window:
        return None
    return _px_usd(window[-1]) / entry_px


def backtest_one(launch_row: dict) -> dict:
    mint = launch_row["mint"]
    trades, summary = load_analyze_trades(mint)
    source = "analyze_30m" if trades else ""
    if not trades:
        trades = load_prescreen_trades(mint)
        source = "prescreen_5m" if trades else ""
    row = {k: "" for k in FIELDS}
    row.update({
        "mint": mint,
        "name": launch_row.get("name"),
        "symbol": launch_row.get("symbol"),
        "slice_window": launch_row.get("slice_window") or hour_window(launch_row.get("launch_ts")),
        "launch_time_eat": launch_row.get("launch_time_eat"),
        "launch_ts": launch_row.get("launch_ts"),
        "tape_source": source,
        "migrated_within_30m": summary.get("migrated_within_30m", ""),
        "migrate_sec": summary.get("migrate_sec", ""),
        "custom_quote": launch_row.get("custom_quote"),
        "is_token_2022": launch_row.get("is_token_2022"),
        "n_trades_tape": len(trades),
    })
    if not trades:
        row["error"] = "no_tape"
        return row
    entry, thresh = qualifying_entry(trades)
    if not entry:
        row["error"] = "no_v1_entry_on_tape"
        return row
    entry_px = _px_usd(entry)
    sol_usd = float(entry.get("sol_usd") or 0) or 0.0
    sim = simulate_trade(trades, entry, sol_usd, DEFAULT_PRIORITY_SOL, f"mid{thresh}", "v1")
    r17 = sl_path(trades, entry, 1.7, None)
    r18 = sl_path(trades, entry, 1.8, None)
    s17 = sl_path(trades, entry, 1.7, SL35)
    s18 = sl_path(trades, entry, 1.8, SL35)
    s17_50 = sl_path(trades, entry, 1.7, SL50)
    s18_50 = sl_path(trades, entry, 1.8, SL50)
    dd5 = max_dd(trades, entry, int(entry.get("t_plus_sec") or 0) + 300)
    horizon = 1800 if source == "analyze_30m" else 300
    dd30 = max_dd(trades, entry, horizon)
    em = end_mult(trades, entry, horizon)
    # Died/rugged: never hit 1.7x and (dd <= -70% in tape, or last print <= 0.30x entry).
    rugged = (not sim.get("1_7_hit")) and (
        (dd30 is not None and dd30 <= -70) or (em is not None and em <= 0.30)
    )
    def rnd(x, n=4):
        if x is None or x == "":
            return ""
        try:
            return round(float(x), n)
        except (TypeError, ValueError):
            return x
    row.update({
        "entry_threshold": thresh,
        "entry_t_plus": entry.get("t_plus_sec"),
        "entry_ts": (int(float(launch_row["launch_ts"])) + int(entry.get("t_plus_sec") or 0))
                    if launch_row.get("launch_ts") not in (None, "") else "",
        "entry_drop": rnd(entry.get("_drop"), 2),
        "entry_mcap": rnd(entry.get("mcap_usd"), 2),
        "entry_price_usd": rnd(entry_px, 10),
        "entry_sig": entry.get("signature"),
        "tp17_hit": int(bool(sim.get("1_7_hit"))),
        "tp17_net": rnd(sim.get("1_7_net_usd")),
        "tp17_secs": sim.get("1_7_secs_after_entry"),
        "tp17_dd": rnd(sim.get("1_7_max_dd_pct_before"), 2),
        "tp18_hit": int(bool(sim.get("1_8_hit"))),
        "tp18_net": rnd(sim.get("1_8_net_usd")),
        "tp18_secs": sim.get("1_8_secs_after_entry"),
        "tp18_dd": rnd(sim.get("1_8_max_dd_pct_before"), 2),
        "timestop_net": rnd(sim.get("time_stop_5m_net_usd")),
        "timestop_mult": rnd(sim.get("time_stop_5m_mult"), 3),
        "timestop_dd": rnd(sim.get("time_stop_5m_dd"), 2),
        "timestop_secs": sim.get("time_stop_5m_secs"),
        "rule17_no_sl_exit": r17["exit"],
        "rule17_no_sl_net": rnd(r17["net"]),
        "rule18_no_sl_exit": r18["exit"],
        "rule18_no_sl_net": rnd(r18["net"]),
        "rule17_sl_exit": s17["exit"],
        "rule17_sl_net": rnd(s17["net"]),
        "rule17_sl_secs": s17["secs"],
        "rule18_sl_exit": s18["exit"],
        "rule18_sl_net": rnd(s18["net"]),
        "rule18_sl_secs": s18["secs"],
        "rule17_sl50_exit": s17_50["exit"],
        "rule17_sl50_net": rnd(s17_50["net"]),
        "rule17_sl50_secs": s17_50["secs"],
        "rule18_sl50_exit": s18_50["exit"],
        "rule18_sl50_net": rnd(s18_50["net"]),
        "rule18_sl50_secs": s18_50["secs"],
        "max_dd_5m_pct": rnd(dd5, 2),
        "max_dd_30m_pct": rnd(dd30, 2),
        "rugged_or_died": int(bool(rugged)),
        "end_mult_30m": rnd(em, 3),
    })
    metrics = entry_setup_metrics(trades, entry, creator=launch_row.get("creator"))
    row.update(metrics)
    # Prefer already-populated prescreen observed/est wallet fields when present.
    for k in ("entry_cum_vol", "entry_cum_trades", "entry_cum_wallets", "entry_wallets_obs"):
        if launch_row.get(k) not in (None, "") and row.get(k) in (None, ""):
            row[k] = launch_row.get(k)
    for sp in DYNAMIC_SPECS:
        res = exit_path(trades, entry, **sp["kwargs"])
        row[f"{sp['key']}_exit"] = res["exit"]
        row[f"{sp['key']}_net"] = rnd(res["net"])
        row[f"{sp['key']}_secs"] = res["secs"]
    return row


def _nums(rows, key):
    out = []
    for r in rows:
        v = r.get(key)
        if v in (None, ""):
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out



BE_EPS = 0.005  # |net| <= 0.5¢ counts as breakeven on a $1 stake


def _classify(net: float | None) -> str | None:
    if net is None:
        return None
    if net > BE_EPS:
        return "win"
    if net < -BE_EPS:
        return "loss"
    return "be"


def _streaks(outcomes: list[str]) -> dict:
    """Win/loss streaks. Breakeven breaks both streaks and is not counted in either."""
    win_lens, loss_lens = [], []
    cur_kind, cur_len = None, 0

    def close():
        nonlocal cur_kind, cur_len
        if cur_kind == "win" and cur_len:
            win_lens.append(cur_len)
        elif cur_kind == "loss" and cur_len:
            loss_lens.append(cur_len)
        cur_kind, cur_len = None, 0

    for o in outcomes:
        if o not in ("win", "loss"):
            close()
            continue
        if o == cur_kind:
            cur_len += 1
        else:
            close()
            cur_kind, cur_len = o, 1
    close()

    def dist(xs):
        from collections import Counter
        return {str(k): v for k, v in sorted(Counter(xs).items())}

    def avg(xs):
        return (sum(xs) / len(xs)) if xs else None

    return {
        "win_streaks": win_lens,
        "loss_streaks": loss_lens,
        "longest_win_streak": max(win_lens) if win_lens else 0,
        "avg_win_streak": avg(win_lens),
        "n_win_streaks": len(win_lens),
        "longest_loss_streak": max(loss_lens) if loss_lens else 0,
        "avg_loss_streak": avg(loss_lens),
        "n_loss_streaks": len(loss_lens),
        "win_streak_length_dist": dist(win_lens),
        "loss_streak_length_dist": dist(loss_lens),
    }


def series_stats(rows: list[dict], net_key: str, label: str) -> dict:
    """Trades ordered by entry time. net_key is the rule net for one TP/SL variant."""
    def ts(r):
        v = r.get("entry_ts") or r.get("launch_ts") or 0
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    ordered = sorted(
        [r for r in rows if r.get(net_key) not in (None, "")],
        key=lambda r: (ts(r), r.get("mint") or ""),
    )
    nets, outcomes, curve, times, mints = [], [], [], [], []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0  # most negative peak-to-trough, in $
    max_dd_peak = 0.0
    for r in ordered:
        try:
            net = float(r[net_key])
        except (TypeError, ValueError):
            continue
        cum += net
        if cum > peak:
            peak = cum
        dd = cum - peak
        if dd < max_dd:
            max_dd = dd
            max_dd_peak = peak
        cls = _classify(net)
        nets.append(net)
        outcomes.append(cls)
        curve.append({
            "n": len(nets),
            "mint": r.get("mint"),
            "symbol": r.get("symbol"),
            "entry_ts": r.get("entry_ts"),
            "launch_time_eat": r.get("launch_time_eat"),
            "entry_t_plus": r.get("entry_t_plus"),
            "net": round(net, 6),
            "cum_pnl": round(cum, 6),
            "outcome": cls,
            "drawdown_from_peak": round(dd, 6),
        })
        times.append(r.get("entry_ts") or r.get("launch_time_eat"))
        mints.append(r.get("mint"))

    n = len(nets)
    wins = [x for x, o in zip(nets, outcomes) if o == "win"]
    losses = [x for x, o in zip(nets, outcomes) if o == "loss"]
    bes = [x for x, o in zip(nets, outcomes) if o == "be"]
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    expectancy = (sum(nets) / n) if n else None
    streaks = _streaks(outcomes)
    return {
        "label": label,
        "net_key": net_key,
        "n": n,
        "win_n": len(wins),
        "loss_n": len(losses),
        "be_n": len(bes),
        "win_pct": (len(wins) / n) if n else None,
        "loss_pct": (len(losses) / n) if n else None,
        "be_pct": (len(bes) / n) if n else None,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy_per_1usd": expectancy,
        "cum_pnl": cum if n else 0.0,
        "max_drawdown": max_dd,
        "max_drawdown_from_peak": max_dd_peak,
        "streaks": {k: v for k, v in streaks.items() if k not in ("win_streaks", "loss_streaks")},
        "curve": curve,
    }



def format_variant_table(variants: dict) -> str:
    """Full markdown table for every TP / stop / dynamic-exit variant."""
    lines = [
        "| Variant | n | Win | Loss | BE | Win % | Avg win | Avg loss | Expectancy /$1 | Cum P&L | Max DD | Longest win | Longest loss |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def usd(x):
        return "" if x is None else f"${x:.3f}"
    def pct(x):
        return "" if x is None else f"{100*x:.1f}%"
    for name, st in variants.items():
        sk = st.get("streaks") or {}
        lines.append(
            f"| {st.get('label') or name} | {st.get('n')} | {st.get('win_n')} | {st.get('loss_n')} | {st.get('be_n')} | "
            f"{pct(st.get('win_pct'))} | {usd(st.get('avg_win'))} | {usd(st.get('avg_loss'))} | "
            f"{usd(st.get('expectancy_per_1usd'))} | {usd(st.get('cum_pnl'))} | {usd(st.get('max_drawdown'))} | "
            f"{sk.get('longest_win_streak')} | {sk.get('longest_loss_streak')} |"
        )
    return "\n".join(lines)


def format_compact_table(variants: dict) -> str:
    """Win %, expectancy, cum P&L, max DD — one row per variant, in declaration order."""
    lines = [
        "| Variant | Win % | Expectancy /$1 | Cum P&L | Max DD | n / W / L / BE |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    def usd(x):
        return "" if x is None else f"${x:.3f}"
    def pct(x):
        return "" if x is None else f"{100 * x:.1f}%"
    for name, st in variants.items():
        lines.append(
            f"| {st.get('label') or name} | {pct(st.get('win_pct'))} | "
            f"{usd(st.get('expectancy_per_1usd'))} | {usd(st.get('cum_pnl'))} | "
            f"{usd(st.get('max_drawdown'))} | "
            f"{st.get('n')} / {st.get('win_n')} / {st.get('loss_n')} / {st.get('be_n')} |"
        )
    return "\n".join(lines)


def _net_of(row: dict, key: str) -> float | None:
    v = row.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compare_to_baseline(rows: list[dict]) -> list[dict]:
    """For each dynamic/SL variant, list losers saved and winners wrongly killed vs its baseline."""
    out = []
    specs = []
    for key, net_key, label, _plot, compare_to in BASELINE_VARIANTS:
        if compare_to:
            specs.append((key, net_key, label, compare_to))
    for sp in DYNAMIC_SPECS:
        specs.append((sp["key"], f"{sp['key']}_net", sp["label"], sp.get("compare_to") or "rule17_no_sl_net"))
    for key, net_key, label, baseline_key in specs:
        saved, small, hard_kills, clipped = [], [], [], []
        for r in rows:
            base = _net_of(r, baseline_key)
            var = _net_of(r, net_key)
            if base is None or var is None:
                continue
            name = r.get("symbol") or (r.get("mint") or "")[:8]
            bcls, vcls = _classify(base), _classify(var)
            rec = {
                "symbol": name,
                "baseline_net": round(base, 4),
                "variant_net": round(var, 4),
                "variant_exit": r.get(f"{key}_exit") or r.get(net_key.replace("_net", "_exit")),
            }
            if bcls == "loss" and var > base:
                if vcls in ("win", "be"):
                    saved.append(rec)
                elif var >= SMALL_LOSS:
                    small.append(rec)
            if bcls == "win":
                if vcls in ("loss", "be"):
                    hard_kills.append(rec)
                elif var < base - 0.10:
                    clipped.append(rec)
        out.append({
            "key": key,
            "label": label,
            "baseline": baseline_key,
            "saved_to_win_or_be": saved,
            "saved_to_small_loss": small,
            "winners_killed": hard_kills,
            "winners_clipped": clipped,
        })
    return out


def format_kill_save_table(rows: list[dict]) -> str:
    comps = compare_to_baseline(rows)
    lines = [
        "Losers converted / winners wrongly killed versus the matching no-stop 5-minute baseline "
        f"(small loss means variant net >= ${SMALL_LOSS:.2f} and better than the baseline). "
        "A winner is 'killed' if the variant turns it into a loss or break-even, and 'clipped' "
        "if it stays a win but expectancy drops by more than $0.10.",
        "",
        "| Variant | Losers → win/BE | Losers → small loss | Winners killed | Winners clipped |",
        "|---|---|---|---|---|",
    ]
    def names(xs, limit=6):
        if not xs:
            return "—"
        bits = [f"{x['symbol']} (${x['baseline_net']:+.2f}→${x['variant_net']:+.2f})" for x in xs]
        extra = f" +{len(bits)-limit}" if len(bits) > limit else ""
        return ", ".join(bits[:limit]) + extra
    for c in comps:
        lines.append(
            f"| {c['label']} | {names(c['saved_to_win_or_be'])} | {names(c['saved_to_small_loss'])} | "
            f"{names(c['winners_killed'])} | {names(c['winners_clipped'])} |"
        )
    return "\n".join(lines)


def write_curves(variants: dict[str, dict], dest: Path, plot_keys: list[str] | None = None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    # one CSV with all variants stacked
    fields = ["variant", "n", "mint", "symbol", "entry_ts", "launch_time_eat",
              "entry_t_plus", "net", "cum_pnl", "outcome", "drawdown_from_peak"]
    with dest.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for name, st in variants.items():
            for pt in st.get("curve") or []:
                w.writerow({"variant": name, **{k: pt.get(k, "") for k in fields if k != "variant"}})

    png = dest.with_suffix(".png")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 6.5))
        for name, st in variants.items():
            if plot_keys is not None and name not in plot_keys:
                continue
            ys = [pt["cum_pnl"] for pt in st.get("curve") or []]
            if not ys:
                continue
            ax.plot(range(1, len(ys) + 1), ys, label=st.get("label") or name, linewidth=1.6)
        ax.axhline(0, color="#888", linewidth=0.8)
        ax.set_xlabel("Trade # (ordered by entry time, Africa/Nairobi)")
        ax.set_ylabel("Cumulative P&L on $1 stakes ($)")
        ax.set_title("Rulebook v1 — cumulative P&L (key variants)")
        ax.legend(loc="best", fontsize=7)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(png, dpi=140)
        plt.close(fig)
        return str(png)
    except Exception as e:
        return f"plot_failed:{type(e).__name__}"


def aggregates(rows: list[dict]) -> dict:
    n = len(rows)
    def rate(key):
        xs = [int(r.get(key) or 0) for r in rows]
        return (sum(xs), n, (sum(xs) / n if n else 0))
    def stats(key):
        xs = _nums(rows, key)
        if not xs:
            return {"n": 0, "avg": None, "median": None, "min": None, "max": None}
        return {"n": len(xs), "avg": sum(xs)/len(xs), "median": median(xs), "min": min(xs), "max": max(xs)}
    by_dip = {}
    for r in rows:
        k = str(r.get("entry_threshold") or "?")
        by_dip.setdefault(k, []).append(r)
    dip = {}
    for k, rs in by_dip.items():
        h17 = sum(int(r.get("tp17_hit") or 0) for r in rs)
        h18 = sum(int(r.get("tp18_hit") or 0) for r in rs)
        nets = _nums(rs, "rule17_no_sl_net")
        dip[k] = {
            "n": len(rs),
            "hit_1_7": h17,
            "hit_1_8": h18,
            "hit_rate_1_7": h17 / len(rs) if rs else 0,
            "hit_rate_1_8": h18 / len(rs) if rs else 0,
            "avg_rule17_no_sl_net": (sum(nets)/len(nets)) if nets else None,
        }
    variants = {}
    plot_keys = []
    for key, net_key, label, plot, _cmp in BASELINE_VARIANTS:
        variants[key] = series_stats(rows, net_key, label)
        if plot:
            plot_keys.append(key)
    for sp in DYNAMIC_SPECS:
        variants[sp["key"]] = series_stats(rows, f"{sp['key']}_net", sp["label"])
        if sp.get("plot"):
            plot_keys.append(sp["key"])
    # strip bulky curves from json summary (kept in CSV)
    slim = {}
    for k, st in variants.items():
        slim[k] = {kk: vv for kk, vv in st.items() if kk != "curve"}
    return {
        "n": n,
        "tape_analyze_30m": sum(1 for r in rows if r.get("tape_source") == "analyze_30m"),
        "tape_prescreen_5m": sum(1 for r in rows if r.get("tape_source") == "prescreen_5m"),
        "tp17_hits": rate("tp17_hit"),
        "tp18_hits": rate("tp18_hit"),
        "rugged_or_died": rate("rugged_or_died"),
        "rule17_no_sl_net": stats("rule17_no_sl_net"),
        "rule18_no_sl_net": stats("rule18_no_sl_net"),
        "rule17_sl_net": stats("rule17_sl_net"),
        "rule18_sl_net": stats("rule18_sl_net"),
        "rule17_sl50_net": stats("rule17_sl50_net"),
        "rule18_sl50_net": stats("rule18_sl50_net"),
        "timestop_net": stats("timestop_net"),
        "worst_rule17_no_sl": min(_nums(rows, "rule17_no_sl_net") or [None]),
        "worst_rule17_sl": min(_nums(rows, "rule17_sl_net") or [None]),
        "worst_rule17_sl50": min(_nums(rows, "rule17_sl50_net") or [None]),
        "by_dip": dip,
        "series": slim,
        "mints": [r["mint"] for r in rows],
        "fade_note": FADE_NOTE,
        "combo_note": COMBO_NOTE,
        "_variants": variants,  # caller may write curves then drop
        "_plot_keys": plot_keys,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-11-21")
    args = ap.parse_args()
    date = args.date
    global DATE_EAT, PRE_DIR, CSV_OUT
    DATE_EAT = date
    PRE_DIR = CACHE / ("prescreen" if date == "2026-11-21" else f"prescreen_{date}")
    CSV_OUT = SCREEN / f"backtest_{date}.csv"
    src = SCREEN / f"prescreen_{date}.csv"
    if not src.exists():
        raise SystemExit(f"missing {src}")
    launches = list(csv.DictReader(src.open()))
    by = {}
    for r in launches:
        if r.get("mint"):
            by[r["mint"]] = r
    passers = [r for r in by.values() if int(r.get("pass_v0") or 0)]
    print(f"backtest strict v0 passers={len(passers)} date={date}", flush=True)
    rows = [backtest_one(r) for r in passers]
    out = SCREEN / f"backtest_{date}.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    sim_rows = [r for r in rows if r.get("error") in (None, "")]
    flow_blocks = []
    for r in sim_rows:
        if _classify(_net_of(r, "rule17_no_sl_net")) != "loss":
            continue
        mint = r["mint"]
        trades, _ = load_analyze_trades(mint)
        if not trades:
            trades = load_prescreen_trades(mint)
        # rebuild entry from the stored sig / t_plus
        entry = None
        if r.get("entry_sig"):
            entry = next((t for t in trades if t.get("signature") == r.get("entry_sig")), None)
        if entry is None and r.get("entry_t_plus") not in (None, ""):
            et = int(float(r["entry_t_plus"]))
            entry = next((t for t in trades if int(t.get("t_plus_sec") or -1) == et and _px_usd(t)), None)
        if not trades or not entry:
            flow_blocks.append(f"No tape/entry for loser {r.get('symbol')} ({mint[:8]}…).")
            continue
        flow = post_entry_flow(trades, entry)
        block = format_flow_table(flow, r.get("symbol") or mint[:8])
        fade = flow.get("first_fade") or {}
        block += (
            f"\nBaseline no-SL 1.7x exit: {r.get('rule17_no_sl_exit')} net ${float(r.get('rule17_no_sl_net') or 0):+.4f}. "
            f"Fade variant exit: {r.get('fade_17_exit')} net ${float(r.get('fade_17_net') or 0):+.4f}"
            + (f" at +{r.get('fade_17_secs')}s." if r.get('fade_17_secs') not in (None, "") else ".")
        )
        if fade:
            block += (
                f" A volume-fade signal first printed {fade.get('secs_after_entry')}s after entry"
                + (f" at {fade['mult']:.2f}x." if fade.get("mult") is not None else ".")
            )
        flow_blocks.append(block)
        atomic_write_json(CACHE / f"backtest_{date}_loser_flow_{mint[:8]}.json", {
            "mint": mint, "symbol": r.get("symbol"), "flow": flow,
            "baseline_exit": r.get("rule17_no_sl_exit"),
            "baseline_net": r.get("rule17_no_sl_net"),
            "fade_17_exit": r.get("fade_17_exit"),
            "fade_17_net": r.get("fade_17_net"),
            "fade_17_secs": r.get("fade_17_secs"),
        })

    agg = aggregates(rows)
    variants = agg.pop("_variants", {})
    plot_keys = agg.pop("_plot_keys", None)
    png = write_curves(variants, CACHE / f"backtest_{date}_pnl.csv", plot_keys=plot_keys)
    agg["pnl_curve_csv"] = str(CACHE / f"backtest_{date}_pnl.csv")
    agg["pnl_curve_png"] = png
    agg["kill_save"] = compare_to_baseline(sim_rows)
    atomic_write_json(CACHE / f"backtest_{date}.summary.json", agg)
    if flow_blocks:
        print("\n".join(flow_blocks), flush=True)
        print("", flush=True)
    print(FADE_NOTE, flush=True)
    print(COMBO_NOTE, flush=True)
    print("", flush=True)
    print("## Compact variant table", flush=True)
    print(format_compact_table(variants), flush=True)
    print("", flush=True)
    print("## Losers converted / winners wrongly killed", flush=True)
    print(format_kill_save_table(sim_rows), flush=True)
    print("", flush=True)
    print("## Full variant table", flush=True)
    print(format_variant_table(variants), flush=True)
    print(json.dumps({k: v for k, v in agg.items() if k != "kill_save"}, indent=2, default=str), flush=True)
    print(f"wrote {out} and {png}", flush=True)


if __name__ == "__main__":
    main()
