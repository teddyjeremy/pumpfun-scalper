#!/usr/bin/env python3
"""Cheap first-5-minute pre-screen. Does NOT download a 30-minute tape.

Resumable: per-mint sigs/trades + result rows are cached under screen/cache/prescreen/.
Hot books are time-stratified (15s buckets) so we never fetch more than SAMPLE_CAP txs.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.constants import WSOL, TOTAL_SUPPLY_UI
from lib.parse import parse_tx, parse_enhanced_tx
from lib.study import volume_active_at, infer_quote_usd_points, quote_usd_fn, _is_wsol
from lib.rpc import rpc_call, rpc_batch
from screen.common import (
    CACHE, DATE_EAT, SCREEN, rpc_call_lim, day_bounds, eat_str, hour_window,
    atomic_write_json, load_json,
)

PRE_DIR = CACHE / "prescreen"
CSV_PATH = SCREEN / f"prescreen_{DATE_EAT}.csv"
WINDOW_SEC = 300
TX_PARAMS = {
    "encoding": "json",
    "maxSupportedTransactionVersion": 1,
    "commitment": "confirmed",
}
TX_WORKERS = 16
COIN_WORKERS = 16  # heavy-path coin workers (gate uses --gate-workers)
GATE_WORKERS = 24  # concurrent JSON-RPC batches of GATE_BATCH coins
GATE_BATCH = 10
TX_BATCH_SIZE = 20
TX_BATCH_SEM = threading.Semaphore(18)  # in-flight getTransaction batches (all coins)
SIG_PAGE_CAP = 40
SAMPLE_CAP = 300  # max curve txs fetched per coin in the cheap pre-screen
SIG_OK_GATE = 250  # skip trade-tx fetch below this many successful 5-min sigs
TX_SEM = threading.Semaphore(TX_WORKERS)
DEX_SEM = threading.Semaphore(6)

THRESHOLDS = (0.25, 0.30, 0.35, 0.40)
MIN_CUM_VOL = 10_000.0
MIN_CUM_TRADES = 300
MIN_CUM_WALLETS = 150
MCAP_MIN = 5_000.0
MCAP_MAX = 40_000.0

FIELDS = [
    "mint", "name", "symbol", "launch_ts", "launch_time_eat", "launch_slot",
    "create_sig", "creator", "curve", "is_token_2022", "extra_mints", "slice_window",
    "n_sigs_5m", "n_ok_sigs_5m", "n_ok_txs", "n_failed_sigs", "n_trades_5m", "n_unique_wallets_5m",
    "volume_usd_5m", "volume_sol_5m", "volume_usd_est",
    "mcap_start_usd", "mcap_end_5m_usd", "mcap_ath_5m_usd", "ath_t_plus_sec",
    "max_drop_pct_5m", "max_drop_mid_pct",
    "custom_quote", "n_sol_zero_trades", "sampled", "n_sampled", "sample_scale",
    "migrated_5m", "migrate_sec", "pumpswap_pair", "n_pumpswap_trades_5m",
    "open25_t", "open30_t", "open35_t", "open40_t",
    "mid25_t", "mid25_drop", "mid25_mcap", "mid25_cum_vol", "mid25_cum_trades", "mid25_cum_wallets", "mid25_va",
    "mid30_t", "mid30_drop", "mid30_mcap", "mid30_cum_vol", "mid30_cum_trades", "mid30_cum_wallets", "mid30_va",
    "mid35_t", "mid35_drop", "mid35_mcap", "mid35_cum_vol", "mid35_cum_trades", "mid35_cum_wallets", "mid35_va",
    "mid40_t", "mid40_drop", "mid40_mcap", "mid40_cum_vol", "mid40_cum_trades", "mid40_cum_wallets", "mid40_va",
    "entry_threshold", "entry_t_plus", "entry_drop", "entry_mcap", "entry_cum_vol",
    "entry_cum_trades", "entry_cum_wallets", "entry_wallets_obs",
    "entry_cum_trades_obs", "entry_trades_per_wallet",
    "entry_high_t_plus", "entry_secs_from_high",
    "entry_15s_vol", "entry_15s_trades", "entry_15s_buys", "entry_15s_buy_vol", "entry_15s_sell_vol", "entry_15s_buy_sell_ratio",
    "entry_10s_vol", "entry_10s_trades", "entry_10s_buys", "entry_10s_buy_vol", "entry_10s_sell_vol", "entry_10s_buy_sell_ratio",
    "entry_drop_sells", "entry_drop_sell_vol", "entry_top1_seller_share", "entry_top2_seller_share", "entry_dev_sold_in_drop",
    "pass_v0", "fail_reasons", "elapsed_s", "error",
]


def sol_px_fn(date_eat: str | None = None):
    date_eat = date_eat or DATE_EAT
    dest = CACHE / f"sol_usd_klines_{date_eat}.json"
    kl = load_json(dest, [])
    if not kl:
        from screen.common import day_bounds
        from lib.fetch import fetch_sol_usd_klines
        t0, t1 = day_bounds(date_eat)
        kl = fetch_sol_usd_klines(t0 - 120, t1 + 120, dest)
    pts = [(int(k[0]) // 1000, float(k[4])) for k in kl]
    pts.sort()
    fallback = pts[len(pts)//2][1] if pts else 69.0

    def fn(ts: int) -> float:
        if not pts:
            return fallback
        t = int(ts)
        lo, hi = 0, len(pts) - 1
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        while lo <= hi:
            mid = (lo + hi) // 2
            if pts[mid][0] <= t:
                lo = mid + 1
            else:
                hi = mid - 1
        return pts[hi][1]
    return fn



def _tx_pubkeys(tx: dict) -> list[str]:
    out: list[str] = []
    msg = tx.get("transaction") or {}
    for k in msg.get("accountKeys") or []:
        if isinstance(k, str):
            out.append(k)
        elif isinstance(k, dict):
            out.append(k.get("pubkey") or "")
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    for group in (loaded.get("writable") or []) + (loaded.get("readonly") or []):
        if isinstance(group, str):
            out.append(group)
        elif isinstance(group, dict):
            out.append(group.get("pubkey") or "")
    return out




def _slim_sig(s: dict) -> dict:
    return {
        "signature": s["signature"],
        "slot": s.get("slot"),
        "blockTime": s.get("blockTime"),
        "err": s.get("err"),
    }


def _classify_gsfa(page: list, t0: int) -> dict:
    """Classify one getSignaturesForAddress page (newest first, limit 1000)."""
    t_end = int(t0) + WINDOW_SEC
    kept: list[dict] = []
    hit = False
    reached = not page or len(page) < 1000
    for s in page or []:
        bt = s.get("blockTime")
        if bt is None:
            continue
        if bt > t_end:
            continue
        hit = True
        if bt < t0:
            reached = True
            break
        kept.append(_slim_sig(s))
    n_ok = sum(1 for s in kept if not s.get("err"))
    missed = bool(page) and len(page) >= 1000 and not hit
    return {
        "kept": kept,
        "hit": hit,
        "reached": reached,
        "missed_window": missed,
        "n_ok": n_ok,
        "n": len(kept),
    }


def _write_sigs(dest: Path, curve: str, t0: int, kept: list[dict], meta_extra: dict):
    t_end = int(t0) + WINDOW_SEC
    kept = sorted(kept, key=lambda s: (s.get("blockTime") or 0, s.get("slot") or 0, s.get("signature") or ""))
    # de-dupe
    seen = set()
    uniq = []
    for s in kept:
        sig = s.get("signature")
        if not sig or sig in seen:
            continue
        seen.add(sig)
        uniq.append(_slim_sig(s) if "err" in s or True else s)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(uniq, separators=(",", ":")))
    meta_p = dest.with_suffix(".meta.json")
    meta = {
        "curve": curve, "t0": t0, "t_end": t_end,
        "n": len(uniq),
        "n_ok": sum(1 for s in uniq if not s.get("err")),
        "reached": bool(meta_extra.get("reached")),
        "pages": int(meta_extra.get("pages") or 1),
        "jumped": False,
        "missed_window": bool(meta_extra.get("missed_window")),
        "source": meta_extra.get("source") or "gsfa",
        "truncated": bool(meta_extra.get("truncated")),
    }
    atomic_write_json(meta_p, meta)
    return uniq, meta


def _sig_just_after(curve: str, t_end: int) -> str | None:
    """One time-filtered signature: the first tx after t_end. Used as GSFA `before` cursor. No getBlock."""
    try:
        res = rpc_call("getTransactionsForAddress", [curve, {
            "transactionDetails": "signatures",
            "sortOrder": "asc",
            "limit": 1,
            "commitment": "confirmed",
            "filters": {"blockTime": {"gt": int(t_end)}},
        }], timeout=30, retries=5)
    except Exception:
        return None
    data = (res or {}).get("data") if isinstance(res, dict) else res
    if data and data[0].get("signature"):
        return data[0]["signature"]
    return None


def fetch_window_sigs_paged(curve: str, t0: int, dest: Path, before: str | None = None,
                            cap_pages: int = SIG_PAGE_CAP, stop_at_gate: bool = False) -> list[dict]:
    """Page getSignaturesForAddress from `before` (newest bound) back through the 5-minute window.

    Never walks post-window history: callers should pass a cursor just after t+300s.
    """
    kept: list[dict] = []
    pages = 0
    reached = False
    n_ok = 0
    cur = before
    while pages < cap_pages:
        params: dict = {"limit": 1000}
        if cur:
            params["before"] = cur
        try:
            page = rpc_call("getSignaturesForAddress", [curve, params], timeout=30, retries=5) or []
        except Exception:
            break
        pages += 1
        if not page:
            reached = True
            break
        info = _classify_gsfa(page, t0)
        kept.extend(info["kept"])
        n_ok = sum(1 for s in kept if not s.get("err"))
        cur = page[-1]["signature"]
        if info["reached"] or len(page) < 1000:
            reached = True
            break
        if stop_at_gate and n_ok >= SIG_OK_GATE and info["hit"]:
            # Enough to clear the dead-coin gate; heavy pass will finish the window.
            break
    uniq, _ = _write_sigs(dest, curve, t0, kept, {
        "reached": reached, "pages": pages, "source": "gsfa_window",
        "missed_window": False,
        "truncated": (not reached and pages >= cap_pages),
    })
    return uniq


def fetch_window_after_miss(curve: str, t0: int, dest: Path, stop_at_gate: bool = False) -> list[dict]:
    """Seek a cursor just after t+300s, then page only the window with getSignaturesForAddress."""
    cursor = _sig_just_after(curve, int(t0) + WINDOW_SEC)
    if cursor is None:
        # Seek failed. Do not walk newest-first through post-window history (that was the stall).
        raise RuntimeError("no after-window cursor for time seek")
    return fetch_window_sigs_paged(curve, t0, dest, before=cursor, stop_at_gate=stop_at_gate)


def _resume_window_sigs(curve: str, t0: int, dest: Path, cached: list[dict], meta: dict) -> list[dict]:
    """Continue GSFA paging older than the oldest cached in-window sig. No GTFA seek."""
    if not cached:
        return fetch_window_after_miss(curve, t0, dest)
    # _write_sigs stores ascending blockTime, so [0] is the oldest (the next `before` cursor).
    cur = cached[0].get("signature")
    if not cur:
        return fetch_window_after_miss(curve, t0, dest)
    already = int(meta.get("pages") or 0)
    cap = max(1, SIG_PAGE_CAP - already)
    extra: list[dict] = []
    pages = 0
    reached = False
    while pages < cap:
        params: dict = {"limit": 1000, "before": cur}
        try:
            page = rpc_call("getSignaturesForAddress", [curve, params], timeout=20, retries=3) or []
        except Exception:
            break
        pages += 1
        if not page:
            reached = True
            break
        info = _classify_gsfa(page, t0)
        extra.extend(info["kept"])
        cur = page[-1]["signature"]
        if info["reached"] or len(page) < 1000:
            reached = True
            break
    uniq, _ = _write_sigs(dest, curve, t0, list(cached) + extra, {
        "reached": reached,
        "pages": already + pages,
        "source": "gsfa_resume",
        "missed_window": False,
        "truncated": (not reached and pages >= cap),
    })
    return uniq


def fetch_curve_sigs(curve: str, t0: int, slot0: int | None, dest: Path) -> list[dict]:
    """Window sigs only. Reuse/resume the cached window; never throw it away to redo a GTFA seek.

    slot0 is accepted for call-site compatibility and is unused (no getBlock).
    """
    meta_p = dest.with_suffix(".meta.json")
    if dest.exists() and meta_p.exists():
        meta = load_json(meta_p, {})
        if meta.get("curve") == curve:
            cached = load_json(dest, [])
            if meta.get("reached"):
                return cached
            # Gate often stops at 250 ok sigs (reached=False, source=gsfa_partial / gsfa_window).
            # Resume backward from the oldest cached sig instead of getTransactionsForAddress.
            if cached:
                return _resume_window_sigs(curve, t0, dest, cached, meta)
    try:
        first = rpc_call("getSignaturesForAddress", [curve, {"limit": 1000}], timeout=20, retries=3) or []
    except Exception:
        first = []
    info = _classify_gsfa(first, t0)
    if info["missed_window"] or (info["hit"] and not info["reached"]):
        return fetch_window_after_miss(curve, t0, dest)
    return _write_sigs(dest, curve, t0, info["kept"], {
        "reached": info["reached"], "pages": 1, "source": "gsfa",
        "missed_window": False,
    })[0]


def fetch_txs_batched(signatures: list[str], timeout: int = 20) -> tuple[dict, int]:
    """JSON-RPC batches of TX_BATCH_SIZE getTransaction calls, sequential per caller, shared in-flight cap.

    No per-coin thread pool: 28 heavy workers were each spawning 8 batch threads, which
    starved the token bucket, leaked sockets (CLOSE_WAIT), and looked like hung connections.
    """
    out: dict = {}
    n_fail = 0
    if not signatures:
        return out, 0
    batches = [signatures[i:i + TX_BATCH_SIZE] for i in range(0, len(signatures), TX_BATCH_SIZE)]
    for batch in batches:
        calls = [("getTransaction", [s, TX_PARAMS]) for s in batch]
        with TX_BATCH_SEM:
            try:
                results = rpc_batch(calls, timeout=min(timeout, 20), retries=2)
            except Exception:
                results = [None] * len(batch)
        for s, r in zip(batch, results or []):
            if r and not (isinstance(r, dict) and r.get("_error")):
                out[s] = r
            else:
                n_fail += 1
    return out, n_fail


def fetch_one_tx(sig: str):
    """Single-tx helper (tests / fallback). Prefer fetch_txs_batched in the hot path."""
    txs, n_fail = fetch_txs_batched([sig])
    if n_fail or sig not in txs:
        raise RuntimeError(f"getTransaction failed for {sig[:8]}")
    return txs[sig]


def _gate_batch(chunk: list[dict]) -> list[dict]:
    """One JSON-RPC batch of getSignaturesForAddress for up to GATE_BATCH coins."""
    calls = [("getSignaturesForAddress", [r["curve"], {"limit": 1000}]) for r in chunk]
    try:
        results = rpc_batch(calls, timeout=30, retries=5)
    except Exception as e:
        results = [{"_error": str(e)} for _ in chunk]
    out = []
    for r, res in zip(chunk, results or []):
        t0 = int(float(r["launch_ts"]))
        d = PRE_DIR / r["mint"]
        dest = d / "sigs.json"
        if not isinstance(res, list):
            # Retry this coin once solo, then treat as empty/error.
            try:
                res = rpc_call("getSignaturesForAddress", [r["curve"], {"limit": 1000}], timeout=30, retries=4) or []
            except Exception:
                row = score(r, [], {"n_ok_txs": 0, "n_failed_sigs": 0, "n_ok_sigs_window": 0,
                                    "dead_by_sig_count": False}, 0, 0.0, err="error:gate_rpc")
                out.append(("error", r, row))
                continue
        info = _classify_gsfa(res, t0)
        if info["missed_window"] or (info["hit"] and not info["reached"] and info["n_ok"] < SIG_OK_GATE):
            # Don't seek/page inside the batch — that serialized the fast dead-coin gate.
            out.append(("need_window", r, None))
            continue
        if info["hit"] and not info["reached"] and info["n_ok"] >= SIG_OK_GATE:
            # Survives the gate; defer full-window fetch to the heavy pass.
            _write_sigs(dest, r["curve"], t0, info["kept"], {
                "reached": False, "pages": 1, "source": "gsfa_partial", "missed_window": False,
            })
            out.append(("heavy", r, None))
            continue
        uniq, meta = _write_sigs(dest, r["curve"], t0, info["kept"], {
            "reached": info["reached"], "pages": 1, "source": "gsfa", "missed_window": False,
        })
        n_ok = meta["n_ok"]
        n_fail = sum(1 for s in uniq if s.get("err"))
        if n_ok < SIG_OK_GATE:
            row = score(r, [], {"n_ok_txs": 0, "n_failed_sigs": n_fail, "n_ok_sigs_window": n_ok,
                                "dead_by_sig_count": True}, len(uniq), 0.0)
            out.append(("dead", r, row))
        else:
            out.append(("heavy", r, None))
    return out


def stratify_sigs(sigs: list[dict], t0: int, cap: int = SAMPLE_CAP):
    ok = [s for s in sigs if not s.get("err") and s.get("blockTime") is not None]
    if len(ok) <= cap:
        return ok, False, 1.0
    buckets: dict[int, list] = {}
    for s in ok:
        b = max(0, int((int(s["blockTime"]) - int(t0)) // 15))
        buckets.setdefault(b, []).append(s)
    per = max(1, cap // max(1, len(buckets)))
    selected = []
    for b in sorted(buckets):
        arr = buckets[b]
        if len(arr) <= per:
            selected.extend(arr)
        else:
            step = len(arr) / per
            selected.extend(arr[int(i * step)] for i in range(per))
    # Keep the first print and the last 15 (complete/migrate often sits at the end).
    for edge in [ok[0], *ok[-15:]]:
        if all(edge["signature"] != s["signature"] for s in selected):
            selected.append(edge)
    seen = set()
    out = []
    for s in selected:
        if s["signature"] in seen:
            continue
        seen.add(s["signature"])
        out.append(s)
    return out, True, len(ok) / max(1, len(out))


def _ingest_curve_tx(s: dict, tx: dict, mint: str, curve: str, t0: int, sol_px,
                     trades: list[dict], migrate_bt: list[int], counters: dict) -> None:
    try:
        row = parse_tx(s["signature"], tx, mint, curve)
    except Exception:
        return
    if row.get("is_migrate"):
        bt = row.get("block_time") or s.get("blockTime")
        if bt is not None and t0 <= int(bt) <= t0 + WINDOW_SEC:
            migrate_bt.append(int(bt))
    for tr in row.get("_trades") or []:
        bt = tr.get("block_time") or s.get("blockTime")
        if bt is None or bt < t0 or bt > t0 + WINDOW_SEC:
            continue
        tr = dict(tr)
        tr["t_plus_sec"] = int(bt) - int(t0)
        sol_usd = float(sol_px(bt))
        tr["sol_usd"] = sol_usd
        sol_ui = tr.get("sol_ui") or 0.0
        if (sol_ui or 0) == 0:
            counters["n_sol_zero"] += 1
            if tr.get("quote_mint") and tr.get("quote_mint") != WSOL:
                counters["custom"] = True
        tr["price_usd"] = (tr["price_sol"] * sol_usd) if tr.get("price_sol") else None
        tr["mcap_usd"] = (tr["mcap_sol"] * sol_usd) if tr.get("mcap_sol") else None
        tr["volume_usd"] = (sol_ui * sol_usd) if sol_ui else 0.0
        tr["venue"] = "curve"
        trades.append(tr)


def _v0_entry_ready(trades: list[dict], plan: list[dict], t0: int, fetched: set[str]) -> bool:
    """True when the first mid-tape live 30/35/40% dip is known (all plan sigs at or before it fetched).

    volume_active_at only looks backward from the dip, so no later trades are required.
    """
    if not trades:
        return False
    _opening, mid = first_live_touches(trades)
    rec = mid.get(0.30) or mid.get(0.35) or mid.get(0.40)
    if not rec:
        return False
    entry_t = rec["t"]
    for s in plan:
        bt = s.get("blockTime")
        if bt is None:
            continue
        if int(bt) - int(t0) <= entry_t and s.get("signature") not in fetched:
            return False
    return True


def build_trades(mint: str, curve: str, t0: int, sigs: list[dict], sol_px, dest: Path) -> tuple[list[dict], dict]:
    # Time-stratify every heavy book (15s buckets, cap SAMPLE_CAP). Books with <= cap fetch all.
    fetch_sigs, sampled, scale = stratify_sigs(sigs, t0, SAMPLE_CAP)
    if dest.exists():
        try:
            blob = json.loads(dest.read_text())
            if blob.get("n_sampled") == len(fetch_sigs) and blob.get("trades") is not None:
                return blob["trades"], blob.get("stats") or {}
        except Exception:
            pass
    plan = sorted(
        [s for s in fetch_sigs if s.get("signature")],
        key=lambda s: (s.get("blockTime") or 0, s.get("slot") or 0, s.get("signature") or ""),
    )
    # Fetch first + last 15 first so a complete/migrate at the end of the curve is visible
    # before we decide we can stop at an early dip.
    edges: list[dict] = []
    if plan:
        edges.append(plan[0])
        edges.extend(plan[-15:])
    edge_sigs = []
    seen_e = set()
    for s in edges:
        sig = s["signature"]
        if sig in seen_e:
            continue
        seen_e.add(sig)
        edge_sigs.append(s)
    rest = [s for s in plan if s["signature"] not in seen_e]

    fetched: set[str] = set()
    n_ok_txs = 0
    n_fail = 0
    trades: list[dict] = []
    migrate_bt: list[int] = []
    counters = {"n_sol_zero": 0, "custom": False}
    early_stop = False

    def run_batch(batch: list[dict]) -> None:
        nonlocal n_ok_txs, n_fail
        need = [s for s in batch if s["signature"] not in fetched]
        if not need:
            return
        txs, fail = fetch_txs_batched([s["signature"] for s in need], timeout=20)
        n_fail += fail
        for s in need:
            fetched.add(s["signature"])
            tx = txs.get(s["signature"])
            if not tx:
                continue
            n_ok_txs += 1
            _ingest_curve_tx(s, tx, mint, curve, t0, sol_px, trades, migrate_bt, counters)

    for i in range(0, len(edge_sigs), TX_BATCH_SIZE):
        run_batch(edge_sigs[i:i + TX_BATCH_SIZE])
    for i in range(0, len(rest), TX_BATCH_SIZE):
        if _v0_entry_ready(trades, plan, t0, fetched):
            early_stop = True
            break
        run_batch(rest[i:i + TX_BATCH_SIZE])
    if not early_stop and _v0_entry_ready(trades, plan, t0, fetched) and len(fetched) < len(plan):
        early_stop = True

    trades.sort(key=lambda x: (x.get("block_time") or 0, x.get("slot") or 0, 0 if x.get("is_create_tx") else 1, x.get("signature") or ""))
    n_ok_window = sum(1 for s in sigs if not s.get("err"))
    stats = {
        "n_ok_txs": n_ok_txs,
        "n_failed_sigs": n_fail + sum(1 for s in sigs if s.get("err")),
        "n_sol_zero_trades": counters["n_sol_zero"],
        "custom_quote": counters["custom"],
        "sampled": sampled,
        "n_sampled": len(plan),
        "sample_scale": scale,
        "n_ok_sigs_window": n_ok_window,
        "migrated_5m": bool(migrate_bt),
        "migrate_sec": (min(migrate_bt) - t0) if migrate_bt else None,
        "pumpswap_checked": False,
        "early_stop": early_stop,
    }
    slim = []
    keep = ("signature", "slot", "block_time", "t_plus_sec", "side", "trader", "price_sol", "price_usd",
            "mcap_usd", "volume_usd", "sol_ui", "sol_usd", "is_create_tx", "quote_mint",
            "quote_ui", "venue", "is_sol_quote", "price_quote")
    for tr in trades:
        slim.append({k: tr.get(k) for k in keep})
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"n_sampled": len(fetch_sigs), "n_window": len(sigs), "trades": slim, "stats": stats}, separators=(",", ":")))
    return slim, stats



def resolve_pumpswap_pair(mint: str, dest: Path) -> dict:
    """Dexscreener first, pump.fun frontend fallback. Cached under the mint dir."""
    if dest.exists():
        hit = load_json(dest, {})
        if hit:
            return hit
    info = {"pair": None, "pair_created_ms": None, "source": None}
    try:
        import requests
        with DEX_SEM:
            r = requests.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        if r.status_code == 200:
            for p in (r.json() or {}).get("pairs") or []:
                if p.get("dexId") == "pumpswap" and (p.get("baseToken") or {}).get("address") == mint:
                    info["pair"] = p.get("pairAddress")
                    info["pair_created_ms"] = p.get("pairCreatedAt")
                    info["source"] = "dexscreener"
                    break
    except Exception:
        pass
    if not info["pair"]:
        try:
            import requests
            with DEX_SEM:
                r = requests.get(
                    f"https://frontend-api-v3.pump.fun/coins/{mint}",
                    timeout=20,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
            if r.status_code == 200:
                blob = r.json() or {}
                pair = blob.get("pump_swap_pool") or blob.get("pumpswap_pool") or blob.get("raydium_pool")
                if pair:
                    info["pair"] = pair
                    info["source"] = "pumpfun_api"
                    # complete timestamp if present (seconds or ms)
                    for k in ("updated_at", "created_timestamp", "last_trade_timestamp"):
                        if blob.get("complete") and blob.get(k):
                            ts = int(blob[k])
                            if ts < 10**12:
                                ts *= 1000
                            info.setdefault("pair_created_ms", ts)
                            break
        except Exception:
            pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, info)
    return info


def fetch_enhanced_prescreen(signatures: list[str], dest: Path) -> dict:
    """Quiet Helius enhanced parse, cached in the prescreen mint dir (not analyze cache)."""
    from lib.rpc import helius_request
    have = load_json(dest, {}) if dest.exists() else {}
    if not isinstance(have, dict):
        have = {}
    todo = [s for s in signatures if s not in have]
    if not todo:
        return have
    got = dict(have)
    batch_size = 50
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        try:
            r = helius_request("enhanced", {"transactions": batch}, timeout=60, retries=8)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise RuntimeError("enhanced response not a list")
            for item in data:
                if isinstance(item, dict) and item.get("signature"):
                    got[item["signature"]] = item
        except Exception:
            # leave this batch missing; caller will just have fewer trades
            pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(got, separators=(",", ":")))
    return got


def _price_pumpswap_trades(trades: list[dict], enhanced: dict, sol_px) -> None:
    """Fill USD prices; infer custom-quote USD from same-tx hops when needed."""
    quote_mints = [
        t.get("quote_mint") for t in trades
        if t.get("quote_mint") and not t.get("is_sol_quote", True) and not _is_wsol(t.get("quote_mint"))
    ]
    if quote_mints and enhanced:
        from collections import Counter
        quote_mint = Counter(quote_mints).most_common(1)[0][0]
        pts = infer_quote_usd_points(enhanced, quote_mint, sol_px)
        qfn = quote_usd_fn(pts, sol_px, quote_mint)
        for tr in trades:
            bt = tr.get("block_time")
            try:
                q_usd = float(qfn(bt)) if bt else float("nan")
            except Exception:
                q_usd = float("nan")
            tr["quote_usd"] = q_usd
            pq, qu = tr.get("price_quote"), tr.get("quote_ui")
            if pq and q_usd == q_usd:
                tr["price_usd"] = pq * q_usd
                tr["mcap_usd"] = tr["price_usd"] * TOTAL_SUPPLY_UI
                if tr.get("sol_usd"):
                    tr["mcap_sol"] = tr["mcap_usd"] / tr["sol_usd"]
                if not tr.get("price_sol"):
                    tr["price_sol"] = pq
            if qu and q_usd == q_usd:
                tr["volume_usd"] = qu * q_usd


def build_pumpswap_trades(mint: str, pair: str, t0: int, slot0: int | None, sol_px, d: Path) -> tuple[list[dict], dict]:
    sigs = fetch_curve_sigs(pair, t0, slot0, d / "pumpswap_sigs.json")
    fetch_sigs, sampled, scale = stratify_sigs(sigs, t0, SAMPLE_CAP)
    ok_sigs = [s["signature"] for s in fetch_sigs]
    enhanced = fetch_enhanced_prescreen(ok_sigs, d / "pumpswap_enhanced.json")
    trades = []
    for s in fetch_sigs:
        item = enhanced.get(s["signature"])
        if not item:
            continue
        try:
            row = parse_enhanced_tx(item, mint, pair)
        except Exception:
            continue
        for tr in row.get("_trades") or []:
            bt = tr.get("block_time") or s.get("blockTime") or item.get("timestamp")
            if bt is None or bt < t0 or bt > t0 + WINDOW_SEC:
                continue
            tr = dict(tr)
            tr["t_plus_sec"] = int(bt) - int(t0)
            tr["block_time"] = int(bt)
            sol_usd = float(sol_px(bt))
            tr["sol_usd"] = sol_usd
            tr["venue"] = "pumpswap"
            is_sol = tr.get("is_sol_quote", True) or _is_wsol(tr.get("quote_mint"))
            sol_ui = tr.get("sol_ui") or 0.0
            if is_sol and sol_ui:
                tr["price_usd"] = (tr["price_sol"] * sol_usd) if tr.get("price_sol") else None
                tr["mcap_usd"] = (tr["mcap_sol"] * sol_usd) if tr.get("mcap_sol") else (
                    (tr["price_usd"] * TOTAL_SUPPLY_UI) if tr.get("price_usd") else None
                )
                tr["volume_usd"] = sol_ui * sol_usd
            else:
                # filled in _price_pumpswap_trades
                tr.setdefault("volume_usd", 0.0)
            trades.append(tr)
    _price_pumpswap_trades(trades, enhanced, sol_px)
    trades.sort(key=lambda x: (x.get("block_time") or 0, x.get("slot") or 0, x.get("signature") or ""))
    n_ok = sum(1 for s in sigs if not s.get("err"))
    stats = {
        "n_pumpswap_sigs": len(sigs),
        "n_pumpswap_ok": n_ok,
        "n_pumpswap_trades_5m": len(trades),
        "pumpswap_sampled": sampled,
        "pumpswap_sample_scale": scale,
        "pumpswap_pair": pair,
    }
    return trades, stats


def merge_curve_and_pumpswap(curve_trades: list[dict], curve_stats: dict,
                             ps_trades: list[dict], ps_stats: dict) -> tuple[list[dict], dict]:
    """Pre-scale each venue's volume, then combine. score() uses scale=1 after this."""
    cscale = float(curve_stats.get("sample_scale") or 1.0) or 1.0
    pscale = float(ps_stats.get("pumpswap_sample_scale") or 1.0) or 1.0
    out = []
    for tr in curve_trades:
        t = dict(tr)
        if cscale != 1.0:
            t["volume_usd"] = (t.get("volume_usd") or 0.0) * cscale
        out.append(t)
    for tr in ps_trades:
        t = dict(tr)
        if pscale != 1.0:
            t["volume_usd"] = (t.get("volume_usd") or 0.0) * pscale
        out.append(t)
    out.sort(key=lambda x: (x.get("block_time") or 0, x.get("slot") or 0,
                            0 if x.get("is_create_tx") else 1, x.get("signature") or ""))
    stats = dict(curve_stats)
    sampled = bool(curve_stats.get("sampled") or ps_stats.get("pumpswap_sampled"))
    n_ok_curve = int(curve_stats.get("n_ok_sigs_window") or 0)
    n_ok_ps = int(ps_stats.get("n_pumpswap_ok") or 0)
    stats.update({
        "sampled": sampled,
        "sample_scale": 1.0,
        "n_sampled": int(curve_stats.get("n_sampled") or 0) + int(ps_stats.get("n_pumpswap_trades_5m") or 0),
        "n_ok_sigs_window": n_ok_curve,  # keep curve-only for the sig-gate funnel column
        "n_ok_sigs_trades": n_ok_curve + n_ok_ps,
        "n_pumpswap_trades_5m": len(ps_trades),
        "pumpswap_pair": ps_stats.get("pumpswap_pair") or "",
        "pumpswap_checked": True,
        "custom_quote": bool(curve_stats.get("custom_quote")) or any(
            t.get("quote_mint") and not t.get("is_sol_quote", True) and not _is_wsol(t.get("quote_mint"))
            for t in ps_trades
        ),
    })
    return out, stats


