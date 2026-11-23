#!/usr/bin/env python3
"""Background loop: incremental backtest every 30 minutes. No RPC. Stops after 2026-11-26 23:59 EAT
or once Nov 19 has a complete backtest and no prescreen/pipeline is running.
"""
from __future__ import annotations
import os, sys, time, subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screen.common import CACHE, NBO, SCREEN
from screen.incremental_backtest import main as inc_main, log, DATES

PID_FILE = CACHE / "watch_new_passers.pid"
INTERVAL = 30 * 60
DEADLINE = datetime(2026, 11, 26, 23, 59, tzinfo=NBO)


def still_needed() -> bool:
    now = datetime.now(tz=NBO)
    if now > DEADLINE:
        return False
    # If later days aren't even enumerated, keep watching while pipeline/prescreen live.
    import os as _os
    live = False
    try:
        out = subprocess.check_output(["pgrep", "-af", "screen/prescreen.py|screen/pipeline.py"], text=True)
        live = any("prescreen.py" in l or "pipeline.py" in l for l in out.splitlines()
                   if "pgrep" not in l and "watch_new_passers" not in l)
    except subprocess.CalledProcessError:
        live = False
    nov19_bt = (SCREEN / "backtest_2026-11-19.csv").exists()
    if nov19_bt and not live:
        return False
    return True


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    log(f"watcher start pid={os.getpid()} interval=30m deadline={DEADLINE.isoformat()}")
    try:
        inc_main()  # run once immediately (usually no_new if we just backtested)
        while still_needed():
            time.sleep(INTERVAL)
            if not still_needed():
                break
            inc_main()
    finally:
        try:
            if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
        except Exception:
            pass
        log("watcher stop")


if __name__ == "__main__":
    raise SystemExit(main())
