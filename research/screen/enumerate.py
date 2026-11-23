#!/usr/bin/env python3
"""Enumerate Pump.fun CREATE txs for an EAT calendar day via the mint-authority PDA.

getSignaturesForAddress on the program would also return every trade (millions).
The mint-authority PDA only appears on Create / CreateV2, so one day is tens of
thousands of signatures, not millions.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from screen.common import (
    CACHE, DATE_EAT, MINT_AUTHORITY, SCREEN, T_END, T_START,
    atomic_write_json, day_bounds, eat_str, extract_create, hour_window,
    load_json, rpc_call_lim,
)

SIGS_PATH = CACHE / f"create_sigs_{DATE_EAT}.json"
SIGS_META = CACHE / f"create_sigs_{DATE_EAT}.meta.json"
CSV_PATH = SCREEN / f"launches_{DATE_EAT}.csv"
EXTRACT_DIR = CACHE / "creates_extracted"
FIELDS = [
    "mint", "name", "symbol", "uri", "launch_ts", "launch_time_eat", "launch_slot",
    "create_sig", "creator", "curve", "token_program", "is_token_2022",
    "extra_mints", "err", "extract_ok", "extract_note", "slice_window",
]
TX_PARAMS = {
    "encoding": "json",
    "maxSupportedTransactionVersion": 1,
    "commitment": "confirmed",
}
# 16 concurrent singles ~100 tx/s without 429s; 32+ collapses under rate limits.
FETCH_WORKERS = 16


def configure(date_eat: str, until_eat: str | None = None):
    """Point path constants at an EAT calendar day. Call from main() before work.

    until_eat: optional exclusive end as 'YYYY-MM-DD HH:MM' (Africa/Nairobi).
    Used for a partial day such as Nov 22 00:00-21:20.
    """
    global DATE_EAT, T_START, T_END, SIGS_PATH, SIGS_META, CSV_PATH, EXTRACT_DIR
    from datetime import datetime
    from screen.common import NBO
    DATE_EAT = date_eat
    T_START, T_END = day_bounds(date_eat)
    if until_eat:
        s = until_eat.strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                T_END = int(datetime.strptime(s, fmt).replace(tzinfo=NBO).timestamp())
                break
            except ValueError:
                continue
        else:
            raise SystemExit(f"bad --until-eat {until_eat!r}; expected 'YYYY-MM-DD HH:MM'")
        if T_END <= T_START:
            raise SystemExit("until-eat must be after the start of date")
    SIGS_PATH = CACHE / f"create_sigs_{DATE_EAT}.json"
    SIGS_META = CACHE / f"create_sigs_{DATE_EAT}.meta.json"
    CSV_PATH = SCREEN / f"launches_{DATE_EAT}.csv"
    EXTRACT_DIR = CACHE / f"creates_extracted_{DATE_EAT}"
    legacy = CACHE / "creates_extracted"
    if date_eat == "2026-11-21" and legacy.exists() and not EXTRACT_DIR.exists():
        EXTRACT_DIR = legacy
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_create_sigs(t_start: int = T_START, t_end: int = T_END) -> list[dict]:
    meta = load_json(SIGS_META, {})
    if SIGS_PATH.exists() and meta.get("reached_past_t0") and int(meta.get("t_start", 0)) == t_start and int(meta.get("t_end", 0)) == t_end:
        sigs = load_json(SIGS_PATH, [])
        if sigs:
            print(f"cached create sigs {len(sigs)}", flush=True)
            return sigs

    print(f"paging mint-authority sigs for {eat_str(t_start)} -> {eat_str(t_end)}", flush=True)
    kept = []
    n_err = 0
    n_after = 0
    before = None
    pages = 0
    reached = False
    t0 = time.time()
    while True:
        params = {"limit": 1000}
        if before:
            params["before"] = before
        page = rpc_call_lim("getSignaturesForAddress", [MINT_AUTHORITY, params]) or []
        pages += 1
        if not page:
            reached = True
            break
        stop = False
        for s in page:
            bt = s.get("blockTime")
            if bt is None:
                continue
            if bt >= t_end:
                n_after += 1
                continue
            if bt < t_start:
                stop = True
                reached = True
                break
            kept.append({
                "signature": s["signature"],
                "slot": s.get("slot"),
                "blockTime": bt,
                "err": s.get("err"),
            })
            if s.get("err"):
                n_err += 1
        before = page[-1]["signature"]
        last_bt = page[-1].get("blockTime")
        if pages % 5 == 0 or stop or len(page) < 1000:
            print(
                f"  page {pages} kept={len(kept)} err_in_window={n_err} "
                f"after_window_skipped={n_after} last_bt={eat_str(last_bt) if last_bt else None} "
                f"{time.time()-t0:.1f}s",
                flush=True,
            )
        if stop or len(page) < 1000:
            if len(page) < 1000:
                reached = True
            break
    kept.sort(key=lambda s: (s.get("blockTime") or 0, s.get("slot") or 0))
    atomic_write_json(SIGS_PATH, kept)
    atomic_write_json(SIGS_META, {
        "address": MINT_AUTHORITY,
        "t_start": t_start,
        "t_end": t_end,
        "reached_past_t0": reached,
        "n": len(kept),
        "n_err": n_err,
        "n_ok": sum(1 for s in kept if not s.get("err")),
        "pages": pages,
        "elapsed_s": round(time.time() - t0, 2),
        "n_after_skipped": n_after,
    })
    print(f"create sigs done n={len(kept)} ok={len(kept)-n_err} err={n_err} pages={pages} {time.time()-t0:.1f}s", flush=True)
    return kept


def load_extracted() -> dict[str, dict]:
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    have = {}
    for part in sorted(EXTRACT_DIR.glob("part_*.json")):
        try:
            blob = json.loads(part.read_text())
            if isinstance(blob, dict):
                have.update(blob)
            elif isinstance(blob, list):
                for rec in blob:
                    if rec.get("create_sig"):
                        have[rec["create_sig"]] = rec
        except Exception:
            continue
    return have


def write_csv(rows: list[dict]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CSV_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            rec = dict(r)
            rec["launch_time_eat"] = eat_str(rec.get("launch_ts"))
            rec["is_token_2022"] = int(bool(rec.get("is_token_2022")))
            rec["extract_ok"] = int(bool(rec.get("extract_ok")))
            rec["slice_window"] = rec.get("slice_window") or hour_window(rec.get("launch_ts"))
            w.writerow({k: rec.get(k, "") for k in FIELDS})
    tmp.replace(CSV_PATH)


def extract_ok_sigs(ok_sigs: list[dict], have: dict[str, dict], workers: int = FETCH_WORKERS) -> dict[str, dict]:
    todo = [s for s in ok_sigs if s["signature"] not in have]
    if not todo:
        print(f"extract cache complete {len(have)}", flush=True)
        return have
    print(f"extracting {len(todo)} create txs (cached {len(have)}) workers={workers}", flush=True)
    t0 = time.time()
    got = 0
    fail = 0
    lock = threading.Lock()
    pending: dict[str, dict] = {}
    flush_every = 500
    part_id = 0
    existing = []
    for f in EXTRACT_DIR.glob("part_*.json"):
        try:
            existing.append(int(f.stem.split("_")[1]))
        except Exception:
            pass
    if existing:
        part_id = max(existing) + 1

    def flush_locked():
        nonlocal pending, part_id
        if not pending:
            return
        path = EXTRACT_DIR / f"part_{part_id:05d}.json"
        path.write_text(json.dumps(pending, separators=(",", ":")))
        part_id += 1
        pending = {}

    def one(s: dict):
        sig = s["signature"]
        try:
            tx = rpc_call_lim("getTransaction", [sig, TX_PARAMS], timeout=30, retries=7)
        except Exception:
            tx = None
        rec = extract_create(sig, tx, s.get("blockTime"), s.get("slot"), s.get("err"))
        return sig, rec, bool(tx)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, s) for s in todo]
        for fut in as_completed(futs):
            sig, rec, has_tx = fut.result()
            have[sig] = rec
            with lock:
                pending[sig] = rec
                got += 1
                if not rec.get("extract_ok"):
                    fail += 1
                if len(pending) >= flush_every:
                    flush_locked()
                if got % 1000 == 0 or got == len(todo):
                    elapsed = max(0.001, time.time() - t0)
                    print(
                        f"  extracted {got}/{len(todo)} fail={fail} "
                        f"{got/elapsed:.1f} tx/s elapsed={elapsed:.1f}s",
                        flush=True,
                    )
    with lock:
        flush_locked()
    return have


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-11-21", help="EAT calendar date YYYY-MM-DD")
    ap.add_argument("--until-eat", default="", help="optional exclusive end 'YYYY-MM-DD HH:MM' Africa/Nairobi")
    ap.add_argument("--workers", type=int, default=FETCH_WORKERS)
    args = ap.parse_args()
    configure(args.date, until_eat=args.until_eat or None)
    t_all = time.time()
    print(f"enumerate date={DATE_EAT} {eat_str(T_START)} -> {eat_str(T_END)} workers={args.workers}", flush=True)
    sigs = fetch_create_sigs(T_START, T_END)
    ok = [s for s in sigs if not s.get("err")]
    failed = [s for s in sigs if s.get("err")]
    print(f"window sigs={len(sigs)} ok={len(ok)} failed_tx={len(failed)}", flush=True)

    have = load_extracted()
    have = extract_ok_sigs(ok, have, workers=args.workers)

    rows = []
    no_mint = 0
    t2022 = 0
    for s in ok:
        rec = have.get(s["signature"]) or extract_create(s["signature"], None, s.get("blockTime"), s.get("slot"))
        if not rec.get("extract_ok"):
            no_mint += 1
        if rec.get("is_token_2022"):
            t2022 += 1
        rows.append(rec)
    for s in failed:
        rows.append({
            "create_sig": s["signature"],
            "launch_ts": s.get("blockTime"),
            "launch_slot": s.get("slot"),
            "err": json.dumps(s.get("err")),
            "extract_ok": False,
            "extract_note": "sig_err",
        })
    rows.sort(key=lambda r: (r.get("launch_ts") or 0, r.get("launch_slot") or 0))
    write_csv(rows)
    ok_mints = [r for r in rows if r.get("extract_ok") and r.get("mint")]
    uniq = sorted({r["mint"] for r in ok_mints})
    elapsed = time.time() - t_all
    # hourly histogram for the slice plan
    hours = {}
    for r in ok_mints:
        ts = int(r["launch_ts"])
        # EAT hour 0-23
        from datetime import datetime
        from screen.common import NBO
        h = datetime.fromtimestamp(ts, tz=NBO).hour
        hours[h] = hours.get(h, 0) + 1
    summary = {
        "date_eat": DATE_EAT,
        "t_start": T_START,
        "t_end": T_END,
        "n_create_sigs": len(sigs),
        "n_failed_txs": len(failed),
        "n_ok_sigs": len(ok),
        "n_extracted": len(have),
        "n_no_mint": no_mint,
        "n_launches": len(ok_mints),
        "n_unique_mints": len(uniq),
        "n_token_2022": t2022,
        "launches_per_eat_hour": {str(h): hours.get(h, 0) for h in range(24)},
        "elapsed_s": round(elapsed, 2),
        "csv": str(CSV_PATH),
        "method": "getSignaturesForAddress on mint-authority PDA TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM + getTransaction extract",
    }
    atomic_write_json(CACHE / f"launches_{DATE_EAT}.summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    print(f"wrote {CSV_PATH} launches={len(ok_mints)} unique={len(uniq)} in {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
