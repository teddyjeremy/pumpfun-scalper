"""Discover a Pump.fun coin, fetch curve signatures and transactions, cache on disk."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from lib.constants import PUMP_PROGRAM, WINDOW_SEC
from lib.rpc import rpc_batch, rpc_call, api_key

ROOT = Path("/workspace/pumpfun")
CACHE = ROOT / "cache"
COINS = ROOT / "coins"
RAW_TX_PARAMS = {
    "encoding": "jsonParsed",
    "maxSupportedTransactionVersion": 1,
    "commitment": "confirmed",
}


def cache_dir(mint: str) -> Path:
    p = CACHE / mint
    p.mkdir(parents=True, exist_ok=True)
    (p / "txparts").mkdir(exist_ok=True)
    return p


def out_dir(mint: str) -> Path:
    p = COINS / mint
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_asset(mint: str) -> dict:
    try:
        return rpc_call("getAsset", {"id": mint}) or {}
    except Exception as e:
        return {"_error": str(e)}


def dexscreener_pairs(mint: str) -> list[dict]:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code != 200:
        return []
    return (r.json() or {}).get("pairs") or []


def derive_bonding_curve(mint: str) -> Optional[str]:
    try:
        from solders.pubkey import Pubkey
        program = Pubkey.from_string(PUMP_PROGRAM)
        m = Pubkey.from_string(mint)
        pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(m)], program)
        return str(pda)
    except Exception:
        return None


def resolve_curve(mint: str) -> dict:
    """Return curve address, pumpswap pair, creation guess, name/symbol."""
    info = {
        "mint": mint,
        "curve": None,
        "pumpswap_pair": None,
        "pumpfun_pair_created_ms": None,
        "pumpswap_pair_created_ms": None,
        "name": None,
        "symbol": None,
        "migrated": False,
        "dex_pairs": [],
    }
    asset = get_asset(mint)
    info["asset"] = {
        "name": ((asset.get("content") or {}).get("metadata") or {}).get("name"),
        "symbol": ((asset.get("content") or {}).get("metadata") or {}).get("symbol"),
        "token_info": asset.get("token_info") or {},
    }
    info["name"] = info["asset"]["name"]
    info["symbol"] = info["asset"]["symbol"]

    pairs = dexscreener_pairs(mint)
    slim = []
    for p in pairs:
        slim.append({
            "dexId": p.get("dexId"),
            "pairAddress": p.get("pairAddress"),
            "pairCreatedAt": p.get("pairCreatedAt"),
            "quote": (p.get("quoteToken") or {}).get("symbol"),
            "base": (p.get("baseToken") or {}).get("symbol"),
            "baseAddress": (p.get("baseToken") or {}).get("address"),
            "marketCap": p.get("marketCap"),
            "priceUsd": p.get("priceUsd"),
            "priceNative": p.get("priceNative"),
        })
        if p.get("dexId") == "pumpfun" and (p.get("baseToken") or {}).get("address") == mint:
            info["curve"] = p.get("pairAddress")
            info["pumpfun_pair_created_ms"] = p.get("pairCreatedAt")
            info["name"] = info["name"] or (p.get("baseToken") or {}).get("name")
            info["symbol"] = info["symbol"] or (p.get("baseToken") or {}).get("symbol")
        if p.get("dexId") == "pumpswap" and (p.get("baseToken") or {}).get("address") == mint:
            info["pumpswap_pair"] = p.get("pairAddress")
            info["pumpswap_pair_created_ms"] = p.get("pairCreatedAt")
            info["migrated"] = True
    info["dex_pairs"] = slim

    if not info["curve"]:
        info["curve"] = derive_bonding_curve(mint)
        info["curve_source"] = "pda"
    else:
        info["curve_source"] = "dexscreener"

    # verify owner
    if info["curve"]:
        acc = rpc_call("getAccountInfo", [info["curve"], {"encoding": "base64"}])
        val = (acc or {}).get("value")
        if not val:
            info["curve_missing"] = True
        else:
            info["curve_owner"] = val.get("owner")
            info["curve_lamports"] = val.get("lamports")
    return info


def fetch_all_sigs(address: str, dest: Path) -> list[dict]:
    if dest.exists():
        sigs = json.loads(dest.read_text())
        if sigs:
            return sigs
    all_sigs: list[dict] = []
    before = None
    while True:
        params: dict[str, Any] = {"limit": 1000}
        if before:
            params["before"] = before
        page = rpc_call("getSignaturesForAddress", [address, params]) or []
        if not page:
            break
        all_sigs.extend(page)
        if len(page) < 1000:
            break
        before = page[-1]["signature"]
    # chronological
    all_sigs = list(reversed(all_sigs))
    dest.write_text(json.dumps(all_sigs))
    return all_sigs


def _slot_for_time(target_ts: int, slot0: int, t0: int) -> int:
    """Binary-search a slot whose blockTime is at or just after target_ts."""
    dt = max(1, int(target_ts) - int(t0))
    est = int(slot0) + int(dt / 0.22)  # ~220ms slots; binary search corrects
    lo = int(slot0)
    hi = est + 20000
    for _ in range(32):
        mid = (lo + hi) // 2
        bt = rpc_call("getBlockTime", [mid])
        if bt is None:
            nxt = rpc_call("getBlockTime", [mid + 1])
            if nxt is None:
                lo = mid + 2
                continue
            mid, bt = mid + 1, nxt
        if bt < target_ts:
            lo = mid + 1
        else:
            hi = mid
        if hi - lo < 2:
            break
    return lo


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
        elif isinstance(group, list):
            out.extend(x for x in group if isinstance(x, str))
    return out


def _find_address_sig_after(address: str, target_ts: int, start_slot: int, max_slots: int = 80) -> dict | None:
    """Return one signature for address in a block with blockTime > target_ts, to use as `before`."""
    for slot in range(int(start_slot), int(start_slot) + int(max_slots)):
        try:
            blk = rpc_call("getBlock", [slot, {
                "encoding": "json",
                "transactionDetails": "accounts",
                "rewards": False,
                "maxSupportedTransactionVersion": 1,
            }], timeout=30, retries=3)
        except Exception as e:
            print(f"  getBlock {slot} {e}", flush=True)
            continue
        if not blk:
            continue
        bt = blk.get("blockTime")
        if bt is None or bt <= target_ts:
            continue
        for tx in (blk.get("transactions") or []):
            if address not in _tx_pubkeys(tx):
                continue
            sigs = (tx.get("transaction") or {}).get("signatures") or []
            if sigs:
                print(f"  window cursor slot={slot} bt={bt} sig={sigs[0][:12]}…", flush=True)
                return {"signature": sigs[0], "slot": slot, "blockTime": bt}
    return None


def fetch_sigs_in_window(address: str, t0: int, t_end: int, dest: Path, slot0: int | None = None) -> list[dict]:
    """Newest-first pagination until blockTime < t0; keep signatures with t0 <= bt <= t_end.

    For a still-hot book, jump to a `before` cursor just after t_end so we don't walk
    hours of later signatures.
    """
    meta_path = Path(str(dest) + ".meta.json")
    if dest.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if (meta.get("address") == address and int(meta.get("t0")) == int(t0)
                    and int(meta.get("t_end")) == int(t_end) and meta.get("reached_past_t0")):
                sigs = json.loads(dest.read_text())
                if sigs:
                    print(f"sig window cache {len(sigs)} for {address[:8]}…", flush=True)
                    return sigs
        except Exception:
            pass
    kept: list[dict] = []
    before = None
    pages = 0
    reached = False
    jumped = False
    before_start = None
    if slot0:
        try:
            print(f"  locating sig cursor just after t_end={t_end} (t+={t_end-t0})", flush=True)
            hint = _slot_for_time(int(t_end) + 1, int(slot0), int(t0))
            print(f"  hint slot={hint}", flush=True)
            cur = _find_address_sig_after(address, int(t_end), hint, max_slots=80)
            if cur and cur.get("signature"):
                before = cur["signature"]
                before_start = before
                jumped = True
                print(f"  jumping pagination to slot={cur.get('slot')} bt={cur.get('blockTime')}", flush=True)
            else:
                print("  no post-window cursor; falling back to newest-first (may be slow)", flush=True)
        except Exception as e:
            print(f"  window jump failed ({e}); falling back to newest-first", flush=True)
    while True:
        params: dict[str, Any] = {"limit": 1000}
        if before:
            params["before"] = before
        page = rpc_call("getSignaturesForAddress", [address, params]) or []
        if not page:
            reached = True
            break
        pages += 1
        stop = False
        for s in page:
            bt = s.get("blockTime")
            if bt is None:
                continue
            if bt > t_end:
                continue
            if bt < t0:
                stop = True
                reached = True
                break
            kept.append(s)
        before = page[-1]["signature"]
        last_bt = page[-1].get("blockTime")
        dt = (last_bt - t0) if last_bt else None
        if pages % 5 == 0 or stop or len(page) < 1000:
            print(f"  sig window page {pages} kept={len(kept)} last_t+={dt}", flush=True)
        if stop or len(page) < 1000:
            if len(page) < 1000:
                reached = True
            break
    kept.sort(key=lambda s: (s.get("blockTime") or 0, s.get("slot") or 0))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(kept))
    meta_path.write_text(json.dumps({
        "address": address, "t0": t0, "t_end": t_end,
        "reached_past_t0": reached, "n": len(kept), "pages": pages,
        "jumped": jumped, "before_start": before_start,
    }))
    return kept


def _load_cached_txs(cdir: Path) -> dict[str, Any]:
    out = {}
    idx = cdir / "fetched_sigs.txt"
    parts = sorted((cdir / "txparts").glob("part_*.json"))
    for part in parts:
        try:
            blob = json.loads(part.read_text())
        except Exception:
            continue
        if isinstance(blob, dict):
            out.update(blob)
    return out


def fetch_transactions(mint: str, signatures: list[str], concurrency: int = 6, batch_size: int = 40) -> dict[str, Any]:
    cdir = cache_dir(mint)
    have = _load_cached_txs(cdir)
    todo = [s for s in signatures if s not in have]
    print(f"tx cache {len(have)} need {len(todo)}", flush=True)
    if not todo:
        return have

    def one_batch(batch: list[str], part_id: int) -> tuple[int, dict]:
        calls = [("getTransaction", [sig, RAW_TX_PARAMS]) for sig in batch]
        try:
            results = rpc_batch(calls, timeout=45)
        except Exception as e:
            print(f"  part {part_id} batch error: {e}", flush=True)
            return part_id, {}
        got = {}
        errn = 0
        for sig, res in zip(batch, results):
            if res and not (isinstance(res, dict) and res.get("_error")):
                got[sig] = res
            else:
                errn += 1
        if errn:
            print(f"  part {part_id} errors {errn}/{len(batch)} got {len(got)}", flush=True)
        part = cdir / "txparts" / f"part_{part_id}.json"
        # merge if exists
        if part.exists():
            try:
                prev = json.loads(part.read_text())
                prev.update(got)
                got = prev
            except Exception:
                pass
        part.write_text(json.dumps(got, separators=(",", ":")))
        return part_id, got

    # assign part ids
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    # start part ids after existing
    existing = list((cdir / "txparts").glob("part_*.json"))
    start_id = 0
    if existing:
        ids = []
        for f in existing:
            try:
                ids.append(int(f.stem.split("_")[1]))
            except Exception:
                pass
        if ids:
            start_id = max(ids) + 1

    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(one_batch, b, start_id + i): i for i, b in enumerate(batches)}
        for fut in as_completed(futs):
            pid, got = fut.result()
            have.update(got)
            done += 1
            if done % 5 == 0 or done == len(batches):
                print(f"  batches {done}/{len(batches)} txs_cached={len(have)}", flush=True)
    # write index
    (cdir / "fetched_sigs.txt").write_text("\n".join(have.keys()) + "\n")
    return have


def fetch_sol_usd_klines(start_ts: int, end_ts: int, dest: Path) -> list[list]:
    """Binance SOLUSDT 1m klines. timestamps in unix seconds."""
    if dest.exists():
        try:
            data = json.loads(dest.read_text())
            if data:
                return data
        except Exception:
            pass
    start_ms = start_ts * 1000 - 60_000
    end_ms = end_ts * 1000 + 60_000
    out = []
    cur = start_ms
    while cur < end_ms:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol=SOLUSDT&interval=1m&startTime={cur}&endTime={end_ms}&limit=1000"
        )
        chunk = None
        for attempt in range(4):
            try:
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                chunk = r.json()
                break
            except Exception:
                time.sleep(0.6 * (2 ** attempt))
        if not chunk:
            # fallback: CoinGecko range, then a constant if we already have any dest data
            try:
                cg = requests.get(
                    "https://api.coingecko.com/api/v3/coins/solana/market_chart/range"
                    f"?vs_currency=usd&from={start_ts-60}&to={end_ts+60}",
                    timeout=15,
                )
                if cg.status_code == 200:
                    prices = (cg.json() or {}).get("prices") or []
                    # synthesize kline-like rows [ms, o,h,l,c,...]
                    chunk = [[int(ms), str(px), str(px), str(px), str(px)] for ms, px in prices]
            except Exception:
                chunk = None
        if not chunk:
            if dest.exists():
                return json.loads(dest.read_text())
            # last resort constant around launch (filled by caller if needed)
            raise RuntimeError("could not fetch SOL USD klines")
        if not chunk:
            break
        out.extend(chunk)
        cur = chunk[-1][0] + 60_000
        if len(chunk) < 1000:
            break
    dest.write_text(json.dumps(out))
    return out


def sol_price_fn(klines: list[list]):
    """Return a function unix_s -> close USD price (forward/back filled)."""
    pts = []
    for row in klines:
        try:
            pts.append((int(row[0]) // 1000, float(row[4])))
        except Exception:
            continue
    pts.sort()

    def price(ts: int) -> float:
        if not pts:
            return float("nan")
        # last close at or before ts
        lo, hi = 0, len(pts) - 1
        if ts <= pts[0][0]:
            return pts[0][1]
        if ts >= pts[-1][0]:
            return pts[-1][1]
        while lo <= hi:
            mid = (lo + hi) // 2
            if pts[mid][0] <= ts:
                lo = mid + 1
            else:
                hi = mid - 1
        return pts[hi][1]

    return price


def first_signatures_window(sigs: list[dict], window_sec: int = WINDOW_SEC) -> tuple[list[dict], int, int]:
    if not sigs:
        return [], 0, 0
    t0 = sigs[0]["blockTime"]
    slot0 = sigs[0]["slot"]
    sub = [s for s in sigs if s.get("blockTime") is not None and s["blockTime"] - t0 <= window_sec]
    return sub, t0, slot0


PUMPSWAP_ENHANCED_CAP = 12000  # ~5 min budget at modest RPC rates


def bound_pumpswap_ok_sigs(ok_sigs: list[dict], cap: int = PUMPSWAP_ENHANCED_CAP) -> tuple[list[str], dict]:
    """If a 30m PumpSwap book is too hot, take a time-stratified sample so later minutes aren't empty."""
    ok = [s for s in ok_sigs if s.get("signature") and not s.get("err")]
    meta = {"ok_total": len(ok), "fetched": len(ok), "sampled": False, "method": "all", "cap": cap}
    if len(ok) <= cap:
        return [s["signature"] for s in ok], meta
    ok = sorted(ok, key=lambda s: (s.get("blockTime") or 0, s.get("slot") or 0))
    from collections import defaultdict
    buckets: dict[int, list] = defaultdict(list)
    tmin = ok[0].get("blockTime") or 0
    for s in ok:
        b = max(0, int(((s.get("blockTime") or tmin) - tmin) // 60))
        buckets[b].append(s)
    n_bins = max(1, len(buckets))
    per = max(1, cap // n_bins)
    chosen: list[dict] = []
    seen: set[str] = set()
    for b in sorted(buckets):
        xs = buckets[b]
        if len(xs) <= per:
            pick = xs
        else:
            step = len(xs) / per
            pick = [xs[min(len(xs)-1, int(i * step))] for i in range(per)]
        for s in pick:
            sig = s["signature"]
            if sig not in seen:
                seen.add(sig)
                chosen.append(s)
    if len(chosen) > cap:
        step = len(chosen) / cap
        chosen = [chosen[min(len(chosen)-1, int(i * step))] for i in range(cap)]
    meta.update({"fetched": len(chosen), "sampled": True, "method": "time_stratified_1min"})
    print(f"pumpswap bounded: {meta['ok_total']} ok -> sample {meta['fetched']} ({meta['method']})", flush=True)
    return [s["signature"] for s in chosen], meta


def fetch_enhanced_transactions(mint: str, signatures: list[str], batch_size: int = 50, pause: float = 0.15, concurrency: int = 1) -> dict:
    """Fetch Helius enhanced parsed txs. Cache under cache/<mint>/enhanced/part_*.json"""
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from lib.rpc import helius_request
    cdir = cache_dir(mint)
    ed = cdir / "enhanced"
    ed.mkdir(parents=True, exist_ok=True)
    have: dict = {}
    for part in sorted(ed.glob("part_*.json")):
        try:
            blob = json.loads(part.read_text())
            if isinstance(blob, dict):
                have.update(blob)
        except Exception:
            continue
    todo = [s for s in signatures if s not in have]
    print(f"enhanced cache {len(have)} need {len(todo)}", flush=True)
    if not todo:
        return have
    existing = list(ed.glob("part_*.json"))
    start_id = 0
    if existing:
        ids = []
        for f in existing:
            try:
                ids.append(int(f.stem.split("_")[1]))
            except Exception:
                pass
        if ids:
            start_id = max(ids) + 1
    from lib.rpc import helius_request
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    workers = max(1, int(concurrency or 1))

    def one_batch(batch: list[str], part_id: int) -> tuple[int, dict, str | None]:
        got = {}
        last_err = None
        try:
            r = helius_request("enhanced", {"transactions": batch}, timeout=60, retries=8)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise RuntimeError("enhanced response not a list")
            for item in data:
                if isinstance(item, dict) and item.get("signature"):
                    got[item["signature"]] = item
            last_err = None
        except Exception as e:
            last_err = str(e)
        if got:
            (ed / f"part_{part_id:04d}.json").write_text(json.dumps(got, separators=(",", ":")))
        return part_id, got, last_err

    done = 0
    if workers <= 1:
        for n, batch in enumerate(batches):
            part_id = start_id + n
            _, got, last_err = one_batch(batch, part_id)
            if not got:
                print(f"  enhanced part {part_id} failed: {last_err}", flush=True)
            else:
                have.update(got)
            done += len(batch)
            if (n + 1) % 4 == 0 or n + 1 == len(batches):
                print(f"  enhanced {done}/{len(todo)} cached={len(have)}", flush=True)
            _time.sleep(pause)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(one_batch, batch, start_id + n) for n, batch in enumerate(batches)]
            for fut in as_completed(futs):
                part_id, got, last_err = fut.result()
                if not got:
                    print(f"  enhanced part {part_id} failed: {last_err}", flush=True)
                else:
                    have.update(got)
                done += 1
                if done % 8 == 0 or done == len(batches):
                    print(f"  enhanced {min(done*batch_size,len(todo))}/{len(todo)} cached={len(have)}", flush=True)
    return have
