#!/usr/bin/env python3
"""Walk-forward rulebook learning.

Train on day T (passers already through strict v0), propose at most 1–2
checkable tweaks (rulebook v1.x), freeze them, and evaluate unchanged on
the next day vs plain v1. Never tune on the test day.

Headline v1 path: TP 1.7x, no SL, 5-minute time stop (rule17_no_sl_net).
Tweaks may skip extra setups or switch the headline exit to the -35% SL path
or TP 1.8x. v0 screen cuts stay unchanged unless a tweak is explicitly a
post-screen skip (applied only at backtest time, not in prescreen.py).
"""
from __future__ import annotations

import csv
import json
import time
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screen.common import CACHE, NBO, SCREEN, atomic_write_json, eat_str
from screen.backtest import BE_EPS, _nums, aggregates, series_stats, write_curves, format_variant_table, format_compact_table, format_kill_save_table, FADE_NOTE, COMBO_NOTE

ROOT = Path("/workspace/pumpfun")
COINS = ROOT / "coins"
WF = CACHE / "walkforward"
WF.mkdir(parents=True, exist_ok=True)

# Features we can actually measure on a passer (prescreen entry + analyze summary).
FEATURE_SPEC = [
    ("entry_cum_wallets", "wallets at dip"),
    ("entry_cum_vol", "cumulative USD volume at dip"),
    ("entry_mcap", "mcap USD at dip"),
    ("entry_drop", "dip size %"),
    ("entry_t_plus", "entry t+ seconds"),
    ("launch_hour_eat", "launch hour EAT"),
    ("sniper_supply_pct", "sniper supply %"),
    ("bundle_supply_pct", "create-slot bundle supply %"),
    ("dev_buy_supply_pct", "dev buy supply %"),
    ("dev_sold_supply_pct_30m", "dev sold supply % in 30m"),
    ("entry_secs_from_high", "seconds from running high to dip"),
    ("entry_trades_per_wallet", "observed trades per wallet at dip"),
    ("entry_15s_vol", "prior-15s USD volume at entry"),
    ("entry_15s_trades", "prior-15s trade count at entry"),
    ("entry_15s_buy_sell_ratio", "prior-15s buy/sell USD ratio"),
    ("entry_10s_buy_sell_ratio", "prior-10s buy/sell USD ratio"),
    ("entry_10s_buys", "prior-10s buy count"),
    ("entry_15s_buys", "prior-15s buy count"),
    ("entry_drop_sells", "sells forming the drop"),
    ("entry_top1_seller_share", "largest seller share of drop sell volume"),
    ("entry_top2_seller_share", "top-2 seller share of drop sell volume"),
    ("entry_dev_sold_in_drop", "dev/creator sold during the drop"),
]

# Explicit skip thresholds to always evaluate (plus the percentile grid).
EXPLICIT_SKIPS = [
    ("entry_secs_from_high", "<", 10),
    ("entry_secs_from_high", "<", 15),
    ("entry_mcap", ">", 15000),
    ("entry_trades_per_wallet", "<", 1.2),
    ("entry_t_plus", ">", 180),
    ("entry_15s_vol", "<", 1000),
    ("entry_15s_trades", "<", 20),
    ("entry_top1_seller_share", ">", 0.40),
    ("entry_top1_seller_share", ">", 0.50),
    ("entry_top1_seller_share", ">", 0.60),
    ("entry_top2_seller_share", ">", 0.70),
    ("entry_top2_seller_share", ">", 0.80),
    ("entry_dev_sold_in_drop", ">", 0),
]


def _f(row, key):
    v = row.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify(net) -> str | None:
    if net is None:
        return None
    try:
        x = float(net)
    except (TypeError, ValueError):
        return None
    if x > BE_EPS:
        return "win"
    if x < -BE_EPS:
        return "loss"
    return "be"


