#!/usr/bin/env python3
"""Bitquery candidate finder for the strict v0 screen (research only, no trading).

Bitquery's Solana archive is minute-granular, so this is a LOOSE superset filter.
Helius strict v0 remains the final decider. Points are logged in
cache/bitquery/points_log.jsonl; every query result is cached under
cache/bitquery/<date>/ so reruns cost 0 points.

stage A (one query per 3h block): pump + pump_amm trades in [b, b+3h+6m] grouped by mint,
        count >= A_MIN (presence/"has data" list). Keep mints launched in the block.
stage B (chunks of mints sorted by launch, span <= 30 min): minute bars of hi/lo price,
        trade count and SOL volume over [first launch, last launch + 6m]. Only mints
        with stage-A count >= B_MIN are queried.
Per coin, over minute bars overlapping (launch, launch+300s]:
  trades_5m, vol_5m (USD via SOL/USD klines), max drop of a later bar's low from the
  running high (launch bar's own hi/lo not compared), max mcap, min low mcap.
Candidate rule = DEFAULTS (loose). Coins absent from stage A = "no Bitquery data"
(fewer than A_MIN trades seen) -> the pipeline still sends them to Helius if the
Helius gate kept them.
"""
from __future__ import annotations
import argparse, bisect, csv, json, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bq_client as bq

SCREEN = Path(__file__).resolve().parent
CACHE = SCREEN / "cache" / "bitquery"
NBO = timezone(timedelta(hours=3))
PROTOS = ["pump", "pump_amm"]
A_MIN = 100  # Nov 22/21 built with 50; raised to save points (passers min seen 404)
B_MIN = 300  # Nov 22/21 built with 250; min passer 5-min trades on Bitquery was 402
BLOCK_H = 3
CHUNK = 150
SPAN = 30 * 60

DEFAULTS = dict(MIN_TRADES=250, MIN_VOL=8000.0, MIN_DROP=25.0, MIN_MCAP_HI=5000.0, MAX_MCAP_LO=60000.0)

Q_A = '''query($a:DateTime,$b:DateTime,$p:[String!],$min:String){ Solana(dataset: archive) {
 DEXTradeByTokens(where:{Block:{Time:{since:$a,till:$b}}, Trade:{Dex:{ProtocolName:{in:$p}}}},
  limit:{count:50000}) {
  Trade { Currency { MintAddress } }
  n: count(selectWhere:{ge:$min})
  Block { first: Time(minimum: Block_Time) }
 } } }'''

Q_B = '''query($a:DateTime,$b:DateTime,$p:[String!],$m:[String!]){ Solana(dataset: archive) {
 DEXTradeByTokens(where:{Block:{Time:{since:$a,till:$b}}, Trade:{Dex:{ProtocolName:{in:$p}}, Currency:{MintAddress:{in:$m}}}},
  limit:{count:50000}) {
  Trade { Currency { MintAddress } hi: Price(maximum: Trade_Price) lo: Price(minimum: Trade_Price) }
  Block { T: Time(interval:{in:minutes, count:1}) }
  n: count sv: sum(of: Trade_Side_Amount)
 } } }'''


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def day_start(date: str) -> int:
    y, m, d = map(int, date.split("-"))
    return int(datetime(y, m, d, tzinfo=NBO).timestamp())


def load_launches(date: str) -> dict:
    p = SCREEN / f"launches_{date}.csv"
    out = {}
    if p.exists():
        for r in csv.DictReader(p.open()):
            try:
                out[r["mint"]] = int(float(r["launch_ts"]))
            except Exception:
                pass
    return out