def maybe_attach_pumpswap(mint: str, t0: int, slot0: int | None, sol_px,
                          curve_trades: list[dict], curve_stats: dict, d: Path) -> tuple[list[dict], dict]:
    """If the curve completed / pair appeared inside t+300s, merge PumpSwap fills (cap 300)."""
    migrate_sec = curve_stats.get("migrate_sec")
    migrated_flag = bool(curve_stats.get("migrated_5m"))
    in_window_curve = False
    if migrate_sec is not None:
        try:
            in_window_curve = 0 <= float(migrate_sec) <= WINDOW_SEC
        except (TypeError, ValueError):
            in_window_curve = False
    if not in_window_curve and not migrated_flag:
        stats = dict(curve_stats)
        stats["pumpswap_checked"] = True
        stats["n_pumpswap_trades_5m"] = 0
        stats["pumpswap_pair"] = stats.get("pumpswap_pair") or ""
        stats["n_ok_sigs_trades"] = int(stats.get("n_ok_sigs_window") or 0)
        return curve_trades, stats

    pair_info = resolve_pumpswap_pair(mint, d / "pumpswap_pair.json")
    pair = pair_info.get("pair")
    created_ms = pair_info.get("pair_created_ms")
    if created_ms not in (None, ""):
        try:
            ms = int(created_ms)
            if ms > 10**12:
                ms = ms  # already ms
            else:
                # heuristic: 10-digit seconds
                if ms < 10**12:
                    # dexscreener is ms already when 13 digits; if 10, seconds
                    if ms < 10**11:
                        ms *= 1000
            pair_migrate = ms / 1000.0 - t0
            if migrate_sec is None or (pair_migrate >= 0 and (migrate_sec is None or pair_migrate < migrate_sec)):
                # prefer on-chain complete time if we have it; otherwise pair created
                if migrate_sec is None:
                    migrate_sec = pair_migrate
        except (TypeError, ValueError):
            pair_migrate = None
    else:
        pair_migrate = None

    # Coverage rule: complete event in the 5m curve tape, OR pair created in the window,
    # OR a pair exists and we already flagged migrated_5m.
    in_window = False
    if migrate_sec is not None:
        try:
            in_window = 0 <= float(migrate_sec) <= WINDOW_SEC
        except (TypeError, ValueError):
            in_window = False
    if not in_window and pair_migrate is not None:
        in_window = 0 <= float(pair_migrate) <= WINDOW_SEC
        if in_window:
            migrate_sec = pair_migrate

    stats = dict(curve_stats)
    stats["pumpswap_checked"] = True
    if migrate_sec is not None:
        stats["migrated_5m"] = bool(in_window)
        stats["migrate_sec"] = float(migrate_sec)
    if not (pair and in_window):
        stats["n_pumpswap_trades_5m"] = 0
        stats["pumpswap_pair"] = pair or ""
        stats["n_ok_sigs_trades"] = int(stats.get("n_ok_sigs_window") or 0)
        return curve_trades, stats

    ps_trades, ps_stats = build_pumpswap_trades(mint, pair, t0, slot0, sol_px, d)
    merged, stats = merge_curve_and_pumpswap(curve_trades, stats, ps_trades, ps_stats)
    stats["migrated_5m"] = True
    stats["migrate_sec"] = float(migrate_sec) if migrate_sec is not None else None
    stats["pumpswap_checked"] = True
    return merged, stats