def launch_hour(row) -> float | None:
    ts = row.get("launch_ts") or row.get("entry_ts")
    if ts in (None, ""):
        return None
    try:
        return float(datetime.fromtimestamp(int(float(ts)), tz=NBO).hour)
    except (TypeError, ValueError):
        return None


def enrich(backtest_row: dict, prescreen_row: dict | None, summary: dict | None) -> dict:
    r = dict(backtest_row)
    if prescreen_row:
        for k in ("entry_cum_wallets", "entry_cum_vol", "entry_cum_trades", "entry_mcap",
                  "entry_drop", "entry_t_plus", "entry_threshold", "slice_window",
                  "launch_ts", "migrated_5m", "migrate_sec", "n_ok_sigs_5m"):
            if r.get(k) in (None, "") and prescreen_row.get(k) not in (None, ""):
                r[k] = prescreen_row[k]
    if summary:
        for k in ("sniper_supply_pct", "bundle_supply_pct", "dev_buy_supply_pct",
                  "dev_sold_supply_pct_30m", "sniper_wallets", "bundle_wallets_create_slot",
                  "dev_buy_sol", "migrated_within_30m", "migrate_sec"):
            if summary.get(k) is not None:
                r[k] = summary.get(k)
    r["launch_hour_eat"] = launch_hour(r)
    r["outcome"] = _classify(r.get("rule17_no_sl_net"))
    return r


def load_day(date: str) -> list[dict]:
    bt_p = SCREEN / f"backtest_{date}.csv"
    pre_p = SCREEN / f"prescreen_{date}.csv"
    if not bt_p.exists():
        return []
    pre = {}
    if pre_p.exists():
        for r in csv.DictReader(pre_p.open()):
            if r.get("mint"):
                pre[r["mint"]] = r
    rows = []
    for r in csv.DictReader(bt_p.open()):
        if not r.get("mint"):
            continue
        summ = {}
        sp = COINS / r["mint"] / "summary.json"
        if sp.exists():
            try:
                summ = json.loads(sp.read_text())
            except Exception:
                summ = {}
        rows.append(enrich(r, pre.get(r["mint"]), summ))
    return rows


def feature_table(rows: list[dict]) -> dict:
    wins = [r for r in rows if r.get("outcome") == "win"]
    losses = [r for r in rows if r.get("outcome") == "loss"]
    out = {"n": len(rows), "n_win": len(wins), "n_loss": len(losses),
           "n_be": sum(1 for r in rows if r.get("outcome") == "be"), "features": {}}
    for key, label in FEATURE_SPEC:
        wv = [x for x in (_f(r, key) for r in wins) if x is not None]
        lv = [x for x in (_f(r, key) for r in losses) if x is not None]
        if not wv and not lv:
            continue
        out["features"][key] = {
            "label": label,
            "win_n": len(wv), "loss_n": len(lv),
            "win_median": median(wv) if wv else None,
            "loss_median": median(lv) if lv else None,
            "win_mean": (sum(wv) / len(wv)) if wv else None,
            "loss_mean": (sum(lv) / len(lv)) if lv else None,
            "win_min": min(wv) if wv else None, "win_max": max(wv) if wv else None,
            "loss_min": min(lv) if lv else None, "loss_max": max(lv) if lv else None,
        }
    return out


def _apply_tweak_row(row: dict, tweak: dict) -> bool:
    """Return False if this tweak skips the trade."""
    typ = tweak.get("type")
    if typ != "skip_if":
        return True
    val = _f(row, tweak["field"])
    if val is None:
        return True  # missing feature: don't skip (conservative)
    thr = float(tweak["value"])
    op = tweak.get("op", "<")
    if op == "<" and val < thr:
        return False
    if op == "<=" and val <= thr:
        return False
    if op == ">" and val > thr:
        return False
    if op == ">=" and val >= thr:
        return False
    return True


