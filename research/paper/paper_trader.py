#!/usr/bin/env python3
"""LIVE PAPER TRADER (research only) for the Pump.fun dip-scalp rulebook v1.1.

NO real trades, NO wallets, NO signing. Measures ENTRY / EXIT execution metrics.

Feed
  * Primary: public Solana mainnet WebSocket (wss://api.mainnet-beta.solana.com),
    logsSubscribe(mentions=Pump program, commitment=processed) + slotSubscribe.
    Free, zero Helius/Alchemy/QuickNode credits. Decodes Anchor CreateEvent/TradeEvent
    straight from "Program data:" logs (post-trade curve reserves, user, block timestamp).
  * Latency probe (light): Helius WS (HELIUS_API_KEY_3) logsSubscribe on the bonding curve of
    "hot" coins only (max N concurrent, daily MB cap) to compare a paid feed's latency.
  * SOL/USD: Binance ticker poll (no RPC).

Rules: strict v0 + v1.1 (top-1 seller share of drop sell volume > 0.5 => skip), using the
screen's own entry_setup_metrics / exit_path (imported read-only, RPC pool stubbed).
"""
from __future__ import annotations

import asyncio
import base64
import csv
import json
import math
import random
import os
import signal
import statistics
import struct
import sys
import time
import traceback
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules as R  # noqa: E402  (stubs lib.rpc before importing screen modules)
import scalp_shadow as SS  # noqa: E402
import quote_events as QE  # noqa: E402  (quote-aware TradeEvent decode + quote USD prices; paper only)
import band_tapes as BT  # noqa: E402  (v9: full tapes of $5-10k band coins to t+600; no extra RPC)

PAPER = Path(__file__).resolve().parent
LOGS = PAPER / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
STATUS = PAPER / "status.json"
NBO = timezone(timedelta(hours=3))

PUBLIC_WS = os.environ.get("PAPER_PUBLIC_WS", "wss://api.mainnet-beta.solana.com")
HELIUS_KEY_ENV = os.environ.get("PAPER_HELIUS_KEY_ENV", "HELIUS_API_KEY_5")  # probe + fallback WS (never the pipeline's KEY/_2/_3)
HELIUS_MAX_SUBS = int(os.environ.get("PAPER_HELIUS_MAX_SUBS", "4"))
HELIUS_DAILY_MB = float(os.environ.get("PAPER_HELIUS_DAILY_MB", "1000"))   # per-coin subscription bytes (probe + cheap mode)/day
HELIUS_SUBS_HOURLY_MB = float(os.environ.get("PAPER_HELIUS_SUBS_HOURLY_MB", "60"))  # spread over the day
CHEAP_MAX_SUBS = int(os.environ.get("PAPER_CHEAP_MAX_SUBS", "12"))   # cheap mode: per-curve logsSubscribe slots
CHEAP_MIN_TRADES = int(os.environ.get("PAPER_CHEAP_MIN_TRADES", "80"))  # only coins approaching the rule floors
CHEAP_MIN_VOL = float(os.environ.get("PAPER_CHEAP_MIN_VOL", "3000"))
CHEAP_T_MIN, CHEAP_T_MAX = 10, 310          # decision window t+30..300 with a little lead / tail
HELIUS_FB_DAILY_MB = float(os.environ.get("PAPER_HELIUS_FB_DAILY_MB", "200"))  # firehose: ONLY for public-feed outages (silence); ~4 min/day
HELIUS_FB_HOURLY_MB = float(os.environ.get("PAPER_HELIUS_FB_HOURLY_MB", "100"))
WS_CREDITS_PER_MB = 20.0
LAG_ON_S = float(os.environ.get("PAPER_LAG_ON_S", "8"))       # public median lag above this ...
LAG_ON_FOR_S = 20.0                                             # ... continuously for this long -> Helius WS on
LAG_OFF_S = 3.0                                                 # public median lag below this ...
LAG_OFF_FOR_S = 60.0                                            # ... continuously for this long -> back to public
LAG_WIN_S = 10.0                                                # trailing window for the median
PUBLIC_WS_B = os.environ.get("PAPER_PUBLIC_WS_B", "wss://api.mainnet-beta.solana.com")
WATCHDOG_S = 20
LATE_S = 5.0  # a trade seen > 5s behind chain block time is tagged late
FALLBACK_AFTER_S = 30
RECOVER_S = 60
PAUSE_S = 5  # ~20 credits/MB => <=3,000 credits/day

THRESHOLDS = (0.25, 0.30, 0.35, 0.40)
ENTRY_TS = (0.30, 0.35, 0.40)
FEE = R.CURVE_FEE_BPS / 10000.0
DELAYS = (0.4, 1.0, 2.0)
PRIMARY_DELAY = 1.0
WATCH_SEC = 300
POST_SEC = 300
TOP1_SKIP = 0.5
HOT_TRADES = 200
HOT_VOL = 6000.0
# ---- non-SOL quote curves ----
QUOTE_WAIT_S = 45.0          # buffer a quote coin's trades this long for a reliable quote price, else skip the coin
# ---- late adoption + backfill ----
ADOPT_MIN_TRADES = int(os.environ.get("PAPER_ADOPT_MIN_TRADES", "5"))         # unknown mint needs >= this many trades seen before an age lookup
ADOPT_MAX_PENDING = 20000
ADOPT_LOOKUP_GAP_S = float(os.environ.get("PAPER_ADOPT_LOOKUP_GAP_S", "0.5"))     # public RPC getSignaturesForAddress pacing (free, off the pipeline pool)
PUBLIC_HTTP = os.environ.get("PAPER_PUBLIC_HTTP", "https://api.mainnet-beta.solana.com")
# Helius keys for history backfill: NOT in lib/rpc's pool (pipeline uses HELIUS_API_KEY/_2/_3)
HIST_KEY_ENVS = [k.strip() for k in os.environ.get(
    "PAPER_HIST_KEY_ENVS", "HELIUS_API_KEY_5,HELIUS_API_KEY_LIVE,HELIUS_API_KEY_4,HELIUS_API_KEY_NEW,HELIUS_API_KEY_BACKUP").split(",") if k.strip()]
ADOPT_WORKERS = int(os.environ.get("PAPER_ADOPT_WORKERS", "3"))
BACKFILL_DAILY_CREDITS = float(os.environ.get("PAPER_BACKFILL_DAILY_CREDITS", "5000"))
BACKFILL_MIN_INTERVAL_S = 2.0
BACKFILL_GAP_S = 30.0        # pauses / data gaps longer than this trigger a backfill of young coins
BACKFILL_LIMIT = 1000        # one getTransactionsForAddress call per coin (10 credits per 100 txs returned)
import gzip

TRADE_B64 = base64.b64encode(R.TRADE_EVENT_DISC).decode()[:10]
CREATE_B64 = base64.b64encode(R.CREATE_EVENT_DISC).decode()[:10]
COMPLETE_B64 = base64.b64encode(R.COMPLETE_EVENT_DISC).decode()[:10]


def now() -> float:
    return time.time()


def eat(ts) -> str:
    if ts in (None, ""):
        return ""
    return datetime.fromtimestamp(float(ts), tz=NBO).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " EAT"


def hour_key(ts) -> str:
    return datetime.fromtimestamp(float(ts), tz=NBO).strftime("%Y-%m-%d %H:00")


def pct(vals, q):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return v[f] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def log(msg: str):
    print(f"[{eat(now())}] {msg}", flush=True)


def append_jsonl(name: str, obj: dict):
    with (LOGS / name).open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def gas_usd(sol_usd: float) -> float:
    return (R.DEFAULT_PRIORITY_SOL + R.DEFAULT_BASE_FEE_SOL) * 2 * sol_usd


# ----------------------------------------------------------------------------------------
class SolPrice:
    def __init__(self):
        self.px = None
        self.ts = 0.0

    async def run(self):
        loop = asyncio.get_running_loop()
        while True:
            for name, url, get in (
                    ("binance", "https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT", lambda j: j["price"]),
                    ("coinbase", "https://api.coinbase.com/v2/prices/SOL-USD/spot", lambda j: j["data"]["amount"]),
                    ("kraken", "https://api.kraken.com/0/public/Ticker?pair=SOLUSD", lambda j: list(j["result"].values())[0]["c"][0])):
                try:
                    r = await loop.run_in_executor(None, lambda u=url: requests.get(u, timeout=6))
                    self.px = float(get(r.json()))
                    self.ts = now()
                    break
                except Exception as e:
                    if now() - self.ts > 120:
                        log(f"solprice {name} error {type(e).__name__}")
            await asyncio.sleep(15)


# ----------------------------------------------------------------------------------------
class Coin:
    __slots__ = ("mint", "name", "symbol", "curve", "creator", "t0", "create_recv", "create_slot",
                 "trades", "wallets", "cum_vol", "peak", "peak_t", "armed", "opening", "mid25",
                 "decision", "decided", "signal", "h_recv", "hot", "h_sub", "migrated", "gap",
                 "finalized", "sig_idx", "create_sig", "scalp",
                 "quote_mint", "qbuf", "qwait", "skip_reason", "late_adopted", "adopt_t", "awaiting", "pre",
                 "backfill", "n_backfilled")

    def __init__(self, mint, name, symbol, curve, creator, t0, recv, slot):
        self.mint, self.name, self.symbol, self.curve, self.creator = mint, name, symbol, curve, creator
        self.t0, self.create_recv, self.create_slot = t0, recv, slot
        self.trades: list[dict] = []
        self.wallets: set = set()
        self.cum_vol = 0.0
        self.peak = None
        self.peak_t = None
        self.armed = {T: True for T in THRESHOLDS}
        self.opening = set()
        self.mid25 = False
        self.decision = None   # dict at first mid >=30% live touch
        self.decided = False
        self.signal = None     # dict when tracked as paper position (v0 pass)
        self.h_recv: dict = {}
        self.hot = False
        self.h_sub = None
        self.migrated = None
        self.gap = False
        self.finalized = False
        self.sig_idx = None
        self.create_sig = None
        self.scalp = None
        self.quote_mint = None     # "SOL" or the curve's quote token mint
        self.qbuf: list = []       # quote-coin events waiting for a reliable quote USD price
        self.qwait = None
        self.skip_reason = None
        self.late_adopted = 0      # 1 = create event missed; adopted from trades (t0 from the curve's first tx)
        self.adopt_t = None
        self.awaiting = False      # adopted coin waiting for its history backfill
        self.pre: list = []        # events received while awaiting
        self.backfill = ""         # "", or summary of backfills applied (e.g. "adopt:ok", "pause:ok:+37")
        self.n_backfilled = 0