def write_final_trades(dest: Path, trades: list[dict], stats: dict, n_window: int, n_sampled: int):
    keep = ("signature", "slot", "block_time", "t_plus_sec", "side", "trader", "price_sol", "price_usd",
            "mcap_usd", "volume_usd", "sol_ui", "sol_usd", "is_create_tx", "quote_mint",
            "quote_ui", "venue", "is_sol_quote", "price_quote")
    slim = [{k: tr.get(k) for k in keep} for tr in trades]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({
        "n_sampled": n_sampled, "n_window": n_window, "trades": slim, "stats": stats,
    }, separators=(",", ":")))
    return slim, stats



def cumulatives(trades: list[dict]):
    vol = 0.0
    wallets = set()
    out = []
    for i, tr in enumerate(trades):
        vol += tr.get("volume_usd") or 0.0
        if tr.get("trader"):
            wallets.add(tr["trader"])
        out.append((vol, i + 1, len(wallets)))
    return out


def _px(tr: dict):
    """USD price when available so curve and PumpSwap (and custom quotes) share one axis."""
    u = tr.get("price_usd")
    if u:
        try:
            return float(u)
        except (TypeError, ValueError):
            pass
    ps, su = tr.get("price_sol"), tr.get("sol_usd")
    if ps and su:
        try:
            return float(ps) * float(su)
        except (TypeError, ValueError):
            pass
    return tr.get("price_sol")