def apply_rules(rows: list[dict], rules: dict) -> tuple[list[dict], str]:
    """Filter rows and choose the headline net key for v1.x."""
    kept = []
    for r in rows:
        ok = True
        for tw in rules.get("tweaks") or []:
            if not _apply_tweak_row(r, tw):
                ok = False
                break
        if ok:
            kept.append(r)
    net_key = "rule17_no_sl_net"
    label = "TP 1.7x, no SL, 5-min time stop"
    for tw in rules.get("tweaks") or []:
        if tw.get("type") == "exit":
            if tw.get("use_sl"):
                net_key = "rule17_sl_net"
                label = "TP 1.7x, -35% SL, 5-min time stop"
            if tw.get("tp") == 1.8:
                net_key = "rule18_sl_net" if tw.get("use_sl") else "rule18_no_sl_net"
                label = f"TP 1.8x, {'-35% SL' if tw.get('use_sl') else 'no SL'}, 5-min time stop"
    return kept, net_key, label


def headline_stats(rows: list[dict], net_key: str, label: str) -> dict:
    st = series_stats(rows, net_key, label)
    return {k: v for k, v in st.items() if k != "curve"}


def _eval_skip(rows, field, op, thr, net_key="rule17_no_sl_net"):
    kept = []
    skipped = []
    for r in rows:
        tw = {"type": "skip_if", "field": field, "op": op, "value": thr}
        if _apply_tweak_row(r, tw):
            kept.append(r)
        else:
            skipped.append(r)
    if not kept:
        return None
    base = _nums(rows, net_key)
    kn = _nums(kept, net_key)
    if not base or not kn:
        return None
    exp0 = sum(base) / len(base)
    exp1 = sum(kn) / len(kn)
    win0 = sum(1 for r in rows if _classify(r.get(net_key)) == "win") / len(rows)
    win1 = sum(1 for r in kept if _classify(r.get(net_key)) == "win") / len(kept)
    skip_loss = sum(1 for r in skipped if _classify(r.get(net_key)) == "loss")
    skip_win = sum(1 for r in skipped if _classify(r.get(net_key)) == "win")
    return {
        "field": field, "op": op, "value": thr,
        "n0": len(rows), "n1": len(kept), "n_skipped": len(skipped),
        "exp0": exp0, "exp1": exp1, "lift": exp1 - exp0,
        "win0": win0, "win1": win1, "win_lift": win1 - win0,
        "skipped_losses": skip_loss, "skipped_wins": skip_win,
    }


