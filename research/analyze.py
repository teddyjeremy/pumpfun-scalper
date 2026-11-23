#!/usr/bin/env python3
"""Analyze a Pump.fun coin's first 30 minutes for the scalping study.

Usage:
    /workspace/pumpfun/.venv/bin/python /workspace/pumpfun/analyze.py <MINT>
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path("/workspace/pumpfun")
sys.path.insert(0, str(ROOT))

from lib.constants import (
    WINDOW_SEC, PREFERRED_SEC, CURVE_FEE_BPS, DEFAULT_PRIORITY_SOL, DEFAULT_BASE_FEE_SOL,
    TOTAL_SUPPLY_UI, ACTIVE_LOOKBACK_SEC, ACTIVE_MIN_TRADES, ACTIVE_MIN_USD, GRADUATION_SOL,
)
from lib.fetch import (
    cache_dir, out_dir, resolve_curve, fetch_all_sigs, fetch_enhanced_transactions,
    fetch_transactions, first_signatures_window, fetch_sol_usd_klines, sol_price_fn,
    fetch_sigs_in_window, bound_pumpswap_ok_sigs,
    _load_cached_txs,
)
from lib.parse import parse_tx, account_keys
from lib.study import (
    build_trade_tape, bucket_timeline, find_dips, volume_active_at, pick_entries,
    simulate_trade, holders_snapshots, classify_early, make_chart, write_csv,
    upsert_dataset, eat_str, DATASET_FIELDS, EAT,
)
from lib.wallets import profile_wallet, fetch_raw_sigs, sol_pre_post, paginate_sigs
from lib.rpc import rpc_call
from datetime import datetime, timezone


def identify_creator(raws: dict, sigs: list[dict], mint: str, curve: str) -> tuple[str, str, dict | None]:
    create_sig = sigs[0]["signature"] if sigs else None
    tx = raws.get(create_sig) if create_sig else None
    creator = None
    if tx:
        row = parse_tx(create_sig, tx, mint, curve)
        # dev buy user or fee payer
        if row.get("_trades"):
            creator = row["_trades"][0].get("trader")
        creator = creator or row.get("fee_payer")
        return creator, create_sig, tx
    # fallback later from first buy
    return creator or "", create_sig or "", tx


def median(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else 0.5 * (xs[mid - 1] + xs[mid])


def analyze(mint: str, skip_wallets_if_present: bool = False, cache_only: bool = False) -> dict:
    odir = out_dir(mint)
    cdir = cache_dir(mint)
    print(f"=== resolve {mint} ===", flush=True)
    info = resolve_curve(mint)
    curve = info.get("curve")
    if not curve:
        raise SystemExit(f"Could not resolve bonding curve for {mint}")
    print(f"name={info.get('name')} symbol={info.get('symbol')} curve={curve} source={info.get('curve_source')}", flush=True)
    (cdir / "meta.json").write_text(json.dumps(info, indent=2, default=str))

    print("=== signatures ===", flush=True)
    sigs = fetch_all_sigs(curve, cdir / "sigs.json")
    if not sigs:
        raise SystemExit("no signatures on bonding curve")
    window, t0, slot0 = first_signatures_window(sigs, WINDOW_SEC)
    create_sig = sigs[0]["signature"]
    print(f"curve sigs={len(sigs)} window={len(window)} t0={t0} ({eat_str(t0)}) slot0={slot0}", flush=True)

    # SOL prices
    klines = fetch_sol_usd_klines(t0 - 120, t0 + WINDOW_SEC + 120, cdir / "sol_klines.json")
    sol_px = sol_price_fn(klines)

    # fetch txs: enhanced all ok+early fail; raw for early slots if missing
    early_slots = 20
    raw_slots = 8  # raw pre/post balances + failed snipes; enhanced covers the rest of the tape
    need, seen = [], set()
    early_need = []
    for s in window:
        take = (not s.get("err")) or (s["slot"] <= slot0 + early_slots)
        if take and s["signature"] not in seen:
            need.append(s["signature"]); seen.add(s["signature"])
        if s["slot"] <= slot0 + raw_slots:
            early_need.append(s["signature"])
    print(f"=== enhanced fetch {len(need)} cache_only={cache_only} ===", flush=True)
    enhanced = fetch_enhanced_transactions(mint, [] if cache_only else need, batch_size=50, pause=0.2)
    print(f"enhanced={len(enhanced)}", flush=True)

    # If the coin migrated inside 30m, pull PumpSwap pair fills for the rest of the window
    pair = info.get("pumpswap_pair")
    migrate_sec_early = None
    if info.get("pumpswap_pair_created_ms"):
        migrate_sec_early = int(info["pumpswap_pair_created_ms"]) / 1000 - t0
    if pair and migrate_sec_early is not None and 0 <= migrate_sec_early <= WINDOW_SEC:
        print(f"=== pumpswap pair {pair} migrate_sec={migrate_sec_early:.1f} ===", flush=True)
        pair_sigs = fetch_sigs_in_window(pair, t0, t0 + WINDOW_SEC, cdir / "pumpswap_sigs.json", slot0=slot0)
        ok_pair_rows = [s for s in pair_sigs if not s.get("err")]
        print(f"pumpswap window sigs={len(pair_sigs)} ok={len(ok_pair_rows)}", flush=True)
        pumpswap_fetch_meta = {"ok_total": len(ok_pair_rows), "fetched": len(ok_pair_rows), "sampled": False, "method": "all"}
        if ok_pair_rows:
            if cache_only:
                ok_pair = [s["signature"] for s in ok_pair_rows]
                have_n = len(enhanced)
                missing = sum(1 for s in ok_pair if s not in enhanced)
                print(f"cache-only: pumpswap ok={len(ok_pair)} already cached ~{have_n}, not fetching {missing} missing", flush=True)
                to_get = []
                pumpswap_fetch_meta = {"ok_total": len(ok_pair), "fetched": have_n, "sampled": missing>0, "method": "cache_only"}
            else:
                ok_pair, pumpswap_fetch_meta = bound_pumpswap_ok_sigs(ok_pair_rows)
                to_get = ok_pair
            enhanced = fetch_enhanced_transactions(mint, to_get, batch_size=100, pause=0.05, concurrency=4)
            print(f"enhanced after pumpswap={len(enhanced)}", flush=True)
        # stash on info for summary
        info["_pumpswap_fetch_meta"] = pumpswap_fetch_meta
    else:
        pair = pair if (pair and migrate_sec_early is not None and 0 <= migrate_sec_early <= WINDOW_SEC) else None

    # raw for early slots + create (balances, failed snipes)
    print(f"=== raw early {len(early_need)} ===", flush=True)
    raws = fetch_transactions(mint, early_need, concurrency=1, batch_size=8)
    # also load whatever raw we already have
    raws.update(_load_cached_txs(cdir))
    print(f"raw cached={len(raws)}", flush=True)

    creator, create_sig, create_tx = identify_creator(raws, sigs, mint, curve)
    if not creator:
        # from first enhanced/raw trade
        for s in window[:20]:
            if s["signature"] in raws:
                row = parse_tx(s["signature"], raws[s["signature"]], mint, curve)
                if row.get("is_create") and row.get("fee_payer"):
                    creator = row["fee_payer"]; break
    if not creator:
        for s in window[:20]:
            item = enhanced.get(s["signature"])
            if not item:
                continue
            typ = (item.get("type") or "")
            if typ in ("CREATE", "CREATE_ACCOUNT") or (item.get("feePayer") and typ == "CREATE"):
                creator = item.get("feePayer")
                break
            if typ == "CREATE" or "create" in (item.get("description") or "").lower()[:40]:
                creator = item.get("feePayer")
                break
        if not creator and window:
            item = enhanced.get(window[0]["signature"]) or {}
            creator = item.get("feePayer") or ""
    print(f"creator={creator}", flush=True)

    print("=== tape ===", flush=True)
    trades, rows_by_sig = build_trade_tape(mint, curve, creator, t0, sol_px, raws, enhanced, amm_pool=pair)
    n_curve = sum(1 for x in trades if (x.get("venue") or "curve") == "curve")
    n_amm = sum(1 for x in trades if x.get("venue") == "pumpswap")
    print(f"trades in 30m={len(trades)} curve={n_curve} pumpswap={n_amm}", flush=True)

    sol_usd0 = float(sol_px(t0))
    name = info.get("name") or "Unknown"
    symbol = info.get("symbol") or ""

    # median tips/priority from trades after t+60s (more "retail")
    late = [t for t in trades if t["t_plus_sec"] >= 60]
    med_prio = median([t.get("priority_sol") for t in late if t.get("priority_sol")]) or DEFAULT_PRIORITY_SOL
    med_jito = median([t.get("jito_tip_sol") for t in late]) or 0.0
    # assumed gas for $1 retail scalp: measured median priority if reasonable, else default, plus default we stated
    assumed_prio = DEFAULT_PRIORITY_SOL
    if med_prio and 0.00001 <= med_prio <= 0.002:
        assumed_prio = med_prio

    # ATH
    ath = max(trades, key=lambda t: t.get("mcap_usd") or 0) if trades else None

    tl1 = bucket_timeline(trades, t0, 60, WINDOW_SEC)
    tl15 = bucket_timeline(trades, t0, 15, PREFERRED_SEC)
    dips = find_dips(trades, 0.25)
    for d in dips:
        va = volume_active_at(trades, d["trough_t_plus"])
        d["volume_active"] = va["active"]
        d["vol_15s_trades"] = va["n_trades"]
        d["vol_15s_usd"] = va["volume_usd"]

    entries = pick_entries(trades, creator, dips)
    sims = []
    for label, tr, reason, extra in entries:
        sims.append(simulate_trade(trades, tr, tr.get("sol_usd") or sol_usd0, assumed_prio, label, reason, extra))

    early = classify_early(trades, rows_by_sig, creator, slot0, t0, curve)
    snaps, final_bal = holders_snapshots(trades, curve, [300, 1800])

    # holders "now" is outside the first-30-minute window — skip current-chain pulls
    now_top = []
    now_holders = []
    now_top10_pct = None

    # dev activity
    dev_buys = [t for t in trades if t.get("is_dev") and t["side"] == "buy"]
    dev_sells = [t for t in trades if t.get("is_dev") and t["side"] == "sell"]
    dev_buy_sol = sum((t.get("sol_ui") or 0) for t in dev_buys)
    dev_buy_tok = sum(t["token_ui"] for t in dev_buys)
    dev_sell_tok = sum(t["token_ui"] for t in dev_sells)
    dev_sell_sol = sum((t.get("sol_ui") or 0) for t in dev_sells)

    # creator balances from raw create + later raw txs
    creator_bal_rows = []
    if create_tx and creator:
        pre, post = sol_pre_post(create_tx, creator)
        creator_bal_rows.append({
            "event": "just_before_create",
            "signature": create_sig,
            "slot": create_tx.get("slot"),
            "block_time": create_tx.get("blockTime"),
            "time_eat": eat_str(create_tx["blockTime"]) if create_tx.get("blockTime") else None,
            "sol_pre": (pre or 0) / 1e9,
            "sol_post": (post or 0) / 1e9,
            "delta_sol": ((post or 0) - (pre or 0)) / 1e9,
            "note": "pre is SOL just before the create+dev-buy transaction; post is after create+initial buy",
        })
        creator_bal_rows.append({
            "event": "after_create_and_dev_buy",
            "signature": create_sig,
            "slot": create_tx.get("slot"),
            "block_time": create_tx.get("blockTime"),
            "time_eat": eat_str(create_tx["blockTime"]) if create_tx.get("blockTime") else None,
            "sol_pre": (pre or 0) / 1e9,
            "sol_post": (post or 0) / 1e9,
            "delta_sol": ((post or 0) - (pre or 0)) / 1e9,
            "note": "same tx as create (BuyV2 in the create transaction)",
        })
    # any later creator txs we have raw for
    for t in dev_sells:
        tx = raws.get(t["signature"])
        if not tx:
            continue
        pre, post = sol_pre_post(tx, creator)
        if pre is None:
            continue
        creator_bal_rows.append({
            "event": "dev_sell",
            "signature": t["signature"],
            "slot": t.get("slot"),
            "block_time": t["block_time"],
            "time_eat": t.get("time_eat"),
            "sol_pre": pre / 1e9,
            "sol_post": post / 1e9,
            "delta_sol": (post - pre) / 1e9,
            "tokens_sold": t["token_ui"],
            "sol_from_curve": t.get("sol_ui"),
            "note": "pre/post SOL on the creator account at this sell",
        })
    # if we missed sells because no raw, fetch them
    missing = [t["signature"] for t in dev_sells if t["signature"] not in raws]
    if missing:
        more = fetch_raw_sigs(missing, cache_mint=mint)
        raws.update(more)
        for t in dev_sells:
            if any(r["signature"] == t["signature"] and r["event"] == "dev_sell" for r in creator_bal_rows):
                continue
            tx = raws.get(t["signature"])
            if not tx:
                continue
            pre, post = sol_pre_post(tx, creator)
            if pre is None:
                continue
            creator_bal_rows.append({
                "event": "dev_sell",
                "signature": t["signature"],
                "slot": t.get("slot"),
                "block_time": t["block_time"],
                "time_eat": t.get("time_eat"),
                "sol_pre": pre / 1e9,
                "sol_post": post / 1e9,
                "delta_sol": (post - pre) / 1e9,
                "tokens_sold": t["token_ui"],
                "sol_from_curve": t.get("sol_ui"),
                "note": "pre/post SOL on the creator account at this sell",
            })

    dev_sol_pre = creator_bal_rows[0]["sol_pre"] if creator_bal_rows else None
    dev_sol_post = next((r["sol_post"] for r in creator_bal_rows if r["event"] == "after_create_and_dev_buy"), None)

    # migrate?
    migrate_sec = None
    if info.get("pumpswap_pair_created_ms"):
        ms = int(info["pumpswap_pair_created_ms"]) / 1000
        migrate_sec = ms - t0
    migrated_30 = bool(migrate_sec is not None and 0 <= migrate_sec <= WINDOW_SEC)

    # wallet table — notable set
    print("=== wallets ===", flush=True)
    # per-wallet activity on this coin
    act = defaultdict(lambda: {"tokens_bought": 0.0, "tokens_sold": 0.0, "sol_spent": 0.0, "sol_received": 0.0,
                               "n_buys": 0, "n_sells": 0, "first_buy_t_plus": None, "first_sell_t_plus": None,
                               "first_sig": None, "priority_or_tip": 0.0})
    for t in trades:
        a = act[t["trader"]]
        if t["side"] == "buy":
            a["tokens_bought"] += t["token_ui"]
            a["sol_spent"] += t.get("sol_ui") or t.get("quote_ui") or 0
            a["n_buys"] += 1
            if a["first_buy_t_plus"] is None:
                a["first_buy_t_plus"] = t["t_plus_sec"]
                a["first_sig"] = t["signature"]
        else:
            a["tokens_sold"] += t["token_ui"]
            a["sol_received"] += t.get("sol_ui") or t.get("quote_ui") or 0
            a["n_sells"] += 1
            if a["first_sell_t_plus"] is None:
                a["first_sell_t_plus"] = t["t_plus_sec"]
        a["priority_or_tip"] = max(a["priority_or_tip"], (t.get("jito_tip_sol") or 0) + (t.get("priority_sol") or 0))
    for w, a in act.items():
        a["tokens_left_30m"] = a["tokens_bought"] - a["tokens_sold"]
        a["supply_pct_bought"] = a["tokens_bought"] / TOTAL_SUPPLY_UI * 100
        a["realized_sol_approx"] = a["sol_received"] - a["sol_spent"]

    notable = set()
    if creator:
        notable.add(creator)
    for w in list(early["snipers_slot0_3"].keys())[:15]:
        notable.add(w)
    for w in list(early["bundle_create_slot"].keys())[:15]:
        notable.add(w)
    for snap in snaps.values():
        for h in snap.get("top10") or []:
            notable.add(h["wallet"])
    # top 5 first-5s
    for w in list(early["first5s"].keys())[:10]:
        notable.add(w)

    wallet_rows = []
    skip_wallets = skip_wallets_if_present and (odir / "wallets.csv").exists()
    if skip_wallets:
        import csv as _csv
        with (odir / "wallets.csv").open() as _f:
            wallet_rows = list(_csv.DictReader(_f))
        print(f"=== wallets (skipped, loaded {len(wallet_rows)} existing rows) ===", flush=True)
        _num_keys = ("tokens_bought","tokens_sold","tokens_left_30m","sol_spent","sol_received",
                     "supply_pct_bought","first_buy_t_plus","first_sell_t_plus",
                     "wallet_age_sec_at_launch","sol_at_first_coin_tx_pre","funding_sol",
                     "n_buys","n_sells","realized_sol_approx","priority_or_tip")
        for _r in wallet_rows:
            for _k in _num_keys:
                _v = _r.get(_k)
                if _v in (None, "", "None"):
                    _r[_k] = None
                    continue
                try:
                    _r[_k] = float(_v)
                except (TypeError, ValueError):
                    pass
    else:
        print("=== wallets ===", flush=True)
    # Profile creator first (more pages), others fewer pages
    if not skip_wallets:
      for i, w in enumerate(sorted(notable, key=lambda x: (0 if x == creator else 1, x))):
        roles = {
            "creator": w == creator,
            "sniper_slot0_3": w in early["snipers_slot0_3"],
            "bundle_create_slot": w in early["bundle_create_slot"],
            "first5s_buyer": w in early["first5s"],
            "top10_5m": w in {h["wallet"] for h in (snaps.get(300) or {}).get("top10") or []},
            "top10_30m": w in {h["wallet"] for h in (snaps.get(1800) or {}).get("top10") or []},
        }
        cap = 12 if w == creator else 3
        try:
            row = profile_wallet(w, t0, slot0, mint, roles, dict(act.get(w, {})), cap_pages=cap)
        except Exception as e:
            row = {"wallet": w, "roles": ",".join(k for k, v in roles.items() if v),
                   "error": str(e), **dict(act.get(w, {}))}
        wallet_rows.append(row)
        if i % 5 == 0:
            print(f"  profiled {i+1}/{len(notable)}", flush=True)

    # Insider heuristic: funded by creator, or same funder as creator, or fresh wallet funded <10 min before launch
    creator_funder = next((r.get("funding_source") for r in wallet_rows if r.get("wallet") == creator), None)
    insiders = []
    for r in wallet_rows:
        if r.get("wallet") == creator:
            r["insider"] = False
            r["insider_reason"] = "is_creator"
            r["insider_confidence"] = "n/a"
            continue
        reasons = []
        conf = "low"
        if r.get("funding_source") and r.get("funding_source") == creator:
            reasons.append("funded_by_creator")
            conf = "high"
        if creator_funder and r.get("funding_source") == creator_funder:
            reasons.append("same_funder_as_creator")
            conf = "high" if conf != "high" else "high"
        age = r.get("wallet_age_sec_at_launch")
        try:
            age = float(age) if age not in (None, "", "None") else None
        except (TypeError, ValueError):
            age = None
        if age is not None and age <= 600 and r.get("funding_source"):
            reasons.append("fresh_funded_within_10m_of_launch")
            if conf == "low":
                conf = "medium"
        r["insider"] = bool(reasons)
        r["insider_reason"] = ",".join(reasons)
        r["insider_confidence"] = conf if reasons else "none"
        if reasons:
            insiders.append(r)

    insider_supply = sum((r.get("tokens_bought") or 0) for r in insiders)
    sniper_supply = sum(v["tokens"] for v in early["snipers_slot0_3"].values())
    bundle_supply = sum(v["tokens"] for v in early["bundle_create_slot"].values())
    sniper_sol = sum(v["sol"] for v in early["snipers_slot0_3"].values())

    # how much of early supply snipers/bundles/insiders dumped into the first 40% dip
    dips40 = [d for d in dips if d["drop_pct"] >= 38]
    dump_into_dip = {}
    if dips40:
        d0 = dips40[0]
        lo, hi = d0["peak_t_plus"], d0["trough_t_plus"]
        watched = set(early["snipers_slot0_3"]) | set(early["bundle_create_slot"]) | {r["wallet"] for r in insiders} | {creator}
        sold = [t for t in trades if t["side"] == "sell" and lo <= t["t_plus_sec"] <= hi and t["trader"] in watched]
        dump_into_dip = {
            "dip_drop_pct": d0["drop_pct"],
            "window_t_plus": [lo, hi],
            "tokens_sold_by_watched": sum(t["token_ui"] for t in sold),
            "sol_sold_by_watched": sum((t.get("sol_ui") or t.get("quote_ui") or 0) for t in sold),
            "n_sells": len(sold),
            "by_role": {
                "creator": sum(t["token_ui"] for t in sold if t["trader"] == creator),
                "snipers": sum(t["token_ui"] for t in sold if t["trader"] in early["snipers_slot0_3"]),
                "bundlers": sum(t["token_ui"] for t in sold if t["trader"] in early["bundle_create_slot"]),
                "insiders": sum(t["token_ui"] for t in sold if t["trader"] in {r["wallet"] for r in insiders}),
            },
        }

    # risk verdict from pre-entry observables
    flags = []
    if dev_buy_tok / TOTAL_SUPPLY_UI >= 0.05:
        flags.append(f"dev bought {dev_buy_tok/TOTAL_SUPPLY_UI*100:.1f}% at launch")
    if sniper_supply / TOTAL_SUPPLY_UI >= 0.10:
        flags.append(f"snipers (slot 0-3) got {sniper_supply/TOTAL_SUPPLY_UI*100:.1f}% supply")
    if bundle_supply / TOTAL_SUPPLY_UI >= 0.05:
        flags.append(f"create-slot bundled wallets got {bundle_supply/TOTAL_SUPPLY_UI*100:.1f}%")
    if insiders:
        flags.append(f"{len(insiders)} suspected insider wallets, {insider_supply/TOTAL_SUPPLY_UI*100:.1f}% supply")
    if snaps.get(300) and snaps[300]["top10_pct_circ"] >= 50:
        flags.append(f"top 10 holders {snaps[300]['top10_pct_circ']:.0f}% of circulating at 5m")
    if not flags:
        verdict = "No strong red flags in the early snapshot; still a memecoin scalp with dump risk."
    else:
        verdict = "Red flags before a mid-tape entry: " + "; ".join(flags) + "."

    # write files
    print("=== write outputs ===", flush=True)
    trade_cols = [
        "signature","slot","block_time","time_eat","t_plus_sec","side","trader","is_dev",
        "sol_ui","quote_ui","quote_mint","is_sol_quote","price_quote","quote_usd","token_ui","price_sol","price_usd","price_curve_sol","mcap_sol","mcap_usd","volume_usd",
        "fee_sol","creator_fee_sol","fee_bps","creator_fee_bps","tx_fee_sol","priority_sol","jito_tip_sol",
        "real_sol_ui","real_sol_ui_recon","curve_progress_pct","is_create_tx","source","venue","fee_payer",
    ]
    write_csv(odir / "trades.csv", trades, trade_cols)
    write_csv(odir / "timeline_1min.csv", tl1)
    write_csv(odir / "timeline_15s.csv", tl15)
    write_csv(odir / "dips.csv", dips)
    write_csv(odir / "simulations.csv", sims)
    write_csv(odir / "creator_balances.csv", creator_bal_rows)
    write_csv(odir / "wallets.csv", wallet_rows)
    # holders
    hold_rows = []
    for tp, snap in snaps.items():
        for h in snap.get("top10") or []:
            hold_rows.append({"snapshot": f"t+{tp}s", **h, "top10_pct_total": snap["top10_pct_total"],
                              "top10_pct_circ": snap["top10_pct_circ"], "n_holders": snap["n_holders"],
                              "circulating": snap["circulating"]})
    for h in now_holders[:10]:
        hold_rows.append({"snapshot": "now", **h, "top10_pct_total": now_top10_pct})
    write_csv(odir / "holders.csv", hold_rows)

    chart_path = odir / "chart_30m.png"
    make_chart(trades, dips, [s for s in sims if s["entry_name"] in (
        "first_public_buy", "first_dip40_volume_active", "first_dip40_volume_quiet")
        or s["entry_name"].startswith("dip")],
               t0, name, symbol, mint, chart_path, ath)

    buys = [t for t in trades if t["side"] == "buy"]
    sells = [t for t in trades if t["side"] == "sell"]
    first_dip40 = dips40[0] if dips40 else None
    sim_pub = next((s for s in sims if s["entry_name"] == "first_public_buy"), None)
    sim_dip = next((s for s in sims if "dip40" in s["entry_name"]), None)

    def pack_dip(sim):
        if not sim:
            return {}
        return {
            "t_plus_sec": sim.get("entry_t_plus_sec"),
            "drop_pct": sim.get("drop_at_touch_pct") or sim.get("drop_pct"),
            "volume_active": sim.get("volume_active"),
            "hit_1_7": sim.get("1_7_hit"),
            "secs_1_7": sim.get("1_7_secs_after_entry"),
            "net_1_7": sim.get("1_7_net_usd"),
            "dd_1_7": sim.get("1_7_max_dd_pct_before"),
            "hit_1_8": sim.get("1_8_hit"),
            "secs_1_8": sim.get("1_8_secs_after_entry"),
            "net_1_8": sim.get("1_8_net_usd"),
            "dd_1_8": sim.get("1_8_max_dd_pct_before"),
            "hit_2_0": sim.get("2_0_hit"),
            "secs_2x": sim.get("2_0_secs_after_entry"),
            "net_2x": sim.get("2_0_net_usd"),
            "dd_2x": sim.get("2_0_max_dd_pct_before"),
            "time_stop_net": sim.get("time_stop_5m_net_usd"),
            "time_stop_mult": sim.get("time_stop_5m_mult"),
            "best_mult": sim.get("best_exit_mult"),
            "best_net": sim.get("best_exit_net_usd"),
        }

    sim_by = {s["entry_name"]: s for s in sims}
    packed = {}
    for pfx, ename in (("dip25", "dip25_touch"), ("dip30", "dip30_touch"),
                      ("dip35", "dip35_touch"), ("dip40t", "dip40_touch")):
        for k, v in pack_dip(sim_by.get(ename)).items():
            packed[f"{pfx}_{k}"] = v
    # Prefer threshold-touch dip40 for the legacy dip40_* columns when present
    sim_dip = sim_by.get("dip40_touch") or sim_dip

    # best entry among realistic (exclude first_print_incl_dev)
    realistic = [s for s in sims if s["entry_name"] != "first_print_incl_dev"]
    def score(s):
        if s.get("2_0_hit"):
            return (2, s.get("2_0_net_usd") or -999, -(s.get("2_0_max_dd_pct_before") or 0))
        if s.get("1_7_hit"):
            return (1, s.get("1_7_net_usd") or -999, -(s.get("1_7_max_dd_pct_before") or 0))
        return (0, s.get("best_exit_net_usd") or -999, 0)
    best = max(realistic, key=score) if realistic else None

    progress5 = next((t["curve_progress_pct"] for t in reversed(trades) if t["t_plus_sec"] <= 300), None)
    progress30 = trades[-1]["curve_progress_pct"] if trades else None
    start_mcap = (trades[0].get("mcap_usd") if trades else None)
    # mcap after dev buy: first trade if dev, else start
    mcap_after_dev = None
    if dev_buys:
        mcap_after_dev = dev_buys[0].get("mcap_usd")

    failed_early = sum(1 for s in window if s.get("err") and s["slot"] <= slot0 + 20)
    ok_30 = sum(1 for s in window if not s.get("err"))

    summary = {
        "mint": mint,
        "name": name,
        "symbol": symbol,
        "curve": curve,
        "pumpswap_pair": info.get("pumpswap_pair"),
        "quote_mint": (trades[0].get("quote_mint") if trades else None) or (trades[0].get("_quote_mint") if trades else None),
        "is_sol_quote": (True if not trades else bool(trades[0].get("is_sol_quote", True))),
        "quote_usd_points": trades[0].get("_quote_usd_points") if trades else None,
        "quote_usd_median": trades[0].get("_quote_usd_median") if trades else None,
        "launch_ts": t0,
        "launch_time_utc": datetime.fromtimestamp(t0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "launch_time_eat": eat_str(t0),
        "launch_slot": slot0,
        "create_sig": create_sig,
        "creator": creator,
        "sol_usd_at_launch": sol_usd0,
        "migrated_within_30m": migrated_30,
        "migrate_sec": migrate_sec,
        "n_ok_tx_30m": ok_30,
        "n_failed_early": failed_early,
        "n_trades_30m": len(trades),
        "n_curve_trades": sum(1 for x in trades if (x.get("venue") or "curve") == "curve"),
        "n_pumpswap_trades": sum(1 for x in trades if x.get("venue") == "pumpswap"),
        "pumpswap_ok_total": (info.get("_pumpswap_fetch_meta") or {}).get("ok_total"),
        "pumpswap_fetched": (info.get("_pumpswap_fetch_meta") or {}).get("fetched"),
        "pumpswap_sampled": (info.get("_pumpswap_fetch_meta") or {}).get("sampled"),
        "pumpswap_sample_method": (info.get("_pumpswap_fetch_meta") or {}).get("method"),
        "tape_end_t_plus_sec": trades[-1]["t_plus_sec"] if trades else None,
        "n_buys": len(buys),
        "n_sells": len(sells),
        "unique_traders": len({t["trader"] for t in trades}),
        "unique_buyers": len({t["trader"] for t in buys}),
        "volume_sol_30m": sum((t.get("sol_ui") or 0) for t in trades),
        "volume_quote_30m": sum((t.get("quote_ui") or 0) for t in trades),
        "volume_usd_30m": sum(t.get("volume_usd") or 0 for t in trades),
        "buy_vol_usd": sum(t.get("volume_usd") or 0 for t in buys),
        "sell_vol_usd": sum(t.get("volume_usd") or 0 for t in sells),
        "start_mcap_usd": start_mcap,
        "mcap_after_devbuy_usd": mcap_after_dev,
        "ath_mcap_usd": ath.get("mcap_usd") if ath else None,
        "ath_price_sol": ath.get("price_sol") if ath else None,
        "ath_t_plus_sec": ath.get("t_plus_sec") if ath else None,
        "end_30m_mcap_usd": trades[-1].get("mcap_usd") if trades else None,
        "curve_progress_5m": progress5,
        "curve_progress_30m": progress30,
        "dev_buy_sol": dev_buy_sol,
        "dev_buy_tokens": dev_buy_tok,
        "dev_buy_supply_pct": dev_buy_tok / TOTAL_SUPPLY_UI * 100,
        "dev_sol_pre": dev_sol_pre,
        "dev_sol_post_create": dev_sol_post,
        "dev_sold_tokens_30m": dev_sell_tok,
        "dev_sold_supply_pct_30m": dev_sell_tok / TOTAL_SUPPLY_UI * 100,
        "dev_realized_sol_30m": dev_sell_sol,
        "n_dips_25pct": sum(1 for d in dips if d["drop_pct"] >= 25),
        "n_dips_30pct": sum(1 for d in dips if d["drop_pct"] >= 30),
        "n_dips_40pct": sum(1 for d in dips if d["drop_pct"] >= 38),
        "first_dip40_t_plus_sec": first_dip40["trough_t_plus"] if first_dip40 else None,
        "first_dip40_drop_pct": first_dip40["drop_pct"] if first_dip40 else None,
        "first_dip40_volume_active": first_dip40.get("volume_active") if first_dip40 else None,
        "firstprint_hit_1_7": (sim_pub or {}).get("1_7_hit"),
        "firstprint_hit_1_8": (sim_pub or {}).get("1_8_hit"),
        "firstprint_hit_2_0": (sim_pub or {}).get("2_0_hit"),
        "firstprint_secs_to_2x": (sim_pub or {}).get("2_0_secs_after_entry"),
        "firstprint_net_usd_2x": (sim_pub or {}).get("2_0_net_usd"),
        "firstprint_dd_before_2x": (sim_pub or {}).get("2_0_max_dd_pct_before"),
        "dip40_hit_1_7": (sim_dip or {}).get("1_7_hit"),
        "dip40_hit_1_8": (sim_dip or {}).get("1_8_hit"),
        "dip40_hit_2_0": (sim_dip or {}).get("2_0_hit"),
        "dip40_secs_to_2x": (sim_dip or {}).get("2_0_secs_after_entry"),
        "dip40_net_usd_2x": (sim_dip or {}).get("2_0_net_usd"),
        "dip40_dd_before_2x": (sim_dip or {}).get("2_0_max_dd_pct_before"),
        **packed,
        "best_entry_name": (best or {}).get("entry_name"),
        "best_entry_net_usd_2x": (best or {}).get("2_0_net_usd") if (best or {}).get("2_0_hit") else (best or {}).get("best_exit_net_usd"),
        "sniper_wallets": len(early["snipers_slot0_3"]),
        "sniper_supply_pct": sniper_supply / TOTAL_SUPPLY_UI * 100,
        "sniper_sol": sniper_sol,
        "bundle_wallets_create_slot": len(early["bundle_create_slot"]),
        "bundle_supply_pct": bundle_supply / TOTAL_SUPPLY_UI * 100,
        "insider_wallets": len(insiders),
        "insider_supply_pct": insider_supply / TOTAL_SUPPLY_UI * 100,
        "top10_circ_pct_5m": (snaps.get(300) or {}).get("top10_pct_circ"),
        "top10_circ_pct_30m": (snaps.get(1800) or {}).get("top10_pct_circ"),
        "top10_circ_pct_now": now_top10_pct,
        "median_priority_sol": med_prio,
        "median_jito_sol": med_jito,
        "curve_fee_bps": CURVE_FEE_BPS,
        "priority_sol_assumed": assumed_prio,
        "volume_active_definition": f">={ACTIVE_MIN_TRADES} trades AND >=${ACTIVE_MIN_USD:.0f} in prior {ACTIVE_LOOKBACK_SEC}s",
        "dump_into_first_40pct_dip": dump_into_dip,
        "risk_verdict": verdict,
        "simulations": sims,
        "dips": dips,
        "early": {
            "snipers": list(early["snipers_slot0_3"].values()),
            "bundle_create_slot": list(early["bundle_create_slot"].values()),
            "first5s": list(early["first5s"].values()),
            "multi_buy_slots": early["multi_buy_slots"][:15],
        },
        "holders_5m": snaps.get(300),
        "holders_30m": snaps.get(1800),
        "holders_now": now_holders[:10],
        "creator_balances": creator_bal_rows,
        "dev_sells": [{"t_plus": t["t_plus_sec"], "sol": t.get("sol_ui"), "quote": t.get("quote_ui"), "tokens": t["token_ui"], "sig": t["signature"]} for t in dev_sells],
        "chart": str(chart_path),
        "outputs_dir": str(odir),
    }
    (odir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    upsert_dataset(summary)
    # short markdown for reuse
    (odir / "README.txt").write_text(
        f"{name} ({symbol}) {mint}\nlaunch {summary['launch_time_eat']}\n"
        f"trades={len(trades)} ATH ${summary['ath_mcap_usd']}\n"
        f"outputs in {odir}\n"
    )
    print(f"wrote {odir}", flush=True)
    return summary


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Pump.fun first-30-min scalping study")
    ap.add_argument("mint")
    ap.add_argument("--skip-wallets-if-present", action="store_true",
                    help="Reuse coins/<mint>/wallets.csv if it already exists")
    ap.add_argument("--cache-only", action="store_true",
                    help="Do not fetch missing txs; use cache only (still full 30m wallets)")
    args = ap.parse_args()
    analyze(args.mint, skip_wallets_if_present=args.skip_wallets_if_present, cache_only=args.cache_only)
