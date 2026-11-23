#!/usr/bin/env python3
"""Shared helpers for the Pump.fun screen pipeline. Never logs API keys."""
from __future__ import annotations

import csv
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from lib.constants import PUMP_PROGRAM, TOKEN_2022, WSOL
from lib.rpc import rpc_batch, rpc_call

ROOT = Path("/workspace/pumpfun")
SCREEN = ROOT / "screen"
CACHE = SCREEN / "cache"
NBO = timezone(timedelta(hours=3))
MINT_AUTHORITY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
DATE_EAT = "2026-11-21"
# Nov 21 00:00-24:00 Africa/Nairobi (UTC+3)
T_START = int(datetime(2026, 11, 21, 0, 0, 0, tzinfo=NBO).timestamp())  # 1795208400
T_END = int(datetime(2026, 11, 22, 0, 0, 0, tzinfo=NBO).timestamp())    # 1795294800


def day_bounds(date_eat: str) -> tuple[int, int]:
    y, m, d = (int(x) for x in date_eat.split("-"))
    start = int(datetime(y, m, d, 0, 0, 0, tzinfo=NBO).timestamp())
    return start, start + 86400


def hour_window(ts) -> str:
    if ts in (None, ""):
        return ""
    dt = datetime.fromtimestamp(int(float(ts)), tz=NBO)
    return f"{dt.hour:02d}:00-{dt.hour:02d}:59"

# High global in-flight cap, spread across 3 Helius keys via rpc.py round-robin.
RPC_CONCURRENCY = 48
_RPC_SEM = threading.Semaphore(RPC_CONCURRENCY)
RAW_TX_PARAMS = {
    "encoding": "jsonParsed",
    "maxSupportedTransactionVersion": 1,
    "commitment": "confirmed",
}


def eat_str(ts: int | float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts), tz=NBO).strftime("%Y-%m-%d %H:%M:%S UTC+03:00")


def rpc_call_lim(method: str, params, timeout: int = 60, retries: int = 6):
    with _RPC_SEM:
        return rpc_call(method, params, timeout=timeout, retries=retries)


def rpc_batch_lim(calls: list[tuple[str, Any]], timeout: int = 120, retries: int = 6):
    with _RPC_SEM:
        return rpc_batch(calls, timeout=timeout, retries=retries)


def derive_bonding_curve(mint: str) -> str:
    from solders.pubkey import Pubkey
    program = Pubkey.from_string(PUMP_PROGRAM)
    m = Pubkey.from_string(mint)
    pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(m)], program)
    return str(pda)


def _pubkey(k) -> str:
    if isinstance(k, str):
        return k
    if isinstance(k, dict):
        return k.get("pubkey") or ""
    return str(k or "")


def resolved_account_keys(tx: dict) -> list[str]:
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [_pubkey(k) for k in (msg.get("accountKeys") or [])]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys += [_pubkey(k) for k in (loaded.get("writable") or [])]
    keys += [_pubkey(k) for k in (loaded.get("readonly") or [])]
    return keys


def ix_program_and_accounts(ix: dict, keys: list[str]) -> tuple[str, list[str]]:
    pid = ix.get("programId")
    if not pid and isinstance(ix.get("programIdIndex"), int) and ix["programIdIndex"] < len(keys):
        pid = keys[ix["programIdIndex"]]
    accs = ix.get("accounts") or []
    out_accs = []
    for a in accs:
        if isinstance(a, int):
            out_accs.append(keys[a] if a < len(keys) else "")
        else:
            out_accs.append(_pubkey(a))
    if not out_accs and ix.get("accountKeyIndexes"):
        out_accs = [keys[i] for i in ix["accountKeyIndexes"] if i < len(keys)]
    return pid or "", out_accs


