#!/usr/bin/env python3
"""Re-score all coins on first VOLUME-LIVE threshold touches."""
from __future__ import annotations
import csv, json, sys
from pathlib import Path
from statistics import mean, median
from collections import defaultdict

ROOT = Path("/workspace/pumpfun")
sys.path.insert(0, str(ROOT))
from lib.study import volume_active_at, simulate_trade
from lib.constants import DEFAULT_PRIORITY_SOL

COINS = sorted((ROOT / "coins").iterdir())
THRESHOLDS = (0.25, 0.30, 0.35, 0.40)
LABELS = {0.25: "25%", 0.30: "30%", 0.35: "35%", 0.40: "40%"}


def fnum(v):
    if v in ("", None, "None"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def inum(v):
    if v in ("", None, "None"):
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def load_trades(path: Path) -> list[dict]:
    rows = []
    with path.open() as f:
        for r in csv.DictReader(f):
            rows.append({
                "signature": r["signature"],
                "slot": inum(r.get("slot")),
                "block_time": inum(r.get("block_time")),
                "time_eat": r.get("time_eat"),
                "t_plus_sec": inum(r.get("t_plus_sec")),
                "side": r.get("side"),
                "is_dev": str(r.get("is_dev") or "").lower() in ("true", "1"),
                "price_sol": fnum(r.get("price_sol")),
                "price_usd": fnum(r.get("price_usd")),
                "mcap_usd": fnum(r.get("mcap_usd")),
                "volume_usd": fnum(r.get("volume_usd")) or 0.0,
                "sol_usd": fnum(r.get("sol_usd")),
            })
    # Keep CSV order: analyze.py already sorted by
    # (block_time, slot, is_create_tx first, signature). Re-sorting by
    # signature alone reorders same-slot prints and fabricates false open dips.
    return rows


def first_volume_live_touches(trades, thresholds=THRESHOLDS) -> dict:
    found = {}
    peak = None
    for tr in trades:
        px = tr.get("price_sol")
        if not px:
            continue
        if peak is None or px >= peak["price_sol"]:
            peak = tr
            continue
        dd = 1 - px / peak["price_sol"]
        va = volume_active_at(trades, tr["t_plus_sec"])
        if not va["active"]:
            continue
        for T in thresholds:
            if T not in found and dd >= T:
                found[T] = {
                    "trade": tr,
                    "peak": peak,
                    "drop_at_touch_pct": dd * 100,
                    "volume_active": True,
                    "va": va,
                    "peak_t_plus": peak.get("t_plus_sec"),
                    "peak_mcap_usd": peak.get("mcap_usd"),
                }
    return found


def first_any_touches(trades, thresholds=THRESHOLDS) -> dict:
    """Same as official first_threshold_touches (volume may be dead)."""
    found = {}
    peak = None
    for tr in trades:
        px = tr.get("price_sol")
        if not px:
            continue
        if peak is None or px >= peak["price_sol"]:
            peak = tr
            continue
        dd = 1 - px / peak["price_sol"]
        va = volume_active_at(trades, tr["t_plus_sec"])
        for T in thresholds:
            if T not in found and dd >= T:
                found[T] = {
                    "t_plus": tr["t_plus_sec"],
                    "drop": dd * 100,
                    "va": va["active"],
                }
    return found


def bucket(t):
    if t is None:
        return None
    t = int(t)
    if 0 <= t <= 30:
        return "opening"
    if 30 < t <= 300:
        return "mid"
    return "late"


def summarize(rows, title):
    print(f"\n===== {title}  n={len(rows)} =====")
    if not rows:
        print("  (none)")
        return {}
    out = {"n": len(rows), "names": [(r["name"], r["t"], r["drop"]) for r in rows]}
    print("  coins:", ", ".join(f"{r['name']} t+{r['t']}s ({r['drop']:.1f}%)" for r in rows))
    for tgt, hitk, netk, seck, ddk in (
        ("1.7x", "hit_1_7", "net_1_7", "secs_1_7", "dd_1_7"),
        ("1.8x", "hit_1_8", "net_1_8", "secs_1_8", "dd_1_8"),
        ("2.0x", "hit_2_0", "net_2_0", "secs_2_0", "dd_2_0"),
    ):
        hits = [r for r in rows if r[hitk]]
        misses = [r for r in rows if not r[hitk]]
        hr = len(hits) / len(rows)
        avg_net_hits = mean([r[netk] for r in hits if r[netk] is not None]) if hits else None
        # P&L if TP else 5m time stop
        pnl = []
        for r in rows:
            if r[hitk] and r[netk] is not None:
                pnl.append(r[netk])
            elif r["stop_net"] is not None:
                pnl.append(r["stop_net"])
        avg_pnl = mean(pnl) if pnl else None
        times = [r[seck] for r in hits if r[seck] is not None]
        dds = [r[ddk] for r in hits if r[ddk] is not None]
        print(f"  {tgt}: {len(hits)}/{len(rows)} ({hr:.1%})"
              f"  avg net on hits ${avg_net_hits:.3f}" if avg_net_hits is not None else f"  {tgt}: {len(hits)}/{len(rows)} ({hr:.1%}) no hit nets")
        if avg_pnl is not None:
            print(f"        avg net if TP-else-5m-stop ${avg_pnl:.3f}")
        if times:
            print(f"        avg time to target {mean(times):.0f}s (med {median(times):.0f}s, min {min(times):.0f}, max {max(times):.0f})")
        if dds:
            print(f"        avg DD before target {mean(dds):.1f}% (med {median(dds):.1f}%, worst {min(dds):.1f}%)")
        out[tgt] = {
            "hits": len(hits), "n": len(rows), "hit_rate": hr,
            "avg_net_hits": avg_net_hits, "avg_net_tp_or_stop": avg_pnl,
            "avg_secs": mean(times) if times else None,
            "med_secs": median(times) if times else None,
            "min_secs": min(times) if times else None,
            "max_secs": max(times) if times else None,
            "avg_dd": mean(dds) if dds else None,
            "med_dd": median(dds) if dds else None,
            "worst_dd": min(dds) if dds else None,
        }
    # time stop for all
    stops = [r for r in rows if r["stop_net"] is not None]
    if stops:
        pos = sum(1 for r in stops if r["stop_net"] > 0)
        print(f"  5-min time stop: n={len(stops)}  avg net ${mean(r['stop_net'] for r in stops):.3f}"
              f"  avg mult {mean(r['stop_mult'] for r in stops if r['stop_mult'] is not None):.2f}x"
              f"  profitable {pos}/{len(stops)} ({pos/len(stops):.1%})"
              f"  avg DD in 5m {mean(r['stop_dd'] for r in stops if r['stop_dd'] is not None):.1f}%")
        # how often stop beats 2x TP (when both exist): if no 2x, stop is the exit
        beat = 0
        cmp_n = 0
        for r in stops:
            if r["hit_2_0"] and r["net_2_0"] is not None:
                cmp_n += 1
                if r["stop_net"] > r["net_2_0"]:
                    beat += 1
        print(f"        time stop beat 2x TP on {beat}/{cmp_n} hits (and is the exit on {len(rows)-sum(1 for r in rows if r['hit_2_0'])} misses)")
        out["time_stop"] = {
            "n": len(stops),
            "avg_net": mean(r["stop_net"] for r in stops),
            "avg_mult": mean(r["stop_mult"] for r in stops if r["stop_mult"] is not None),
            "profitable": pos,
            "avg_dd": mean(r["stop_dd"] for r in stops if r["stop_dd"] is not None),
            "beat_2x_on_hits": beat,
            "n_hits_compared": cmp_n,
        }
    return out


def main():
    all_rows = []
    official_vs_live = []
    for d in COINS:
        sp = d / "summary.json"
        tp = d / "trades.csv"
        if not sp.exists() or not tp.exists():
            continue
        s = json.loads(sp.read_text())
        trades = load_trades(tp)
        sol_usd = s.get("sol_usd_at_launch") or 69.0
        any_t = first_any_touches(trades)
        live = first_volume_live_touches(trades)
        print(f"\n--- {s.get('name')} ({d.name[:8]}) trades={len(trades)} ---")
        for T in THRESHOLDS:
            a = any_t.get(T)
            L = live.get(T)
            if a:
                print(f"  first any {LABELS[T]}: t+{a['t_plus']}s drop={a['drop']:.1f}% VA={a['va']}")
            else:
                print(f"  first any {LABELS[T]}: none")
            if not L:
                print(f"  first LIVE {LABELS[T]}: none")
                official_vs_live.append({
                    "name": s.get("name"), "threshold": LABELS[T],
                    "first_any_t": a["t_plus"] if a else None,
                    "first_any_va": a["va"] if a else None,
                    "first_live_t": None,
                })
                continue
            entry = L["trade"]
            sim = simulate_trade(
                trades, entry, sol_usd, DEFAULT_PRIORITY_SOL,
                f"live_{LABELS[T]}", "first volume-live touch",
                {"volume_active": True, "drop_at_touch_pct": L["drop_at_touch_pct"],
                 "va_n_trades": L["va"]["n_trades"], "va_volume_usd": L["va"]["volume_usd"]},
            )
            rec = {
                "name": s.get("name"),
                "symbol": s.get("symbol"),
                "mint": s.get("mint"),
                "threshold": LABELS[T],
                "T": T,
                "t": entry["t_plus_sec"],
                "bucket": bucket(entry["t_plus_sec"]),
                "drop": L["drop_at_touch_pct"],
                "mcap": entry.get("mcap_usd"),
                "peak_t": L["peak_t_plus"],
                "peak_mcap": L["peak_mcap_usd"],
                "va_n": L["va"]["n_trades"],
                "va_usd": L["va"]["volume_usd"],
                "hit_1_7": bool(sim.get("1_7_hit")),
                "net_1_7": sim.get("1_7_net_usd"),
                "secs_1_7": sim.get("1_7_secs_after_entry"),
                "dd_1_7": sim.get("1_7_max_dd_pct_before"),
                "hit_1_8": bool(sim.get("1_8_hit")),
                "net_1_8": sim.get("1_8_net_usd"),
                "secs_1_8": sim.get("1_8_secs_after_entry"),
                "dd_1_8": sim.get("1_8_max_dd_pct_before"),
                "hit_2_0": bool(sim.get("2_0_hit")),
                "net_2_0": sim.get("2_0_net_usd"),
                "secs_2_0": sim.get("2_0_secs_after_entry"),
                "dd_2_0": sim.get("2_0_max_dd_pct_before"),
                "stop_net": sim.get("time_stop_5m_net_usd"),
                "stop_mult": sim.get("time_stop_5m_mult"),
                "stop_dd": sim.get("time_stop_5m_dd"),
                "stop_secs": sim.get("time_stop_5m_secs"),
                "best_mult": sim.get("best_exit_mult"),
                "best_net": sim.get("best_exit_net_usd"),
                "sampled": bool(s.get("pumpswap_sampled")),
                "n_pumpswap": s.get("n_pumpswap_trades") or 0,
            }
            all_rows.append(rec)
            same = (a and a["t_plus"] == rec["t"] and a["va"] is True)
            official_vs_live.append({
                "name": s.get("name"), "threshold": LABELS[T],
                "first_any_t": a["t_plus"] if a else None,
                "first_any_va": a["va"] if a else None,
                "first_live_t": rec["t"],
                "same_as_official_first": same,
            })
            print(f"  first LIVE {LABELS[T]}: t+{rec['t']}s {rec['bucket']} drop={rec['drop']:.1f}% mcap=${rec['mcap']:.0f} "
                  f"VA {rec['va_n']}/ ${rec['va_usd']:.0f}  "
                  f"2x={rec['hit_2_0']} in {rec['secs_2_0']}s net={rec['net_2_0']} dd={rec['dd_2_0']}  "
                  f"stop {rec['stop_mult']}x net={rec['stop_net']}")

    # summaries
    summaries = {}
    for T, lab in LABELS.items():
        rows = [r for r in all_rows if r["T"] == T]
        summaries[f"{lab} all_live"] = summarize(rows, f"{lab} ALL volume-live (any time in 30m)")
        le5 = [r for r in rows if r["t"] is not None and r["t"] <= 300]
        summaries[f"{lab} <=5m"] = summarize(le5, f"{lab} volume-live AND t+<=300s")
        summaries[f"{lab} opening"] = summarize([r for r in le5 if r["bucket"] == "opening"], f"{lab} opening-dump t+0-30s")
        summaries[f"{lab} mid"] = summarize([r for r in le5 if r["bucket"] == "mid"], f"{lab} mid-tape t+30-300s")
        summaries[f"{lab} late"] = summarize([r for r in rows if r["bucket"] == "late"], f"{lab} late first-live t+>300s")

    out_csv = ROOT / "live_touch_rescore.csv"
    fields = list(all_rows[0].keys()) if all_rows else []
    with out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    out_json = ROOT / "live_touch_rescore.json"
    # make json serializable summaries (drop names tuples ok)
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(x) for x in o]
        if isinstance(o, tuple):
            return list(o)
        return o
    out_json.write_text(json.dumps({
        "rows": all_rows,
        "summaries": clean(summaries),
        "official_vs_live": official_vs_live,
    }, indent=2, default=str))
    print(f"\nwrote {out_csv} and {out_json}")


if __name__ == "__main__":
    main()