def _fetch_sol_usd_klines(date: str, dest: Path) -> list:
    """Binance SOLUSDT 1m klines (CoinGecko fallback). Same format as lib.fetch, no RPC import."""
    import requests
    t0 = day_start(date) - 120
    t1 = day_start(date) + 86400 + 120
    start_ms = t0 * 1000 - 60_000
    end_ms = t1 * 1000 + 60_000
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
                if isinstance(chunk, list) and chunk:
                    break
                chunk = None
            except Exception:
                chunk = None
                time.sleep(0.6 * (2 ** attempt))
        if not chunk:
            try:
                cg = requests.get(
                    "https://api.coingecko.com/api/v3/coins/solana/market_chart/range"
                    f"?vs_currency=usd&from={t0-60}&to={t1+60}",
                    timeout=20,
                )
                if cg.status_code == 200:
                    prices = (cg.json() or {}).get("prices") or []
                    chunk = [[int(ms), str(px), str(px), str(px), str(px)] for ms, px in prices]
            except Exception:
                chunk = None
        if not chunk:
            raise RuntimeError(f"could not fetch SOL/USD klines for {date}")
        out.extend(chunk)
        cur = int(chunk[-1][0]) + 60_000
        if len(chunk) < 1000:
            break
        time.sleep(0.3)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    tmp.rename(dest)
    return out


def load_sol(date: str):
    p = SCREEN / "cache" / f"sol_usd_klines_{date}.json"
    kl = []
    if p.exists():
        try:
            kl = json.loads(p.read_text()) or []
        except Exception:
            kl = []
    if not kl:
        kl = _fetch_sol_usd_klines(date, p)
    px = {int(k[0]) // 60000: float(k[4]) for k in kl}
    keys = sorted(px)
    def f(ts):
        m = int(ts) // 60
        if m in px:
            return px[m]
        if not keys:
            raise RuntimeError(f"no SOL/USD klines for {date}")
        i = min(bisect.bisect_left(keys, m), len(keys) - 1)
        return px[keys[i]]
    return f


def cached(path: Path, fn):
    if path.exists():
        return json.loads(path.read_text())
    data = fn()
    tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(data)); tmp.rename(path)
    return data


def _launches_from_stage_a(date: str, log=print) -> dict:
    """When launches_<date>.csv is not ready yet, use each mint's earliest stage-A first-trade.

    This does not change evaluate() / DEFAULTS. It only prevents later 3h blocks from
    overwriting a mint's launch_ts (and 5-minute window) when the official launch list
    is still missing.
    """
    d = CACHE / date
    d.mkdir(parents=True, exist_ok=True)
    launches = {}
    nblk = 24 // BLOCK_H
    for blk in range(nblk):
        b0 = day_start(date) + blk * BLOCK_H * 3600
        b1 = b0 + BLOCK_H * 3600
        rows = cached(d / f"A_{blk}.json", lambda b0=b0, b1=b1, blk=blk: bq.query(
            Q_A, {"a": iso(b0), "b": iso(b1 + 6 * 60), "p": PROTOS, "min": str(A_MIN)},
            label=f"A {date} blk{blk}")["Solana"]["DEXTradeByTokens"])
        if len(rows) >= 50000:
            raise RuntimeError("stage A hit row limit")
        for r in rows:
            m = r["Trade"]["Currency"]["MintAddress"]
            try:
                lt = parse_iso(r["Block"]["first"])
            except Exception:
                continue
            if m not in launches or lt < launches[m]:
                launches[m] = lt
    log(f"[bq] {date}: no launches CSV; synthetic launches={len(launches)} from stage-A first trade")
    return launches


def stage_a(date: str, blk: int, launches: dict) -> dict:
    """mint -> (launch_ts, window_count) for mints launched in the block."""
    d = CACHE / date; d.mkdir(parents=True, exist_ok=True)
    b0 = day_start(date) + blk * BLOCK_H * 3600
    b1 = b0 + BLOCK_H * 3600
    rows = cached(d / f"A_{blk}.json", lambda: bq.query(
        Q_A, {"a": iso(b0), "b": iso(b1 + 6 * 60), "p": PROTOS, "min": str(A_MIN)},
        label=f"A {date} blk{blk}")["Solana"]["DEXTradeByTokens"])
    if len(rows) >= 50000:
        raise RuntimeError("stage A hit row limit")
    out = {}
    for r in rows:
        m = r["Trade"]["Currency"]["MintAddress"]
        lt = launches.get(m)
        if lt is None and not launches:
            lt = parse_iso(r["Block"]["first"])
        if lt is not None and b0 <= lt < b1:
            out[m] = (lt, int(r["n"]))
    return out