def propose_tweaks(rows: list[dict], trained_on: list[str], min_n: int = 8) -> dict:
    """At most two simple tweaks, or none if evidence is thin."""
    n = len(rows)
    train_set = set(trained_on)
    if trained_on == ["2026-11-22"]:
        version = "v1.1"
    elif train_set >= {"2026-11-22", "2026-11-21", "2026-11-20"} and "2026-11-19" not in train_set:
        version = "v1.3"
    elif train_set >= {"2026-11-22", "2026-11-21"} and "2026-11-20" not in train_set:
        version = "v1.2"
    else:
        version = f"v1.x_{'_'.join(trained_on)}"
    rules = {
        "version": version,
        "base": "v1",
        "trained_on": list(trained_on),
        "trained_n": n,
        "headline_v1": "rule17_no_sl_net",
        "tweaks": [],
        "rejected": [],
        "note": "",
    }
    if n < min_n:
        rules["note"] = f"No tweak: only {n} train trades (need >= {min_n}). v1.x identical to v1."
        rules["version"] = "v1" if n == 0 else rules["version"]
        return rules

    net_key = "rule17_no_sl_net"
    candidates = []

    # Exit tweak: -35% SL if it clearly improves expectancy and doesn't wreck wins.
    sl = _nums(rows, "rule17_sl_net")
    nosl = _nums(rows, "rule17_no_sl_net")
    if sl and nosl and len(sl) == len(nosl):
        exp_sl = sum(sl) / len(sl)
        exp_ns = sum(nosl) / len(nosl)
        if exp_sl >= exp_ns + 0.04:
            rules_try = {"tweaks": [{"type": "exit", "use_sl": True}]}
            # record as candidate
            candidates.append({
                "kind": "exit",
                "tweak": {
                    "id": "use_sl35",
                    "type": "exit",
                    "use_sl": True,
                    "evidence": (
                        f"In-sample n={n}: headline no-SL expectancy ${exp_ns:.3f} vs "
                        f"-35% SL ${exp_sl:.3f} (lift ${exp_sl-exp_ns:.3f})."
                    ),
                },
                "lift": exp_sl - exp_ns,
                "n1": n,
            })

    # Skip filters: try a few robust thresholds per feature.
    for key, label in FEATURE_SPEC:
        vals = [( _f(r, key), r) for r in rows if _f(r, key) is not None]
        if len(vals) < min_n:
            continue
        xs = sorted(v for v, _ in vals)
        # candidate cuts: 25/40/50/60/75th percentiles, rounded nicely
        fracs = (0.25, 0.4, 0.5, 0.6, 0.75)
        raw = sorted({xs[min(len(xs)-1, max(0, int(round(f*(len(xs)-1)))))] for f in fracs})
        # also try rounded coins/pct
        grid = set()
        for t in raw:
            if key.endswith("_pct") or key == "entry_drop":
                grid.update({round(t), round(t / 5) * 5, math.floor(t), math.ceil(t)})
            elif key in ("entry_cum_wallets", "entry_t_plus", "launch_hour_eat"):
                grid.update({round(t), int(t), int(round(t / 10) * 10), int(round(t / 5) * 5)})
            else:
                # usd
                step = 500 if t >= 2000 else 100
                grid.update({round(t), round(t / step) * step})
        for thr in sorted(grid):
            for op in ("<", ">", "<=", ">="):
                ev = _eval_skip(rows, key, op, thr, net_key)
                if not ev:
                    continue
                # keep enough of the sample; skip mostly losers; lift expectancy
                # "Mostly losers" = more losers skipped than winners, at least one loser.
                # Remaining n must be >= 40% of the train set (the stated acceptance rule).
                if ev["n1"] < max(2, int(math.ceil(0.4 * n))):
                    continue
                if ev["lift"] < 0.04:
                    continue
                if ev["skipped_losses"] < 1 or ev["skipped_losses"] <= ev["skipped_wins"]:
                    continue
                ev["kind"] = "skip"
                ev["tweak"] = {
                    "id": f"skip_{key}_{op}_{thr}",
                    "type": "skip_if",
                    "field": key,
                    "op": op,
                    "value": thr,
                    "label": label,
                    "evidence": (
                        f"In-sample n={ev['n0']}: skip if {label} {op} {thr} drops "
                        f"{ev['n_skipped']} trades ({ev['skipped_losses']} losers, {ev['skipped_wins']} winners); "
                        f"expectancy ${ev['exp0']:.3f} -> ${ev['exp1']:.3f} (lift ${ev['lift']:.3f}), "
                        f"win rate {ev['win0']*100:.1f}% -> {ev['win1']*100:.1f}%, remaining n={ev['n1']}."
                    ),
                }
                candidates.append(ev)

    # Always also try the pre-specified loser-avoidance thresholds.
    labels = dict(FEATURE_SPEC)
    seen = {(c["tweak"].get("field"), c["tweak"].get("op"), c["tweak"].get("value"))
            for c in candidates if c.get("tweak", {}).get("type") == "skip_if"}
    for key, op, thr in EXPLICIT_SKIPS:
        ev = _eval_skip(rows, key, op, thr, net_key)
        if not ev:
            continue
        if ev["n1"] < max(2, int(math.ceil(0.4 * n))):
            continue
        if ev["lift"] < 0.04:
            continue
        if ev["skipped_losses"] < 1 or ev["skipped_losses"] <= ev["skipped_wins"]:
            continue
        sig = (key, op, thr)
        if sig in seen:
            continue
        seen.add(sig)
        ev["kind"] = "skip"
        ev["tweak"] = {
            "id": f"skip_{key}_{op}_{thr}",
            "type": "skip_if",
            "field": key,
            "op": op,
            "value": thr,
            "label": labels.get(key, key),
            "evidence": (
                f"In-sample n={ev['n0']}: skip if {labels.get(key, key)} {op} {thr} drops "
                f"{ev['n_skipped']} trades ({ev['skipped_losses']} losers, {ev['skipped_wins']} winners); "
                f"expectancy ${ev['exp0']:.3f} -> ${ev['exp1']:.3f} (lift ${ev['lift']:.3f}), "
                f"win rate {ev['win0']*100:.1f}% -> {ev['win1']*100:.1f}%, remaining n={ev['n1']}."
            ),
        }
        candidates.append(ev)

    # Rank: expectancy lift, then fewer skipped winners, then larger remaining n
    candidates.sort(key=lambda c: (-c.get("lift", 0), c.get("skipped_wins", 0), -c.get("n1", 0)))

    # Dedup correlated skips (same field)
    chosen = []
    used_fields = set()
    used_exit = False
    for c in candidates:
        tw = c["tweak"]
        if tw["type"] == "exit":
            if used_exit:
                rules["rejected"].append({**tw, "why": "already have an exit tweak"})
                continue
            chosen.append(tw)
            used_exit = True
        else:
            field = tw["field"]
            family = "seller_share" if "seller_share" in str(field) else field
            if family in used_fields or field in used_fields:
                rules["rejected"].append({**tw, "why": "same feature already used"})
                continue
            # A second skip must catch at least one additional loser; otherwise it only
            # costs winners (e.g. drop_sells<=12 on top of top1>50%).
            if any(x.get("type") == "skip_if" for x in chosen):
                extra_losers = int(c.get("skipped_losses") or 0)
                # conservative: if we already skipped some losers via the first tweak,
                # require this candidate's skip set to not be a subset of winner-heavy extras.
                if extra_losers <= (chosen[0].get("_skipped_losses") or 0):
                    # If skipped_losses isn't stored on the tweak, compare evidence n_skipped losers via candidate c
                    # We only add if this candidate skips strictly more losers than the first.
                    first_l = next((x.get("_skipped_losses") for x in chosen if x.get("type")=="skip_if"), 0) or 0
                    if (c.get("skipped_losses") or 0) <= first_l:
                        rules["rejected"].append({**tw, "why": "no additional losers beyond the first skip"})
                        continue
            tw = dict(tw)
            tw["_skipped_losses"] = c.get("skipped_losses")
            chosen.append(tw)
            used_fields.add(field)
            used_fields.add(family)
        if len(chosen) >= 2:
            break

    rules["tweaks"] = chosen
    if not chosen:
        rules["note"] = (
            f"No tweak met the evidence bar on n={n} "
            f"(need expectancy lift >= $0.04, skip mostly losers, keep >=40% of trades)."
        )
    else:
        rules["note"] = f"Accepted {len(chosen)} tweak(s) from {len(candidates)} candidates on n={n}."
    return rules


