#!/usr/bin/env python3
"""Background pipeline: finish the Nov 21 slice, extract+prescreen the rest of
Nov 21 and Nov 22 EAT 00:00-21:20, analyze strict v0 passers, backtest, checkpoint.

Does not start over. Resumes caches. Research only.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screen.common import CACHE, NBO, SCREEN, atomic_write_json, eat_str

ROOT = Path("/workspace/pumpfun")
PY = sys.executable
LOG_DIR = CACHE
CKPT_DIR = CACHE / "checkpoints"
CKPT_DIR.mkdir(parents=True, exist_ok=True)

DATES = [
    # (date, until_eat or "", src for prescreen, label)
    # Walk-forward order: pre-screen/backtest 22, learn, test on 21, learn, then 20.
    ("2026-11-22", "2026-11-22 21:20", "full", "Nov 22 EAT 00:00-21:20"),
    ("2026-11-21", "", "full", "Nov 21 EAT 00:00-24:00"),
    ("2026-11-20", "", "full", "Nov 20 EAT 00:00-24:00"),
    ("2026-11-19", "", "full", "Nov 19 EAT 00:00-24:00"),
]
ALL_DATES = [d[0] for d in DATES]


def log(msg: str):
    ts = datetime.now(tz=NBO).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts} UTC+03:00] {msg}", flush=True)


def run(cmd: list[str], log_path: Path, cwd=ROOT) -> int:
    log(f"$ {' '.join(cmd)}  > {log_path}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(f"\n\n===== {datetime.now(tz=NBO).isoformat()} =====\n")
        f.write(" ".join(cmd) + "\n")
        f.flush()
        p = subprocess.run(cmd, cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT)
    log(f"exit {p.returncode} for {cmd[:4]}")
    return p.returncode


def wait_for_pids(pattern: str, timeout=None):
    """Wait until no matching python processes remain (except this one)."""
    t0 = time.time()
    while True:
        out = subprocess.check_output(["bash", "-lc", f"pgrep -af '{pattern}' || true"], text=True)
        lines = [ln for ln in out.splitlines() if ln.strip() and "pipeline.py" not in ln and "pgrep" not in ln]
        if not lines:
            return
        if timeout and time.time() - t0 > timeout:
            log(f"timeout waiting for {pattern}: still {lines}")
            return
        time.sleep(15)


def prescreen_stats(date: str) -> dict:
    p = SCREEN / f"prescreen_{date}.csv"
    if not p.exists():
        return {"date": date, "n": 0, "pass": 0, "dead": 0, "errors": 0, "gate": 0, "passers": []}
    by = {}
    for r in csv.DictReader(p.open()):
        if r.get("mint"):
            by[r["mint"]] = r
    rows = list(by.values())
    passers = [r for r in rows if int(r.get("pass_v0") or 0)]
    return {
        "date": date,
        "n": len(rows),
        "pass": len(passers),
        "dead": sum(1 for r in rows if (r.get("fail_reasons") or "").startswith("dead_by_sig_count") and not r.get("error")),
        "errors": sum(1 for r in rows if r.get("error")),
        "gate": sum(1 for r in rows if int(r.get("n_ok_sigs_5m") or 0) >= 250),
        "passers": [{"mint": r["mint"], "symbol": r.get("symbol"), "window": r.get("slice_window"),
                     "entry_t": r.get("entry_t_plus"), "mcap": r.get("entry_mcap"),
                     "vol": r.get("entry_cum_vol"), "wallets": r.get("entry_cum_wallets")} for r in passers],
    }


def write_checkpoint(reason: str):
    by = {d: prescreen_stats(d) for d in ALL_DATES}
    total_pass = sum(s["pass"] for s in by.values())
    now = datetime.now(tz=NBO).strftime("%Y%m%d_%H%M")
    blob = {
        "time_eat": datetime.now(tz=NBO).strftime("%Y-%m-%d %H:%M:%S UTC+03:00"),
        "reason": reason,
        "days": {d: {k: v for k, v in s.items() if k != "passers"} for d, s in by.items()},
        "total_pass_v0": total_pass,
        "passers": {d: s["passers"] for d, s in by.items()},
        "validation_reference10": str(CACHE / "validation_reference10.md"),
        "walkforward_dir": str(CACHE / "walkforward"),
    }
    path = CKPT_DIR / f"checkpoint_{now}.json"
    atomic_write_json(path, blob)
    atomic_write_json(CKPT_DIR / "latest.json", blob)
    parts = [f"{d} scored={by[d]['n']} pass={by[d]['pass']} dead={by[d]['dead']} gate={by[d]['gate']}" for d in ALL_DATES]
    log(f"CHECKPOINT {reason}: " + "; ".join(parts) + f"; total_pass={total_pass} -> {path}")
    return blob


def expected_launch_mints(date: str) -> set[str]:
    """Unique extract-ok mints in the day's launches csv (full-day file)."""
    path = SCREEN / f"launches_{date}.csv"
    if not path.exists():
        return set()
    out = set()
    for r in csv.DictReader(path.open()):
        if r.get("extract_ok") in ("1", "True", "true") and r.get("mint"):
            out.add(r["mint"])
    return out