def stage_b(date: str, blk: int, amap: dict) -> list:
    d = CACHE / date
    done = d / f"B_{blk}.done"
    if done.exists():  # block finished earlier (chunking may differ): reuse every cached chunk
        rows = []
        for f in sorted(d.glob(f"B_{blk}_*.json")):
            rows += json.loads(f.read_text())
        return rows
    todo = sorted(((lt, m) for m, (lt, n) in amap.items() if n >= B_MIN))
    chunks, cur = [], []
    for lt, m in todo:
        if cur and (len(cur) >= CHUNK or lt - cur[0][0] > SPAN):
            chunks.append(cur); cur = []
        cur.append((lt, m))
    if cur:
        chunks.append(cur)
    rows = []
    for i, ch in enumerate(chunks):
        a = (ch[0][0] // 60) * 60
        b = ch[-1][0] + 6 * 60
        ms = [m for _, m in ch]
        rows += cached(d / f"B_{blk}_{i:03d}.json", lambda: bq.query(
            Q_B, {"a": iso(a), "b": iso(b), "p": PROTOS, "m": ms},
            label=f"B {date} blk{blk} c{i} n={len(ms)}")["Solana"]["DEXTradeByTokens"])
    done.write_text(json.dumps({"b_min": B_MIN, "chunks": len(chunks)}))
    return rows


def evaluate(amap: dict, rows: list, sol, th: dict) -> dict:
    bars = {}
    for r in rows:
        m = r["Trade"]["Currency"]["MintAddress"]
        bars.setdefault(m, []).append((parse_iso(r["Block"]["T"]), float(r["Trade"]["hi"] or 0), float(r["Trade"]["lo"] or 0),
                                      int(r["n"]), float(r["sv"] or 0)))
    out = {}
    for m, (lt, n_win) in amap.items():
        rec = {"launch_ts": lt, "a_count": n_win}
        if n_win < B_MIN:
            rec.update(cand=0, why="a_count<%d" % B_MIN); out[m] = rec; continue
        bs = sorted(b for b in bars.get(m, []) if b[0] + 60 > lt and b[0] <= lt + 300)
        if not bs:
            rec.update(cand=0, why="no_bars_5m"); out[m] = rec; continue
        px = sol(lt)
        trades = sum(b[3] for b in bs)
        vol = sum(b[4] for b in bs) * px
        run_hi = max_hi = max_drop = 0.0
        min_lo_mcap = None; drop_lo_mcap = None
        for k, (t, hi, lo, n, sv) in enumerate(bs):
            max_hi = max(max_hi, hi)
            lo_mcap = lo * 1e9 * px
            if lo > 0:
                min_lo_mcap = lo_mcap if min_lo_mcap is None else min(min_lo_mcap, lo_mcap)
            if k > 0:
                run_hi = max(run_hi, hi)  # same-bar hi counts (order unknown -> loose)
                if run_hi > 0 and lo > 0:
                    dd = (1 - lo / run_hi) * 100
                    if dd > max_drop:
                        max_drop, drop_lo_mcap = dd, lo_mcap
            else:
                run_hi = max(run_hi, hi)
        rec.update(trades_5m=trades, vol_5m=round(vol, 2), max_drop=round(max_drop, 2),
                   max_mcap=round(max_hi * 1e9 * px, 1), min_lo_mcap=round(min_lo_mcap or 0, 1),
                   drop_lo_mcap=round(drop_lo_mcap or 0, 1))
        if rec["max_mcap"] > 2e6 or (trades and vol / trades > 5000):
            # price/volume not SOL-denominated (custom quote) -> can't judge; let Helius decide
            rec["cand"] = 1; rec["why"] = "quote_anomaly"; out[m] = rec; continue
        fails = []
        if trades < th["MIN_TRADES"]: fails.append("trades")
        if vol < th["MIN_VOL"]: fails.append("vol")
        if max_drop < th["MIN_DROP"]: fails.append("drop")
        if rec["max_mcap"] < th["MIN_MCAP_HI"]: fails.append("mcap_hi")
        if rec["min_lo_mcap"] > th["MAX_MCAP_LO"]: fails.append("mcap_lo")
        rec["cand"] = int(not fails); rec["why"] = ",".join(fails)
        out[m] = rec
    return out


def _pid_alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0); return True
    except Exception:
        return False


