"""Wallet profiles for creator and notable traders. Caps RPC so later coins stay fast."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from lib.constants import PUMP_PROGRAM, JITO_TIPS
from lib.rpc import rpc_call, rpc_batch, enhanced_addr_url, _session
from lib.fetch import RAW_TX_PARAMS, cache_dir
from lib.parse import account_keys, parse_tx

EAT = timezone(timedelta(hours=3))


def paginate_sigs(address: str, limit_pages: int = 10, until_ts: int | None = None) -> list[dict]:
    """Newest-first pages, stop after limit_pages or once all remaining are older than until_ts."""
    all_sigs = []
    before = None
    for _ in range(limit_pages):
        params: dict = {"limit": 1000}
        if before:
            params["before"] = before
        page = rpc_call("getSignaturesForAddress", [address, params]) or []
        if not page:
            break
        all_sigs.extend(page)
        if len(page) < 1000:
            return all_sigs  # complete
        if until_ts is not None and page[-1].get("blockTime") and page[-1]["blockTime"] < until_ts:
            break
        before = page[-1]["signature"]
    return all_sigs  # possibly truncated


def fetch_raw_sigs(sigs: list[str], cache_mint: str | None = None) -> dict:
    """Small raw getTransaction helper (batch 8, serial)."""
    have = {}
    todo = list(sigs)
    # reuse mint cache if present
    if cache_mint:
        from lib.fetch import _load_cached_txs
        cached = _load_cached_txs(cache_dir(cache_mint))
        for s in list(todo):
            if s in cached:
                have[s] = cached[s]
                todo.remove(s)
    i = 0
    while i < len(todo):
        batch = todo[i:i+8]
        try:
            res = rpc_batch([("getTransaction", [s, RAW_TX_PARAMS]) for s in batch], timeout=45)
        except Exception:
            i += 8
            time.sleep(0.4)
            continue
        for s, r in zip(batch, res):
            if r and not (isinstance(r, dict) and r.get("_error")):
                have[s] = r
        i += 8
        time.sleep(0.1)
    return have


def first_inbound_sol(address: str, txs_chrono: list[tuple[str, dict]], before_ts: int) -> Optional[dict]:
    """Find last inbound SOL transfer to address before before_ts."""
    last = None
    for sig, tx in txs_chrono:
        if (tx.get("blockTime") or 0) >= before_ts:
            break
        meta = tx.get("meta") or {}
        msg = (tx.get("transaction") or {}).get("message") or {}
        def walk(ixs):
            nonlocal last
            for ix in ixs or []:
                parsed = ix.get("parsed")
                if isinstance(parsed, dict) and parsed.get("type") == "transfer":
                    info = parsed.get("info") or {}
                    if info.get("destination") == address:
                        last = {
                            "signature": sig,
                            "slot": tx.get("slot"),
                            "block_time": tx.get("blockTime"),
                            "from": info.get("source"),
                            "lamports": int(info.get("lamports") or 0),
                            "sol": int(info.get("lamports") or 0) / 1e9,
                        }
        walk(msg.get("instructions") or [])
        for g in meta.get("innerInstructions") or []:
            walk(g.get("instructions") or [])
    return last


def count_pump_creates(address: str, txs: dict) -> list[dict]:
    found = []
    for sig, tx in txs.items():
        meta = tx.get("meta") or {}
        if meta.get("err"):
            continue
        logs = meta.get("logMessages") or []
        if any("Instruction: Create" in x for x in logs) and any(PUMP_PROGRAM[:8] in x or "CreateV2" in x or "Create" in x for x in logs):
            if any("Instruction: Create" in x for x in logs):
                found.append({"signature": sig, "block_time": tx.get("blockTime"), "slot": tx.get("slot")})
    return found


def sol_pre_post(tx: dict, address: str) -> tuple[Optional[int], Optional[int]]:
    keys = account_keys(tx)
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if address in keys:
        i = keys.index(address)
        if i < len(pre) and i < len(post):
            return pre[i], post[i]
    return None, None


def profile_wallet(address: str, launch_ts: int, launch_slot: int, mint: str,
                   role_flags: dict, activity: dict, cap_pages: int = 8) -> dict:
    """Build one wallet row. activity has tokens_bought etc from this coin."""
    sigs = paginate_sigs(address, limit_pages=cap_pages, until_ts=launch_ts - 90 * 24 * 3600)
    truncated = len(sigs) >= cap_pages * 1000 and (not sigs or (sigs[-1].get("blockTime") or 0) > launch_ts - 90 * 24 * 3600)
    # sigs are newest-first
    first = sigs[-1] if sigs else None
    # find first sig actually
    # if truncated, first is unknown (older than last page)
    before = [s for s in sigs if s.get("blockTime") and s["blockTime"] < launch_ts]
    at_or_after = [s for s in sigs if s.get("blockTime") and s["blockTime"] >= launch_ts]
    tx_before = len(before)
    if truncated:
        tx_before_label = f">={tx_before}"
        age_unknown = True
        first_time = sigs[-1].get("blockTime") if sigs else None
        age_sec = (launch_ts - first_time) if first_time else None
        age_label = f"> {age_sec/3600:.1f}h (truncated, busy wallet)" if age_sec else "unknown (busy)"
    else:
        age_unknown = False
        first_time = first.get("blockTime") if first else None
        age_sec = (launch_ts - first_time) if first_time else None
        if age_sec is None:
            age_label = "unknown"
        elif age_sec < 60:
            age_label = f"fresh {age_sec}s"
        elif age_sec < 3600:
            age_label = f"fresh {age_sec/60:.1f}m"
        elif age_sec < 86400:
            age_label = f"{age_sec/3600:.1f}h"
        else:
            age_label = f"{age_sec/86400:.1f}d"
        tx_before_label = str(tx_before)

    # funding: all pre-launch txs if the history is complete and short; otherwise last 20
    need = []
    if first and not truncated:
        need.append(first["signature"])
    pre_for_fund = before if (not truncated and len(before) <= 40) else before[:20]
    need.extend([s["signature"] for s in pre_for_fund])
    raw = fetch_raw_sigs(list(dict.fromkeys(need)), cache_mint=mint)
    chrono = []
    for s in reversed(pre_for_fund):
        if s["signature"] in raw:
            chrono.append((s["signature"], raw[s["signature"]]))
    fund = first_inbound_sol(address, chrono, launch_ts)
    # If the wallet was born with a non-transfer fill (e.g. Jupiter route), capture that too.
    birth = None
    largest_in = fund
    for sig, tx in chrono:
        pre, post = sol_pre_post(tx, address)
        if pre is None or post is None:
            continue
        if pre == 0 and post > 0 and birth is None:
            birth = {"signature": sig, "block_time": tx.get("blockTime"), "sol": post / 1e9, "from": "wallet_birth_fill"}
        # any net inbound larger than current fund
        delta = post - pre
        if delta > 0 and (largest_in is None or delta / 1e9 > (largest_in.get("sol") or 0)):
            # prefer an explicit transfer if we already have fund at this size; else record delta
            if not fund or delta / 1e9 > (fund.get("sol") or 0) + 1e-9:
                largest_in = {
                    "signature": sig,
                    "block_time": tx.get("blockTime"),
                    "from": (fund or {}).get("from") if fund and fund.get("signature")==sig else "see_tx_or_birth_fill",
                    "sol": delta / 1e9,
                }
    if birth and (not fund or (fund.get("sol") or 0) < birth["sol"] * 0.5):
        # main money arrived at birth; keep last inbound in note
        fund = fund or {}
        fund = {
            "signature": birth["signature"],
            "block_time": birth["block_time"],
            "from": (fund.get("from") if fund else None) or birth["from"],
            "sol": birth["sol"],
            "note": f"wallet born with {birth['sol']:.4f} SOL; last simple inbound may be smaller",
        }

    looks = []
    if not truncated and age_sec is not None and age_sec <= 600:
        looks.append("fresh_wallet")
    if tx_before <= 5 and not truncated:
        looks.append("few_prior_txs")
    if activity.get("first_buy_t_plus") is not None and activity["first_buy_t_plus"] <= 5:
        looks.append("sniper_timing")
    if activity.get("n_buys", 0) >= 1 and activity.get("first_buy_t_plus", 99) <= 2 and (activity.get("priority_or_tip") or 0) > 0.001:
        looks.append("bot_or_sniper")

    # prior pump creates among fetched raw (limited)
    creates = count_pump_creates(address, raw)

    # SOL just before first appearance in this coin if we have that tx raw
    sol_launch = None
    first_coin_sig = activity.get("first_sig")
    if first_coin_sig:
        raw2 = fetch_raw_sigs([first_coin_sig], cache_mint=mint)
        if first_coin_sig in raw2:
            pre, post = sol_pre_post(raw2[first_coin_sig], address)
            if pre is not None:
                sol_launch = pre / 1e9

    row = {
        "wallet": address,
        "roles": ",".join(sorted(k for k, v in role_flags.items() if v)),
        "wallet_age_sec_at_launch": age_sec if not age_unknown else None,
        "wallet_age_label": age_label,
        "wallet_age_truncated": truncated,
        "first_tx_time": first_time,
        "first_tx_sig": (first or {}).get("signature") if not truncated else None,
        "tx_count_before_launch": tx_before_label,
        "tx_count_fetched": len(sigs),
        "sol_at_first_coin_tx_pre": sol_launch,
        "funding_source": (fund or {}).get("from"),
        "funding_sol": (fund or {}).get("sol"),
        "funding_time": (fund or {}).get("block_time"),
        "funding_sig": (fund or {}).get("signature"),
        "funding_confidence": ("medium" if fund else "low_not_found_in_recent_pre_launch_txs"),
        "funding_note": (fund or {}).get("note"),
        "prior_pump_creates_seen": len(creates),
        "looks_like": ",".join(looks) or "unclassified",
        **activity,
    }
    return row