class Trader:
    def __init__(self):
        self.sol = SolPrice()
        self.coins: dict[str, Coin] = {}
        self.seen = OrderedDict()
        self.slot_seen = OrderedDict()
        self.seq = 0
        self.start = now()
        self.health = defaultdict(float)
        self.lags: list[float] = []
        self.slot_lags: list[float] = []
        self.h_lags: list[float] = []
        self.feeds = {n: {"connected": False, "reconnects": 0, "drops": 0, "last_msg": 0.0, "conn_ts": 0.0,
                          "connected_since": None, "last_error": "", "msgs": 0, "bytes": 0, "first_wins": 0,
                          "ws": None, "state": "init", "activations": 0, "want": False}
                      for n in self._feed_urls()}
        self.pauses: list = []
        self.gaps: list = []
        self.in_gap = False
        self.gap_start = 0.0
        self.resumed_at = 0.0
        self.wd_tick = now()
        try:
            self.husage = json.loads((LOGS / "helius_usage.json").read_text())
        except Exception:
            self.husage = {}
        self.hfeed = {"connected": False, "reconnects": 0, "bytes_today": 0, "day": "",
                      "subs": 0, "subs_total": 0, "skipped_full": 0, "skipped_budget": 0,
                      "last_error": "", "msgs": 0}
        self.hourly = self._load_hourly()
        self.h_ws = None
        self.h_pending: dict[int, str] = {}   # request id -> mint
        self.h_subid: dict[int, str] = {}     # sub id -> mint
        self.h_req = 100
        self.recent_signals: list[dict] = []
        self.proc_ms: list[float] = []
        self.lag_recent = deque(maxlen=3000)
        self.probe_cov: list = []
        self.shadow = SS.ScalpShadow(self, log, eat)
        self.shadow_ms: list = []
        self.recent_shadow: list = []
        self.probe_ahead: list = []
        self.qp = QE.QuotePrices(log=log)
        self.done_mints: OrderedDict = OrderedDict()   # finalized / known-old mints: never adopt again
        self.adopt: dict = {}                          # unknown mint -> pending adoption state
        self.adopt_q = None
        self.bf_q = None
        self.adopt_stats = defaultdict(int)
        self.bf_last = 0.0
        self.hist_dead: dict = {}   # key env name -> parked until (max usage reached)
        self.quote_trades = defaultdict(int)
        self.sig_bt: OrderedDict = OrderedDict()          # tx signature -> block time (for per-feed lag on dupes)
        self.feed_lag = defaultdict(lambda: deque(maxlen=4000))   # feed -> (recv, lag) samples, first + dupe arrivals
        self.lag_state = {"bad_since": None, "good_since": None, "switch_reason": "", "on_at": None,
                          "lag_on": 0, "lag_off": 0}
        self.cheap_on = False          # lag-triggered: per-curve Helius subscriptions for coins in their decision window
        self.cheap_subs: set = set()   # mints subscribed because of cheap mode
        self.cheap_meter: list = []    # (t, subs bytes total) samples for MB/min
        self.QE, self.QE_SOL = QE, QE.SOL
        self.band = BT.BandTapes(self, LOGS, log)
        try:
            old = json.loads((LOGS / "old_mints.json").read_text())
            cut = now() - 6 * 3600
            for m, ts in old.items():
                if ts > cut:
                    self.done_mints[m] = ts
        except Exception:
            pass

    # ---------------- hourly counters ----------------
    def _load_hourly(self):
        p = LOGS / "hourly.json"
        try:
            d = json.loads(p.read_text())
            return defaultdict(lambda: defaultdict(int), {k: defaultdict(int, v) for k, v in d.items()})
        except Exception:
            return defaultdict(lambda: defaultdict(int))

    def bump(self, key, n=1, ts=None):
        self.hourly[hour_key(ts or now())][key] += n

    def write_hourly(self):
        d = {k: dict(v) for k, v in sorted(self.hourly.items())}
        tmp = LOGS / "hourly.json.tmp"
        tmp.write_text(json.dumps(d, indent=1))
        tmp.replace(LOGS / "hourly.json")
        keys = sorted({kk for v in d.values() for kk in v})
        with (LOGS / "hourly.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["hour_eat"] + keys)
            for h, v in d.items():
                w.writerow([h] + [v.get(k, 0) for k in keys])

    # ---------------- public feed ----------------
    # ---------------- feeds: redundant public x2 + budgeted Helius fallback ----------------
    def _feed_urls(self):
        urls = {"public_a": PUBLIC_WS, "public_b": PUBLIC_WS_B}
        k = os.environ.get(HELIUS_KEY_ENV)
        if k:
            urls["helius_fb"] = "wss://mainnet.helius-rpc.com/?api-key=" + k
        return urls

    def _usage(self):
        day = datetime.now(NBO).strftime("%Y-%m-%d")
        u = self.husage.setdefault(day, {"probe": 0, "fallback": 0})
        for d in sorted(self.husage)[:-7]:
            self.husage.pop(d, None)
        return u

    def save_usage(self):
        try:
            tmp = LOGS / "helius_usage.json.tmp"
            tmp.write_text(json.dumps(self.husage))
            tmp.replace(LOGS / "helius_usage.json")
        except Exception:
            pass

    def public_last_msg(self):
        return max([self.feeds[n]["last_msg"] for n in ("public_a", "public_b") if n in self.feeds] + [0.0])

    def public_lag_med(self, window: float = LAG_WIN_S):
        """Median lag (recv - block time) of the BEST public feed over the trailing window; None if too few samples."""
        t = now()
        best = None
        for n in ("public_a", "public_b"):
            v = [l for r, l in self.feed_lag.get(n, ()) if r >= t - window]
            if len(v) >= 8:
                m = pct(v, 0.5)
                best = m if best is None else min(best, m)
        return best

    def fb_budget_ok(self):
        u = self._usage()
        hk = hour_key(now())
        hb = u.setdefault("fallback_hour", {})
        for k in list(hb):
            if k != hk:
                hb.pop(k, None)
        if u["fallback"] > HELIUS_FB_DAILY_MB * 1e6:
            return False, "daily_cap"
        if hb.get(hk, 0) > HELIUS_FB_HOURLY_MB * 1e6:
            return False, "hourly_cap"
        return True, ""

    def feed_lag_meds(self, window: float = 60.0):
        t = now()
        out = {}
        for n, dq in self.feed_lag.items():
            v = [l for r, l in dq if r >= t - window]
            out[n] = {"med": round(pct(v, 0.5), 2) if v else None, "p90": round(pct(v, 0.9), 2) if v else None, "n": len(v)}
        return out

    def helius_budget(self, t):
        u = self._usage()
        fb_mb = u.get("fallback", 0) / 1e6
        pr_mb = u.get("probe", 0) / 1e6
        bf = u.get("backfill_credits", 0)
        fb = self.feeds.get("helius_fb") or {}
        return {
            "key_env": HELIUS_KEY_ENV, "history_keys": HIST_KEY_ENVS,
            "fallback_ws_on": bool(fb.get("want")), "fallback_state": fb.get("state"),
            "fallback_mb_today": round(fb_mb, 2),
            "fallback_mb_this_hour": round((u.get("fallback_hour") or {}).get(hour_key(t), 0) / 1e6, 2),
            "fallback_cap_mb_day": HELIUS_FB_DAILY_MB, "fallback_cap_mb_hour": HELIUS_FB_HOURLY_MB,
            "probe_mb_today": round(pr_mb, 2), "probe_cap_mb_day": HELIUS_DAILY_MB,
            "backfill_credits_today": bf, "backfill_calls_today": u.get("backfill_calls", 0),
            "backfill_cap_credits_day": BACKFILL_DAILY_CREDITS,
            "est_credits_today": round((fb_mb + pr_mb) * WS_CREDITS_PER_MB + bf),
            "cap_credits_day": round((HELIUS_FB_DAILY_MB + HELIUS_DAILY_MB) * WS_CREDITS_PER_MB + BACKFILL_DAILY_CREDITS),
            "subs_mb_this_hour": round((u.get("probe_hour") or {}).get(hour_key(t), 0) / 1e6, 2),
            "subs_cap_mb_hour": HELIUS_SUBS_HOURLY_MB,
            "cheap_mode_on": self.cheap_on, "cheap_subs": len(self.cheap_subs), "subs_active": len(self.h_subid),
            "subs_mb_per_min_2m": self.subs_mb_per_min(),
            "subs_credits_per_min_2m": (round(self.subs_mb_per_min() * WS_CREDITS_PER_MB, 1) if self.subs_mb_per_min() is not None else None),
            "lag_switch": {k: (eat(v) if k in ("bad_since", "good_since", "on_at") and v else v) for k, v in self.lag_state.items()},
            "rule": f"cheap mode (per-curve subs, decision-window coins + open positions) ON if best public median lag > "
                    f"{LAG_ON_S:g}s for {LAG_ON_FOR_S:.0f}s, OFF when < {LAG_OFF_S:g}s for {LAG_OFF_FOR_S:.0f}s; "
                    f"firehose only if public silent {FALLBACK_AFTER_S}s",
            "feed_lag_60s": self.feed_lag_meds(),
        }

    def fallback_wanted(self) -> bool:
        t = now()
        fb = self.feeds.get("helius_fb")
        if fb is None:
            return False
        ok, why = self.fb_budget_ok()
        if not ok:
            if fb.get("want"):
                fb["want"] = False
                append_jsonl("health.jsonl", {"ts": eat(t), "event": "fallback_off", "reason": "budget_" + why})
                log(f"Helius fallback OFF: {why} reached")
            fb["state"] = "budget_" + why
            return False
        if t - self.wd_tick > 3 or t - self.resumed_at < 15:
            return fb.get("want", False)  # just resumed from a pause: give public feeds time to reconnect
        silent = t - (self.public_last_msg() or self.start)
        if not fb.get("want") and silent > FALLBACK_AFTER_S:
            fb["want"] = True
            fb["activations"] += 1
            append_jsonl("health.jsonl", {"ts": eat(t), "event": "fallback_on", "reason": f"public silent {silent:.0f}s"})
            log(f"public feeds silent {silent:.0f}s -> Helius firehose ON (outage mode)")
        elif fb.get("want"):
            healthy = [n for n in ("public_a", "public_b") if self.feeds[n]["connected"]
                       and t - self.feeds[n]["last_msg"] < 3 and self.feeds[n]["conn_ts"]
                       and t - self.feeds[n]["conn_ts"] > RECOVER_S]
            if healthy:
                fb["want"] = False
                append_jsonl("health.jsonl", {"ts": eat(t), "event": "fallback_off", "recovered": healthy})
                log(f"public feed {healthy[0]} healthy for {RECOVER_S}s -> Helius firehose OFF")
        return fb.get("want", False)

    async def run_feed(self, name: str, url: str):
        f = self.feeds[name]
        backoff = 1.0
        while True:
            if name == "helius_fb" and not self.fallback_wanted():
                await asyncio.sleep(1.0)
                continue
            if name != "helius_fb" and self.forced_outage():
                f.update(connected=False, state="forced_outage_test")
                await asyncio.sleep(1.0)
                continue
            t_conn = now()
            try:
                async with websockets.connect(url, max_size=2 ** 24, ping_interval=20, ping_timeout=20,
                                              open_timeout=15, close_timeout=3) as ws:
                    f["ws"] = ws
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                              "params": [{"mentions": [R.PUMP_PROGRAM]}, {"commitment": "processed"}]}))
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "slotSubscribe"}))
                    f.update(connected=True, connected_since=eat(now()), conn_ts=now(), last_msg=now(), state="up")
                    append_jsonl("health.jsonl", {"ts": eat(now()), "event": "connected", "feed": name})
                    log(f"{name} connected")
                    sub_slot = None
                    while True:
                        if name == "helius_fb" and not self.fallback_wanted():
                            raise RuntimeError("fallback no longer needed")
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=WATCHDOG_S)
                        except asyncio.TimeoutError:
                            raise RuntimeError(f"watchdog: no message for {WATCHDOG_S}s")
                        recv = now()
                        f["last_msg"] = recv
                        f["msgs"] += 1
                        f["bytes"] += len(raw)
                        self.health["msgs"] += 1
                        self.health["bytes"] += len(raw)
                        self.health[f"msgs_{name}"] += 1
                        if name == "helius_fb":
                            u_ = self._usage()
                            u_["fallback"] += len(raw)
                            hb_ = u_.setdefault("fallback_hour", {})
                            hk_ = hour_key(recv)
                            hb_[hk_] = hb_.get(hk_, 0) + len(raw)
                        if now() - t_conn > 60:
                            backoff = 1.0
                        m = json.loads(raw)
                        if "id" in m:
                            if m["id"] == 2:
                                sub_slot = m.get("result")
                            elif "error" in m:
                                raise RuntimeError(f"subscribe error {str(m['error'])[:100]}")
                            continue
                        p = m.get("params")
                        if not p:
                            continue
                        if p.get("subscription") == sub_slot:
                            sl = p["result"].get("slot")
                            if sl is not None and sl not in self.slot_seen:
                                self.slot_seen[sl] = recv
                                if len(self.slot_seen) > 3000:
                                    self.slot_seen.popitem(last=False)
                            continue
                        sig = ((p.get("result") or {}).get("value") or {}).get("signature")
                        if sig in self.seen:
                            self.health["dupes"] += 1
                            bt0 = self.sig_bt.get(sig)
                            if bt0 is not None:
                                self.feed_lag[name].append((recv, recv - bt0))
                            continue
                        f["first_wins"] += 1
                        t1 = time.perf_counter()
                        self.on_logs(p["result"], recv)
                        bt0 = self.sig_bt.get(sig)
                        if bt0 is not None:
                            self.feed_lag[name].append((recv, recv - bt0))
                        self.proc_ms.append((time.perf_counter() - t1) * 1000)
                        if len(self.proc_ms) > 5000:
                            self.proc_ms = self.proc_ms[-2000:]
            except Exception as e:
                err = f"{type(e).__name__}: {str(e)[:140]}"
                if "api-key" in err:
                    err = type(e).__name__
                planned = "no longer needed" in err
                f.update(connected=False, ws=None, last_error=err, state="idle" if planned else "down")
                if not planned:
                    f["drops"] += 1
                    f["reconnects"] += 1
                    append_jsonl("health.jsonl", {"ts": eat(now()), "event": "disconnect", "feed": name, "error": err,
                                                  "up_s": round(now() - t_conn, 1)})
                    wait = backoff * (0.7 + 0.6 * random.random())
                    log(f"{name} dropped: {err}; retry in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    backoff = min(backoff * 2, 30.0)
                else:
                    append_jsonl("health.jsonl", {"ts": eat(now()), "event": "closed", "feed": name, "reason": "fallback off"})

    @staticmethod
    def forced_outage() -> bool:
        """Test hook: `echo 90 > paper/force_outage` blocks the public feeds for 90s."""
        p = PAPER / "force_outage"
        try:
            secs = float(p.read_text().strip() or 60)
            return now() < p.stat().st_mtime + secs
        except Exception:
            return False

    async def watchdog(self):
        """Independent of recv(): aborts silent sockets, detects process/box pauses, marks tape gaps."""
        last = now()
        while True:
            await asyncio.sleep(1.0)
            t = now()
            pause = t - last - 1.0
            last = t
            self.wd_tick = t
            if pause > PAUSE_S:
                self.pauses.append({"at": eat(t), "paused_s": round(pause, 1)})
                self.pauses = self.pauses[-50:]
                self.resumed_at = t
                append_jsonl("health.jsonl", {"ts": eat(t), "event": "process_paused", "paused_s": round(pause, 1)})
                self.band.add_gap(t - pause - 1.0, t)
                log(f"event loop / box was paused for {pause:.0f}s; forcing feed reconnects")
                if pause > BACKFILL_GAP_S:
                    self.backfill_young(t - pause - 2, f"pause_{pause:.0f}s")
                for c in self.coins.values():
                    if not c.finalized:
                        c.gap = True
                for n, f in self.feeds.items():
                    f["last_msg"] = t  # do not count the pause as feed silence
                    ws = f.get("ws")
                    if ws is not None and pause > WATCHDOG_S:
                        f.update(connected=False, last_error=f"forced reconnect after {pause:.0f}s pause")
                        try:
                            ws.transport.abort()
                        except Exception:
                            pass
                continue
            if self.forced_outage():
                for n in ("public_a", "public_b"):
                    ws = self.feeds[n].get("ws")
                    if ws is not None:
                        self.feeds[n].update(connected=False, last_error="forced outage (test)")
                        try:
                            ws.transport.abort()
                        except Exception:
                            pass
            for n, f in self.feeds.items():
                ws = f.get("ws")
                if ws is not None and f["connected"] and t - f["last_msg"] > WATCHDOG_S + 2:
                    f.update(connected=False, last_error=f"watchdog abort: silent {t - f['last_msg']:.0f}s")
                    try:
                        ws.transport.abort()
                    except Exception:
                        pass
            any_last = max([f["last_msg"] for f in self.feeds.values()] + [0.0])
            if any_last and t - any_last > 5:
                if not self.in_gap:
                    self.in_gap = True
                    self.gap_start = any_last
                for c in self.coins.values():
                    if not c.finalized:
                        c.gap = True
            elif self.in_gap:
                self.in_gap = False
                g = t - self.gap_start
                self.gaps.append({"at": eat(t), "gap_s": round(g, 1)})
                self.band.add_gap(self.gap_start, t)
                self.gaps = self.gaps[-50:]
                append_jsonl("health.jsonl", {"ts": eat(t), "event": "data_gap", "gap_s": round(g, 1)})
                if g > BACKFILL_GAP_S:
                    self.backfill_young(self.gap_start - 2, f"gap_{g:.0f}s")
            self.band.fb_tick(bool((self.feeds.get("helius_fb") or {}).get("want")), t)
            if int(t) % 30 == 0:
                self.save_usage()
            if int(t) % 60 == 0:
                try:
                    tmp = LOGS / "old_mints.json.tmp"
                    cut = t - 6 * 3600
                    tmp.write_text(json.dumps({m: ts for m, ts in self.done_mints.items() if ts > cut}))
                    tmp.replace(LOGS / "old_mints.json")
                except Exception:
                    pass

    def on_logs(self, res: dict, recv: float):
        v = res.get("value") or {}
        slot = (res.get("context") or {}).get("slot")
        if v.get("err") is not None:
            return
        sig = v.get("signature")
        if sig in self.seen:
            self.health["dupes"] += 1
            return
        self.seen[sig] = 1
        if len(self.seen) > 300000:
            for _ in range(50000):
                self.seen.popitem(last=False)
        logs = v.get("logs") or []
        evs = []
        for line in logs:
            if not line.startswith("Program data: "):
                continue
            b = line[14:]
            if b.startswith(TRADE_B64) or b.startswith(CREATE_B64) or b.startswith(COMPLETE_B64):
                try:
                    evs.append(base64.b64decode(b))
                except Exception:
                    pass
        if not evs:
            return
        for raw in evs:
            d = raw[:8]
            if d == R.CREATE_EVENT_DISC:
                self.on_create(raw, sig, slot, recv)
            elif d == R.TRADE_EVENT_DISC:
                self.on_trade(raw, sig, slot, recv)
            elif d == R.COMPLETE_EVENT_DISC:
                # mint is first field after disc? layout: user, mint, bonding_curve, timestamp
                try:
                    mint = R.b58(raw[40:72])
                    c = self.coins.get(mint)
                    if c and c.migrated is None:
                        c.migrated = recv
                    self.band.on_complete(mint, recv)
                except Exception:
                    pass

    def on_create(self, raw, sig, slot, recv):
        try:
            o = 8
            strs = []
            for _ in range(3):
                n = struct.unpack_from("<I", raw, o)[0]
                o += 4
                strs.append(raw[o:o + n].decode("utf-8", errors="replace"))
                o += n
            mint = R.b58(raw[o:o + 32])
            curve = R.b58(raw[o + 32:o + 64])
            creator = R.b58(raw[o + 96:o + 128])
            ts = struct.unpack_from("<q", raw, o + 128)[0]
        except Exception:
            self.health["create_parse_err"] += 1
            return
        if mint in self.coins:
            return
        self.health["creates"] += 1
        self.bump("launches_seen", ts=ts)
        c = Coin(mint, strs[0][:40], strs[1][:20], curve, creator, ts, recv, slot)
        c.create_sig = sig
        self.coins[mint] = c
        a = self.adopt.pop(mint, None)
        if a:
            for ev, sg, sl, rc in a["events"]:
                self.feed_event(c, ev, sg, sl, rc)

    def on_trade(self, raw, sig, slot, recv):
        self.health["trade_events"] += 1
        try:
            ev = QE.decode_trade(raw)
        except Exception:
            ev = None
        if not ev:
            return
        bt = ev["timestamp"]
        if sig not in self.sig_bt:
            self.sig_bt[sig] = bt
            if len(self.sig_bt) > 100000:
                for _ in range(20000):
                    self.sig_bt.popitem(last=False)
        if len(self.lags) < 20000:
            self.lags.append(recv - bt)
        self.lag_recent.append((recv, recv - bt))
        if slot in self.slot_seen and len(self.slot_lags) < 20000:
            self.slot_lags.append((recv - self.slot_seen[slot]) * 1000)
        self.quote_trades[self.qp.symbol(ev.get("quote_mint"))] += 1
        c = self.coins.get(ev["mint"])
        if (c is None or c.finalized) and self.band.on_tail_trade(ev, sig, slot, recv):
            return
        if c is None:
            self.consider_adopt(ev, sig, slot, recv)
            return
        if c.finalized or c.skip_reason:
            return
        if c.awaiting:
            c.pre.append((ev, sig, slot, recv))
            return
        self.feed_event(c, ev, sig, slot, recv)

    # ---------------- quote-aware routing ----------------
    def feed_event(self, c: Coin, ev: dict, sig, slot, recv, backfill: bool = False):
        """Price one decoded event (SOL or quote-token curve) and push it onto the coin's tape."""
        if c.finalized or c.skip_reason:
            return
        qm = ev.get("quote_mint")
        if c.quote_mint is None:
            c.quote_mint = qm or "unknown"
        if qm == QE.SOL:
            self.push_event(c, ev, sig, slot, recv, backfill)
            return
        if qm is None:
            self.skip_coin(c, "unknown_quote_layout")
            return
        got = self.qp.get(qm)
        if got is None or not self.sol.px:
            if c.qwait is None:
                c.qwait = now()
            if len(c.qbuf) < 5000:
                c.qbuf.append((ev, sig, slot, recv, backfill))
            if now() - c.qwait > QUOTE_WAIT_S:
                self.skip_coin(c, f"quote_price_unavailable:{self.qp.symbol(qm)}:{self.qp.status(qm)}")
            return
        if c.qbuf:
            self.flush_qbuf(c, got)
        QE.to_sol_equiv(ev, got[0], got[1], self.sol.px)
        self.push_event(c, ev, sig, slot, recv, backfill)

    def flush_qbuf(self, c: Coin, got):
        buf, c.qbuf, c.qwait = c.qbuf, [], None
        for ev, sig, slot, recv, bf in buf:
            QE.to_sol_equiv(ev, got[0], got[1], self.sol.px)
            self.push_event(c, ev, sig, slot, recv, bf)

    def skip_coin(self, c: Coin, reason: str):
        if c.skip_reason:
            return
        c.skip_reason = reason
        c.qbuf, c.pre = [], []
        rec = {"ts": eat(now()), "mint": c.mint, "symbol": c.symbol, "quote_mint": c.quote_mint,
               "quote_symbol": self.qp.symbol(c.quote_mint), "late_adopted": c.late_adopted,
               "t_plus_at_skip": round(now() - c.t0, 1), "reason": reason}
        append_jsonl("skipped_coins.jsonl", rec)
        self.bump("skip_coin:" + reason.split(":")[0])
        log(f"SKIP COIN {c.symbol} {c.mint[:8]} ({rec['quote_symbol']}): {reason}")

    def make_tr(self, c: Coin, ev: dict, sig, slot, recv, backfill: bool = False):
        sol_usd = self.sol.px
        if not sol_usd:
            return None
        bt = ev["timestamp"]
        t = int(bt) - int(c.t0)
        tok = ev["token_amount"] / 1e6
        sol = ev["sol_amount"] / 1e9
        px_sol = (sol / tok) if tok else None
        vs, vt = ev["virtual_sol_reserves"], ev["virtual_token_reserves"]
        spot_sol = (vs / 1e9) / (vt / 1e6) if vs and vt else None
        self.seq += 1
        tr = {
            "signature": sig, "seq": self.seq, "block_time": self.seq, "bt": bt, "slot": slot,
            "t_plus_sec": t, "side": "buy" if ev["is_buy"] else "sell", "trader": ev["user"],
            "sol_ui": sol, "token_ui": tok, "price_sol": px_sol,
            "price_usd": px_sol * sol_usd if px_sol else None,
            "spot_usd": spot_sol * sol_usd if spot_sol else None,
            "mcap_usd": spot_sol * R.TOTAL_SUPPLY_UI * sol_usd if spot_sol else None,
            "volume_usd": sol * sol_usd, "sol_usd": sol_usd, "vs": vs, "vt": vt,
            "fee_bps_total": (ev.get("fee_bps") or 0) + (ev.get("creator_fee_bps") or 0),
            "recv": recv, "recv_raw": recv, "is_create_tx": int(sig == c.create_sig),
        }
        if ev.get("quote_usd") is not None:
            tr["quote_usd"] = ev["quote_usd"]
        if ev.get("quote_amount") is not None and ev.get("quote_mint") != QE.SOL:
            tr["quote_raw"] = ev["quote_amount"]
        if backfill:
            tr["backfill"] = 1
        if sig in c.h_recv:
            tr["h_recv"] = c.h_recv[sig]
        return tr

    def push_tr(self, c: Coin, tr: dict):
        c.trades.append(tr)
        try:
            self.band.note(c, tr)
        except Exception:
            pass
        c.cum_vol += tr["volume_usd"]
        c.wallets.add(tr["trader"])
        if tr.get("backfill"):
            c.n_backfilled += 1
        if c.signal is None and not c.decided:
            self.check_signal(c, tr)
        try:
            SS.ScalpShadow.mark(c, tr)
        except Exception:
            pass
        t = tr["t_plus_sec"]
        if (not c.hot and len(c.trades) >= HOT_TRADES and c.cum_vol >= HOT_VOL and t <= 250
                and not c.decided):
            c.hot = True
            try:
                asyncio.get_running_loop().create_task(self.h_subscribe(c))
            except RuntimeError:
                pass

    def push_event(self, c: Coin, ev: dict, sig, slot, recv, backfill: bool = False):
        tr = self.make_tr(c, ev, sig, slot, recv, backfill)
        if tr is not None:
            self.push_tr(c, tr)

    def coin_tags(self, c: Coin) -> dict:
        return {"quote_mint": c.quote_mint or QE.SOL, "quote_symbol": self.qp.symbol(c.quote_mint or QE.SOL),
                "late_adopted": int(c.late_adopted), "backfill": c.backfill,
                "n_backfilled_trades": c.n_backfilled, "coin_skip_reason": c.skip_reason or ""}

    # ---------------- late adoption (create event missed) ----------------
    def consider_adopt(self, ev, sig, slot, recv):
        m = ev["mint"]
        if m in self.done_mints:
            return
        a = self.adopt.get(m)
        if a is None:
            if len(self.adopt) >= ADOPT_MAX_PENDING:
                self.adopt_stats["dropped_pending_full"] += 1
                return
            a = self.adopt[m] = {"first": recv, "events": [], "creator": ev.get("creator"), "state": "new"}
        if len(a["events"]) < 3000:
            a["events"].append((ev, sig, slot, recv))
        if a["state"] == "new" and len(a["events"]) >= ADOPT_MIN_TRADES and self.adopt_q is not None:
            a["state"] = "queued"
            self.adopt_q.put_nowait(m)

    @staticmethod
    def curve_of(mint: str):
        try:
            from solders.pubkey import Pubkey
            pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(mint))],
                                                 Pubkey.from_string(R.PUMP_PROGRAM))
            return str(pda)
        except Exception:
            return None

    @staticmethod
    def lookup_age(curve: str) -> dict:
        """Launch time = block time of the curve's first tx (public RPC getSignaturesForAddress; free).
        Small page first (old coins answer cheaply), a 1000-sig page only for hot young-looking curves."""
        before = None
        for lim in (50, 1000, 1000):
            p = {"limit": lim}
            if before:
                p["before"] = before
            r = requests.post(PUBLIC_HTTP, json={"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                                 "params": [curve, p]}, timeout=15)
            if r.status_code == 429:
                return {"error": "429"}
            res = (r.json() or {}).get("result")
            if res is None:
                return {"error": "no_result"}
            if not res:
                if before:
                    break
                return {"young": False, "why": "no_history"}
            last = res[-1]
            if now() - (last.get("blockTime") or 0) > WATCH_SEC:
                return {"young": False, "why": "older_tx_exists", "oldest_seen_bt": last.get("blockTime")}
            if len(res) < lim:
                return {"young": True, "t0": last.get("blockTime"), "create_sig": last.get("signature")}
            before = last.get("signature")
        return {"young": False, "why": "gt2000_txs"}

    async def adopt_worker(self):
        loop = asyncio.get_running_loop()
        while True:
            m = await self.adopt_q.get()
            a = self.adopt.get(m)
            if a is None or m in self.coins or m in self.done_mints:
                continue
            if now() - a["first"] > 240:     # first seen 4+ min ago: past the entry window even if young
                self.adopt.pop(m, None)
                self.mark_done(m)
                self.adopt_stats["stale_in_queue"] += 1
                continue
            curve = self.curve_of(m)
            if not curve:
                self.adopt.pop(m, None)
                continue
            try:
                info = await loop.run_in_executor(None, self.lookup_age, curve)
            except Exception as e:
                info = {"error": type(e).__name__}
            await asyncio.sleep(ADOPT_LOOKUP_GAP_S)
            if info.get("error"):
                self.adopt_stats["lookup_err"] += 1
                a["tries"] = a.get("tries", 0) + 1
                if a["tries"] < 3:
                    await asyncio.sleep(5 if info["error"] == "429" else 1)
                    self.adopt_q.put_nowait(m)
                else:
                    self.adopt.pop(m, None)
                continue
            self.adopt_stats["lookups"] += 1
            if not info.get("young") or not info.get("t0") or now() - info["t0"] > WATCH_SEC - 5:
                self.adopt.pop(m, None)
                self.mark_done(m)
                self.adopt_stats["old"] += 1
                continue
            self.adopt.pop(m, None)
            if m in self.coins:
                continue
            self.adopt_coin(m, curve, a, info)

    def adopt_coin(self, m, curve, a, info):
        t = now()
        c = Coin(m, m[:6], m[:6], curve, a.get("creator") or "", info["t0"], a["first"], None)
        c.create_sig = info.get("create_sig")
        c.late_adopted = 1
        c.adopt_t = t
        c.awaiting = True
        c.pre = list(a["events"])
        self.coins[m] = c
        self.adopt_stats["adopted"] += 1
        self.bump("late_adopted", ts=c.t0)
        append_jsonl("adoptions.jsonl", {"ts": eat(t), "mint": m, "t0": c.t0, "launch_time_eat": eat(c.t0),
                                         "t_plus_at_adopt": round(t - c.t0, 1), "first_seen_eat": eat(a["first"]),
                                         "buffered_events": len(c.pre), "n_sigs": info.get("n_sigs")})
        log(f"ADOPT {m[:8]} launched {eat(c.t0)} (t+{t - c.t0:.0f}s, {len(c.pre)} buffered trades); backfilling")
        self.request_backfill(c, c.t0, t, "adopt")

    def mark_done(self, m):
        self.done_mints[m] = now()
        if len(self.done_mints) > 200000:
            for _ in range(20000):
                self.done_mints.popitem(last=False)

    # ---------------- history backfill (Helius getTransactionsForAddress, budgeted) ----------------
    def request_backfill(self, c: Coin, since: float, until: float, reason: str):
        if self.bf_q is None:
            return
        self.bf_q.put_nowait((c, since, until, reason))

    def fetch_history(self, curve: str, since: float, until: float):
        """One getTransactionsForAddress call (full, asc, <=1000 txs). Tries off-pool keys in order; a key that
        answers 'max usage reached' is parked for an hour. Never logs the URL (it carries the key)."""
        p = {"transactionDetails": "full", "encoding": "json", "maxSupportedTransactionVersion": 1,
             "sortOrder": "asc", "limit": BACKFILL_LIMIT, "commitment": "confirmed",
             "filters": {"blockTime": {"gte": int(since) - 2, "lte": int(until) + 2}, "status": "succeeded"}}
        last = "no_key"
        for name in HIST_KEY_ENVS:
            key = os.environ.get(name)
            if not key:
                continue
            if self.hist_dead.get(name, 0) > now():
                last = "all_keys_max_usage_parked" if last in ("no_key", "max_usage_reached", "all_keys_max_usage_parked") else last
                continue
            try:
                r = requests.post("https://mainnet.helius-rpc.com/?api-key=" + key,
                                  json={"jsonrpc": "2.0", "id": 1, "method": "getTransactionsForAddress", "params": [curve, p]},
                                  timeout=45)
            except Exception as e:
                last = f"http_{type(e).__name__}"
                continue
            if r.status_code == 429 and "max usage" in r.text.lower():
                self.hist_dead[name] = now() + 3600
                last = "max_usage_reached"
                continue
            if r.status_code != 200:
                last = f"http_{r.status_code}"
                continue
            try:
                j = r.json()
            except Exception:
                last = "bad_json"
                continue
            if "error" in j:
                last = "rpc_error:" + str((j.get("error") or {}).get("code"))
                continue
            return ((j.get("result") or {}).get("data") or []), None
        return None, last

    async def backfill_worker(self):
        loop = asyncio.get_running_loop()
        while True:
            c, since, until, reason = await self.bf_q.get()
            if c.finalized or c.mint not in self.coins:
                continue
            t = now()
            if reason != "adopt" and t - c.t0 > WATCH_SEC:
                continue
            u = self._usage()
            if u.get("backfill_credits", 0) + 100 > BACKFILL_DAILY_CREDITS:
                self.adopt_stats["backfill_budget_skips"] += 1
                self.apply_backfill(c, None, reason, "daily_budget")
                continue
            wait = BACKFILL_MIN_INTERVAL_S - (t - self.bf_last)
            if wait > 0:
                await asyncio.sleep(wait)
            self.bf_last = now()
            txs, err = await loop.run_in_executor(None, self.fetch_history, c.curve, since, until)
            if txs is not None:
                u = self._usage()
                u["backfill_credits"] = u.get("backfill_credits", 0) + max(10, math.ceil(len(txs) / 100) * 10)
                u["backfill_calls"] = u.get("backfill_calls", 0) + 1
            elif err == "http_429":
                await asyncio.sleep(30)
            self.adopt_stats["backfill_calls"] += 1
            try:
                self.apply_backfill(c, txs, reason, err)
            except Exception:
                append_jsonl("errors.jsonl", {"ts": eat(now()), "mint": c.mint, "where": "backfill",
                                              "tb": traceback.format_exc()[-1500:]})
                c.awaiting = False

    def decode_history(self, c: Coin, txs):
        """-> (meta from CreateEvent or None, [(order, ev, sig, slot)]) for this coin from full txs."""
        meta, out = None, []
        for k, tx in enumerate(txs or []):
            m = tx.get("meta") or {}
            if m.get("err") is not None:
                continue
            sig = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
            slot = tx.get("slot")
            j = 0
            for line in m.get("logMessages") or []:
                if not line.startswith("Program data: "):
                    continue
                b = line[14:]
                if not (b.startswith(TRADE_B64) or b.startswith(CREATE_B64)):
                    continue
                try:
                    raw = base64.b64decode(b)
                except Exception:
                    continue
                if raw[:8] == R.CREATE_EVENT_DISC and meta is None:
                    try:
                        o, strs = 8, []
                        for _ in range(3):
                            n = struct.unpack_from("<I", raw, o)[0]
                            o += 4
                            strs.append(raw[o:o + n].decode("utf-8", errors="replace"))
                            o += n
                        if R.b58(raw[o:o + 32]) == c.mint:
                            meta = {"name": strs[0][:40], "symbol": strs[1][:20], "creator": R.b58(raw[o + 96:o + 128]),
                                    "sig": sig}
                    except Exception:
                        pass
                elif raw[:8] == R.TRADE_EVENT_DISC:
                    ev = QE.decode_trade(raw)
                    if ev and ev["mint"] == c.mint:
                        out.append(((slot or 0, k, j), ev, sig, slot))
                        j += 1
        return meta, out

    @staticmethod
    def _k_ev(ev, sig):
        return (sig, bool(ev["is_buy"]), int(ev["token_amount"]))

    @staticmethod
    def _k_tr(tr):
        return (tr["signature"], tr["side"] == "buy", int(round(tr["token_ui"] * 1e6)))

    def apply_backfill(self, c: Coin, txs, reason: str, err=None):
        t = now()
        meta, hist = self.decode_history(c, txs) if txs else (None, [])
        if meta and c.late_adopted:
            c.name, c.symbol = meta["name"], meta["symbol"]
            c.creator = meta["creator"] or c.creator
            c.create_sig = meta["sig"] or c.create_sig
        rec = {"ts": eat(t), "mint": c.mint, "symbol": c.symbol, "reason": reason, "t_plus": round(t - c.t0, 1),
               "txs": len(txs or []), "hist_trades": len(hist), "error": err,
               "truncated": int(len(txs or []) >= BACKFILL_LIMIT)}
        if reason == "adopt":
            pre, c.pre, c.awaiting = c.pre, [], False
            have = {self._k_ev(ev, sg) for ev, sg, sl, rc in pre}
            evs = [(o, ev, sg, sl, t, True) for o, ev, sg, sl in hist if self._k_ev(ev, sg) not in have]
            evs += [((sl or 0, 10 ** 9, i), ev, sg, sl, max(rc, t), False) for i, (ev, sg, sl, rc) in enumerate(pre)]
            evs.sort(key=lambda x: x[0])
            for _, ev, sg, sl, rc, bf in evs:
                self.feed_event(c, ev, sg, sl, rc, backfill=bf)
            rec["added"] = sum(1 for x in evs if x[5])
            rec["buffered"] = len(pre)
            c.backfill = f"adopt:{'ok' if txs is not None else 'failed_' + str(err)}:+{rec['added']}"
        else:
            if c.decided or c.signal is not None or (c.scalp and c.scalp.get("pos")) or c.skip_reason or c.awaiting:
                rec["skipped"] = "coin_decided_or_open_or_waiting"
            elif txs is None:
                rec["skipped"] = "no_history"
            else:
                have = {self._k_tr(x) for x in c.trades} | {self._k_ev(e, sg) for e, sg, sl, rc, bf in c.qbuf}
                new = [(o, ev, sg, sl) for o, ev, sg, sl in hist if self._k_ev(ev, sg) not in have]
                got = None
                if new and (c.quote_mint or QE.SOL) != QE.SOL:
                    got = self.qp.get(c.quote_mint)
                    if got is None:
                        rec["skipped"] = "quote_price_unavailable"
                        new = []
                trs = []
                for o, ev, sg, sl in new:
                    if got:
                        QE.to_sol_equiv(ev, got[0], got[1], self.sol.px)
                    tr = self.make_tr(c, ev, sg, sl, t, True)
                    if tr is not None:
                        tr["_o"] = o
                        trs.append(tr)
                if trs:
                    old = c.trades
                    for i, x in enumerate(old):
                        x["_o"] = (x["slot"] or 0, -1, i)
                    allt = sorted(old + trs, key=lambda x: x["_o"])
                    ids = {id(x) for x in trs}
                    first_new = next(i for i, x in enumerate(allt) if id(x) in ids)
                    for i, x in enumerate(allt):
                        x.pop("_o", None)
                        if i >= first_new:
                            x["recv"] = max(x["recv"], t)   # actionable only once the backfill landed
                    c.trades, c.wallets, c.cum_vol, c.n_backfilled = [], set(), 0.0, 0
                    c.peak = c.peak_t = None
                    c.armed = {T: True for T in THRESHOLDS}
                    c.opening, c.mid25, c.scalp = set(), False, None
                    for x in allt:
                        self.seq += 1
                        x["seq"] = x["block_time"] = self.seq
                        self.push_tr(c, x)
                rec["added"] = len(trs)
            c.backfill = (c.backfill + ";" if c.backfill else "") + f"{reason}:{rec.get('skipped') or 'ok'}:+{rec.get('added', 0)}"
        append_jsonl("backfills.jsonl", rec)
        self.bump(f"backfill:{reason}")
        log(f"BACKFILL {reason} {c.symbol} {c.mint[:8]} t+{rec['t_plus']}s txs={rec['txs']} added={rec.get('added', 0)}"
            f"{' skipped=' + rec['skipped'] if rec.get('skipped') else ''}{' err=' + str(err) if err else ''}")

    def backfill_young(self, since: float, why: str):
        t = now()
        n = 0
        for c in list(self.coins.values()):
            if not c.finalized and t - c.t0 < WATCH_SEC and not c.awaiting and not c.skip_reason:
                self.request_backfill(c, max(c.t0, since), t, why)
                n += 1
        if n:
            log(f"{why} > {BACKFILL_GAP_S:.0f}s: backfill queued for {n} young coins")

    async def quote_loop(self):
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(1.0)
            try:
                due = self.qp.due()
                if due:
                    await loop.run_in_executor(None, self.qp.refresh, due)
                t = now()
                for c in list(self.coins.values()):
                    if c.qbuf and not c.skip_reason and not c.finalized:
                        got = self.qp.get(c.quote_mint)
                        if got and self.sol.px:
                            self.flush_qbuf(c, got)
                        elif c.qwait and t - c.qwait > QUOTE_WAIT_S:
                            self.skip_coin(c, f"quote_price_unavailable:{self.qp.symbol(c.quote_mint)}:{self.qp.status(c.quote_mint)}")
                for m, a in list(self.adopt.items()):
                    if a["state"] == "new" and t - a["first"] > 120:
                        self.adopt.pop(m, None)
            except Exception:
                append_jsonl("errors.jsonl", {"ts": eat(now()), "where": "quote_loop", "tb": traceback.format_exc()[-1500:]})

    # ---------------- signal logic (mirrors first_live_touches + score) ----------------
    def feed_lag_now(self, window: float = 10.0):
        t = now()
        v = [l for r, l in self.lag_recent if r >= t - window]
        return round(pct(v, 0.5), 3) if v else None

    def vol_active(self, c: Coin, t: int):
        lo = t - R.ACTIVE_LOOKBACK_SEC
        n = 0
        usd = 0.0
        for tr in reversed(c.trades):
            tp = tr["t_plus_sec"]
            if tp < lo - 3:
                break
            if lo <= tp <= t:
                n += 1
                usd += tr["volume_usd"] or 0
        return (n >= R.ACTIVE_MIN_TRADES and usd >= R.ACTIVE_MIN_USD), n, usd

    def check_signal(self, c: Coin, tr: dict):
        px = tr["price_usd"]
        if not px:
            return
        if c.peak is None or px >= c.peak:
            c.peak, c.peak_t = px, tr["t_plus_sec"]
            c.armed = {T: True for T in THRESHOLDS}
            return
        dd = 1 - px / c.peak
        t = tr["t_plus_sec"]
        if not any(c.armed[T] and dd >= T for T in THRESHOLDS):
            return
        active, n15, v15 = self.vol_active(c, t)
        if not active:
            return
        touched = [T for T in THRESHOLDS if c.armed[T] and dd >= T]
        for T in touched:
            c.armed[T] = False
        if 0 <= t <= 30:
            c.opening.update(touched)
            return
        if not (30 < t <= WATCH_SEC):
            return
        ent = [T for T in touched if T in ENTRY_TS]
        if not ent:
            c.mid25 = True
            return
        self.decide(c, tr, min(ent), dd, n15, v15)

    def decide(self, c: Coin, tr: dict, T: float, dd: float, n15: int, v15: float):
        t_dec = now()
        c.decided = True
        idx = len(c.trades) - 1
        n_tr = len(c.trades)
        n_w = len(c.wallets)
        mcap = tr["mcap_usd"]
        reasons = []
        if c.cum_vol < R.MIN_CUM_VOL:
            reasons.append("low_vol")
        if n_tr < R.MIN_CUM_TRADES:
            reasons.append("low_trades")
        if n_w < R.MIN_CUM_WALLETS:
            reasons.append("low_wallets")
        if mcap is None:
            reasons.append("mcap_missing")
        elif mcap < R.MCAP_MIN:
            reasons.append("mcap_low")
        elif mcap > R.MCAP_MAX:
            reasons.append("mcap_high")
        try:
            em = R.entry_setup_metrics(list(c.trades), tr, creator=c.creator)
        except Exception as e:
            em = {"error": f"entry_setup_metrics {type(e).__name__}"}
        top1 = em.get("entry_top1_seller_share")
        v0_pass = not reasons
        v11_skip = isinstance(top1, (int, float)) and top1 > TOP1_SKIP
        slot = tr["slot"]
        d = {
            "mint": c.mint, "name": c.name, "symbol": c.symbol, "creator": c.creator,
            "launch_ts": c.t0, "launch_time_eat": eat(c.t0),
            "signal_sig": tr["signature"], "signal_slot": slot,
            "signal_block_time": tr["bt"], "signal_block_time_eat": eat(tr["bt"]),
            "signal_recv": tr["recv"], "signal_recv_eat": eat(tr["recv"]),
            "decision_time_eat": eat(t_dec),
            "detect_latency_s_vs_blocktime": round(tr["recv"] - tr["bt"], 3),
            "detect_latency_ms_vs_slot_seen": round((tr["recv"] - self.slot_seen[slot]) * 1000, 1) if slot in self.slot_seen else None,
            "decision_proc_ms": round((t_dec - tr["recv"]) * 1000, 2),
            "feed_lag_med10s_at_signal": self.feed_lag_now(),
            "late": int(tr["recv"] - tr["bt"] > LATE_S),
            "entry_t_plus": tr["t_plus_sec"], "entry_threshold": int(round(T * 100)),
            "entry_drop_pct": round(dd * 100, 2), "running_high_usd": c.peak, "running_high_t_plus": c.peak_t,
            "signal_px_usd": tr["price_usd"], "signal_spot_usd": tr["spot_usd"], "signal_mcap_usd": round(mcap or 0, 2),
            "entry_15s_trades_live": n15, "entry_15s_vol_live": round(v15, 2),
            "entry_cum_vol": round(c.cum_vol, 2), "entry_cum_trades": n_tr, "entry_cum_wallets": n_w,
            "obs_trades": n_tr, "obs_wallets": n_w,
            "flag_mcap_lt_12k": int(bool(mcap and mcap < 12000)), "flag_t_lt_200": int(tr["t_plus_sec"] < 200),
            "entry_top1_seller_share": top1, "entry_top2_seller_share": em.get("entry_top2_seller_share"),
            "entry_secs_from_high": em.get("entry_secs_from_high"), "entry_drop_sells": em.get("entry_drop_sells"),
            "entry_dev_sold_in_drop": em.get("entry_dev_sold_in_drop"),
            "entry_10s_buy_sell_ratio": em.get("entry_10s_buy_sell_ratio"),
            "sol_usd": tr["sol_usd"], "fee_bps_observed": tr["fee_bps_total"],
            "v0_pass": int(v0_pass), "v11_skip_top1": int(bool(v11_skip)),
            "skip_reasons": ",".join(reasons + (["v11_top1_seller>0.5"] if v0_pass and v11_skip else [])),
            "opening_touch": ",".join(str(int(x * 100)) for x in sorted(c.opening)),
            "feed_gap_before_signal": int(c.gap),
            "quote_usd_at_signal": tr.get("quote_usd"), "signal_trade_backfilled": int(bool(tr.get("backfill"))),
            **self.coin_tags(c),
        }
        c.decision = d
        if v0_pass and not v11_skip:
            d["status"] = "SIGNAL"
            self.bump("signals_v11", ts=tr["bt"])
        elif v0_pass:
            d["status"] = "SHADOW_v11_skip"
            self.bump("skip:v11_top1_seller", ts=tr["bt"])
        else:
            d["status"] = "SKIP"
            for r in reasons:
                self.bump("skip:" + r, ts=tr["bt"])
        self.bump("dip_candidates", ts=tr["bt"])
        append_jsonl("decisions.jsonl", d)
        if v0_pass:
            # paper-track v1.1 signals AND v1.1-skipped v0 passers (labelled) for execution stats
            c.signal = d
            c.sig_idx = idx
            log(f"{d['status']} {c.symbol} {c.mint[:8]} t+{tr['t_plus_sec']}s drop {dd*100:.1f}% "
                f"mcap ${mcap:,.0f} lat {d['detect_latency_s_vs_blocktime']}s")
        if not v0_pass and c.h_sub is not None and c.mint not in self.cheap_subs:
            asyncio.get_running_loop().create_task(self.h_unsubscribe(c))

    # ---------------- Helius latency probe ----------------
    # ---------------- cheap Helius mode (lag-triggered, per-curve subscriptions) ----------------
    def subs_budget_ok(self):
        u = self._usage()
        hk = hour_key(now())
        sh = u.setdefault("probe_hour", {})
        for k in list(sh):
            if k != hk:
                sh.pop(k, None)
        if u["probe"] > HELIUS_DAILY_MB * 1e6:
            return False, "daily_cap"
        if sh.get(hk, 0) > HELIUS_SUBS_HOURLY_MB * 1e6:
            return False, "hourly_cap"
        return True, ""

    def lag_switch_tick(self):
        t = now()
        ls = self.lag_state
        if t - self.wd_tick > 3 or t - self.resumed_at < 15:
            return
        lag = self.public_lag_med()
        ls["public_lag_med10s"] = None if lag is None else round(lag, 2)
        if lag is not None and lag > LAG_ON_S:
            ls["bad_since"] = ls["bad_since"] or t
            ls["good_since"] = None
        elif lag is not None and lag < LAG_OFF_S:
            ls["good_since"] = ls["good_since"] or t
            ls["bad_since"] = None
        elif lag is not None:
            ls["bad_since"] = ls["good_since"] = None
        ok, why = self.subs_budget_ok()
        if self.cheap_on and not ok:
            self.cheap_on = False
            append_jsonl("health.jsonl", {"ts": eat(t), "event": "cheap_off", "reason": "budget_" + why})
            log(f"Helius cheap mode OFF: {why} reached")
        elif not self.cheap_on and ok and ls["bad_since"] and t - ls["bad_since"] >= LAG_ON_FOR_S:
            self.cheap_on = True
            ls["lag_on"] += 1
            ls["on_at"] = t
            ls["switch_reason"] = f"public median lag {lag:.1f}s > {LAG_ON_S:g}s for {t - ls['bad_since']:.0f}s"
            append_jsonl("health.jsonl", {"ts": eat(t), "event": "cheap_on", "reason": ls["switch_reason"]})
            log(ls["switch_reason"] + f" -> Helius cheap mode ON (per-curve subs, KEY env {HELIUS_KEY_ENV})")
        elif self.cheap_on and ls["good_since"] and t - ls["good_since"] >= LAG_OFF_FOR_S:
            self.cheap_on = False
            ls["lag_off"] += 1
            append_jsonl("health.jsonl", {"ts": eat(t), "event": "cheap_off", "reason": "public recovered",
                                          "on_s": round(t - (ls["on_at"] or t), 1)})
            log(f"public median lag {lag:.1f}s < {LAG_OFF_S:g}s for {LAG_OFF_FOR_S:.0f}s -> Helius cheap mode OFF")

    def cheap_targets(self) -> list:
        t = now()
        must, cand = [], []
        for c in self.coins.values():
            if c.finalized or c.skip_reason or c.awaiting or not c.curve:
                continue
            if c.signal is not None or (c.scalp and c.scalp.get("pos")):
                must.append(c)
                continue
            age = t - c.t0
            if CHEAP_T_MIN <= age <= CHEAP_T_MAX and len(c.trades) >= CHEAP_MIN_TRADES and c.cum_vol >= CHEAP_MIN_VOL:
                cand.append(c)
        cand.sort(key=lambda c: -c.cum_vol)
        return (must + cand)[:CHEAP_MAX_SUBS]

    async def cheap_loop(self):
        force = PAPER / "force_cheap"   # test hook: `echo 300 > paper/force_cheap` keeps cheap mode on for 300s
        while True:
            await asyncio.sleep(1.0)
            try:
                self.lag_switch_tick()
                forced = False
                try:
                    forced = now() < force.stat().st_mtime + float(force.read_text().strip() or 60)
                except Exception:
                    pass
                if forced and not self.cheap_on and self.subs_budget_ok()[0]:
                    self.cheap_on = True
                    self.lag_state["switch_reason"] = "forced test"
                    self.lag_state["on_at"] = now()
                    append_jsonl("health.jsonl", {"ts": eat(now()), "event": "cheap_on", "reason": "forced test"})
                    log("Helius cheap mode ON (forced test)")
                want = {c.mint for c in self.cheap_targets()} if self.cheap_on else set()
                for m in list(self.cheap_subs):
                    if m not in want:
                        self.cheap_subs.discard(m)
                        c = self.coins.get(m)
                        if c is not None and c.h_sub is not None and not (c.hot and not c.decided):
                            await self.h_unsubscribe(c)
                for m in want:
                    c = self.coins.get(m)
                    if c is None:
                        continue
                    if m not in self.cheap_subs:
                        self.cheap_subs.add(m)
                        self.band.mark_cheap(m)
                    if c.h_sub is None and m not in self.h_pending.values():
                        await self.h_subscribe(c, cheap=True)
                self.cheap_meter.append((now(), self._usage()["probe"]))
                self.cheap_meter = self.cheap_meter[-900:]
            except Exception:
                append_jsonl("errors.jsonl", {"ts": eat(now()), "where": "cheap_loop", "tb": traceback.format_exc()[-1500:]})

    def subs_mb_per_min(self, window=120):
        m = [x for x in self.cheap_meter if x[0] >= now() - window]
        if len(m) < 2 or m[-1][0] - m[0][0] < 20:
            return None
        return round((m[-1][1] - m[0][1]) / 1e6 / ((m[-1][0] - m[0][0]) / 60), 3)

    async def helius_feed(self):
        key = os.environ.get(HELIUS_KEY_ENV)
        if not key:
            log("helius probe disabled (no key)")
            return
        backoff = 2.0
        while True:
            try:
                async with websockets.connect("wss://mainnet.helius-rpc.com/?api-key=" + key, max_size=2 ** 22,
                                              ping_interval=30, ping_timeout=20, open_timeout=15) as ws:
                    self.h_ws = ws
                    self.hfeed["connected"] = True
                    self.h_subid.clear()
                    self.h_pending.clear()
                    for c in self.coins.values():
                        c.h_sub = None
                    log("helius probe connected")
                    backoff = 2.0
                    async for raw in ws:
                        r = now()
                        self.account_hbytes(len(raw))
                        m = json.loads(raw)
                        if "id" in m:
                            mint = self.h_pending.pop(m["id"], None)
                            if mint and "result" in m and isinstance(m["result"], int):
                                self.h_subid[m["result"]] = mint
                                c = self.coins.get(mint)
                                if c and not c.finalized and (c.signal is not None or not c.decided or mint in self.cheap_subs):
                                    c.h_sub = m["result"]
                                elif c:
                                    c.h_sub = m["result"]
                                    asyncio.get_running_loop().create_task(self.h_unsubscribe(c))
                                else:
                                    self.h_subid.pop(m["result"], None)
                                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsUnsubscribe", "params": [m["result"]]}))
                            elif mint:
                                self.hfeed["last_error"] = str(m.get("error"))[:120]
                            continue
                        p = m.get("params") or {}
                        mint = self.h_subid.get(p.get("subscription"))
                        if not mint:
                            continue
                        self.hfeed["msgs"] += 1
                        val = (p.get("result") or {}).get("value") or {}
                        sig = val.get("signature")
                        if val.get("err") is not None or not any(
                                l.startswith("Program data: " + TRADE_B64) for l in (val.get("logs") or [])):
                            continue
                        c = self.coins.get(mint)
                        if c and sig:
                            c.h_recv.setdefault(sig, r)
                            if sig not in self.seen:      # Helius first: feed it into the tape (dedupe by signature)
                                self.hfeed["first_wins"] = self.hfeed.get("first_wins", 0) + 1
                                self.on_logs(p["result"], r)
                            else:
                                # if public already delivered it, attach now
                                for tr in reversed(c.trades[-50:]):
                                    if tr["signature"] == sig:
                                        tr.setdefault("h_recv", r)
                                        break
                            bt0 = self.sig_bt.get(sig)
                            if bt0 is not None:
                                self.feed_lag["helius_subs"].append((r, r - bt0))
            except Exception as e:
                self.hfeed["last_error"] = f"{type(e).__name__}: {str(e)[:120]}"
                self.hfeed["connected"] = False
                self.hfeed["reconnects"] += 1
                self.h_ws = None
                log(f"helius probe dropped: {self.hfeed['last_error']}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    def account_hbytes(self, n):
        day = datetime.now(NBO).strftime("%Y-%m-%d")
        if self.hfeed["day"] != day:
            self.hfeed["day"] = day
            self.hfeed["bytes_today"] = 0
        self.hfeed["bytes_today"] += n
        u = self._usage()
        u["probe"] += n
        ph = u.setdefault("probe_hour", {})
        hk_ = hour_key(now())
        ph[hk_] = ph.get(hk_, 0) + n
        hb = self.hfeed.setdefault("hour_bytes", {})
        hk = hour_key(now())
        hb[hk] = hb.get(hk, 0) + n
        if len(hb) > 30:
            for k in sorted(hb)[:-30]:
                hb.pop(k, None)

    async def h_subscribe(self, c: Coin, cheap: bool = False):
        ws = self.h_ws
        if ws is None:
            return
        if not self.subs_budget_ok()[0]:
            self.hfeed["skipped_budget"] += 1
            return
        cap = HELIUS_MAX_SUBS + (CHEAP_MAX_SUBS if self.cheap_on else 0)
        if len(self.h_subid) + len(self.h_pending) >= cap:
            self.hfeed["skipped_full"] += 1
            return
        self.h_req += 1
        self.h_pending[self.h_req] = c.mint
        self.hfeed["subs_total"] += 1
        try:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": self.h_req, "method": "logsSubscribe",
                                      "params": [{"mentions": [c.curve]}, {"commitment": "processed"}]}))
        except Exception:
            self.h_pending.pop(self.h_req, None)

    async def h_unsubscribe(self, c: Coin):
        sid = c.h_sub
        if sid is None:
            return
        self.h_subid.pop(sid, None)
        c.h_sub = None
        ws = self.h_ws
        if ws is None:
            return
        self.h_req += 1
        try:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": self.h_req, "method": "logsUnsubscribe", "params": [sid]}))
        except Exception:
            pass

    # ---------------- simulation helpers ----------------
    @staticmethod
    def state_at(c: Coin, wall: float, min_idx: int) -> int:
        """Index of the last trade received at or before `wall` (never before min_idx)."""
        j = min_idx
        for i in range(min_idx, len(c.trades)):
            if c.trades[i]["recv"] <= wall:
                j = i
            else:
                break
        return j

    @staticmethod
    def buy_fill(tr: dict, usd: float = 1.0):
        sol_usd = tr["sol_usd"]
        vs, vt = tr["vs"], tr["vt"]
        net_lamports = usd * (1 - FEE) / sol_usd * 1e9
        tok_raw = vt - (vs * vt) / (vs + net_lamports)
        tok = tok_raw / 1e6
        eff = usd * (1 - FEE) / tok  # USD per token, net of fee (backtest convention)
        return tok, eff

    @staticmethod
    def sell_fill(tr: dict, tok: float):
        vs, vt = tr["vs"], tr["vt"]
        tok_raw = tok * 1e6
        sol_out = (vs - (vs * vt) / (vt + tok_raw)) / 1e9
        gross = sol_out * tr["sol_usd"]
        return gross, gross / tok

    def simulate(self, c: Coin) -> dict:
        d = dict(c.signal)
        sidx = c.sig_idx
        st = c.trades[sidx]
        sig_recv = st["recv"]
        sig_spot = st["spot_usd"]
        sig_px = st["price_usd"]
        peak = d["running_high_usd"]
        T = d["entry_threshold"] / 100.0
        fills = {}
        for dl in DELAYS:
            j = self.state_at(c, sig_recv + dl, sidx)
            ft = c.trades[j]
            between = c.trades[sidx + 1:j + 1]
            tok, eff = self.buy_fill(ft)
            hi_between = max([b["price_usd"] or 0 for b in between] + [0])
            lo_between = min([b["price_usd"] for b in between if b["price_usd"]] + [sig_px])
            key = f"d{int(dl*1000)}"
            fills[key] = ft
            d.update({
                f"fill_{key}_trades_between": len(between),
                f"fill_{key}_buys_between": sum(1 for b in between if b["side"] == "buy"),
                f"fill_{key}_vol_between": round(sum(b["volume_usd"] for b in between), 2),
                f"fill_{key}_spot_usd": ft["spot_usd"],
                f"fill_{key}_price_usd": eff,
                f"fill_{key}_mcap_usd": round(ft["mcap_usd"] or 0, 2),
                f"fill_{key}_spot_move_pct": round((ft["spot_usd"] / sig_spot - 1) * 100, 3),
                f"fill_{key}_impact_pct": round((eff / ft["spot_usd"] - 1) * 100, 4),
                f"fill_{key}_slip_vs_signal_spot_pct": round((eff / sig_spot - 1) * 100, 3),
                f"fill_{key}_slip_vs_signal_px_pct": round((eff / sig_px - 1) * 100, 3),
                f"fill_{key}_drop_from_high_pct": round((1 - ft["spot_usd"] / peak) * 100, 2),
                f"fill_{key}_still_in_dip": int((1 - ft["spot_usd"] / peak) >= T),
                f"fill_{key}_new_high_before_fill": int(hi_between >= peak),
                f"fill_{key}_max_move_between_pct": round((hi_between / sig_px - 1) * 100, 2) if hi_between else 0.0,
                f"fill_{key}_min_move_between_pct": round((lo_between / sig_px - 1) * 100, 2),
                f"fill_{key}_tokens": tok,
            })
        # ---- exits from the primary (1s) fill ----
        pk = f"d{int(PRIMARY_DELAY*1000)}"
        ft = fills[pk]
        fidx = ft["seq"]
        tok, eff = self.buy_fill(ft)
        entry = {"price_usd": eff, "t_plus_sec": ft["t_plus_sec"], "block_time": ft["seq"], "slot": 10 ** 15,
                 "signature": "", "sol_usd": ft["sol_usd"]}
        after = [x for x in c.trades if x["seq"] > fidx]
        cap_t = ft["t_plus_sec"] + POST_SEC
        win = [x for x in after if x["t_plus_sec"] <= cap_t and x["price_usd"]]
        g = gas_usd(ft["sol_usd"])
        d["entry_primary"] = pk
        d["entry_px_usd"] = eff
        d["entry_t_plus_fill"] = ft["t_plus_sec"]
        d["post_trades_5m"] = len(win)
        if win:
            mx = max(win, key=lambda x: x["price_usd"])
            mn = min(win, key=lambda x: x["price_usd"])
            d["mfe_pct"] = round((mx["price_usd"] / eff - 1) * 100, 2)
            d["mfe_secs"] = mx["t_plus_sec"] - ft["t_plus_sec"]
            d["mae_pct"] = round((mn["price_usd"] / eff - 1) * 100, 2)
            d["mae_secs"] = mn["t_plus_sec"] - ft["t_plus_sec"]
        for m, tag in ((1.7, "17"), (1.8, "18")):
            hit = next((x for x in win if x["price_usd"] >= eff * m), None)
            d[f"tp{tag}_touched"] = int(hit is not None)
            d[f"tp{tag}_touch_secs"] = (hit["t_plus_sec"] - ft["t_plus_sec"]) if hit else None
            d[f"tp{tag}_touch_wall_s"] = round(hit["recv"] - ft["recv"], 2) if hit else None
            sig_hit = next((x for x in c.trades[sidx + 1:] if x["price_usd"] and x["price_usd"] >= sig_px * m
                            and x["t_plus_sec"] <= st["t_plus_sec"] + POST_SEC), None)
            d[f"tp{tag}_touch_secs_vs_signal_px"] = (sig_hit["t_plus_sec"] - st["t_plus_sec"]) if sig_hit else None
        specs = [("tp17", dict(tp_mult=1.7, sl=None, time_stop_sec=300)),
                 ("tp18", dict(tp_mult=1.8, sl=None, time_stop_sec=300))]
        specs += [(s["key"], s["kwargs"]) for s in R.DYNAMIC_SPECS]
        exits = {}
        first_dyn = None
        for key, kw in specs:
            try:
                res = R.exit_path(c.trades, entry, **kw)
            except Exception as e:
                exits[key] = {"exit": f"error:{type(e).__name__}"}
                continue
            rec = {"exit": res.get("exit"), "secs": res.get("secs"), "trigger_mult": res.get("mult"),
                   "net_at_trigger": res.get("net")}
            trig = None
            if res.get("secs") is not None and res.get("mult"):
                tt = ft["t_plus_sec"] + res["secs"]
                tp_target = eff * res["mult"]
                trig = next((x for x in after if x["t_plus_sec"] == tt and x["price_usd"]
                             and abs(x["price_usd"] / tp_target - 1) < 1e-7), None)
            if trig is not None:
                tidx = c.trades.index(trig)
                rec["trigger_wall_after_fill_s"] = round(trig["recv"] - ft["recv"], 2)
                for dl in DELAYS:
                    j = self.state_at(c, trig["recv"] + dl, tidx)
                    xt = c.trades[j]
                    gross, epx = self.sell_fill(xt, tok)
                    net = gross * (1 - FEE) - 1.0 - g
                    k2 = f"d{int(dl*1000)}"
                    rec[f"exit_{k2}_slip_pct"] = round((epx / trig["price_usd"] - 1) * 100, 3)
                    rec[f"exit_{k2}_trades_between"] = j - tidx
                    rec[f"exit_{k2}_net"] = round(net, 4)
                    rec[f"exit_{k2}_mult"] = round(epx / eff, 4)
            else:
                rec["trigger_not_matched"] = 1
            exits[key] = rec
            ex = rec.get("exit") or ""
            if key not in ("tp17", "tp18") and rec.get("secs") is not None and not ex.startswith("time_stop") \
                    and not ex.startswith("take_profit"):
                if first_dyn is None or rec["secs"] < first_dyn[1]:
                    first_dyn = (key, rec["secs"], ex)
        d["exits"] = exits
        d["first_dynamic_exit"] = first_dyn[0] if first_dyn else "none_before_tp_or_cap"
        d["first_dynamic_exit_reason"] = first_dyn[2] if first_dyn else ""
        d["first_dynamic_exit_secs"] = first_dyn[1] if first_dyn else None
        # Helius vs public latency on this coin's tape
        hl = [x["recv"] - x["h_recv"] for x in c.trades if x.get("h_recv")]
        d["helius_probe_trades"] = len(hl)
        pub = {x["signature"] for x in c.trades}
        hs = set(c.h_recv)
        d["helius_trade_sigs"] = len(hs)
        d["public_coverage_vs_helius"] = round(len(hs & pub) / len(hs), 4) if hs else None
        d["helius_minus_public_med_ms"] = round(-statistics.median(hl) * 1000, 1) if hl else None
        sh = st.get("h_recv")
        d["helius_signal_latency_s_vs_blocktime"] = round(sh - st["bt"], 3) if sh else None
        d["migrated_during_track"] = int(c.migrated is not None)
        d["feed_gap_during_track"] = int(c.gap)
        d["finalized_eat"] = eat(now())
        return d

    def write_signal_rows(self, d: dict):
        append_jsonl("signals.jsonl", d)
        flat = {k: v for k, v in d.items() if k != "exits"}
        for key, rec in (d.get("exits") or {}).items():
            flat[f"{key}_exit"] = rec.get("exit")
            flat[f"{key}_secs"] = rec.get("secs")
            flat[f"{key}_net_trigger"] = rec.get("net_at_trigger")
            flat[f"{key}_net_d1000"] = rec.get("exit_d1000_net")
            flat[f"{key}_slip_d400"] = rec.get("exit_d400_slip_pct")
            flat[f"{key}_slip_d1000"] = rec.get("exit_d1000_slip_pct")
            flat[f"{key}_slip_d2000"] = rec.get("exit_d2000_slip_pct")
        p = LOGS / "signals.csv"
        cols = None
        if p.exists():
            with p.open() as f:
                cols = next(csv.reader(f), None)
        if not cols:
            cols = list(flat.keys())
            with p.open("w", newline="") as f:
                csv.writer(f).writerow(cols)
        extra = [k for k in flat if k not in cols]
        if extra:  # rare: rewrite header to include new columns
            rows = list(csv.DictReader(p.open()))
            cols = cols + extra
            with p.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    w.writerow(r)
        with p.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=cols, extrasaction="ignore").writerow(flat)

    def write_shadow_row(self, d: dict):
        append_jsonl("shadow_trades.jsonl", d)
        self.bump("scalp_finalized:" + d.get("track", "?"))
        self.recent_shadow = (self.recent_shadow + [{k: d.get(k) for k in (
            "track", "symbol", "sig_recv_eat", "exit_reason", "net_per1", "entry_d1000_slip_pct")}])[-20:]
        log(f"FINAL SHADOW {d.get('track')} {d.get('symbol')} exit={d.get('exit_reason')} hold={d.get('hold_s_wall')}s "
            f"net$1={d.get('net_per1')} $3={d.get('net_per3')} $5={d.get('net_per5')} entry slip1s={d.get('entry_d1000_slip_pct')}%")
        p = LOGS / "shadow_trades.csv"
        cols = None
        if p.exists():
            with p.open() as f:
                cols = next(csv.reader(f), None)
        if not cols:
            cols = list(d.keys())
            with p.open("w", newline="") as f:
                csv.writer(f).writerow(cols)
        extra = [k for k in d if k not in cols]
        if extra:
            rows = list(csv.DictReader(p.open()))
            cols = cols + extra
            with p.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    w.writerow(r)
        with p.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=cols, extrasaction="ignore").writerow(d)

    async def shadow_loop(self):
        while True:
            await asyncio.sleep(0.25)
            t1 = time.perf_counter()
            try:
                self.shadow.tick()
            except Exception:
                append_jsonl("errors.jsonl", {"ts": eat(now()), "where": "shadow_tick", "tb": traceback.format_exc()[-1500:]})
            ms = (time.perf_counter() - t1) * 1000
            self.shadow_ms.append(ms)
            if len(self.shadow_ms) > 2000:
                self.shadow_ms = self.shadow_ms[-1000:]

    def dump_tape(self, c: Coin):
        try:
            d = LOGS / "tapes"
            d.mkdir(exist_ok=True)
            keep = ("signature", "slot", "bt", "t_plus_sec", "side", "trader", "sol_ui", "token_ui", "price_usd",
                    "spot_usd", "mcap_usd", "volume_usd", "vs", "vt", "recv", "h_recv", "fee_bps_total",
                    "recv_raw", "backfill", "quote_usd")
            blob = {"mint": c.mint, "symbol": c.symbol, "t0": c.t0, "creator": c.creator, "curve": c.curve,
                    **self.coin_tags(c),
                    "decision": c.decision, "gap": c.gap, "helius_sigs": list(c.h_recv.items()),
                    "trades": [{k: x.get(k) for k in keep} for x in c.trades]}
            with gzip.open(d / f"{c.mint}.json.gz", "wt") as f:
                json.dump(blob, f)
        except Exception:
            pass

    # ---------------- housekeeping ----------------
    async def finalizer(self):
        while True:
            await asyncio.sleep(1.0)
            t = now()
            done = []
            for mint, c in self.coins.items():
                if SS.ScalpShadow.pending(c):
                    continue
                if c.signal is not None:
                    if t > c.signal["signal_recv"] + PRIMARY_DELAY + POST_SEC + 15:
                        done.append(c)
                elif t > c.t0 + WATCH_SEC + 8:
                    done.append(c)
            try:
                self.band.tick()
            except Exception:
                append_jsonl("errors.jsonl", {"ts": eat(now()), "where": "band_tick", "tb": traceback.format_exc()[-1500:]})
            for c in done:
                c.finalized = True
                try:
                    self.band.on_finalize(c)   # band coins keep recording (tail) to t+600 before their tape is written
                except Exception:
                    append_jsonl("errors.jsonl", {"ts": eat(now()), "where": "band_finalize", "tb": traceback.format_exc()[-1500:]})
                try:
                    if c.signal is not None:
                        d = self.simulate(c)
                        d.update(self.coin_tags(c))
                        self.write_signal_rows(d)
                        self.recent_signals = (self.recent_signals + [{
                            "status": d["status"], "symbol": d["symbol"], "mint": d["mint"],
                            "signal_eat": d["signal_recv_eat"],
                            "tp17": d["exits"].get("tp17", {}).get("exit"),
                            "net_tp17_d1000": d["exits"].get("tp17", {}).get("exit_d1000_net"),
                            "entry_slip_d1000": d.get("fill_d1000_slip_vs_signal_spot_pct")}])[-20:]
                        self.bump("finalized_signals")
                        log(f"FINAL {d['status']} {d['symbol']} tp17={d['exits'].get('tp17',{}).get('exit')} "
                            f"net(1s fills)={d['exits'].get('tp17',{}).get('exit_d1000_net')} "
                            f"entry slip 1s={d.get('fill_d1000_slip_vs_signal_spot_pct')}%")
                    elif not c.decided:
                        n = len(c.trades)
                        if n < 50:
                            r = "dead_lt50_trades"
                        elif c.mid25:
                            r = "mid_25_only"
                        elif c.opening & set(ENTRY_TS):
                            r = "opening_30plus_only"
                        else:
                            r = "no_mid_live_30"
                        self.bump("nodip:" + r, ts=c.t0)
                    self.bump("coins_finalized", ts=c.t0)
                except Exception:
                    append_jsonl("errors.jsonl", {"ts": eat(now()), "mint": c.mint, "tb": traceback.format_exc()[-1500:]})
                try:
                    for row in self.shadow.finalize(c):
                        row.update(self.coin_tags(c))
                        self.write_shadow_row(row)
                except Exception:
                    append_jsonl("errors.jsonl", {"ts": eat(now()), "mint": c.mint, "where": "shadow",
                                                  "tb": traceback.format_exc()[-1500:]})
                if c.decided or c.h_recv or (c.scalp and c.scalp.get("pos")):
                    self.dump_tape(c)
                if c.h_recv:
                    pubs = {x["signature"]: x["recv"] for x in c.trades}
                    both = [pubs[sg] - hr for sg, hr in c.h_recv.items() if sg in pubs]
                    self.probe_cov.append((len(c.h_recv), len(both)))
                    self.probe_ahead.extend(both[-500:])
                    self.probe_ahead = self.probe_ahead[-20000:]
                if c.h_sub is not None:
                    await self.h_unsubscribe(c)
                try:
                    tg = self.coin_tags(c)
                    append_jsonl("coins.jsonl", {"ts": eat(now()), "mint": c.mint, "symbol": c.symbol, "t0": c.t0,
                                                 **tg, "n_trades": len(c.trades), "decided": int(c.decided),
                                                 "status": (c.decision or {}).get("status", ""),
                                                 "shadow_pos": len((c.scalp or {}).get("pos") or {})})
                    self.bump("coins_quote:" + tg["quote_symbol"], ts=c.t0)
                except Exception:
                    pass
                self.mark_done(c.mint)
                del self.coins[c.mint]

    async def reporter(self):
        last_min = int(now() // 60)
        while True:
            await asyncio.sleep(5)
            t = now()
            lag = self.lags[-5000:]
            st = {
                "updated_eat": eat(t), "pid": os.getpid(), "uptime_min": round((t - self.start) / 60, 1),
                "mode": "PAPER ONLY - no wallets, no signing, no orders",
                "band_tapes": self.band.status(),
                "feed": self.feed_status(t),
                "helius_probe": {**self.hfeed, "active_subs": len(self.h_subid), "key_env": HELIUS_KEY_ENV,
                                 "mb_today": round(self.hfeed["bytes_today"] / 1e6, 2),
                                 "est_credits_today": round(self.hfeed["bytes_today"] / 1e6 * 20)},
                "sol_usd": self.sol.px, "sol_usd_age_s": round(t - self.sol.ts, 1) if self.sol.ts else None,
                "probe_coins": len(self.probe_cov),
                "public_coverage_vs_helius": round(sum(b for a, b in self.probe_cov) / max(1, sum(a for a, b in self.probe_cov)), 4) if self.probe_cov else None,
                "helius_ahead_of_public_ms_med": round(pct(self.probe_ahead, 0.5) * 1000, 1) if self.probe_ahead else None,
                "helius_ahead_of_public_ms_p90": round(pct(self.probe_ahead, 0.9) * 1000, 1) if self.probe_ahead else None,
                "coins_tracking": len(self.coins),
                "paper_positions_open": sum(1 for c in self.coins.values() if c.signal is not None),
                "lag_s_vs_blocktime_med": round(pct(lag, 0.5), 3) if lag else None,
                "lag_s_vs_blocktime_p90": round(pct(lag, 0.9), 3) if lag else None,
                "lag_ms_vs_slot_seen_med": round(pct(self.slot_lags[-5000:], 0.5), 1) if self.slot_lags else None,
                "proc_ms_p50": round(pct(self.proc_ms[-2000:], 0.5), 3) if self.proc_ms else None,
                "proc_ms_p99": round(pct(self.proc_ms[-2000:], 0.99), 3) if self.proc_ms else None,
                "this_hour": dict(self.hourly.get(hour_key(t), {})),
                "today": self._today_totals(),
                "recent_signals": self.recent_signals[-10:],
                "shadow_open": sum(len(c.scalp["pos"]) for c in self.coins.values() if c.scalp and not c.finalized),
                "shadow_tick_ms_p50": round(pct(self.shadow_ms[-500:], 0.5), 2) if self.shadow_ms else None,
                "shadow_tick_ms_p99": round(pct(self.shadow_ms[-500:], 0.99), 2) if self.shadow_ms else None,
                "recent_shadow": self.recent_shadow[-10:],
                "quote_trades_seen": dict(self.quote_trades),
                "quote_prices": {self.qp.symbol(m): {"usd": q.get("usd"), "age_s": round(t - q.get("ts", 0), 1),
                                                     "reliable": not q.get("reason"), "reason": q.get("reason")}
                                 for m, q in self.qp.p.items()},
                "coins_by_quote": dict(__import__("collections").Counter(self.qp.symbol(c.quote_mint) if c.quote_mint else "no_trades_yet" for c in self.coins.values())),
                "adoption": {**self.adopt_stats, "pending": len(self.adopt), "queue": self.adopt_q.qsize() if self.adopt_q else 0,
                             "backfill_queue": self.bf_q.qsize() if self.bf_q else 0,
                             "backfill_credits_today": self._usage().get("backfill_credits", 0),
                             "backfill_cap": BACKFILL_DAILY_CREDITS,
                             "hist_keys_parked": sorted(k for k, v in self.hist_dead.items() if v > t)},
                "helius_budget": self.helius_budget(t),
            }
            tmp = STATUS.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=1, default=str))
            tmp.replace(STATUS)
            m = int(t // 60)
            if m != last_min:
                last_min = m
                hrec = {"ts": eat(t), "event": "minute", **{k: round(v, 1) for k, v in self.health.items()},
                        "lag_med_s": st["lag_s_vs_blocktime_med"], "lag_p90_s": st["lag_s_vs_blocktime_p90"],
                        "slot_lag_med_ms": st["lag_ms_vs_slot_seen_med"],
                        "proc_ms_p99": st["proc_ms_p99"], "connected": st["feed"]["connected"],
                        "feeds_up": [n for n, f in self.feeds.items() if f["connected"]],
                        "coins_tracking": len(self.coins), "helius_mb_today": st["helius_probe"]["mb_today"],
                        "fb_on": int(bool((self.feeds.get("helius_fb") or {}).get("want"))),
                        "cheap_on": int(self.cheap_on), "cheap_subs": len(self.cheap_subs),
                        "subs_mb_today": round(self._usage()["probe"] / 1e6, 2),
                        "feed_lag_60s": {n: v["med"] for n, v in self.feed_lag_meds().items()},
                        "fb_mb_today": st["helius_budget"]["fallback_mb_today"],
                        "adopt_queue": st["adoption"]["queue"]}
                append_jsonl("health.jsonl", hrec)
                self.health.clear()
                self.lags = self.lags[-2000:]
                self.slot_lags = self.slot_lags[-2000:]
                self.write_hourly()

    def feed_status(self, t):
        anyl = max([f["last_msg"] for f in self.feeds.values()] + [0.0])
        u = self._usage()
        return {
            "connected": any(f["connected"] for f in self.feeds.values()),
            "last_msg_age_s": round(t - anyl, 2) if anyl else None,
            "reconnects": sum(f["reconnects"] for f in self.feeds.values()),
            "fallback_active": bool(self.feeds.get("helius_fb", {}).get("connected")),
            "fallback_mb_today": round(u["fallback"] / 1e6, 2), "fallback_cap_mb": HELIUS_FB_DAILY_MB,
            "probe_mb_today": round(u["probe"] / 1e6, 2),
            "recent_gaps": self.gaps[-5:], "recent_pauses": self.pauses[-5:],
            "feeds": {n: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.items()
                          if k not in ("ws", "last_msg", "conn_ts")} |
                      {"last_msg_age_s": round(t - f["last_msg"], 2) if f["last_msg"] else None}
                      for n, f in self.feeds.items()},
        }

    def _today_totals(self):
        day = datetime.now(NBO).strftime("%Y-%m-%d")
        tot = defaultdict(int)
        for h, v in self.hourly.items():
            if h.startswith(day):
                for k, n in v.items():
                    tot[k] += n
        return dict(tot)

    async def main(self):
        log(f"paper trader starting pid={os.getpid()} (PAPER ONLY, no wallets/signing)")
        append_jsonl("health.jsonl", {"ts": eat(now()), "event": "start", "pid": os.getpid()})
        asyncio.get_running_loop().create_task(self.sol.run())
        for _ in range(40):
            if self.sol.px:
                break
            await asyncio.sleep(0.25)
        self.adopt_q = asyncio.LifoQueue()   # newest unknown mints first (most likely still young)
        self.bf_q = asyncio.Queue()
        feeds = [self.run_feed(n, u) for n, u in self._feed_urls().items()]
        await asyncio.gather(*feeds, self.watchdog(), self.shadow_loop(), self.helius_feed(), self.finalizer(), self.reporter(),
                             self.quote_loop(), *[self.adopt_worker() for _ in range(ADOPT_WORKERS)], self.backfill_worker(),
                             self.cheap_loop())


def _sigterm(*_a):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sigterm)
    tr = Trader()
    try:
        asyncio.run(tr.main())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            tr.band.flush_all("shutdown")
        except Exception:
            pass
        try:
            tr.write_hourly()
        except Exception:
            pass
