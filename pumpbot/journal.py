"""Structured JSONL logs, status file and daily summary under DATA_DIR."""
from __future__ import annotations

import json
import os
import statistics as st
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

EAT = timezone(timedelta(hours=3))


def eat(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), EAT).strftime("%Y-%m-%d %H:%M:%S EAT")


def eat_day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), EAT).strftime("%Y-%m-%d")


class Journal:
    def __init__(self, data_dir: str, mode: str):
        self.dir = data_dir
        self.mode = mode
        os.makedirs(data_dir, exist_ok=True)

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def write(self, kind: str, obj: dict):
        obj = {"ts": eat(), "mode": self.mode, **obj}
        with open(self.path(f"{kind}_{self.mode}.jsonl"), "a") as f:
            f.write(json.dumps(obj, default=str) + "\n")

    def log(self, msg: str):
        line = f"[{eat()}] {msg}"
        print(line, flush=True)
        with open(self.path("bot.log"), "a") as f:
            f.write(line + "\n")

    def status(self, obj: dict):
        tmp = self.path("status.json.tmp")
        with open(tmp, "w") as f:
            json.dump({"updated": eat(), "mode": self.mode, **obj}, f, indent=1, default=str)
        os.replace(tmp, self.path("status.json"))

    def read_trades(self, mode: str | None = None) -> list[dict]:
        return self.read_kind("trades", mode)

    def read_kind(self, kind: str, mode: str | None = None) -> list[dict]:
        p = self.path(f"{kind}_{mode or self.mode}.jsonl")
        if not os.path.exists(p):
            return []
        out = []
        with open(p) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out


def daily_summary(trades: list[dict], day: str) -> dict:
    T = [t for t in trades if str(t.get("ts", "")).startswith(day)]
    if not T:
        return {"day": day, "trades": 0}
    n = [t["net_per_1usd"] for t in T]
    pnl = [t.get("pnl_usd", t["net_per_1usd"]) for t in T]
    by_lane = {}
    for ln in sorted({t["lane"] for t in T}):
        x = [t["net_per_1usd"] for t in T if t["lane"] == ln]
        by_lane[ln] = {"n": len(x), "avg_per_1usd": round(st.mean(x), 4), "win": round(sum(v > 0 for v in x) / len(x), 2)}
    return {"day": day, "trades": len(T), "wins": sum(v > 0 for v in n), "win_rate": round(sum(v > 0 for v in n) / len(n), 2),
            "avg_per_1usd": round(st.mean(n), 4), "pnl_usd": round(sum(pnl), 4), "worst": round(min(n), 3), "best": round(max(n), 3),
            "exits": dict(Counter(t["exit"] for t in T)), "by_lane": by_lane,
            "quotes": dict(Counter(t.get("quote_mint", "SOL") for t in T))}
