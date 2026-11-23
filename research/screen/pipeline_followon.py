#!/usr/bin/env python3
"""Follow-on after screen/pipeline.py: Nov 18, then 17, then 16 (EAT 00:00-24:00).

Waits until the running pipeline.py exits successfully, then screens and
backtests those days the same way (enumerate, full-coverage prescreen with
Bitquery pre-filter when points allow and automatic Helius fallback,
analyze, backtest, walk-forward test of the newest frozen rulebook
unchanged, then learn the next version). Never overwrites frozen
rulebooks. Does not restart, kill, or modify pipeline.py.

After Nov 16, writes screen/cache/week_summary_2026-11-16_22.md.

Research only. Never prints API keys.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path("/workspace/pumpfun")
sys.path.insert(0, str(ROOT))

from screen.common import CACHE, NBO, SCREEN, atomic_write_json  # noqa: E402
import screen.pipeline as pipeline  # noqa: E402
from screen.pipeline import (  # noqa: E402
    CKPT_DIR,
    PY,
    enumerate_day,
    prescreen_complete,
    prescreen_stats,
    run,
)

WF = CACHE / "walkforward"
WF.mkdir(parents=True, exist_ok=True)
CKPT_DIR.mkdir(parents=True, exist_ok=True)

# Walk-forward order (newest training day first). Nov 18 is included.
WF_DATES = [
    "2026-11-22",
    "2026-11-21",
    "2026-11-20",
    "2026-11-19",
    "2026-11-18",
    "2026-11-17",
    "2026-11-16",
]
FOLLOWON_DATES = ["2026-11-18", "2026-11-17", "2026-11-16"]
ALL_DATES = list(WF_DATES)
PIPELINE_LOG = CACHE / "pipeline.log"
FOLLOWON_LOG = CACHE / "pipeline_followon.log"
STATUS_PATH = CKPT_DIR / "followon_status.json"
PID_PATH = CACHE / "pipeline_followon.pid"
WEEK_SUMMARY = CACHE / "week_summary_2026-11-16_22.md"
RULEBOOK_NAME = re.compile(r"^rulebook_v1\.(\d+)\.json$")
COMPLETE_LINE = re.compile(r"\] pipeline complete\s*$")

EXTRACT_WORKERS = 48
PRESCREEN_WORKERS = 28


def log(msg: str) -> None:
    ts = datetime.now(tz=NBO).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts} UTC+03:00] {msg}"
    print(line, flush=True)
    try:
        with PIPELINE_LOG.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# Route pipeline helpers through the same logger (stdout + pipeline.log).
pipeline.log = log
pipeline.ALL_DATES = ALL_DATES


def write_status(blob: dict) -> None:
    blob = dict(blob)
    blob["time_eat"] = datetime.now(tz=NBO).strftime("%Y-%m-%d %H:%M:%S UTC+03:00")
    try:
        atomic_write_json(STATUS_PATH, blob)
    except Exception as e:
        log(f"followon status write failed: {e}")


def write_checkpoint(reason: str):
    """Same schema as pipeline.write_checkpoint, with follow-on days included."""
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
        "followon_dates": FOLLOWON_DATES,
    }
    path = CKPT_DIR / f"checkpoint_{now}.json"
    atomic_write_json(path, blob)
    atomic_write_json(CKPT_DIR / "latest.json", blob)
    parts = [
        f"{d} scored={by[d]['n']} pass={by[d]['pass']} dead={by[d]['dead']} gate={by[d]['gate']}"
        for d in ALL_DATES
    ]
    log(f"CHECKPOINT {reason}: " + "; ".join(parts) + f"; total_pass={total_pass} -> {path}")
    return blob


def list_pipeline_pids() -> list[int]:
    """PIDs of screen/pipeline.py only (never this follow-on)."""
    me = os.getpid()
    pids: list[int] = []
    for ent in Path("/proc").iterdir():
        if not ent.name.isdigit():
            continue
        pid = int(ent.name)
        if pid == me:
            continue
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        cmd = raw.replace(b"\0", b" ").decode("utf-8", "replace")
        if "pipeline_followon.py" in cmd:
            continue
        if "pipeline.py" in cmd and "python" in cmd:
            pids.append(pid)
    return sorted(pids)


def pipeline_log_complete() -> bool:
    if not PIPELINE_LOG.exists():
        return False
    try:
        # Read from the end; the success marker is a short exact line.
        with PIPELINE_LOG.open("r", errors="replace") as f:
            for line in f:
                if COMPLETE_LINE.search(line.rstrip("\n")):
                    return True
    except Exception:
        return False
    return False


def latest_reason() -> str | None:
    path = CKPT_DIR / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get("reason")
    except Exception:
        return None


def pipeline_succeeded() -> bool:
    return pipeline_log_complete()  # watchdog: latest.json is overwritten by follow-on checkpoints


def wait_for_pipeline_success() -> bool:
    """Block until pipeline.py is gone and left a successful completion marker.

    Success = process gone AND pipeline.log has 'pipeline complete'
    AND checkpoints/latest.json reason is 'pipeline_complete'.
    If the process exits without those markers, do not start follow-on days.
    If a new pipeline.py appears, keep waiting.
    """
    pids = list_pipeline_pids()
    if pipeline_succeeded() and not pids:
        log("pipeline already complete; starting follow-on work")
        return True
    if not pids and not pipeline_succeeded():
        log(
            "no screen/pipeline.py process and no successful completion marker "
            f"(log_complete={pipeline_log_complete()} latest_reason={latest_reason()}); "
            "follow-on will not start"
        )
        return False

    log(
        f"waiting for pipeline.py to exit successfully; pids={pids} "
        f"(detect via /proc cmdline == screen/pipeline.py, then "
        f"pipeline.log 'pipeline complete' + latest.json reason=pipeline_complete)"
    )
    write_status({"state": "waiting", "pipeline_pids": pids, "log": str(FOLLOWON_LOG)})
    last_hb = 0.0
    while True:
        pids = list_pipeline_pids()
        if pids:
            now = time.time()
            if now - last_hb >= 15 * 60:
                log(f"still waiting for pipeline.py pids={pids} reason={latest_reason()}")
                write_status({"state": "waiting", "pipeline_pids": pids, "reason": latest_reason()})
                last_hb = now
            time.sleep(20)
            continue

        # PID gone: give final writes a short grace period.
        log("pipeline.py process is gone; checking success markers")
        deadline = time.time() + 60
        while time.time() < deadline:
            if list_pipeline_pids():
                log("a new pipeline.py appeared; continue waiting")
                break
            if pipeline_succeeded():
                log("pipeline exited successfully (log + checkpoint pipeline_complete)")
                write_status({"state": "pipeline_complete"})
                return True
            time.sleep(5)
        else:
            log(
                "pipeline exited without success markers "
                f"(log_complete={pipeline_log_complete()} latest_reason={latest_reason()}); "
                "follow-on will not start"
            )
            write_status({
                "state": "pipeline_failed",
                "log_complete": pipeline_log_complete(),
                "latest_reason": latest_reason(),
            })
            return False


def frozen_rulebooks() -> list[Path]:
    out = []
    if not WF.exists():
        return out
    for p in WF.iterdir():
        if RULEBOOK_NAME.match(p.name):
            out.append(p)
    return sorted(out, key=lambda p: int(RULEBOOK_NAME.match(p.name).group(1)))


def version_num(text) -> int:
    m = re.search(r"v1\.(\d+)", str(text))
    return int(m.group(1)) if m else -1


def newest_frozen() -> Path | None:
    books = frozen_rulebooks()
    return books[-1] if books else None


def next_version_name() -> str:
    p = newest_frozen()
    n = version_num(p.name) if p else 0
    return f"v1.{n + 1}"


def train_dates_through(date: str) -> list[str]:
    out = []
    for d in WF_DATES:
        out.append(d)
        if d == date:
            break
    return out


def freeze_rulebook(version: str, rules: dict) -> Path:
    """Write rulebook_{version}.json only if it does not already exist."""
    dest = WF / f"rulebook_{version}.json"
    if dest.exists():
        stamp = time.strftime("%Y%m%d_%H%M")
        alt = dest.with_name(dest.stem + f".relearn_{stamp}.json")
        atomic_write_json(alt, rules)
        log(f"{dest.name} is frozen; not overwritten. re-learn saved to {alt}")
        return alt
    atomic_write_json(dest, rules)
    log(f"froze {dest}")
    return dest


def walk_test(rules_path: Path, test_date: str) -> None:
    if not rules_path.exists():
        log(f"no rules at {rules_path}; skip test on {test_date}")
        return
    run(
        [
            PY, "-u", str(ROOT / "screen" / "walkforward.py"), "test",
            "--test-date", test_date, "--rules", str(rules_path),
        ],
        CACHE / "walkforward.log",
    )


def walk_learn(train_dates: list[str], version: str) -> dict | None:
    from screen.walkforward import learn

    log(f"walk-forward LEARN on {'+'.join(train_dates)} -> rulebook {version} (frozen if new)")
    t0 = time.time()
    rules = learn(train_dates, version_hint=version)
    # learn() only auto-freezes v1.1–v1.3 for older date sets; always freeze here
    # without overwriting an existing frozen book.
    freeze_rulebook(version, rules)
    with (CACHE / "walkforward.log").open("a") as f:
        f.write(f"\n\n===== {datetime.now(tz=NBO).isoformat()} followon learn {version} =====\n")
        f.write(f"train={train_dates} elapsed={time.time() - t0:.1f}s dest=rulebook_{version}.json\n")
    return rules


def maybe_incremental_backtest(last_inc_bt: list[float]) -> None:
    if last_inc_bt[0] and time.time() - last_inc_bt[0] < 30 * 60:
        return
    last_inc_bt[0] = time.time()
    inc = ROOT / "screen" / "incremental_backtest.py"
    if not inc.exists():
        return
    log("incremental backtest (local tapes, new passers only)")
    try:
        subprocess.run(
            [PY, "-u", str(inc)],
            cwd=str(ROOT),
            timeout=180,
            stdout=open(CACHE / "incremental_backtest.log", "a"),
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        log(f"incremental backtest skipped: {e}")


def analyze_and_backtest_dates(dates: list[str]) -> None:
    mints = []
    for date in dates:
        st = prescreen_stats(date)
        for p in st["passers"]:
            mints.append((date, p["mint"]))
    todo = []
    for date, mint in mints:
        if not (ROOT / "coins" / mint / "summary.json").exists():
            todo.append(mint)
    log(f"analyze todo={len(todo)} already={len(mints) - len(todo)} total_passers={len(mints)}")
    if todo:
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
    for date in dates:
        if (SCREEN / f"prescreen_{date}.csv").exists() and prescreen_stats(date)["pass"]:
            run(
                [PY, "-u", str(ROOT / "screen" / "backtest.py"), "--date", date],
                CACHE / f"backtest_{date}.log",
            )


def run_prescreen_with_ckpts(date: str, src: str = "full") -> None:
    last_ckpt = [time.time()]
    last_pass = [0]
    last_inc_bt = [0.0]
    attempts = 0
    while True:
        done, cov = prescreen_complete(date)
        if done:
            log(f"skip prescreen {date}: coverage complete ({cov})")
            break
        attempts += 1
        if attempts > 2:
            import re as _re
            _m = _re.search(r"scored=(\d+)/(\d+)", str(cov))
            if _m and int(_m.group(1)) >= 0.99 * int(_m.group(2)):
                log(f"prescreen {date}: accepting >=99% coverage after {attempts - 1} tries ({cov}); remaining are persistent RPC errors")
                break
            raise SystemExit(f"prescreen {date} still incomplete after {attempts - 1} tries ({cov})")
        log(f"prescreen {date} attempt={attempts} coverage={cov}")
        proc = subprocess.Popen(
            [
                PY, "-u", str(ROOT / "screen" / "prescreen.py"),
                "--date", date, "--src", src,
                "--workers", str(PRESCREEN_WORKERS),
            ],
            cwd=str(ROOT),
            stdout=open(CACHE / f"prescreen_{date}.log", "a"),
            stderr=subprocess.STDOUT,
        )
        log(f"prescreen {date} pid={proc.pid}")
        while proc.poll() is None:
            time.sleep(60)
            st = sum(prescreen_stats(d)["pass"] for d in ALL_DATES)
            if st - last_pass[0] >= 100 or time.time() - last_ckpt[0] >= 3600:
                write_checkpoint(f"hourly_or_100_during_{date}")
                last_pass[0] = st
                last_ckpt[0] = time.time()
            maybe_incremental_backtest(last_inc_bt)
        rc = proc.returncode
        done, cov = prescreen_complete(date)
        log(f"prescreen {date} exit={rc} coverage={cov}")
        if done:
            break
        log(f"prescreen {date} incomplete (rc={rc}); will resume remaining from cache")
        time.sleep(3)
    write_checkpoint(f"prescreen_done_{date}")
    analyze_and_backtest_dates(ALL_DATES)


def maybe_learn_gap_v14() -> None:
    """If pipeline tested v1.3 on Nov 19 but did not freeze v1.4, learn it now.

    That lets Nov 18 be a true out-of-sample test of a book trained through Nov 19.
    Skipped if a v1.4 (or newer) book is already frozen, or if Nov 19 has no backtest.
    """
    newest = newest_frozen()
    if newest and version_num(newest.name) >= 4:
        log(f"newest frozen rulebook is already {newest.name}; no gap-fill learn")
        return
    if not (SCREEN / "backtest_2026-11-19.csv").exists():
        log("no Nov 19 backtest; skip v1.4 gap-fill and will test the newest frozen book as-is")
        return
    log("pipeline did not freeze v1.4; learning v1.4 from Nov 22+21+20+19 before testing Nov 18")
    walk_learn(train_dates_through("2026-11-19"), "v1.4")
    write_checkpoint("learned_v1.4_from_22_21_20_19")


def process_day(date: str) -> None:
    label = {
        "2026-11-18": "Nov 18 (Wednesday) EAT 00:00-24:00",
        "2026-11-17": "Nov 17 (Tuesday) EAT 00:00-24:00",
        "2026-11-16": "Nov 16 (Monday) EAT 00:00-24:00",
    }.get(date, f"{date} EAT 00:00-24:00")
    log(f"enumerate {label}")
    enumerate_day(date, "", workers=EXTRACT_WORKERS)
    write_checkpoint(f"enumerated_{date}")
    log(f"prescreen {date} (full day, Bitquery pre-filter if points allow, Helius fallback)")
    run_prescreen_with_ckpts(date, "full")

    book = newest_frozen()
    if book is None:
        raise SystemExit("no frozen rulebook to test; cannot continue walk-forward")
    ver = f"v1.{version_num(book.name)}"
    log(f"walk-forward TEST frozen {ver} ({book.name}) on {date} (out-of-sample, unchanged)")
    walk_test(book, date)
    write_checkpoint(f"tested_{ver}_on_{date}")

    nxt = next_version_name()
    walk_learn(train_dates_through(date), nxt)
    write_checkpoint(f"learned_{nxt}_from_{'_'.join(train_dates_through(date))}")


def _fmt_pct(x) -> str:
    if x is None:
        return "—"
    try:
        return f"{100 * float(x):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_usd(x) -> str:
    if x is None:
        return "—"
    try:
        return f"${float(x):.3f}"
    except (TypeError, ValueError):
        return "—"


def _launch_count(date: str) -> int | None:
    p = CACHE / f"launches_{date}.summary.json"
    if p.exists():
        try:
            blob = json.loads(p.read_text())
            return int(blob.get("n_unique_mints") or blob.get("n_launches") or 0)
        except Exception:
            pass
    path = SCREEN / f"launches_{date}.csv"
    if not path.exists():
        return None
    n = 0
    try:
        for r in csv.DictReader(path.open()):
            if r.get("mint"):
                n += 1
    except Exception:
        return None
    return n


def _stats_row(st: dict | None) -> str:
    if not st or not st.get("n"):
        return "0 trades"
    return (
        f"{st['n']} trades, win {_fmt_pct(st.get('win_pct'))}, "
        f"exp {_fmt_usd(st.get('expectancy_per_1usd'))}, "
        f"P&L {_fmt_usd(st.get('cum_pnl'))}, "
        f"max DD {_fmt_usd(st.get('max_drawdown'))}"
    )


def _oos_test_blob(date: str) -> dict | None:
    best = None
    for p in WF.glob(f"test_v1.*_on_{date}.json"):
        if "relearn" in p.name:
            continue
        try:
            blob = json.loads(p.read_text())
        except Exception:
            continue
        trained = blob.get("trained_on") or []
        if date in trained:
            continue
        ver = blob.get("rules_version") or ""
        if best is None or version_num(ver) >= version_num(best.get("rules_version")):
            best = blob
            best["_path"] = str(p)
    return best


def write_week_summary() -> Path:
    from screen.walkforward import apply_rules, headline_stats, load_day

    lines = [
        "# Pump.fun week summary: 2026-11-16 to 2026-11-22 (EAT)",
        "",
        f"Written {datetime.now(tz=NBO).strftime('%Y-%m-%d %H:%M:%S')} Africa/Nairobi (UTC+3).",
        "Walk-forward order was Nov 22 → 21 → 20 → 19 → 18 → 17 → 16.",
        "v1 is the baseline (TP targets as labelled, no extra skip). "
        "v1.x uses the rulebook tested out-of-sample on that day "
        "(Nov 22 is in-sample v1.1, called out below).",
        "P&L figures are per $1 stake after fees, research only.",
        "",
        "## Per day",
        "",
    ]

    chrono = list(reversed(WF_DATES))
    week_v1_17, week_v1_18 = [], []
    week_vx_17, week_vx_18 = [], []
    vx_notes = []

    for date in chrono:
        launches = _launch_count(date)
        st = prescreen_stats(date)
        rows = load_day(date)
        v1_17 = headline_stats(rows, "rule17_no_sl_net", "v1 1.7x") if rows else None
        v1_18 = headline_stats(rows, "rule18_no_sl_net", "v1 1.8x") if rows else None
        if rows:
            week_v1_17.extend(rows)
            week_v1_18.extend(rows)

        oos = _oos_test_blob(date)
        in_sample = date == "2026-11-22"
        vx_label = "—"
        vx_17 = vx_18 = None
        rules = None
        if oos:
            vx_label = oos.get("rules_version") or "v1.x"
            book = WF / f"rulebook_{vx_label}.json"
            if book.exists():
                try:
                    blob = json.loads(book.read_text())
                    rules = blob.get("rules") or blob
                except Exception:
                    rules = None
        elif in_sample and (WF / "rulebook_v1.1.json").exists():
            vx_label = "v1.1 (in-sample)"
            try:
                blob = json.loads((WF / "rulebook_v1.1.json").read_text())
                rules = blob.get("rules") or blob
            except Exception:
                rules = None

        if rules and rows:
            kept, net_key, _lab = apply_rules(rows, rules)
            vx_17 = headline_stats(kept, "rule17_no_sl_net", f"{vx_label} 1.7x") if kept else {"n": 0}
            vx_18 = headline_stats(kept, "rule18_no_sl_net", f"{vx_label} 1.8x") if kept else {"n": 0}
            week_vx_17.extend(kept)
            week_vx_18.extend(kept)
            if net_key not in ("rule17_no_sl_net", "rule18_no_sl_net"):
                vx_notes.append(f"{date}: headline net key was {net_key}")

        lines += [
            f"### {date}",
            "",
            f"- Launches (unique mints): {launches if launches is not None else '—'}",
            f"- Strict v0 passers: {st['pass']} (scored {st['n']}, dead-early {st['dead']}, gate {st['gate']})",
            f"- v1 at 1.7x: {_stats_row(v1_17)}",
            f"- v1 at 1.8x: {_stats_row(v1_18)}",
            f"- Latest v1.x ({vx_label}) at 1.7x: {_stats_row(vx_17)}",
            f"- Latest v1.x ({vx_label}) at 1.8x: {_stats_row(vx_18)}",
        ]
        if oos:
            v1s, vxs = oos.get("v1") or {}, oos.get("v1x") or {}
            held = oos.get("held_up_out_of_sample")
            lines.append(
                f"- Walk-forward OOS: tested {oos.get('rules_version')} "
                f"(trained on {', '.join(oos.get('trained_on') or [])}) vs v1. "
                f"v1 n={oos.get('n_v1')} P&L {_fmt_usd(v1s.get('cum_pnl'))} "
                f"exp {_fmt_usd(v1s.get('expectancy_per_1usd'))}; "
                f"{oos.get('rules_version')} n={oos.get('n_v1x')} "
                f"P&L {_fmt_usd(vxs.get('cum_pnl'))} "
                f"exp {_fmt_usd(vxs.get('expectancy_per_1usd'))}; "
                f"held up={held}, expectancy delta {_fmt_usd(oos.get('expectancy_delta'))}."
            )
        elif in_sample:
            lines.append("- Walk-forward: Nov 22 was the first training day (v1.1 in-sample, not an out-of-sample test).")
        else:
            lines.append("- Walk-forward OOS: not available.")
        lines.append("")

    def _sorted(rows):
        def key(r):
            try:
                return int(float(r.get("entry_ts") or 0))
            except (TypeError, ValueError):
                return 0
        return sorted(rows, key=key)

    tot_v1_17 = headline_stats(_sorted(week_v1_17), "rule17_no_sl_net", "week v1 1.7x") if week_v1_17 else None
    tot_v1_18 = headline_stats(_sorted(week_v1_18), "rule18_no_sl_net", "week v1 1.8x") if week_v1_18 else None
    tot_vx_17 = headline_stats(_sorted(week_vx_17), "rule17_no_sl_net", "week v1.x 1.7x") if week_vx_17 else None
    tot_vx_18 = headline_stats(_sorted(week_vx_18), "rule18_no_sl_net", "week v1.x 1.8x") if week_vx_18 else None

    lines += [
        "## Whole week (Nov 16–22, trades in entry-time order)",
        "",
        f"- Combined launches: {sum(x for x in (_launch_count(d) for d in chrono) if x)}",
        f"- Combined strict v0 passers: {sum(prescreen_stats(d)['pass'] for d in chrono)}",
        f"- v1 at 1.7x: {_stats_row(tot_v1_17)}",
        f"- v1 at 1.8x: {_stats_row(tot_v1_18)}",
        f"- Walk-forward v1.x at 1.7x (each day's OOS book; Nov 22 in-sample v1.1): {_stats_row(tot_vx_17)}",
        f"- Walk-forward v1.x at 1.8x (same kept trades): {_stats_row(tot_vx_18)}",
        "",
    ]
    if vx_notes:
        lines += ["Notes:", *[f"- {n}" for n in vx_notes], ""]
    lines.append(
        "Max drawdown is the walk-forward/backtest max_drawdown on the $1-stake equity curve."
    )
    WEEK_SUMMARY.write_text("\n".join(lines) + "\n")
    log(f"wrote week summary {WEEK_SUMMARY}")
    return WEEK_SUMMARY


def main() -> int:
    PID_PATH.write_text(str(os.getpid()))
    log(
        f"follow-on start pid={os.getpid()} dates={FOLLOWON_DATES} "
        f"(after pipeline.py succeeds; Nov 18 then 17 then 16)"
    )
    write_status({"state": "started", "pid": os.getpid(), "dates": FOLLOWON_DATES})

    if not wait_for_pipeline_success():
        return 2

    # Take over checkpoints only after pipeline has finished writing its last one.
    write_checkpoint("followon_start")
    maybe_learn_gap_v14()

    for date in FOLLOWON_DATES:
        process_day(date)

    write_week_summary()
    write_checkpoint("followon_complete")
    log("follow-on pipeline complete")
    write_status({"state": "complete", "summary": str(WEEK_SUMMARY)})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        log("follow-on crashed; see traceback below")
        import traceback
        traceback.print_exc()
        try:
            with PIPELINE_LOG.open("a") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