def prescreen_row_final(r: dict) -> bool:
    if not r or not r.get("mint"):
        return False
    err = str(r.get("error") or "")
    if err.startswith("error:") or err.startswith("in_progress"):
        return False
    return True


def prescreen_complete(date: str) -> tuple[bool, str]:
    """True when every extract-ok launch has a non-error prescreen row."""
    expected = expected_launch_mints(date)
    if not expected:
        return False, "no launches csv"
    path = SCREEN / f"prescreen_{date}.csv"
    if not path.exists():
        return False, f"0/{len(expected)} scored"
    done = set()
    errors = 0
    for r in csv.DictReader(path.open()):
        mint = r.get("mint")
        if not mint or mint not in expected:
            continue
        if prescreen_row_final(r):
            done.add(mint)
        else:
            errors += 1
    missing = len(expected) - len(done)
    ok = missing == 0
    return ok, f"scored={len(done)}/{len(expected)} missing={missing} retryable_errors={errors}"


def launches_csv_complete(date: str) -> bool:
    path = SCREEN / f"launches_{date}.csv"
    if not path.exists() or path.stat().st_size < 100:
        return False
    try:
        n = sum(1 for _ in path.open()) - 1
    except Exception:
        return False
    return n > 100


def enumerate_day(date: str, until_eat: str, workers: int = 24):
    if launches_csv_complete(date):
        log(f"skip enumerate {date}: launches csv already present")
        return 0
    cmd = [PY, "-u", str(ROOT / "screen" / "enumerate.py"), "--date", date, "--workers", str(workers)]
    if until_eat:
        cmd += ["--until-eat", until_eat]
    rc = run(cmd, LOG_DIR / f"enumerate_{date}.log")
    if rc != 0:
        log(f"enumerate {date} failed rc={rc}; will not skip ahead")
        raise SystemExit(f"enumerate {date} failed rc={rc}")
    return rc


def prescreen_day(date: str, src: str, workers: int = 28):
    cmd = [PY, "-u", str(ROOT / "screen" / "prescreen.py"), "--date", date, "--src", src, "--workers", str(workers)]
    return run(cmd, LOG_DIR / f"prescreen_{date}.log")