def compare(train_rules: dict, test_date: str, rows: list[dict]) -> dict:
    v1_stats = headline_stats(rows, "rule17_no_sl_net", "v1: TP 1.7x, no SL, 5-min stop")
    kept, key, label = apply_rules(rows, train_rules)
    vx_stats = headline_stats(kept, key, f"{train_rules.get('version')}: {label}")
    held = None
    if v1_stats.get("expectancy_per_1usd") is not None and vx_stats.get("expectancy_per_1usd") is not None:
        held = vx_stats["expectancy_per_1usd"] >= v1_stats["expectancy_per_1usd"]
    return {
        "test_date": test_date,
        "rules_version": train_rules.get("version"),
        "trained_on": train_rules.get("trained_on"),
        "tweaks": train_rules.get("tweaks"),
        "n_v1": v1_stats.get("n"),
        "n_v1x": vx_stats.get("n"),
        "v1": v1_stats,
        "v1x": vx_stats,
        "held_up_out_of_sample": held,
        "expectancy_delta": (
            (vx_stats["expectancy_per_1usd"] - v1_stats["expectancy_per_1usd"])
            if (vx_stats.get("expectancy_per_1usd") is not None and v1_stats.get("expectancy_per_1usd") is not None)
            else None
        ),
        "win_pct_delta": (
            (vx_stats["win_pct"] - v1_stats["win_pct"])
            if (vx_stats.get("win_pct") is not None and v1_stats.get("win_pct") is not None)
            else None
        ),
    }


