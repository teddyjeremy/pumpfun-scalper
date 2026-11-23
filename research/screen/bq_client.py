"""Minimal Bitquery v2 client with points logging. Never logs the token."""
from __future__ import annotations
import json, os, time, threading
from datetime import datetime
from pathlib import Path
import requests

URL = "https://streaming.bitquery.io/graphql"
METRICS_URL = "https://graphql.bitquery.io"
LOG = Path(__file__).resolve().parent / "cache" / "bitquery" / "points_log.jsonl"
LOG.parent.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
_last_query_end = 0.0
_min_gap = 5.0  # seconds between query end and next start; raised after 429s
MIN_GAP = 5.0
MAX_GAP = 15.0


def _hdr():
    return {"Authorization": "Bearer " + os.environ["BITQUERY_API_KEY"], "Content-Type": "application/json"}


def points_for(qid: str, tries: int = 4) -> float | None:
    for i in range(tries):
        try:
            r = requests.post(METRICS_URL, headers=_hdr(), json={"query": '{ utilities { metrics(queryId: "%s") { points } } }' % qid}, timeout=20)
            m = (((r.json() or {}).get("data") or {}).get("utilities") or {}).get("metrics")
            if m and m.get("points") is not None:
                return float(m["points"])
        except Exception:
            pass
        if i + 1 < tries:
            time.sleep(3 + 3 * i)
    return None


class QuotaExceeded(RuntimeError):
    pass


def query(q: str, variables: dict | None = None, label: str = "", timeout: int = 300, retries: int = 8) -> dict:
    global _last_query_end, _min_gap
    last = None
    for a in range(retries):
        wait = _min_gap - (time.time() - _last_query_end)
        if wait > 0:
            time.sleep(wait)
        t0 = time.time()
        try:
            r = requests.post(URL, headers=_hdr(), json={"query": q, "variables": variables or {}}, timeout=timeout)
        except Exception as e:
            last = f"net:{type(e).__name__}"
            _last_query_end = time.time()
            time.sleep(5 * (a + 1)); continue
        qid = r.headers.get("x-bitquery-gql-query-id", "")
        pts = None  # filled later by reconcile() (saves metrics calls / rate limit)
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "label": label, "status": r.status_code,
               "qid": qid, "points": pts, "secs": round(time.time() - t0, 1), "bytes": len(r.content)}
        try:
            body = r.json()
        except Exception:
            body = {"errors": [{"message": r.text[:300]}]}
        if body.get("errors"):
            rec["error"] = str(body["errors"])[:300]
        with _lock, LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        _last_query_end = time.time()
        if r.status_code == 200 and body.get("data") and not body.get("errors"):
            return body["data"]
        last = rec.get("error") or f"http {r.status_code}"
        if r.status_code == 402 or "points limit" in str(last) or "usage quota reached" in str(last):
            raise QuotaExceeded(f"bitquery quota reached: {last}")
        if "rate limit" in str(last) or r.status_code == 429:
            # Back off a full minute-plus, then keep a wider gap so the next burst stays under the cap.
            _min_gap = min(MAX_GAP, max(_min_gap, MIN_GAP) + 3.0)
            time.sleep(70 + 20 * a)
            continue
        if r.status_code in (400, 401, 403) or "no API tables" in str(last) or "Cannot query" in str(last):
            break
        time.sleep(5 * (a + 1))
    raise RuntimeError(f"bitquery failed: {last}")


def reconcile(since_ts: str | None = None, max_lookups: int = 80) -> None:
    """Backfill points for logged queries whose metrics were not ready yet."""
    if not LOG.exists():
        return
    recs = []
    for l in LOG.open():
        if not l.strip():
            continue
        try:
            recs.append(json.loads(l))
        except Exception:
            continue
    changed = False
    looked = 0
    for r in recs:
        if r.get("points") is None and r.get("qid") and not r.get("error"):
            if since_ts and (r.get("ts") or "") < since_ts:
                continue
            if looked >= max_lookups:
                break
            p = points_for(r["qid"], tries=1)
            looked += 1
            if p is not None:
                r["points"] = p; changed = True
            time.sleep(1.2)
    if changed:
        with _lock:
            tmp = LOG.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(r) + "\n" for r in recs)); tmp.rename(LOG)


def points_used() -> float:
    tot = 0.0
    if LOG.exists():
        for ln in LOG.open():
            try:
                tot += float(json.loads(ln).get("points") or 0)
            except Exception:
                pass
    return tot