def analyze_and_backtest():
    # all dates' passers; run_analyze_passers currently reads one DATE. Call backtest per date after analyze.
    # Analyze any strict passer that lacks summary.json, across both CSVs.
    mints = []
    for date in ALL_DATES:
        st = prescreen_stats(date)
        for p in st["passers"]:
            mints.append((date, p["mint"]))
    todo = []
    for date, mint in mints:
        if not (ROOT / "coins" / mint / "summary.json").exists():
            todo.append(mint)
    log(f"analyze todo={len(todo)} already={len(mints)-len(todo)} total_passers={len(mints)}")
    if todo:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def one(mint):
            logp = CACHE / "analyze_logs" / f"{mint}.log"
            logp.parent.mkdir(parents=True, exist_ok=True)
            rc = run([PY, "-u", str(ROOT / "analyze.py"), mint], logp)
            return mint, rc
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = [ex.submit(one, m) for m in todo]
            for fut in as_completed(futs):
                mint, rc = fut.result()
                log(f"analyze {mint} rc={rc}")
    for date in ALL_DATES:
        if (SCREEN / f"prescreen_{date}.csv").exists() and prescreen_stats(date)["pass"]:
            run([PY, "-u", str(ROOT / "screen" / "backtest.py"), "--date", date], LOG_DIR / f"backtest_{date}.log")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-wait-slice", action="store_true")
    ap.add_argument("--extract-workers", type=int, default=48)
    ap.add_argument("--prescreen-workers", type=int, default=28)
    args = ap.parse_args()

    log("pipeline start")
    write_checkpoint("pipeline_start")

    if not args.skip_wait_slice:
        log("waiting for any in-flight screen/prescreen.py slice to finish")
        wait_for_pids("screen/prescreen.py")
        log("no prescreen process running")
        write_checkpoint("slice_done_or_absent")

    # Extract Nov 21 remainder (sigs already cached) and Nov 22 00:00-21:20 first,
    # so pre-screen can start with the reference-coin day. Nov 20 extract waits
    # until 22 and 21 are fully screened, backtested, and walk-forward has learned.
    log("enumerate Nov 21 (resume remaining create txs)")
    enumerate_day("2026-11-21", "", workers=args.extract_workers)
    write_checkpoint("enumerated_2026-11-21")

    log("enumerate Nov 22 EAT 00:00-21:20")
    enumerate_day("2026-11-22", "2026-11-22 21:20", workers=args.extract_workers)
    write_checkpoint("enumerated_2026-11-22")

    last_ckpt = time.time()
    last_pass = 0
    last_inc_bt = 0.0

    def maybe_incremental_backtest():
        # Local tapes only. Cheap, no Helius, safe while prescreen holds the keys.
        # Re-scores any new strict v0 passers and writes cache/incremental_backtest_*.md.
        nonlocal last_inc_bt
        if last_inc_bt and time.time() - last_inc_bt < 30 * 60:
            return
        last_inc_bt = time.time()
        inc = ROOT / "screen" / "incremental_backtest.py"
        if not inc.exists():
            return
        log("incremental backtest (local tapes, new passers only)")
        try:
            subprocess.run([PY, "-u", str(inc)], cwd=str(ROOT), timeout=180,
                           stdout=open(LOG_DIR / "incremental_backtest.log", "a"),
                           stderr=subprocess.STDOUT)
        except Exception as e:
            log(f"incremental backtest skipped: {e}")

    def maybe_ckpt(tag):
        # Checkpoints only. Never start analyze mid-prescreen: that
        # blocks the wait loop for hours, contends for RPC keys, and previously
        # let a killed incomplete prescreen be treated as done.
        # Incremental day backtests are allowed: they read cached tapes only.
        nonlocal last_ckpt, last_pass
        st = sum(prescreen_stats(d)["pass"] for d in ALL_DATES)
        if st - last_pass >= 100 or time.time() - last_ckpt >= 3600:
            write_checkpoint(tag)
            last_pass = st
            last_ckpt = time.time()

    def run_prescreen_with_ckpts(date, src):
        attempts = 0
        while True:
            done, cov = prescreen_complete(date)
            if done:
                log(f"skip prescreen {date}: coverage complete ({cov})")
                break
            attempts += 1
            if attempts > 8:
                raise SystemExit(f"prescreen {date} still incomplete after {attempts-1} tries ({cov})")
            log(f"prescreen {date} attempt={attempts} coverage={cov}")
            proc = subprocess.Popen(
                [PY, "-u", str(ROOT / "screen" / "prescreen.py"), "--date", date, "--src", src,
                 "--workers", str(args.prescreen_workers)],
                cwd=str(ROOT),
                stdout=open(LOG_DIR / f"prescreen_{date}.log", "a"),
                stderr=subprocess.STDOUT,
            )
            log(f"prescreen {date} pid={proc.pid}")
            while proc.poll() is None:
                time.sleep(60)
                maybe_ckpt(f"hourly_or_100_during_{date}")
                maybe_incremental_backtest()
            rc = proc.returncode
            done, cov = prescreen_complete(date)
            log(f"prescreen {date} exit={rc} coverage={cov}")
            if done:
                break
            log(f"prescreen {date} incomplete (rc={rc}); will resume remaining from cache")
            time.sleep(3)
        write_checkpoint(f"prescreen_done_{date}")
        analyze_and_backtest()

    def walk_learn(train_dates, version_hint=None):
        cmd = [PY, "-u", str(ROOT / "screen" / "walkforward.py"), "learn", "--train", *train_dates]
        run(cmd, LOG_DIR / "walkforward.log")

    def walk_test(rules_path: Path, test_date: str):
        if not rules_path.exists():
            log(f"no rules at {rules_path}; skip test on {test_date}")
            return
        run(
            [PY, "-u", str(ROOT / "screen" / "walkforward.py"), "test",
             "--test-date", test_date, "--rules", str(rules_path)],
            LOG_DIR / "walkforward.log",
        )

    # --- Day 1 (learn): Nov 22 ---
    log("prescreen Nov 22 (full launches, 00:00-21:20, PumpSwap coverage fix)")
    run_prescreen_with_ckpts("2026-11-22", "full")
    log("walk-forward LEARN on Nov 22 -> rulebook v1.1 (frozen)")
    walk_learn(["2026-11-22"])
    write_checkpoint("learned_v1.1_from_2026-11-22")

    # --- Day 2 (test v1.1, then learn v1.2): Nov 21 ---
    log("prescreen Nov 21 (full day, incl. re-score of early migrants)")
    run_prescreen_with_ckpts("2026-11-21", "full")
    log("walk-forward TEST frozen v1.1 on Nov 21 (out-of-sample vs v1)")
    walk_test(CACHE / "walkforward" / "rulebook_v1.1.json", "2026-11-21")
    write_checkpoint("tested_v1.1_on_2026-11-21")
    log("walk-forward LEARN on Nov 22+Nov 21 -> rulebook v1.2 (frozen; Nov 21 test already logged)")
    walk_learn(["2026-11-22", "2026-11-21"])
    write_checkpoint("learned_v1.2_from_22_and_21")

    # --- Day 3 (test v1.2): Nov 20 ---
    log("enumerate Nov 20 EAT 00:00-24:00")
    enumerate_day("2026-11-20", "", workers=args.extract_workers)
    write_checkpoint("enumerated_2026-11-20")
    log("prescreen Nov 20 (full day, PumpSwap coverage fix)")
    run_prescreen_with_ckpts("2026-11-20", "full")
    log("walk-forward TEST frozen v1.2 on Nov 20 (out-of-sample vs v1)")
    walk_test(CACHE / "walkforward" / "rulebook_v1.2.json", "2026-11-20")
    # Also report v1.1 (trained only on 22) on Nov 20, still out-of-sample.
    if (CACHE / "walkforward" / "rulebook_v1.1.json").exists():
        log("also TEST frozen v1.1 (trained only on Nov 22) on Nov 20")
        walk_test(CACHE / "walkforward" / "rulebook_v1.1.json", "2026-11-20")
    write_checkpoint("tested_v1.2_on_2026-11-20")

    # Learn through Nov 20 (after the Nov 20 test is already logged), freeze v1.3, test on Nov 19.
    log("walk-forward LEARN on Nov 22+21+20 -> rulebook v1.3 (frozen; Nov 20 test already logged)")
    walk_learn(["2026-11-22", "2026-11-21", "2026-11-20"])
    write_checkpoint("learned_v1.3_from_22_21_20")

    log("enumerate Nov 19 EAT 00:00-24:00")
    enumerate_day("2026-11-19", "", workers=args.extract_workers)
    write_checkpoint("enumerated_2026-11-19")
    log("prescreen Nov 19 (full day, PumpSwap coverage fix, 150-wallet estimate)")
    run_prescreen_with_ckpts("2026-11-19", "full")
    log("walk-forward TEST frozen v1.3 (learned through Nov 20) on Nov 19 (out-of-sample vs v1)")
    walk_test(CACHE / "walkforward" / "rulebook_v1.3.json", "2026-11-19")
    write_checkpoint("tested_v1.3_on_2026-11-19")

    analyze_and_backtest()
    write_checkpoint("pipeline_complete")
    log("pipeline complete")


if __name__ == "__main__":
    main()
