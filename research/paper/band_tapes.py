"""Band-tape recorder (research only, PAPER ONLY; no wallets/signing, no extra RPC calls).

Saves the FULL live trade tape of every coin (SOL- or quote-token-paired) whose market cap range
touches the $5k-$10k band by t+300s, from first seen through t+600s (or graduation), including
coins that die.  Uses only trades the paper trader already receives on its existing feeds.

Files: logs/band_tapes/YYYY-MM-DD/<mint>.jsonl.gz  (EAT launch date)
         line 1 = {"_meta": {...coverage flags...}}, then one JSON object per trade (block-time order)
       logs/band_tapes/YYYY-MM-DD/index.jsonl     one line per saved tape (same meta)
Coverage flags are honest: feed gaps / process pauses overlapping the window, firehose-fallback
seconds, late-received trades, late adoption with/without backfill, coin launched before the
process started, and how the tape ended (t600 / graduated / shutdown).
"""
from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BAND_LO, BAND_HI = 5000.0, 10000.0
TOUCH_BY_S = 300          # band must be touched by t+300 (entry window)
TAPE_END_S = 600          # record through t+600
MAX_TAIL = 5000           # coins kept alive past finalization (memory bound)
MAX_TRADES = 30000        # per-coin trade cap
LATE_S = 5.0
NBO = timezone(timedelta(hours=3))


def _eat(ts):
    return datetime.fromtimestamp(ts, NBO).strftime("%Y-%m-%d %H:%M:%S")


