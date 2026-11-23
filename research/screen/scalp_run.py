#!/usr/bin/env python3
"""Grid evaluation of scalp_setups families over cached full tapes (no RPC)."""
from __future__ import annotations
import csv, glob, json, os, sys, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scalp_setups as S

SCREEN = Path(__file__).resolve().parent
TAPES = SCREEN / "cache" / "scalp_tapes"
DAYS = ["2026-11-22", "2026-11-21", "2026-11-20"]

# Jeremy's hard rules: entry mcap $5k-$10k, entries t+30..300s, exit by <=120s, TP >= 1.5x (main target).
BAND = dict(mcap_min=5000, mcap_max=10000)
FLOORS = {
    "L1": dict(cum_vol=3000, cum_n=100, cum_w=50, live_n=5, **BAND),
    "L2": dict(cum_vol=5000, cum_n=150, cum_w=80, live_n=5, **BAND),
}
ENTRIES = {}
for fl, fp in FLOORS.items():
    for d in (0.20, 0.30):
        ENTRIES[f"A_dip{int(d*100)}_{fl}"] = ("A", dict(dip=d, dip_max=0.5, **fp))
    ENTRIES[f"B_brk_{fl}"] = ("B", dict(surge=2.0, high_age=10, bs_ratio=1.5, min_buy10=150, **fp))
    for d in (0.25, 0.35):
        ENTRIES[f"C_recl{int(d*100)}_{fl}"] = ("C", dict(dip=d, dip_max=0.6, hold_low=10, retrace=0.5, live_n10=4, **fp))
    for d in (0.20, 0.30):
        ENTRIES[f"E_conf{int(d*100)}_k2_{fl}"] = ("E", dict(dip=d, dip_max=0.5, k=2, **fp))
    ENTRIES[f"E_conf20_k3_{fl}"] = ("E", dict(dip=0.20, dip_max=0.5, k=3, **fp))
    for r in (1.5, 2.0):
        ENTRIES[f"H_press{r:g}_nb4_{fl}"] = ("H", dict(ratio=r, new_buyers=4, min_buy10=150, **fp))
FLOORS0 = dict(cum_vol=1500, cum_n=50, cum_w=30, live_n=5, **BAND)
for fl, fp in (("L0", FLOORS0), ("L1", FLOORS["L1"]), ("L2", FLOORS["L2"])):
    for r in (1.0, 2.0):
        ENTRIES[f"K_band_r{r:g}_{fl}"] = ("K", dict(ratio=r, live_n10=4, **fp))
# I = steady-curve filter layered on each family (L1 floors)
for base in ("A_dip20_L1", "B_brk_L1", "C_recl25_L1", "E_conf20_k2_L1", "H_press2_nb4_L1", "K_band_r1_L1"):
    fam, p = ENTRIES[base]
    ENTRIES["I_" + base] = (fam, dict(p, steady=True))

# Rug/dump pre-filter (chosen on Sunday in scalp_rug.py; entry-time info only). Applied to EVERY entry.
RUG = {"creator_hold": (">", 0.08), "bundle_hold": (">", 0.25), "uniq60": ("<", 0.35)}
for _k, (_fam, _p) in list(ENTRIES.items()):
    ENTRIES[_k] = (_fam, dict(_p, rug=RUG))

_inv = dict(inval_low=True, inval_sec=60, inval_flow=True)
EXITS = {}
for tp in (1.5, 1.6, 1.7):
    for h in (30, 60, 90, 120):
        EXITS[f"tp{tp:g}_h{h}_fade"] = dict(tp=tp, hold=h, fade=True)
    for h in (60, 120):
        EXITS[f"G_tp{tp:g}_h{h}_fade_inv"] = dict(tp=tp, hold=h, fade=True, **_inv)       # G fast invalidation
        EXITS[f"tp{tp:g}_h{h}_stall20_fade"] = dict(tp=tp, hold=h, fade=True, stall=20)
        EXITS[f"Hx_tp{tp:g}_h{h}_flip10_inv"] = dict(tp=tp, hold=h, fade=False, flip10=True, **_inv)
    EXITS[f"tp{tp:g}_h120_nofade"] = dict(tp=tp, hold=120, fade=False)
    EXITS[f"G_tp{tp:g}_h90_fade_inv_stall20"] = dict(tp=tp, hold=90, fade=True, stall=20, **_inv)
    EXITS[f"G_tp{tp:g}_h120_fade_inv_be20"] = dict(tp=tp, hold=120, fade=True, be=0.20, **_inv)
# F scale-out: partial earlier, main target >= 1.5x
for first in (1.2, 1.25):
    for rest in (1.5, 1.7):
        EXITS[f"F_half{first:g}_rest{rest:g}_h120_inv"] = {"first": dict(tp=first, hold=60, fade=True, **_inv),
                                                           "rest": dict(tp=rest, hold=120, fade=True, **_inv)}
EXITS["F_half1.25_rest1.5_h90"] = {"first": dict(tp=1.25, hold=60, fade=True), "rest": dict(tp=1.5, hold=90, fade=True)}


