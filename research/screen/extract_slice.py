#!/usr/bin/env python3
"""Extract create txs for the time-stratified slice first, then write a slice launches CSV."""
from __future__ import annotations
import csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screen.common import CACHE, DATE_EAT, SCREEN, T_START, eat_str, load_json
from screen.enumerate import (
    FIELDS, SIGS_PATH, extract_ok_sigs, load_extracted, write_csv, CSV_PATH,
)

# 4x15 minutes spread across the EAT day
SLICE_HOURS = (0, 6, 12, 18)
SLICE_MINUTES = 15


def windows():
    out = []
    for h in SLICE_HOURS:
        a = T_START + h * 3600
        b = a + SLICE_MINUTES * 60
        out.append((f"{h:02d}:00-{h:02d}:{SLICE_MINUTES:02d}", a, b))
    return out


def in_slice(ts: int) -> str | None:
    for name, a, b in windows():
        if a <= int(ts) < b:
            return name
    return None


def main():
    sigs = load_json(SIGS_PATH, [])
    ok = [s for s in sigs if not s.get("err")]
    slice_ok = []
    for name, a, b in windows():
        recs = [s for s in ok if a <= s["blockTime"] < b]
        print(f"window {name} {eat_str(a)} -> {eat_str(b)} n={len(recs)}", flush=True)
        slice_ok.extend(recs)
    have = load_extracted()
    have = extract_ok_sigs(slice_ok, have)
    rows = []
    for s in slice_ok:
        rec = have.get(s["signature"])
        if not rec:
            continue
        rec = dict(rec)
        rec["slice_window"] = in_slice(rec.get("launch_ts") or 0)
        rows.append(rec)
    # write slice csv (adds slice_window)
    slice_csv = SCREEN / f"launches_{DATE_EAT}_slice.csv"
    fields = FIELDS + ["slice_window"]
    with slice_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            rec = dict(r)
            rec["launch_time_eat"] = eat_str(rec.get("launch_ts"))
            rec["is_token_2022"] = int(bool(rec.get("is_token_2022")))
            rec["extract_ok"] = int(bool(rec.get("extract_ok")))
            w.writerow({k: rec.get(k, "") for k in fields})
    ok_mints = [r for r in rows if r.get("extract_ok") and r.get("mint")]
    print(f"wrote {slice_csv} slice_ok={len(slice_ok)} extracted_ok={len(ok_mints)} unique={len({r['mint'] for r in ok_mints})}", flush=True)


if __name__ == "__main__":
    main()