def write_report(path: Path, title: str, feat: dict, rules: dict, comparison: dict | None,
                 variant_rows: list[dict] | None = None):
    lines = [f"# {title}", "", f"Written {datetime.now(tz=NBO).strftime('%Y-%m-%d %H:%M:%S')} Africa/Nairobi (UTC+3).", ""]
    if feat:
        lines += [f"Train/sample size: n={feat.get('n')} wins={feat.get('n_win')} losses={feat.get('n_loss')} BE={feat.get('n_be')}.", "",
                  "## Winners vs losers (feature medians)", "",
                  "| Feature | Win median | Loss median | Win n | Loss n |",
                  "|---|---:|---:|---:|---:|"]
        for key, lab in FEATURE_SPEC:
            f = (feat.get("features") or {}).get(key)
            if not f:
                continue
            def fmt(x):
                return "" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))
            lines.append(f"| {lab} | {fmt(f['win_median'])} | {fmt(f['loss_median'])} | {f['win_n']} | {f['loss_n']} |")
        lines.append("")
    if rules:
        lines += [f"## Proposed {rules.get('version')}", "", rules.get("note") or "", ""]
        if not rules.get("tweaks"):
            lines.append("No tweaks — v1.x is identical to v1.")
        for tw in rules.get("tweaks") or []:
            lines.append(f"- **{tw.get('id')}** ({tw.get('type')}): {tw.get('evidence')}")
        lines.append("")
    if comparison:
        v1, vx = comparison.get("v1") or {}, comparison.get("v1x") or {}
        held = comparison.get("held_up_out_of_sample")
        lines += ["## Out-of-sample comparison", "",
                  f"Test day: {comparison.get('test_date')}. Rules trained on {comparison.get('trained_on')}.",
                  f"Held up (v1.x expectancy >= v1)? **{held}**.", "",
                  "| Book | n | Win % | Loss % | BE % | Expectancy /$1 | Cum P&L | Max DD | Longest win streak | Longest loss streak |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        def row(name, st):
            def pct(x):
                return "" if x is None else f"{100*x:.1f}%"
            def usd(x):
                return "" if x is None else f"${x:.3f}"
            sk = st.get("streaks") or {}
            return (f"| {name} | {st.get('n')} | {pct(st.get('win_pct'))} | {pct(st.get('loss_pct'))} | "
                    f"{pct(st.get('be_pct'))} | {usd(st.get('expectancy_per_1usd'))} | {usd(st.get('cum_pnl'))} | "
                    f"{usd(st.get('max_drawdown'))} | {sk.get('longest_win_streak')} | {sk.get('longest_loss_streak')} |")
        lines.append(row("v1", v1))
        lines.append(row(comparison.get("rules_version") or "v1.x", vx))
        lines.append("")
    if variant_rows:
        agg = aggregates(variant_rows)
        variants = agg.get("_variants") or agg.get("series") or {}
        # series in aggregates is slim (no curve); prefer _variants then slim
        if not variants:
            variants = agg.get("series") or {}
        lines += [
            "## Dynamic and stop/TP variants (all strict v0 passers, unfiltered)",
            "",
            FADE_NOTE,
            "",
            COMBO_NOTE,
            "",
            "### Compact (win %, expectancy, cum P&L, max DD)",
            "",
            format_compact_table(variants),
            "",
            "### Losers converted / winners wrongly killed",
            "",
            format_kill_save_table(variant_rows),
            "",
            "### Full variant table",
            "",
            format_variant_table(variants),
            "",
        ]
    path.write_text("\n".join(lines))
    return path


def learn(dates: list[str], version_hint: str | None = None) -> dict:
    rows = []
    for d in dates:
        rows.extend(load_day(d))
    feat = feature_table(rows)
    rules = propose_tweaks(rows, dates)
    if version_hint:
        rules["version"] = version_hint
    out = WF / f"rules_{rules['version']}_train_{'_'.join(dates)}.json"
    stamp = time.strftime("%Y%m%d_%H%M")
    if out.exists():  # never overwrite a frozen training record
        out = out.with_name(out.stem + f".relearn_{stamp}.json")
    atomic_write_json(out, {"rules": rules, "features": feat})
    write_report(WF / f"learn_{'_'.join(dates)}.md",
                 f"Walk-forward learn on {', '.join(dates)} → {rules['version']}", feat, rules, None,
                 variant_rows=rows)
    # also point a conventional name
    def _freeze(name: str):
        # A rulebook, once written, is frozen for its out-of-sample test; a re-learn goes beside it.
        dest = WF / name
        if dest.exists():
            alt = dest.with_name(dest.stem + f".relearn_{stamp}.json")
            atomic_write_json(alt, rules)
            print(f"{name} is frozen; not overwritten. re-learn saved to {alt}", flush=True)
        else:
            atomic_write_json(dest, rules)
    if dates == ["2026-11-22"]:
        _freeze("rulebook_v1.1.json")
    elif set(dates) >= {"2026-11-22", "2026-11-21"} and "2026-11-20" not in dates:
        _freeze("rulebook_v1.2.json")
    elif set(dates) >= {"2026-11-22", "2026-11-21", "2026-11-20"} and "2026-11-19" not in dates:
        _freeze("rulebook_v1.3.json")
    print(json.dumps(rules, indent=2, default=str), flush=True)
    print(f"wrote {out}", flush=True)
    return rules


def test(rules_path_or_dict, test_date: str) -> dict:
    if isinstance(rules_path_or_dict, (str, Path)):
        blob = json.loads(Path(rules_path_or_dict).read_text())
        rules = blob.get("rules") or blob
    else:
        rules = rules_path_or_dict
    rows = load_day(test_date)
    cmp_ = compare(rules, test_date, rows)
    dest = WF / f"test_{rules.get('version')}_on_{test_date}.json"
    atomic_write_json(dest, cmp_)
    write_report(WF / f"test_{rules.get('version')}_on_{test_date}.md",
                 f"Out-of-sample: {rules.get('version')} trained on {rules.get('trained_on')} → test {test_date}",
                 feature_table(rows), rules, cmp_, variant_rows=rows)
    print(json.dumps({k: v for k, v in cmp_.items() if k not in ("v1", "v1x") or True}, indent=2, default=str)[:4000], flush=True)
    print(f"wrote {dest}", flush=True)
    return cmp_


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("learn", "test"))
    ap.add_argument("--train", nargs="+", default=[])
    ap.add_argument("--test-date", default="")
    ap.add_argument("--rules", default="")
    args = ap.parse_args()
    if args.cmd == "learn":
        if not args.train:
            raise SystemExit("--train DATE [DATE...]")
        learn(args.train)
    else:
        if not args.test_date or not args.rules:
            raise SystemExit("--test-date DATE --rules PATH")
        test(args.rules, args.test_date)


if __name__ == "__main__":
    main()