def first_live_touches(trades: list[dict]):
    opening, mid = {}, {}
    peak = None
    armed = {T: True for T in THRESHOLDS}
    for tr in trades:
        px = _px(tr)
        if not px:
            continue
        if peak is None or px >= peak:
            peak = px
            armed = {T: True for T in THRESHOLDS}
            continue
        dd = 1 - px / peak
        t = tr.get("t_plus_sec")
        if t is None:
            continue
        if not any(armed[T] and dd >= T for T in THRESHOLDS):
            continue
        va = volume_active_at(trades, t)
        if not va.get("active"):
            continue
        for T in THRESHOLDS:
            if not armed[T] or dd < T:
                continue
            armed[T] = False
            rec = {"trade": tr, "drop": dd * 100, "va": va, "t": t, "mcap": tr.get("mcap_usd")}
            if 0 <= t <= 30:
                opening.setdefault(T, rec)
            elif 30 < t <= 300:
                mid.setdefault(T, rec)
    return opening, mid




def _trade_wallet(tr: dict) -> str:
    return (tr.get("trader") or tr.get("fee_payer") or "").strip()


def _is_dev_or_creator(tr: dict, creator: str | None, devs: set[str] | None = None) -> bool:
    w = _trade_wallet(tr)
    if w and creator and w == creator:
        return True
    if w and devs and w in devs:
        return True
    flag = tr.get("is_dev")
    return flag in (1, True, "1", "true", "True")