class BandTapes:
    def __init__(self, trader, logs: Path, log=print):
        self.tr = trader
        self.dir = logs / "band_tapes"
        self.log = log
        self.tail: dict = {}          # mint -> Coin (finalized by the trader, still recording to t+600)
        self.state: dict = {}         # mint -> band state for live coins
        self.gap_log: list = []       # (start, end) feed gaps / process pauses
        self.fb_log: list = []        # (start, end) Helius firehose fallback on
        self._fb_on = None
        self.cheap_ever: dict = {}    # mint -> 1 if a per-curve Helius sub (cheap / hot probe) covered it
        self.stats = {"saved": 0, "bytes": 0, "trades": 0, "dropped_tail_full": 0, "tail_trades": 0,
                      "unpriced_tail": 0, "errors": 0}
        self.proc_start = time.time()

    # ---------- coverage bookkeeping (called from the trader's watchdog) ----------
    def add_gap(self, start, end):
        self.gap_log.append((start, end))
        self.gap_log = self.gap_log[-2000:]

    def fb_tick(self, on: bool, t: float):
        if on and self._fb_on is None:
            self._fb_on = t
        elif not on and self._fb_on is not None:
            self.fb_log.append((self._fb_on, t))
            self.fb_log = self.fb_log[-2000:]
            self._fb_on = None

    @staticmethod
    def _merged(iv, a, b):
        out = []
        for s, e in sorted((max(s, a), min(e, b)) for s, e in iv if e > a and s < b):
            if out and s <= out[-1][1]:
                out[-1][1] = max(out[-1][1], e)
            else:
                out.append([s, e])
        return out

    @staticmethod
    def _overlap(iv, a, b):
        """Seconds of [a, b] covered by the union of intervals (pause + feed-silence intervals overlap; merge first)."""
        tot, cur_s, cur_e = 0.0, None, None
        for s, e in sorted((max(s, a), min(e, b)) for s, e in iv if e > a and s < b):
            if cur_e is None or s > cur_e:
                if cur_e is not None:
                    tot += cur_e - cur_s
                cur_s, cur_e = s, e
            else:
                cur_e = max(cur_e, e)
        if cur_e is not None:
            tot += cur_e - cur_s
        return tot

    # ---------- live coins ----------
    def note(self, c, tr):
        """After every trade pushed onto a live coin's tape: track whether its mcap range hit the band."""
        m = tr.get("mcap_usd")
        if m is None:
            return
        st = self.state.get(c.mint)
        if st is None:
            st = self.state[c.mint] = {"lo": m, "hi": m, "touch_t": None, "inside": 0}
            if len(self.state) > 60000:
                for k in list(self.state)[:10000]:
                    if k not in self.tr.coins and k not in self.tail:
                        self.state.pop(k, None)
        st["lo"], st["hi"] = min(st["lo"], m), max(st["hi"], m)
        t = tr["t_plus_sec"]
        if BAND_LO <= m <= BAND_HI and t <= TOUCH_BY_S:
            st["inside"] += 1
        if st["touch_t"] is None and t <= TOUCH_BY_S and st["lo"] <= BAND_HI and st["hi"] >= BAND_LO:
            st["touch_t"] = t

    def mark_cheap(self, mint):
        self.cheap_ever[mint] = 1
        if len(self.cheap_ever) > 50000:
            for k in list(self.cheap_ever)[:10000]:
                self.cheap_ever.pop(k, None)

    def on_finalize(self, c):
        """Trader finalized a coin (t+~308 or later). Band coins keep recording to t+600; others are dropped."""
        st = self.state.get(c.mint)
        if not st or st["touch_t"] is None:
            self.state.pop(c.mint, None)
            return
        if time.time() >= c.t0 + TAPE_END_S or c.migrated is not None:
            self.write(c, "t600" if c.migrated is None else "graduated")
            return
        if len(self.tail) >= MAX_TAIL:
            self.stats["dropped_tail_full"] += 1
            self.write(c, "tail_full")
            return
        self.tail[c.mint] = c

    def on_tail_trade(self, ev, sig, slot, recv) -> bool:
        c = self.tail.get(ev.get("mint"))
        if c is None:
            return False
        if len(c.trades) >= MAX_TRADES:
            return True
        tr = None
        qm = ev.get("quote_mint")
        try:
            if qm == self.tr.QE_SOL:
                tr = self.tr.make_tr(c, ev, sig, slot, recv)
            elif qm:
                got = self.tr.qp.get(qm)
                if got is not None and self.tr.sol.px:
                    self.tr.QE.to_sol_equiv(ev, got[0], got[1], self.tr.sol.px)
                    tr = self.tr.make_tr(c, ev, sig, slot, recv)
        except Exception:
            tr = None
        if tr is None:      # price unavailable: keep the raw trade, flagged
            self.stats["unpriced_tail"] += 1
            tr = {"signature": sig, "slot": slot, "bt": ev.get("timestamp"), "t_plus_sec": int(ev.get("timestamp", 0)) - int(c.t0),
                  "side": "buy" if ev.get("is_buy") else "sell", "trader": ev.get("user"),
                  "token_ui": (ev.get("token_amount") or 0) / 1e6, "recv": recv, "recv_raw": recv, "unpriced": 1}
        if ev.get("quote_amount") is not None and qm != self.tr.QE_SOL:
            tr["quote_raw"] = ev.get("quote_amount")
        tr["tail"] = 1
        c.trades.append(tr)
        self.stats["tail_trades"] += 1
        return True

    def on_complete(self, mint, recv):
        c = self.tail.get(mint)
        if c is not None and c.migrated is None:
            c.migrated = recv

    def tick(self):
        t = time.time()
        for m, c in list(self.tail.items()):
            if t >= c.t0 + TAPE_END_S + 3:
                self.tail.pop(m, None)
                self.write(c, "t600")
            elif c.migrated is not None and t >= c.migrated + 10:
                self.tail.pop(m, None)
                self.write(c, "graduated")

    def flush_all(self, reason="shutdown"):
        for m, c in list(self.tail.items()):
            self.tail.pop(m, None)
            self.write(c, reason)

    # ---------- output ----------
    def write(self, c, end_reason):
        try:
            st = self.state.pop(c.mint, None) or {}
            t = time.time()
            end_t = min(t, c.t0 + TAPE_END_S)
            win_a = c.t0
            cur_gaps = list(self.gap_log)
            if getattr(self.tr, "in_gap", False):
                cur_gaps.append((self.tr.gap_start, t))
            fb = list(self.fb_log) + ([(self._fb_on, t)] if self._fb_on is not None else [])
            gap_s = self._overlap(cur_gaps, max(win_a, self.proc_start), end_t)
            gaps_tplus = [[round(x - c.t0, 1), round(y - c.t0, 1)] for x, y in self._merged(cur_gaps, win_a, end_t)]
            if c.t0 < self.proc_start - 2:
                gaps_tplus.insert(0, [0.0, round(self.proc_start - c.t0, 1)])   # process was not running yet
            fb_s = self._overlap(fb, win_a, end_t)
            trades = sorted(c.trades, key=lambda x: ((x.get("slot") or 0), x.get("bt") or 0, x.get("seq") or 0))
            qm = c.quote_mint or self.tr.QE_SOL
            dec = None
            if qm != self.tr.QE_SOL:
                g = self.tr.qp.get(qm)
                dec = g[1] if g else None
            rows, lags, n_late, n_bf, n_unpriced = [], [], 0, 0, 0
            for x in trades:
                bt, rr = x.get("bt"), x.get("recv_raw", x.get("recv"))
                lag = (rr - bt) if (rr is not None and bt is not None) else None
                late = int(lag is not None and lag > LATE_S)
                if x.get("backfill"):
                    n_bf += 1
                else:
                    if lag is not None:
                        lags.append(lag)
                    n_late += late
                n_unpriced += int(bool(x.get("unpriced")))
                qraw = x.get("quote_raw")
                quote_ui = (qraw / 10 ** dec) if (qraw is not None and dec is not None) else (x.get("sol_ui") if qm == self.tr.QE_SOL else None)
                rows.append({"bt": bt, "t": x.get("t_plus_sec"), "recv": x.get("recv"), "recv_raw": rr,
                             "lag": round(lag, 3) if lag is not None else None, "late": late,
                             "slot": x.get("slot"), "sig": x.get("signature"), "side": x.get("side"),
                             "wallet": x.get("trader"), "tok": x.get("token_ui"), "quote": quote_ui,
                             "sol_eq": x.get("sol_ui"), "usd": x.get("volume_usd"), "px_usd": x.get("price_usd"),
                             "mcap": x.get("mcap_usd"), "sol_usd": x.get("sol_usd"), "quote_usd": x.get("quote_usd"),
                             "vs": x.get("vs"), "vt": x.get("vt"), "fee_bps": x.get("fee_bps_total"),
                             "create_tx": x.get("is_create_tx"), "h_recv": x.get("h_recv"),
                             "backfill": int(bool(x.get("backfill"))), "tail": int(bool(x.get("tail"))),
                             "unpriced": int(bool(x.get("unpriced")))})
            n_live = len(rows) - n_bf
            lags.sort()
            pre_start = int(c.t0 < self.proc_start - 2)
            adopt_ok = (not c.late_adopted) or (":ok" in (c.backfill or "") and "adopt" in (c.backfill or ""))
            late_frac = (n_late / n_live) if n_live else 0.0
            first_t = rows[0]["t"] if rows else None
            flags = []
            if pre_start: flags.append("launched_before_process_start")
            if gap_s > 0: flags.append(f"feed_gap_{gap_s:.0f}s")
            if c.late_adopted and not adopt_ok: flags.append("late_adopted_no_backfill(early_trades_missing)")
            elif c.late_adopted: flags.append("late_adopted_backfilled")
            if late_frac > 0.10: flags.append(f"late_trades_{late_frac:.0%}")
            if end_reason in ("shutdown", "tail_full"): flags.append(f"truncated_{end_reason}")
            if n_unpriced: flags.append(f"unpriced_{n_unpriced}")
            if fb_s > 0: flags.append(f"firehose_fallback_{fb_s:.0f}s")
            complete = int(not pre_start and gap_s == 0 and adopt_ok and end_reason not in ("shutdown", "tail_full"))
            on_time = int(complete and late_frac <= 0.10)   # complete tape AND received in time (<=10% trades >5 s late)
            meta = {"mint": c.mint, "symbol": c.symbol, "name": c.name, "creator": c.creator, "curve": c.curve,
                    "t0": c.t0, "launch_eat": _eat(c.t0), "quote_mint": qm,
                    "quote_symbol": self.tr.qp.symbol(qm), "quote_decimals": dec,
                    "band_touch_t": st.get("touch_t"), "band_trades_inside": st.get("inside", 0),
                    "mcap_lo": st.get("lo"), "mcap_hi": st.get("hi"),
                    "n_trades": len(rows), "n_backfilled": n_bf, "n_late": n_late, "late_frac": round(late_frac, 3),
                    "lag_p50": round(lags[len(lags) // 2], 2) if lags else None,
                    "lag_max": round(lags[-1], 2) if lags else None,
                    "first_trade_t": first_t, "create_seen": int(not c.late_adopted), "late_adopted": int(c.late_adopted),
                    "backfill": c.backfill, "early_trades_ok": int(not pre_start and adopt_ok),
                    "gap_overlap_s": round(gap_s, 1), "gaps_tplus": gaps_tplus, "firehose_s": round(fb_s, 1),
                    "cheap_mode_sub": int(bool(c.h_recv) or c.mint in self.cheap_ever),
                    "feed": "public_ws" + ("+helius_curve_sub" if (c.h_recv or c.mint in self.cheap_ever) else "")
                            + ("+helius_firehose" if fb_s > 0 else ""),
                    "graduated": int(c.migrated is not None), "end_reason": end_reason,
                    "covered_to_t": round(end_t - c.t0, 1), "decided_v0": int(c.decided),
                    "skip_reason": c.skip_reason or "", "complete": complete, "on_time": on_time, "flags": flags,
                    "saved_eat": _eat(t)}
            day = datetime.fromtimestamp(c.t0, NBO).strftime("%Y-%m-%d")
            d = self.dir / day
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{c.mint}.jsonl.gz"
            with gzip.open(p, "wt") as f:
                f.write(json.dumps({"_meta": meta}) + "\n")
                for r in rows:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            sz = p.stat().st_size
            meta["bytes"] = sz
            with (d / "index.jsonl").open("a") as f:
                f.write(json.dumps(meta) + "\n")
            self.stats["saved"] += 1
            self.stats["bytes"] += sz
            self.stats["trades"] += len(rows)
        except Exception as e:
            self.stats["errors"] += 1
            self.log(f"band_tape write error {c.mint[:8]} {type(e).__name__}: {e}")

    def status(self):
        return {**self.stats, "tail_live": len(self.tail), "tracking": len(self.state)}