def extract_create(sig: str, tx: dict | None, block_time: int | None = None, slot: int | None = None, err=None) -> dict:
    """Pull mint / creator / name from a Pump.fun Create / CreateV2 transaction."""
    out = {
        "create_sig": sig,
        "launch_ts": block_time if block_time is not None else (tx or {}).get("blockTime"),
        "launch_slot": slot if slot is not None else (tx or {}).get("slot"),
        "err": json.dumps(err) if err not in (None, "", {}) else "",
        "mint": "",
        "name": "",
        "symbol": "",
        "uri": "",
        "creator": "",
        "token_program": "",
        "is_token_2022": False,
        "curve": "",
        "extra_mints": "",
        "extract_ok": False,
        "extract_note": "",
    }
    if not tx or err:
        out["extract_note"] = "no_tx" if not tx else "tx_err"
        return out
    try:
        msg = (tx.get("transaction") or {}).get("message") or {}
        meta = tx.get("meta") or {}
        if meta.get("err"):
            out["err"] = json.dumps(meta["err"])
            out["extract_note"] = "meta_err"
            return out
        keys = resolved_account_keys(tx)
        if keys:
            out["creator"] = keys[0]
        ixs = list(msg.get("instructions") or [])
        for group in (meta.get("innerInstructions") or []):
            ixs.extend(group.get("instructions") or [])
        for ix in ixs:
            pid, accs = ix_program_and_accounts(ix, keys)
            if pid != PUMP_PROGRAM:
                continue
            if accs:
                out["mint"] = accs[0]
                if len(accs) > 2 and accs[1] == MINT_AUTHORITY:
                    out["curve"] = accs[2]
                if TOKEN_2022 in accs:
                    out["is_token_2022"] = True
                    out["token_program"] = TOKEN_2022
                break
        mints_seen = []
        for b in (meta.get("postTokenBalances") or []) + (meta.get("preTokenBalances") or []):
            m = b.get("mint")
            if m:
                mints_seen.append(m)
                if m == out["mint"] or (not out["mint"] and str(m).endswith("pump")):
                    if not out["mint"]:
                        out["mint"] = m
                    out["token_program"] = b.get("programId") or out["token_program"]
                    if out["token_program"] == TOKEN_2022:
                        out["is_token_2022"] = True
        extra = sorted({m for m in mints_seen if m not in (out["mint"], WSOL)})
        out["extra_mints"] = ",".join(extra[:6])
        from lib.parse import decode_program_data_logs
        for ev in decode_program_data_logs(meta.get("logMessages") or []):
            if ev.get("event") == "CreateEvent":
                out["name"] = ev.get("name") or out["name"]
                out["symbol"] = ev.get("symbol") or out["symbol"]
                out["uri"] = ev.get("uri") or out["uri"]
        if out["mint"] and not out["curve"]:
            try:
                out["curve"] = derive_bonding_curve(out["mint"])
            except Exception:
                pass
        if not out["token_program"] and out["is_token_2022"]:
            out["token_program"] = TOKEN_2022
        out["extract_ok"] = bool(out["mint"])
        if not out["extract_ok"]:
            out["extract_note"] = "no_mint"
        out["launch_ts"] = out["launch_ts"] or tx.get("blockTime")
        out["launch_slot"] = out["launch_slot"] or tx.get("slot")
        return out
    except Exception as e:
        out["extract_note"] = f"exc:{type(e).__name__}"
        return out


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(path)


def fetch_transactions(signatures: list[str], dest_dir: Path, batch_size: int = 40, workers: int = 36, label: str = "txs") -> dict[str, Any]:
    """High-concurrency getTransaction; caches JSON parts under dest_dir/part_*.json."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    have: dict[str, Any] = {}
    for part in sorted(dest_dir.glob("part_*.json")):
        try:
            blob = json.loads(part.read_text())
            if isinstance(blob, dict):
                have.update(blob)
        except Exception:
            continue
    todo = [s for s in signatures if s not in have]
    if not todo:
        return have
    existing_ids = []
    for f in dest_dir.glob("part_*.json"):
        try:
            existing_ids.append(int(f.stem.split("_")[1]))
        except Exception:
            pass
    start_id = (max(existing_ids) + 1) if existing_ids else 0
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    t0 = time.time()
    done = 0
    got_n = 0
    err_n = 0
    lock = threading.Lock()

    def one(batch: list[str], part_id: int):
        nonlocal got_n, err_n
        try:
            results = rpc_batch_lim([("getTransaction", [sig, RAW_TX_PARAMS]) for sig in batch], timeout=60)
        except Exception:
            results = [None] * len(batch)
        got = {}
        local_err = 0
        for sig, res in zip(batch, results):
            if res and not (isinstance(res, dict) and res.get("_error")):
                got[sig] = res
            else:
                local_err += 1
        if got:
            (dest_dir / f"part_{part_id:05d}.json").write_text(json.dumps(got, separators=(",", ":")))
        with lock:
            got_n += len(got)
            err_n += local_err
        return got

    print(f"{label} fetch {len(todo)} txs in {len(batches)} batches workers={workers} batch={batch_size}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, b, start_id + i): i for i, b in enumerate(batches)}
        for fut in as_completed(futs):
            got = fut.result()
            have.update(got)
            done += 1
            if done % 20 == 0 or done == len(batches):
                elapsed = max(0.001, time.time() - t0)
                print(f"  {label} {done}/{len(batches)} cached={len(have)} new={got_n} err={err_n} {got_n/elapsed:.1f} tx/s", flush=True)
    return have