def load_day(day: str):
    plan = json.load(open(TAPES / f"plan_{day}.json"))
    v0 = {r["mint"] for r in csv.DictReader(open(SCREEN / f"prescreen_{day}.csv")) if r.get("pass_v0") == "1"}
    coins = []
    for m, info in plan["mints"].items():
        p = TAPES / day / f"{m}.json.gz"
        if not p.exists():
            continue
        tape = S.load_tape(str(p))
        f = S.build_features(tape["trades"]); f.creator = tape.get("creator")
        coins.append({"mint": m, "symbol": tape.get("symbol"), "t0": tape["t0"], "tier": info["tier"], "f": f, "v0": m in v0})
    # weights: tier universe / fetched in tier
    have = {}
    for c in coins:
        have[c["tier"]] = have.get(c["tier"], 0) + 1
    tw = {i: t["universe"] / max(1, have.get(i, 0)) for i, t in enumerate(plan["tiers"])}
    for c in coins:
        c["w"] = tw[c["tier"]]
    return coins, plan, tw


PAPER = "live-2026-11-23"


def load_paper():
    """Today's live paper-trader tapes (read-only). Kept only for coins the paper trader decided on
    (mid-tape dips) -> dip-biased universe, unweighted (w=1), per-trade quality only."""
    import gzip
    coins = []
    for p in sorted(glob.glob(str(SCREEN.parent / "paper" / "logs" / "tapes" / "*.json.gz"))):
        try:
            b = json.load(gzip.open(p))
        except Exception:
            continue
        t = S.from_paper_tape(b)
        f = S.build_features(t["trades"]); f.creator = t.get("creator")
        coins.append({"mint": t["mint"], "symbol": t.get("symbol"), "t0": t["t0"], "tier": -1, "f": f, "v0": False, "w": 1.0})
    return coins, {"tiers": []}, {}


def evaluate(days=DAYS, entries=ENTRIES, exits=EXITS):
    res = {}
    meta = {}
    for day in days:
        coins, plan, tw = load_paper() if day == PAPER else load_day(day)
        meta[day] = {"coins": len(coins), "tier_w": tw, "tiers": plan["tiers"], "v0_in_sample": sum(c["v0"] for c in coins)}
        for en, (fam, p) in entries.items():
            trades = []
            for c in coins:
                r = S.run_coin(c["f"], fam, p, exits)
                if r:
                    r.update(mint=c["mint"], symbol=c["symbol"], w=c["w"], v0=c["v0"], t0=c["t0"])
                    trades.append(r)
            trades.sort(key=lambda r: r["t0"] + r["entry_t"])
            res[(day, en)] = trades
    return res, meta


def table(res, day, en, ex):
    tr = res[(day, en)]
    rows = [dict(net=t["ex"][ex]["net"], hold_s=t["ex"][ex]["hold_s"], v0=t["v0"]) for t in tr]
    out = S.summarize(rows, [t["w"] for t in tr])
    if tr:
        hits = sorted(t["t_to_1.5"] for t in tr if t.get("t_to_1.5") is not None)
        out["reach15"] = len(hits) / len(tr)
        out["reach15_med_s"] = hits[len(hits) // 2] if hits else None
        out["reach15_est_day"] = sum(t["w"] for t in tr if t.get("t_to_1.5") is not None)
        out["tp_exits"] = sum(1 for t in tr if str(t["ex"][ex]["exit"]).startswith("tp")) / len(tr)
    return out


def base_rates(day):
    """Share of band coins ($5k-$10k at some point in t+30..300s) whose +1s fill saw >= 1.5x within 120s."""
    coins, plan, tw = load_paper() if day == PAPER else load_day(day)
    res = {}
    for mult in (1.5, 1.7):
        n_band = w_band = n_first = w_first = n_any = w_any = 0.0; fs = []
        for c in coins:
            b = S.base_rate(c["f"], mult=mult)
            if not b["in_band"]:
                continue
            n_band += 1; w_band += c["w"]
            if b["first_hit"]:
                n_first += 1; w_first += c["w"]; fs.append(b["first_hit_s"])
            if b["any_hit"]:
                n_any += 1; w_any += c["w"]
        fs.sort()
        res[mult] = {"coins_sampled": len(coins), "band_sampled": n_band, "band_est_day": w_band,
                     "first_entry_hit": n_first, "first_entry_hit_est_day": w_first,
                     "first_hit_share": n_first / n_band if n_band else None, "first_hit_med_s": fs[len(fs) // 2] if fs else None,
                     "any_entry_hit": n_any, "any_entry_hit_est_day": w_any, "any_hit_share": n_any / n_band if n_band else None}
    return res


if __name__ == "__main__":
    res, meta = evaluate()
    out = SCREEN / "cache" / "scalp_tapes" / "grid.pkl"
    pickle.dump({"res": res, "meta": meta}, open(out, "wb"))
    print(json.dumps(meta, indent=1, default=str)[:2000])