def wait_for_builder(date: str, timeout: int = 3600, log=print) -> None:
    lk = CACHE / f"building_{date}.lock"
    t0 = time.time()
    while lk.exists():
        try:
            pid = int(lk.read_text().strip() or 0)
        except Exception:
            pid = 0
        if not pid or not _pid_alive(pid) or time.time() - t0 > timeout:
            return
        log(f"[bq] waiting for candidate builder pid={pid} for {date}")
        time.sleep(30)


def run_day(date: str, blocks=None, th=None, log=print) -> dict:
    import os
    wait_for_builder(date, log=log)
    fin = CACHE / f"candidates_{date}.json"
    if blocks is None and th is None and fin.exists():
        d = json.loads(fin.read_text())
        if sorted(d.get("blocks") or []) == list(range(24 // BLOCK_H)):
            return d
    lk = CACHE / f"building_{date}.lock"
    lk.write_text(str(os.getpid()))
    try:
        return _run_day(date, blocks, th, log)
    finally:
        try:
            if lk.read_text().strip() == str(os.getpid()):
                lk.unlink()
        except Exception:
            pass


def _run_day(date: str, blocks=None, th=None, log=print) -> dict:
    th = {**DEFAULTS, **(th or {})}
    launches = load_launches(date)
    if not launches:
        launches = _launches_from_stage_a(date, log=log)
    sol = load_sol(date)
    p0 = bq.points_used()
    allrec = {}
    nblk = 24 // BLOCK_H
    for blk in (blocks if blocks is not None else range(nblk)):
        t = time.time()
        amap = stage_a(date, blk, launches)
        rows = stage_b(date, blk, amap)
        ev = evaluate(amap, rows, sol, th)
        for m, rec in ev.items():
            allrec.setdefault(m, rec)
        time.sleep(2)
        log(f"[bq] {date} blk{blk} ({blk*BLOCK_H:02d}-{blk*BLOCK_H+BLOCK_H:02d}h EAT): seen={len(amap)} "
            f"queried={sum(1 for v in amap.values() if v[1] >= B_MIN)} cand={sum(r['cand'] for r in ev.values())} "
            f"{time.time()-t:.0f}s points_logged={bq.points_used():.1f}")
    bq.reconcile()
    out = {"date": date, "thresholds": th, "a_min": A_MIN, "b_min": B_MIN,
           "blocks": list(blocks) if blocks is not None else list(range(nblk)),
           "n_launches": len(launches), "n_seen": len(allrec),
           "candidates": sorted(m for m, r in allrec.items() if r.get("cand")),
           "records": allrec, "points_this_run": round(bq.points_used() - p0, 2),
           "points_total_logged": round(bq.points_used(), 2),
           "generated": datetime.now(tz=NBO).isoformat(timespec="seconds")}
    p = CACHE / f"candidates_{date}.json"
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(out)); tmp.rename(p)
    log(f"[bq] {date}: launches={len(launches)} seen={len(allrec)} candidates={len(out['candidates'])} "
        f"points_this_run~{out['points_this_run']} total_logged={out['points_total_logged']}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--blocks", default="")
    a = ap.parse_args()
    run_day(a.date, [int(x) for x in a.blocks.split(",")] if a.blocks else None)