def entry_setup_metrics(trades: list[dict], entry: dict, creator: str | None = None) -> dict:
    """Entry-only (no lookahead) crash-vs-dip and liquidity metrics for filters / output.

    Windows and the running high use only trades at or before the entry print.
    """
    out = {
        "entry_high_t_plus": "",
        "entry_secs_from_high": "",
        "entry_cum_trades_obs": "",
        "entry_wallets_obs": "",
        "entry_trades_per_wallet": "",
        "entry_15s_vol": "",
        "entry_15s_trades": "",
        "entry_15s_buys": "",
        "entry_15s_buy_vol": "",
        "entry_15s_sell_vol": "",
        "entry_15s_buy_sell_ratio": "",
        "entry_10s_vol": "",
        "entry_10s_trades": "",
        "entry_10s_buys": "",
        "entry_10s_buy_vol": "",
        "entry_10s_sell_vol": "",
        "entry_10s_buy_sell_ratio": "",
        "entry_drop_sells": "",
        "entry_drop_sell_vol": "",
        "entry_top1_seller_share": "",
        "entry_top2_seller_share": "",
        "entry_dev_sold_in_drop": "",
    }
    if not trades or not entry:
        return out

    def tplus(tr):
        v = tr.get("t_plus_sec")
        if v in (None, ""):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def vol(tr):
        try:
            return float(tr.get("volume_usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    def side(tr):
        return (tr.get("side") or "").lower()

    entry_sig = entry.get("signature")
    entry_t = tplus(entry)
    # Locate the entry print: prefer signature, else same t+ as the entry trade.
    entry_idx = None
    if entry_sig:
        entry_idx = next((i for i, tr in enumerate(trades)
                          if tr.get("signature") == entry_sig and (entry_t is None or tplus(tr) == entry_t)), None)
    if entry_idx is None and entry_t is not None:
        entry_idx = next((i for i, tr in enumerate(trades) if tplus(tr) == entry_t and _px(tr)), None)
    if entry_idx is None:
        return out
    if entry_t is None:
        entry_t = tplus(trades[entry_idx])
    if entry_t is None:
        return out

    # Running local high: same walk as first_live_touches, stopping at the entry print.
    peak = None
    peak_t = None
    devs = set()
    if creator:
        devs.add(creator)
    for i, tr in enumerate(trades):
        if i > entry_idx:
            break
        if _is_dev_or_creator(tr, creator):
            w = _trade_wallet(tr)
            if w:
                devs.add(w)
        if tr.get("is_create_tx") in (1, True, "1", "true", "True"):
            w = _trade_wallet(tr)
            if w:
                devs.add(w)
        px = _px(tr)
        tp = tplus(tr)
        if not px or tp is None or tp > entry_t:
            continue
        if i == entry_idx:
            continue  # the dip print itself is not a new high
        if peak is None or px >= peak:
            peak = px
            peak_t = tp
    if peak_t is not None:
        out["entry_high_t_plus"] = peak_t
        out["entry_secs_from_high"] = entry_t - peak_t

    # Observed trades / wallets at or before entry (never Chao1, never sample-scaled).
    prefix = trades[: entry_idx + 1]
    wallets = []
    for tr in prefix:
        w = _trade_wallet(tr)
        if w:
            wallets.append(w)
    n_tr = len(prefix)
    n_w = len(set(wallets))
    out["entry_cum_trades_obs"] = n_tr
    out["entry_wallets_obs"] = n_w
    if n_w:
        out["entry_trades_per_wallet"] = round(n_tr / n_w, 4)

    def window_flow(width: int) -> dict:
        lo = entry_t - width
        win = [tr for tr in prefix if (tplus(tr) is not None and lo < tplus(tr) <= entry_t)]
        buys = [tr for tr in win if side(tr) == "buy"]
        sells = [tr for tr in win if side(tr) == "sell"]
        bv = sum(vol(tr) for tr in buys)
        sv = sum(vol(tr) for tr in sells)
        ratio = ""
        if sv > 0:
            ratio = round(bv / sv, 4)
        elif bv > 0:
            ratio = "inf"
        return {
            "vol": round(sum(vol(tr) for tr in win), 2),
            "n": len(win),
            "buys": len(buys),
            "buy_vol": round(bv, 2),
            "sell_vol": round(sv, 2),
            "ratio": ratio,
        }

    for width, prefix_key in ((15, "entry_15s"), (10, "entry_10s")):
        fl = window_flow(width)
        out[f"{prefix_key}_vol"] = fl["vol"]
        out[f"{prefix_key}_trades"] = fl["n"]
        out[f"{prefix_key}_buys"] = fl["buys"]
        out[f"{prefix_key}_buy_vol"] = fl["buy_vol"]
        out[f"{prefix_key}_sell_vol"] = fl["sell_vol"]
        out[f"{prefix_key}_buy_sell_ratio"] = fl["ratio"]

    # Sells that form the drop: after the running high, up to and including the entry print.
    if peak_t is not None:
        drop = []
        for tr in prefix:
            tp = tplus(tr)
            if tp is None or tp <= peak_t or tp > entry_t:
                continue
            if side(tr) == "sell":
                drop.append(tr)
        sell_vol = sum(vol(tr) for tr in drop)
        out["entry_drop_sells"] = len(drop)
        out["entry_drop_sell_vol"] = round(sell_vol, 2)
        by_w: dict[str, float] = {}
        dev_sold = 0
        for tr in drop:
            w = _trade_wallet(tr) or "?"
            by_w[w] = by_w.get(w, 0.0) + vol(tr)
            if _is_dev_or_creator(tr, creator, devs):
                dev_sold = 1
        out["entry_dev_sold_in_drop"] = dev_sold
        if sell_vol > 0 and by_w:
            ranked = sorted(by_w.values(), reverse=True)
            out["entry_top1_seller_share"] = round(ranked[0] / sell_vol, 4)
            top2 = sum(ranked[:2])
            out["entry_top2_seller_share"] = round(top2 / sell_vol, 4)
        elif drop:
            out["entry_top1_seller_share"] = ""
            out["entry_top2_seller_share"] = ""
    return out


def estimate_unique_wallets(traders: list) -> int:
    """Chao1 species estimate of unique wallets from a sampled prefix (or observed if thin)."""
    from collections import Counter
    counts = Counter(t for t in traders if t)
    observed = len(counts)
    if observed == 0:
        return 0
    f1 = sum(1 for v in counts.values() if v == 1)
    f2 = sum(1 for v in counts.values() if v == 2)
    if f2 > 0:
        est = observed + (f1 * f1) / (2.0 * f2)
    else:
        est = observed + f1 * (f1 - 1) / 2.0
    return int(round(est))


def score(launch: dict, trades: list[dict], stats: dict, n_sigs: int, elapsed: float, err: str = "") -> dict:
    scale = float(stats.get("sample_scale") or 1.0) or 1.0
    sampled = bool(stats.get("sampled"))
    n_ok_window = int(stats.get("n_ok_sigs_window") or n_sigs)
    cum = cumulatives(trades)
    max_dd = 0.0
    max_dd_mid = 0.0
    peak = None
    for tr in trades:
        px = _px(tr)
        if not px:
            continue
        if peak is None or px >= peak:
            peak = px
            continue
        dd = (1 - px / peak) * 100
        if dd > max_dd:
            max_dd = dd
        if 30 < (tr.get("t_plus_sec") or -1) <= 300 and dd > max_dd_mid:
            max_dd_mid = dd
    opening, mid = first_live_touches(trades) if trades else ({}, {})
    mcaps = [tr.get("mcap_usd") for tr in trades if tr.get("mcap_usd")]
    ath = max(mcaps) if mcaps else None
    ath_t = None
    if ath is not None:
        for tr in trades:
            if tr.get("mcap_usd") == ath:
                ath_t = tr.get("t_plus_sec")
                break
    vol_sample = sum(tr.get("volume_usd") or 0 for tr in trades)
    vol_est = vol_sample * scale
    row = {
        "mint": launch.get("mint"),
        "name": launch.get("name"),
        "symbol": launch.get("symbol"),
        "launch_ts": launch.get("launch_ts"),
        "launch_time_eat": eat_str(launch.get("launch_ts")),
        "launch_slot": launch.get("launch_slot"),
        "create_sig": launch.get("create_sig"),
        "creator": launch.get("creator"),
        "curve": launch.get("curve"),
        "is_token_2022": int(bool(int(launch.get("is_token_2022") or 0))),
        "extra_mints": launch.get("extra_mints") or "",
        "slice_window": launch.get("slice_window") or "",
        "n_sigs_5m": n_sigs,
        "n_ok_sigs_5m": n_ok_window,
        "n_ok_txs": stats.get("n_ok_txs", 0),
        "n_failed_sigs": stats.get("n_failed_sigs", 0),
        "n_trades_5m": len(trades),
        "n_unique_wallets_5m": len({tr.get("trader") for tr in trades if tr.get("trader")}),
        "volume_usd_5m": round(vol_sample, 2),
        "volume_sol_5m": round(sum(tr.get("sol_ui") or 0 for tr in trades), 4),
        "volume_usd_est": round(vol_est, 2),
        "mcap_start_usd": trades[0].get("mcap_usd") if trades else None,
        "mcap_end_5m_usd": trades[-1].get("mcap_usd") if trades else None,
        "mcap_ath_5m_usd": ath,
        "ath_t_plus_sec": ath_t,
        "max_drop_pct_5m": round(max_dd, 2),
        "max_drop_mid_pct": round(max_dd_mid, 2),
        "custom_quote": int(bool(stats.get("custom_quote"))),
        "n_sol_zero_trades": stats.get("n_sol_zero_trades", 0),
        "sampled": int(sampled),
        "n_sampled": stats.get("n_sampled", len(trades)),
        "sample_scale": round(scale, 3),
        "migrated_5m": int(bool(stats.get("migrated_5m"))),
        "migrate_sec": stats.get("migrate_sec") if stats.get("migrate_sec") is not None else "",
        "pumpswap_pair": stats.get("pumpswap_pair") or "",
        "n_pumpswap_trades_5m": stats.get("n_pumpswap_trades_5m", 0),
        "pass_v0": 0,
        "fail_reasons": "",
        "entry_threshold": "",
        "entry_t_plus": "",
        "entry_drop": "",
        "entry_mcap": "",
        "entry_cum_vol": "",
        "entry_cum_trades": "",
        "entry_cum_wallets": "",
        "elapsed_s": round(elapsed, 2),
        "error": err,
    }
    if err and n_ok_window == 0 and not trades:
        # Fetch failed — do not bucket as dead_by_sig_count (retry on resume).
        row["fail_reasons"] = err
        return row
    if stats.get("dead_by_sig_count") or (n_ok_window < SIG_OK_GATE and not trades):
        reasons = ["dead_by_sig_count"]
        if err:
            reasons.append(err)
        row["fail_reasons"] = ",".join(reasons)
        return row

    for T, name in ((0.25, "25"), (0.30, "30"), (0.35, "35"), (0.40, "40")):
        rec = opening.get(T)
        row[f"open{name}_t"] = rec["t"] if rec else ""
    def fill_mid(T, name):
        rec = mid.get(T)
        prefix = f"mid{name}_"
        if not rec:
            for k in ("t", "drop", "mcap", "cum_vol", "cum_trades", "cum_wallets", "va"):
                row[prefix + k] = ""
            return None
        sig = rec["trade"].get("signature")
        t = rec["t"]
        idx = next((i for i, tr in enumerate(trades) if tr.get("signature") == sig and tr.get("t_plus_sec") == t), None)
        if idx is None:
            cvol, ntr, nw = "", "", ""
        else:
            cvol, ntr, nw = cum[idx]
            cvol = (cvol * scale) if cvol != "" else ""
            ntr = (ntr * scale) if ntr != "" else ""
        row[prefix + "t"] = rec["t"]
        row[prefix + "drop"] = round(rec["drop"], 2)
        row[prefix + "mcap"] = rec["mcap"]
        row[prefix + "cum_vol"] = round(cvol, 2) if cvol != "" else ""
        row[prefix + "cum_trades"] = int(ntr) if ntr != "" else ""
        row[prefix + "cum_wallets"] = nw
        row[prefix + "va"] = int(bool(rec["va"].get("active")))
        return rec, idx
    for T, name in ((0.25, "25"), (0.30, "30"), (0.35, "35"), (0.40, "40")):
        fill_mid(T, name)

    reasons = []
    entry = None
    entry_T = None
    for T, name in ((0.30, "30"), (0.35, "35"), (0.40, "40")):
        if mid.get(T):
            entry = mid[T]
            entry_T = name
            break
    if not entry:
        if mid.get(0.25):
            reasons.append("mid_25_only")
        elif opening.get(0.30) or opening.get(0.35) or opening.get(0.40):
            reasons.append("opening_30plus_only")
        elif not trades:
            reasons.append("no_trades")
        else:
            reasons.append("no_mid_live_30")
    else:
        sig = entry["trade"].get("signature")
        t = entry["t"]
        idx = next((i for i, tr in enumerate(trades) if tr.get("signature") == sig and tr.get("t_plus_sec") == t), 0)
        cvol, ntr_s, nw = cum[idx]
        cvol_est = cvol * scale
        # Trade count: scale sample prefix, but never above window sigs; also consider window size for the filter.
        n_ok_for_filter = int(stats.get("n_ok_sigs_trades") or n_ok_window)
        ntr_est = min(n_ok_for_filter, max(ntr_s, int(ntr_s * scale)))
        # Wallets: observed unique in the (possibly sampled) prefix; if sampled,
        # estimate the true unique count (Chao1) and still require >= 150. No 60-wallet floor.
        traders_prefix = [tr.get("trader") for tr in trades[: idx + 1] if tr.get("trader")]
        nw_obs = len(set(traders_prefix))
        nw_est = estimate_unique_wallets(traders_prefix) if sampled else nw_obs
        if n_ok_for_filter:
            nw_est = min(int(n_ok_for_filter), nw_est)
        nw_est = max(nw_obs, nw_est)
        wallets_ok = nw_est >= MIN_CUM_WALLETS
        row["entry_threshold"] = entry_T
        row["entry_t_plus"] = entry["t"]
        row["entry_drop"] = round(entry["drop"], 2)
        row["entry_mcap"] = entry.get("mcap")
        row["entry_cum_vol"] = round(cvol_est, 2)
        row["entry_cum_trades"] = ntr_est
        row["entry_cum_wallets"] = nw_est
        row["entry_wallets_obs"] = nw_obs
        metrics = entry_setup_metrics(trades, entry["trade"], creator=launch.get("creator"))
        # Keep the score-path observed wallet count if metrics also computed one.
        if metrics.get("entry_wallets_obs") in (None, ""):
            metrics.pop("entry_wallets_obs", None)
        row.update(metrics)
        filt = []
        if cvol_est < MIN_CUM_VOL:
            filt.append(f"low_vol:{cvol_est:.0f}")
        # Prefer window trade count once we are mid-tape on a busy book.
        trades_for_filter = max(ntr_est, n_ok_for_filter if (sampled and entry["t"] >= 30) else ntr_est)
        if trades_for_filter < MIN_CUM_TRADES:
            filt.append(f"low_trades:{trades_for_filter}")
        if not wallets_ok:
            filt.append(f"low_wallets:{nw_est}")
        mcap = entry.get("mcap")
        if mcap is None:
            filt.append("mcap_missing")
        else:
            if mcap < MCAP_MIN:
                filt.append(f"mcap_low:{mcap:.0f}")
            if mcap > MCAP_MAX:
                filt.append(f"mcap_high:{mcap:.0f}")
        if filt:
            reasons.extend(filt)
        else:
            row["pass_v0"] = 1
    if err:
        reasons.append(err)
    row["fail_reasons"] = ",".join(reasons)
    return row



def write_csv(rows: list[dict]):
    tmp = CSV_PATH.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    tmp.replace(CSV_PATH)


def cache_ready(launch: dict, sol_px) -> dict | None:
    """Rescore from cache if this mint can be finalized without new RPC."""
    mint = launch["mint"]
    d = PRE_DIR / mint
    sp, mp, tp = d / "sigs.json", d / "sigs.meta.json", d / "trades.json"
    if not sp.exists() or not mp.exists():
        return None
    meta = load_json(mp, {})
    if not meta.get("reached"):
        return None  # incomplete window — refetch, maybe with a jump
    sigs = load_json(sp, [])
    n_ok = sum(1 for s in sigs if not s.get("err"))
    n_fail = sum(1 for s in sigs if s.get("err"))
    if n_ok < SIG_OK_GATE:
        return score(launch, [], {
            "n_ok_txs": 0,
            "n_failed_sigs": n_fail,
            "n_ok_sigs_window": n_ok,
            "dead_by_sig_count": True,
        }, len(sigs), 0.0)
    if n_ok < MIN_CUM_TRADES and meta.get("reached"):
        # Window is complete and still under the 300-trade floor: finalize without getTransaction.
        row = score(launch, [], {
            "n_ok_txs": 0,
            "n_failed_sigs": n_fail,
            "n_ok_sigs_window": n_ok,
            "dead_by_sig_count": False,
        }, len(sigs), 0.0)
        reasons = [x for x in (row.get("fail_reasons") or "").split(",") if x and x != "no_trades"]
        tag = f"low_trades:{n_ok}"
        row["fail_reasons"] = ",".join([tag] + reasons)
        row["n_ok_sigs_5m"] = n_ok
        return row
    if not tp.exists():
        return None
    try:
        blob = json.loads(tp.read_text())
        trades = blob.get("trades") or []
        stats = dict(blob.get("stats") or {})
        # Re-score early graduates: old curve-only caches must go through PumpSwap attach.
        if not stats.get("pumpswap_checked"):
            return None
        stats["n_ok_sigs_window"] = stats.get("n_ok_sigs_window") or n_ok
        return score(launch, trades, stats, len(sigs), 0.0)
    except Exception:
        return None


def load_done() -> set[str]:

    done = set()
    if CSV_PATH.exists():
        with CSV_PATH.open() as f:
            for r in csv.DictReader(f):
                if r.get("mint") and not (r.get("error") or "").startswith("in_progress"):
                    done.add(r["mint"])
    return done


def _header_ok() -> bool:
    if not CSV_PATH.exists():
        return False
    with CSV_PATH.open() as f:
        line = f.readline()
    try:
        cols = next(csv.reader([line]))
    except Exception:
        return False
    return cols == list(FIELDS)


def append_row(row: dict, lock: threading.Lock, header_written: list):
    with lock:
        if not _header_ok():
            # recover from a stale header or empty file
            existing = []
            if CSV_PATH.exists():
                try:
                    existing = list(csv.DictReader(CSV_PATH.open()))
                except Exception:
                    existing = []
            by = {r["mint"]: r for r in existing if r.get("mint")}
            by[row["mint"]] = row
            write_csv(list(by.values()))
            return
        with CSV_PATH.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writerow({k: row.get(k, "") for k in FIELDS})


def process_one(launch: dict, sol_px) -> dict:
    mint = launch["mint"]
    t0 = int(float(launch["launch_ts"]))
    if not launch.get("slice_window"):
        launch["slice_window"] = hour_window(t0)
    curve = launch.get("curve") or ""
    if not curve:
        from screen.common import derive_bonding_curve
        curve = derive_bonding_curve(mint)
        launch["curve"] = curve
    d = PRE_DIR / mint
    d.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    try:
        sigs = fetch_curve_sigs(curve, t0, int(launch["launch_slot"]) if launch.get("launch_slot") else None, d / "sigs.json")
        n_ok = sum(1 for s in sigs if not s.get("err"))
        n_fail = sum(1 for s in sigs if s.get("err"))
        if n_ok < SIG_OK_GATE:
            stats = {
                "n_ok_txs": 0,
                "n_failed_sigs": n_fail,
                "n_ok_sigs_window": n_ok,
                "dead_by_sig_count": True,
            }
            return score(launch, [], stats, len(sigs), time.time() - t_start)
        # Cannot pass v0 without 300 window trades, regardless of dips. Skip getTransaction.
        if n_ok < MIN_CUM_TRADES:
            stats = {
                "n_ok_txs": 0,
                "n_failed_sigs": n_fail,
                "n_ok_sigs_window": n_ok,
                "dead_by_sig_count": False,
            }
            row = score(launch, [], stats, len(sigs), time.time() - t_start)
            reasons = [x for x in (row.get("fail_reasons") or "").split(",") if x]
            tag = f"low_trades:{n_ok}"
            if tag not in reasons:
                # Keep this ahead of no_trades so the floor that actually binds is visible.
                reasons = [tag] + [r for r in reasons if r != "no_trades"]
            row["fail_reasons"] = ",".join(reasons)
            row["n_ok_sigs_5m"] = n_ok
            return row
        slot0 = int(launch["launch_slot"]) if launch.get("launch_slot") else None
        curve_dest = d / "trades_curve.json"
        # Reuse a pre-fix trades.json as the curve cache so we don't refetch curve txs.
        legacy = d / "trades.json"
        if (not curve_dest.exists()) and legacy.exists():
            try:
                blob = json.loads(legacy.read_text())
                if blob.get("trades") is not None and not (blob.get("stats") or {}).get("pumpswap_checked"):
                    curve_dest.write_text(legacy.read_text())
            except Exception:
                pass
        trades, stats = build_trades(mint, curve, t0, sigs, sol_px, curve_dest)
        trades, stats = maybe_attach_pumpswap(mint, t0, slot0, sol_px, trades, stats, d)
        stats["n_ok_sigs_window"] = stats.get("n_ok_sigs_window") or n_ok
        stats["pumpswap_checked"] = True
        write_final_trades(d / "trades.json", trades, stats, len(sigs), int(stats.get("n_sampled") or len(trades)))
        return score(launch, trades, stats, len(sigs), time.time() - t_start)
    except Exception as e:
        return score(launch, [], {}, 0, time.time() - t_start, err=f"error:{type(e).__name__}")


def _bq_load_candidates(date: str) -> dict | None:
    """Load (or build) cache/bitquery/candidates_<date>.json. None = no filter."""
    p = CACHE / "bitquery" / f"candidates_{date}.json"
    try:
        from screen import bitquery_candidates as _bqc
        _bqc.wait_for_builder(date, log=lambda m: print(m, flush=True))
        if not p.exists():
            if not os.environ.get("BITQUERY_API_KEY"):
                print("bq: no candidates file and no BITQUERY_API_KEY; filter off", flush=True)
                return None
            print(f"bq: building candidates for {date} (Bitquery archive)", flush=True)
            from screen import bitquery_candidates as bqc
            bqc.run_day(date, log=lambda m: print(m, flush=True))
        d = json.loads(p.read_text())
    except Exception as e:  # any Bitquery problem -> plain Helius path
        print(f"bq: candidates unavailable ({type(e).__name__}: {str(e)[:200]}); filter off", flush=True)
        return None
    if sorted(d.get("blocks") or []) != list(range(8)):
        print(f"bq: candidates file covers blocks {d.get('blocks')} only; filter off", flush=True)
        return None
    return d


def _bq_skip_row(r: dict, why: str) -> dict:
    n_ok = 0
    try:
        sigs = json.loads((PRE_DIR / r["mint"] / "sigs.json").read_text())
        n_ok = sum(1 for s in sigs if not s.get("err"))
    except Exception:
        pass
    row = score(r, [], {"n_ok_txs": 0, "n_failed_sigs": 0, "n_ok_sigs_window": n_ok}, n_ok, 0.0)
    row["fail_reasons"] = "bq_skip:" + why
    row["pass_v0"] = 0
    return row


def _bq_filter_need_work(need_work: list, lock, note_row) -> list:
    """Before the Helius gate. Keep: Bitquery candidates (incl. quote anomalies).
    Skip: coins Bitquery scored as non-candidates, and coins Bitquery saw with < A_MIN
    trades in their 3h block (validated on Nov 22: every Helius-active coin and all 22
    known passers were in Bitquery's list). PRESCREEN_BQ_GATE=0 keeps the latter on Helius."""
    d = _bq_load_candidates(DATE_EAT)
    if not d:
        return need_work
    cands = set(d.get("candidates") or [])
    recs = d.get("records") or {}
    skip_absent = os.environ.get("PRESCREEN_BQ_GATE", "1") != "0"
    keep, n_nc, n_abs = [], 0, 0
    for r in need_work:
        m = r["mint"]
        if m in cands:
            keep.append(r)
        elif m in recs:
            append_row(_bq_skip_row(r, recs[m].get("why") or "not_candidate"), lock, []); n_nc += 1
        elif skip_absent:
            append_row(_bq_skip_row(r, "bq_lt%d_trades" % int(d.get("a_min") or 50)), lock, []); n_abs += 1
        else:
            keep.append(r)
    print(f"bq pre-gate: need_work {len(need_work)} -> {len(keep)} to Helius; skipped {n_nc} non-candidates "
          f"+ {n_abs} with < {d.get('a_min')} Bitquery trades; bq candidates={len(cands)} "
          f"points_logged={d.get('points_total_logged')}", flush=True)
    return keep


def _bq_filter_heavy(heavy: list, lock, note_row) -> list:
    d = _bq_load_candidates(DATE_EAT)
    if not d:
        return heavy
    cands = set(d.get("candidates") or [])
    recs = d.get("records") or {}
    keep, skip = [], []
    for r in heavy:
        m = r["mint"]
        if m in cands or m not in recs:  # candidate, or Bitquery has no data -> Helius decides
            keep.append(r)
        else:
            skip.append(r)
    for r in skip:
        append_row(_bq_skip_row(r, (recs.get(r["mint"]) or {}).get("why") or "not_candidate"), lock, [])
    print(f"bq filter: heavy {len(heavy)} -> {len(keep)} to Helius "
          f"(candidates {sum(1 for r in keep if r['mint'] in cands)}, no-bq-data {sum(1 for r in keep if r['mint'] not in recs)}), "
          f"skipped {len(skip)} as bq_skip; points_logged={d.get('points_total_logged')}", flush=True)
    return keep


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=COIN_WORKERS, help="heavy-path coin workers")
    ap.add_argument("--gate-workers", type=int, default=GATE_WORKERS, help="concurrent gate batches")
    ap.add_argument("--gate-batch-size", type=int, default=GATE_BATCH)
    ap.add_argument("--src", choices=("slice", "full"), default="slice")
    ap.add_argument("--date", default="2026-11-21", help="EAT calendar date YYYY-MM-DD")
    ap.add_argument("--skip-gate", action="store_true", help="only run heavy path on cached survivors")
    ap.add_argument("--no-bq", action="store_true",
                    help="disable the Bitquery candidate filter (also re-queues earlier bq_skip rows)")
    args = ap.parse_args()
    use_bq = not args.no_bq and os.environ.get("PRESCREEN_BQ", "1") != "0"

    global DATE_EAT, CSV_PATH, PRE_DIR
    DATE_EAT = args.date
    CSV_PATH = SCREEN / f"prescreen_{DATE_EAT}.csv"
    PRE_DIR = CACHE / ("prescreen" if DATE_EAT == "2026-11-21" else f"prescreen_{DATE_EAT}")
    PRE_DIR.mkdir(parents=True, exist_ok=True)

    src = SCREEN / (f"launches_{DATE_EAT}.csv" if args.src == "full" else f"launches_{DATE_EAT}_slice.csv")
    if not src.exists():
        raise SystemExit(f"missing {src}")
    launches = list(csv.DictReader(src.open()))
    launches = [r for r in launches if r.get("extract_ok") in ("1", "True", "true") and r.get("mint")]
    seen = set()
    uniq = []
    from screen.common import derive_bonding_curve
    for r in launches:
        if r["mint"] in seen:
            continue
        seen.add(r["mint"])
        if not r.get("curve"):
            try:
                r["curve"] = derive_bonding_curve(r["mint"])
            except Exception:
                pass
        uniq.append(r)
    launches = uniq
    if args.limit:
        launches = launches[: args.limit]
    sol_px = sol_px_fn(DATE_EAT)

    def row_done(row: dict) -> bool:
        if not row or not row.get("mint"):
            return False
        if str(row.get("error") or "").startswith("in_progress"):
            return False
        if str(row.get("error") or "").startswith("error:"):
            return False
        if not use_bq and str(row.get("fail_reasons") or "").startswith("bq_skip"):
            return False
        return True

    # Merge existing CSV + cache_ready rescores so a restart never drops scored rows.
    by: dict[str, dict] = {}
    if CSV_PATH.exists():
        try:
            for r in csv.DictReader(CSV_PATH.open()):
                if row_done(r):
                    by[r["mint"]] = r
        except Exception:
            pass
    cached_rows = []
    n_dead_cached = 0
    n_pass_cached = 0
    need_work = []
    for r in launches:
        row = cache_ready(r, sol_px)
        if row is not None:
            by[r["mint"]] = row
            cached_rows.append(row)
            n_dead_cached += int(str(row.get("fail_reasons") or "").startswith("dead_by_sig_count"))
            n_pass_cached += int(row.get("pass_v0") or 0)
            continue
        if r["mint"] in by and row_done(by[r["mint"]]):
            continue
        need_work.append(r)
    if by:
        write_csv(list(by.values()))
    print(
        f"prescreen need={len(need_work)} from_cache={len(cached_rows)} csv_kept={len(by)} "
        f"dead_cached={n_dead_cached} pass_cached={n_pass_cached} "
        f"total={len(launches)} heavy_workers={args.workers} gate_workers={args.gate_workers} "
        f"gate_batch={args.gate_batch_size} gate={SIG_OK_GATE}",
        flush=True,
    )

    lock = threading.Lock()
    t_run = time.time()
    n_pass = sum(int(r.get("pass_v0") or 0) for r in by.values())
    n_dead = sum(1 for r in by.values() if str(r.get("fail_reasons") or "").startswith("dead_by_sig_count"))
    n_gated_tx = sum(1 for r in by.values() if int(r.get("n_ok_sigs_5m") or 0) >= SIG_OK_GATE)

    def note_row(row: dict):
        nonlocal n_pass, n_dead, n_gated_tx
        n_pass += int(row.get("pass_v0") or 0)
        fr = row.get("fail_reasons") or ""
        if fr == "dead_by_sig_count" or fr.startswith("dead_by_sig_count"):
            n_dead += 1
        elif int(row.get("n_ok_sigs_5m") or 0) >= SIG_OK_GATE:
            n_gated_tx += 1
        if int(row.get("pass_v0") or 0):
            print(
                f"  PASS v0 {row.get('symbol') or ''} {row.get('mint')} "
                f"entry={row.get('entry_threshold')}% t+{row.get('entry_t_plus')}s "
                f"mcap={row.get('entry_mcap')} vol={row.get('entry_cum_vol')} "
                f"wallets={row.get('entry_cum_wallets')}",
                flush=True,
            )

    # ---- BITQUERY PRE-GATE: only Bitquery candidates (and coins it can't judge) reach Helius ----
    if use_bq and need_work:
        need_work = _bq_filter_need_work(need_work, lock, note_row)

    # ---- FAST GATE PASS: batched getSignaturesForAddress, dead coins stop here ----
    heavy: list[dict] = []
    # Already-cached survivors whose trades aren't finalized still need the heavy path.
    for r in need_work:
        sp = PRE_DIR / r["mint"] / "sigs.json"
        mp = PRE_DIR / r["mint"] / "sigs.meta.json"
        if sp.exists() and mp.exists():
            meta = load_json(mp, {})
            try:
                n_ok = sum(1 for s in json.loads(sp.read_text()) if not s.get("err"))
            except Exception:
                n_ok = 0
            if meta.get("reached") and n_ok < SIG_OK_GATE:
                # should have been cache_ready; if not, score now
                sigs = load_json(sp, [])
                row = score(r, [], {"n_ok_txs": 0, "n_failed_sigs": sum(1 for s in sigs if s.get("err")),
                                    "n_ok_sigs_window": n_ok, "dead_by_sig_count": True}, len(sigs), 0.0)
                append_row(row, lock, [])
                note_row(row)
                continue
            if n_ok >= SIG_OK_GATE:
                heavy.append(r)
                continue
        if not args.skip_gate:
            continue  # goes to gate
        heavy.append(r)

    gate_todo = [r for r in need_work if r["mint"] not in {x["mint"] for x in heavy}
                 and r["mint"] not in {row.get("mint") for row in []} ]
    # Recompute gate_todo as need_work minus those already sent to heavy or just scored dead from cache.
    already = {r["mint"] for r in heavy}
    # also anything we just appended from reached-dead above is in CSV; skip if sigs reached dead
    def still_needs_gate(r):
        if r["mint"] in already:
            return False
        sp = PRE_DIR / r["mint"] / "sigs.json"
        mp = PRE_DIR / r["mint"] / "sigs.meta.json"
        if sp.exists() and mp.exists():
            meta = load_json(mp, {})
            try:
                n_ok = sum(1 for s in json.loads(sp.read_text()) if not s.get("err"))
            except Exception:
                n_ok = 0
            if meta.get("reached") and n_ok < SIG_OK_GATE:
                return False
            if n_ok >= SIG_OK_GATE:
                return False
        return not args.skip_gate

    gate_todo = [r for r in need_work if still_needs_gate(r)]
    print(f"gate pass todo={len(gate_todo)} already_heavy={len(heavy)}", flush=True)
    need_window: list[dict] = []
    if gate_todo:
        t_gate = time.time()
        n_g = 0
        bs = max(1, int(args.gate_batch_size))
        chunks = [gate_todo[i:i + bs] for i in range(0, len(gate_todo), bs)]
        with ThreadPoolExecutor(max_workers=max(1, int(args.gate_workers))) as ex:
            futs = [ex.submit(_gate_batch, ch) for ch in chunks]
            for fut in as_completed(futs):
                for kind, launch, row in fut.result():
                    n_g += 1
                    if kind == "dead" and row is not None:
                        append_row(row, lock, [])
                        note_row(row)
                    elif kind == "heavy":
                        heavy.append(launch)
                    elif kind == "need_window":
                        need_window.append(launch)
                    elif kind == "error" and row is not None:
                        append_row(row, lock, [])
                    if n_g % 500 == 0 or n_g == len(gate_todo):
                        elapsed = max(0.001, time.time() - t_gate)
                        remain = len(gate_todo) - n_g
                        eta = remain / max(0.001, n_g / elapsed)
                        print(
                            f"  gate {n_g}/{len(gate_todo)} dead={n_dead} heavy={len(heavy)} "
                            f"need_window={len(need_window)} "
                            f"{n_g/elapsed:.2f} coins/s elapsed={elapsed:.1f}s eta={eta/60:.1f}m",
                            flush=True,
                        )
        print(
            f"gate page-1 done in {time.time()-t_gate:.1f}s survivors={len(heavy)} "
            f"need_window={len(need_window)} "
            f"rate={len(gate_todo)/max(0.001, time.time()-t_gate):.2f} coins/s",
            flush=True,
        )

    if need_window:
        t_nw = time.time()
        print(f"gate follow-up need_window={len(need_window)} (seek cursor + window-only GSFA)", flush=True)
        n_nw = 0

        def _follow(r: dict):
            t0w = int(float(r["launch_ts"]))
            dest = PRE_DIR / r["mint"] / "sigs.json"
            try:
                sigs = fetch_window_after_miss(r["curve"], t0w, dest, stop_at_gate=True)
            except Exception as e:
                row = score(r, [], {"n_ok_txs": 0, "n_failed_sigs": 0, "n_ok_sigs_window": 0}, 0, 0.0,
                            err=f"error:{type(e).__name__}")
                return "error", r, row
            n_ok = sum(1 for s in sigs if not s.get("err"))
            n_fail = sum(1 for s in sigs if s.get("err"))
            if n_ok < SIG_OK_GATE:
                row = score(r, [], {"n_ok_txs": 0, "n_failed_sigs": n_fail, "n_ok_sigs_window": n_ok,
                                    "dead_by_sig_count": True}, len(sigs), 0.0)
                return "dead", r, row
            return "heavy", r, None

        with ThreadPoolExecutor(max_workers=max(8, int(args.gate_workers))) as ex:
            futs = [ex.submit(_follow, r) for r in need_window]
            for fut in as_completed(futs):
                kind, launch, row = fut.result()
                n_nw += 1
                if kind == "dead" and row is not None:
                    append_row(row, lock, [])
                    note_row(row)
                elif kind == "heavy":
                    heavy.append(launch)
                elif kind == "error" and row is not None:
                    append_row(row, lock, [])
                if n_nw % 200 == 0 or n_nw == len(need_window):
                    elapsed = max(0.001, time.time() - t_nw)
                    print(
                        f"  follow-up {n_nw}/{len(need_window)} dead={n_dead} heavy={len(heavy)} "
                        f"{n_nw/elapsed:.2f} coins/s elapsed={elapsed:.1f}s",
                        flush=True,
                    )
        print(f"gate follow-up done in {time.time()-t_nw:.1f}s", flush=True)

    # ---- BITQUERY CANDIDATE FILTER (loose superset; Helius strict v0 stays the decider) ----
    if use_bq and heavy:
        heavy = _bq_filter_heavy(heavy, lock, note_row)

    # ---- HEAVY PASS: trade fetch + score, PumpSwap only if curve completed in 300s ----
    print(f"heavy pass todo={len(heavy)} workers={args.workers}", flush=True)
    t_heavy = time.time()
    n_h = 0
    if heavy:
        with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as ex:
            futs = {ex.submit(process_one, r, sol_px): r for r in heavy}
            for fut in as_completed(futs):
                row = fut.result()
                append_row(row, lock, [])
                n_h += 1
                note_row(row)
                if n_h % 10 == 0 or n_h == len(heavy):
                    elapsed = max(0.001, time.time() - t_heavy)
                    remain = len(heavy) - n_h
                    eta = remain / max(0.001, n_h / elapsed)
                    print(
                        f"  heavy {n_h}/{len(heavy)} pass={n_pass} dead={n_dead} "
                        f"tx_books={n_gated_tx} {n_h/elapsed:.2f} coins/s elapsed={elapsed:.1f}s eta={eta/60:.1f}m",
                        flush=True,
                    )
        print(f"heavy done in {time.time()-t_heavy:.1f}s", flush=True)

    elapsed = time.time() - t_run
    rows = list(csv.DictReader(CSV_PATH.open())) if CSV_PATH.exists() else []
    by = {}
    for r in rows:
        if r.get("mint"):
            by[r["mint"]] = r
    rows = list(by.values())
    n_pass = sum(int(r.get("pass_v0") or 0) for r in rows)
    n_dead = sum(1 for r in rows if (r.get("fail_reasons") or "").startswith("dead_by_sig_count"))
    n_gate = sum(1 for r in rows if int(r.get("n_ok_sigs_5m") or 0) >= SIG_OK_GATE)
    summary = {
        "date_eat": DATE_EAT,
        "n_scored": len(rows),
        "n_pass_v0": n_pass,
        "n_dead_by_sig_count": n_dead,
        "n_cleared_sig_gate": n_gate,
        "pass_rate": (n_pass / len(rows)) if rows else 0,
        "elapsed_s": round(elapsed, 2),
        "csv": str(CSV_PATH),
        "passers": [r["mint"] for r in rows if int(r.get("pass_v0") or 0)],
    }
    atomic_write_json(CACHE / f"prescreen_{DATE_EAT}.summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
