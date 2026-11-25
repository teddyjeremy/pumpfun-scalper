"""Main loop: python -m pumpbot.bot (MODE=paper by default)."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import statistics as st
import sys
import time

from .alerts import telegram
from .config import Config
from .engine import CoinEngine, HARD_MAX_ENTRY_T, HARD_MAX_HOLD_S
from .events import SOL
from .execution import ChainExecutor, PaperExecutor
from .feed import Feed
from .journal import Journal, daily_summary, eat_day
from .prices import Prices
from .risk import RiskManager, pnl_for_stake
from .tape import Tape
from .entry_research import candidate_capture_reasons

OBSERVATION_S = int(os.environ.get("COIN_OBSERVATION_SECONDS", "600"))
UNTRADED_RETENTION_S = HARD_MAX_ENTRY_T + HARD_MAX_HOLD_S + 5


class Coin:
    __slots__ = (
        "mint", "t0", "creator", "curve", "quote_mint", "fee_bps", "engine",
        "shadow", "buf", "buf_since", "symbol", "live", "completed",
        "finalized", "capture", "recorded_tape_n", "raw_buf", "capture_reason",
        "capture_t_plus", "entry_filled",
    )

    def __init__(self, ev, cfg, gate):
        self.mint, self.t0, self.creator = ev["mint"], int(ev["timestamp"]), ev.get("creator")
        self.curve = ev.get("bonding_curve")
        self.symbol = ev.get("symbol") or self.mint[:6]
        self.quote_mint = ev.get("quote_mint") or SOL
        self.fee_bps = None
        self.engine = CoinEngine(Tape(self.mint, self.t0, self.creator, self.quote_mint), cfg, gate=gate)
        # Shadow engine retains the existing ungated research measurement.
        self.shadow = CoinEngine(Tape(self.mint, self.t0, self.creator, self.quote_mint), cfg, gate=None)
        self.buf = []
        self.buf_since = None
        self.live = None
        self.completed = False
        self.finalized = False
        self.capture = False
        self.capture_reason = None
        self.capture_t_plus = None
        self.entry_filled = False
        self.recorded_tape_n = 0
        self.raw_buf = []


class Bot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.started_at = time.time()
        self.discovery_seconds = int(os.environ.get("DISCOVERY_SECONDS", "3600"))
        self.j = Journal(cfg.data_dir, cfg.mode)
        self.prices = Prices(log=self.j.log)
        self.risk = RiskManager(cfg)
        self.risk.paper_reset = cfg.mode == "paper" and os.environ.get("PAPER_RESET_AFTER_WIPEOUT", "yes") == "yes"
        self.coins: dict[str, Coin] = {}
        self.stats = {"creates": 0, "trades": 0, "signals": 0, "taken": 0, "skips": {},
                      "coins_captured": 0, "candidate_captures": 0, "filled_entry_captures": 0,
                      "observation_windows_finalized": 0, "coins_pruned_untraded": 0}
        self.exec = PaperExecutor() if cfg.mode == "paper" else ChainExecutor(cfg, self.j.log, cfg.mode)
        self.stop = False
        self._restore_lane_history()

    def _restore_lane_history(self):
        for t in self.j.read_trades(self.cfg.mode)[-500:]:
            self.risk.lane_hist[t["lane"]].append(t["net_per_1usd"])

    def live_gate(self) -> list[str]:
        if self.cfg.mode != "live" or not self.cfg.live_require_paper_edge:
            return self.cfg.lanes
        paper = Journal(self.cfg.data_dir, "paper").read_kind("shadow")
        keep = []
        for ln in self.cfg.lanes:
            x = [t["net_per_1usd"] for t in paper if t["lane"] == ln][-self.cfg.lane_window:]
            ok = len(x) >= self.cfg.lane_min_trades_for_gate and st.mean(x) > 0
            self.j.log(f"live gate lane {ln}: paper shadow n={len(x)} avg={st.mean(x) if x else None} -> {'ON' if ok else 'OFF'}")
            if ok:
                keep.append(ln)
        return keep

    def gate(self, info) -> tuple[bool, str, float]:
        self.stats["signals"] += 1
        ok, why, stake = self.risk.allow(info["lane"], eat_day())
        if ok:
            self.risk.on_open()
            self.stats["taken"] += 1
        else:
            self.stats["skips"][why] = self.stats["skips"].get(why, 0) + 1
        return ok, why, stake

    def on_events(self, evs, sig, slot, recv):
        for kind, ev in evs:
            if kind == "create":
                if ev["mint"] not in self.coins and recv <= self.started_at + self.discovery_seconds:
                    self.coins[ev["mint"]] = Coin(ev, self.cfg, self.gate)
                    self.stats["creates"] += 1
                    self.j.write("coins", {
                        "mint": ev["mint"], "symbol": ev.get("symbol"), "name": ev.get("name"),
                        "creator": ev.get("creator"), "bonding_curve": ev.get("bonding_curve"),
                        "quote_mint": ev.get("quote_mint") or SOL, "created_at": int(ev["timestamp"]),
                        "entry_window_end_t_plus": HARD_MAX_ENTRY_T,
                        "full_observation_seconds_if_traded": OBSERVATION_S,
                    })
            elif kind == "trade":
                c = self.coins.get(ev["mint"])
                if c is None:
                    continue
                t_plus = int(ev["timestamp"]) - c.t0
                end_s = OBSERVATION_S if c.capture else UNTRADED_RETENTION_S
                if t_plus < 0 or t_plus > end_s:
                    continue
                self.stats["trades"] += 1
                if c.capture:
                    self.record_raw_coin_trade(c, ev, slot)
                elif t_plus <= HARD_MAX_ENTRY_T + 5:
                    # Keep only a temporary pre-entry buffer; discard it for coins
                    # that never get a filled entry.
                    c.raw_buf.append((dict(ev), slot))
                if c.fee_bps is None:
                    c.fee_bps = (ev.get("fee_bps") or 0) + (ev.get("creator_fee_bps") or 0)
                if ev.get("quote_mint") and ev["quote_mint"] != c.quote_mint:
                    c.quote_mint = ev["quote_mint"]
                    c.engine.tape.quote_mint = c.quote_mint
                    c.shadow.tape.quote_mint = c.quote_mint
                tr = self.to_trade(c, ev, slot)
                if tr is None:
                    c.buf.append((ev, slot))
                    c.buf_since = c.buf_since or recv
                    continue
                if c.buf:
                    for e2, s2 in c.buf:
                        t2 = self.to_trade(c, e2, s2)
                        if t2:
                            self.handle(c, c.engine.on_trade(t2))
                            self.shadow_handle(c, c.shadow.on_trade(t2))
                            self.record_coin_trade(c, t2)
                    c.buf = []
                self.handle(c, c.engine.on_trade(tr))
                self.shadow_handle(c, c.shadow.on_trade(tr))
                self.record_coin_trade(c, tr)
            elif kind == "complete":
                c = self.coins.get(ev["mint"])
                if c:
                    c.completed = True
                    self.j.write("coin_events", {
                        "mint": c.mint, "symbol": c.symbol, "event": "complete",
                        "t_plus": int(time.time()) - c.t0,
                    })

    def to_trade(self, c: Coin, ev: dict, slot) -> dict | None:
        if c.quote_mint == SOL:
            q = self.prices.quote(SOL)
            if not q:
                return None
            usd, dec = q
            amt, vq = ev["sol_amount"], ev["virtual_sol_reserves"]
        else:
            q = self.prices.quote(c.quote_mint)
            if not q or ev.get("virtual_quote_reserves") is None:
                return None
            usd, dec = q
            amt, vq = ev["quote_amount"], ev["virtual_quote_reserves"]
        vt = ev["virtual_token_reserves"]
        if not vq or not vt:
            return None
        spot = (vq / 10 ** dec) / (vt / 1e6) * usd
        sol_usd = self.prices.sol_usd or 0
        return {
            "bt": int(ev["timestamp"]), "t": int(ev["timestamp"]) - c.t0,
            "side": "buy" if ev["is_buy"] else "sell",
            "usd": amt / 10 ** dec * usd, "spot": spot, "trader": ev["user"],
            "tok": ev["token_amount"] / 1e6,
            "sol": (amt / 10 ** dec * usd / sol_usd) if sol_usd else 0.0,
            "slot": slot or 0,
        }

    def activate_coin_capture(self, c: Coin, reason: str, t_plus: int | None = None):
        """Retain a full ten-minute tape for a filled entry or a research candidate."""
        if t_plus is None:
            t_plus = int(time.time()) - c.t0
        if c.capture:
            if reason == "accepted_entry_filled" and not c.entry_filled:
                c.entry_filled = True
                self.stats["filled_entry_captures"] += 1
                self.j.write("coin_events", {
                    "mint": c.mint, "symbol": c.symbol,
                    "event": "entry_filled_after_research_capture", "t_plus": t_plus,
                })
            return
        c.capture = True
        c.capture_reason = reason
        c.capture_t_plus = int(t_plus)
        if reason == "accepted_entry_filled":
            c.entry_filled = True
            self.stats["filled_entry_captures"] += 1
        else:
            self.stats["candidate_captures"] += 1
        self.stats["coins_captured"] += 1
        self.j.write("coin_events", {
            "mint": c.mint, "symbol": c.symbol, "event": "capture_started",
            "reason": reason, "t_plus": int(t_plus),
        })
        for ev, slot in c.raw_buf:
            self.record_raw_coin_trade(c, ev, slot)
        c.raw_buf.clear()
        # Flush the in-memory priced tape so the capture includes pre-entry context.
        tp = c.engine.tape
        for i in range(tp.n):
            self.record_coin_tape_index(c, i)
        c.recorded_tape_n = tp.n
        self.j.log(f"CAPTURE {c.symbol}: full {OBSERVATION_S}s coin tape enabled")

    def record_raw_coin_trade(self, c: Coin, ev: dict, slot):
        if not c.capture:
            return
        t = int(ev["timestamp"]) - c.t0
        if t < 0 or t > OBSERVATION_S:
            return
        self.j.write("coin_raw_tape", {
            "mint": c.mint, "symbol": c.symbol, "t_plus": t,
            "block_timestamp": int(ev["timestamp"]),
            "side": "buy" if ev["is_buy"] else "sell", "trader": ev.get("user"),
            "slot": slot or 0, "sol_amount_raw": ev.get("sol_amount"),
            "quote_amount_raw": ev.get("quote_amount"), "token_amount_raw": ev.get("token_amount"),
            "quote_mint": ev.get("quote_mint") or c.quote_mint,
            "virtual_sol_reserves_raw": ev.get("virtual_sol_reserves"),
            "virtual_quote_reserves_raw": ev.get("virtual_quote_reserves"),
            "virtual_token_reserves_raw": ev.get("virtual_token_reserves"),
            "fee_bps": ev.get("fee_bps"), "creator_fee_bps": ev.get("creator_fee_bps"),
        })

    def record_coin_tape_index(self, c: Coin, i: int):
        tp = c.engine.tape
        if i < 0 or i >= tp.n:
            return
        f = tp.features(i)
        self.j.write("coin_tape", {
            "mint": c.mint, "symbol": c.symbol, "t_plus": tp.t[i], "bt": tp.bt[i],
            "side": "buy" if tp.buy[i] else "sell", "price_usd": tp.spot[i],
            "market_cap_usd": tp.spot[i] * 1e9, "trade_usd": tp.usd[i],
            "trader": tp.trader[i], "slot": tp.slot[i], "cumulative_trades": i + 1,
            "cumulative_volume_usd": tp.cum_vol[i], "unique_traders": tp.cum_w[i],
            "buy_usd_10s": f["b10"], "sell_usd_10s": f["s10"], "trades_10s": f["n10"],
            "new_buyers_30s": f["nw30"], "creator_hold": f["creator_hold"],
            "bundle_hold": f["bundle_hold"], "top_seller_share": f["top1"],
            "features": f, "quote_mint": c.quote_mint,
        })

    def record_coin_trade(self, c: Coin, trade: dict):
        if not c.capture or trade.get("t", OBSERVATION_S + 1) > OBSERVATION_S:
            return
        tp = c.engine.tape
        while c.recorded_tape_n < tp.n:
            self.record_coin_tape_index(c, c.recorded_tape_n)
            c.recorded_tape_n += 1

    def finalize_coin(self, c: Coin):
        if c.finalized or not c.capture:
            return
        tp = c.engine.tape
        observed = max(0, min(OBSERVATION_S, int(time.time()) - c.t0))
        if tp.n:
            peak_i = max(range(tp.n), key=lambda k: tp.spot[k])
            low_i = min(range(tp.n), key=lambda k: tp.spot[k])
            first = tp.spot[0]
            self.j.write("coin_summaries", {
                "mint": c.mint, "symbol": c.symbol, "creator": c.creator,
                "quote_mint": c.quote_mint, "created_at": c.t0,
                "observation_seconds": OBSERVATION_S, "observed_seconds": observed,
                "trade_count": tp.n, "buy_count": sum(tp.buy), "sell_count": tp.n - sum(tp.buy),
                "volume_usd": round(tp.cum_vol[-1], 4), "unique_traders": tp.cum_w[-1],
                "first_price_usd": first, "last_price_usd": tp.spot[-1],
                "peak_price_usd": tp.spot[peak_i], "peak_t_plus": tp.t[peak_i],
                "low_price_usd": tp.spot[low_i], "low_t_plus": tp.t[low_i],
                "last_t_plus": tp.t[-1], "completed": c.completed,
                "capture_reason": c.capture_reason, "capture_t_plus": c.capture_t_plus,
                "entry_filled": c.entry_filled,
                "observation_complete": observed >= OBSERVATION_S,
                "return_from_first": (tp.spot[-1] / first - 1) if first else None,
                "peak_return_from_first": (tp.spot[peak_i] / first - 1) if first else None,
            })
        else:
            self.j.write("coin_summaries", {
                "mint": c.mint, "symbol": c.symbol, "created_at": c.t0,
                "observation_seconds": OBSERVATION_S, "observed_seconds": observed,
                "trade_count": 0, "completed": c.completed,
                "capture_reason": c.capture_reason, "capture_t_plus": c.capture_t_plus,
                "entry_filled": c.entry_filled,
                "observation_complete": observed >= OBSERVATION_S, "no_priced_trades": True,
            })
        c.finalized = True
        self.stats["observation_windows_finalized"] += 1

    def shadow_handle(self, c: Coin, events: list):
        for kind, r in events:
            if kind == "exited":
                self.j.write("shadow", {
                    "mint": c.mint, "symbol": c.symbol, "quote_mint": c.quote_mint,
                    "lane": r["lane"], "entry_t_plus": r["entry_t"], "hold_s": r["hold_s"],
                    "exit": r["exit"], "mult": round(r["mult"], 4),
                    "net_per_1usd": round(r["net"], 5), "mfe": round(r["mfe"], 4),
                    "mae": round(r["mae"], 4),
                })

    def handle(self, c: Coin, events: list):
        for kind, info in events:
            if kind == "diagnostic":
                self.j.write("coin_decisions", {"mint": c.mint, "symbol": c.symbol, **info})
            elif kind == "signal":
                f = info.pop("feat", {})
                candidate_reasons = candidate_capture_reasons(f)
                if candidate_reasons:
                    self.activate_coin_capture(
                        c, "research_candidate:" + ",".join(candidate_reasons), t_plus=info["t"]
                    )
                self.j.write("signals", {
                    "mint": c.mint, "symbol": c.symbol, "quote_mint": c.quote_mint,
                    "lane": info["lane"], "t_plus": info["t"], "mcap": round(info["mc"]),
                    "taken": info["taken"], "gate": info["gate_reason"], "stake": info["stake"],
                    "feat": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()},
                })
                if info["taken"]:
                    self.j.log(f"ENTRY ACCEPTED {info['lane']} {c.symbol} {c.mint[:8]} t+{info['t']}s stake ${info['stake']:.2f}")
                    if self.cfg.mode != "paper":
                        asyncio.get_event_loop().create_task(self.exec_buy(c, info["stake"]))
            elif kind == "entered":
                self.activate_coin_capture(c, "accepted_entry_filled")
                if self.cfg.mode != "paper":
                    self.j.log(f"POSITION FILLED {c.symbol} at ${info['px']:.10f}")
            elif kind == "exit_trigger":
                if self.cfg.mode != "paper" and c.live and c.live.get("ok"):
                    asyncio.get_event_loop().create_task(self.exec_sell(c, info["reason"]))
            elif kind == "exited":
                r = info
                pnl = pnl_for_stake(r["net"], r["stake"], self.cfg.gas_usd_round_trip)
                if self.cfg.mode != "live":
                    self.risk.on_close(r["lane"], pnl, r["net"])
                elif not (c.live and c.live.get("ok")):
                    self.risk.on_close(r["lane"], 0.0, 0.0)
                    pnl = 0.0
                rec = {
                    "mint": c.mint, "symbol": c.symbol, "quote_mint": c.quote_mint,
                    "lane": r["lane"], "entry_t_plus": r["entry_t"], "entry_bt": r["ent_bt"],
                    "exit_bt": r["exit_bt"], "ent_px": r["ent_px"], "exit_px": r["exit_px"],
                    "hold_s": r["hold_s"], "exit": r["exit"], "mult": round(r["mult"], 4),
                    "net_per_1usd": round(r["net"], 5), "stake": r["stake"],
                    "pnl_usd": round(pnl, 5), "mfe": round(r["mfe"], 4),
                    "mae": round(r["mae"], 4), "live": c.live,
                    "wallet_after": round(self.risk.wallet, 4),
                }
                self.j.write("trades", rec)
                self.j.log(f"EXIT {r['lane']} {c.symbol} {r['exit']} hold {r['hold_s']}s x{r['mult']:.3f} pnl ${pnl:+.3f} wallet ${self.risk.wallet:.3f}")
                asyncio.get_event_loop().create_task(telegram(
                    f"[{self.cfg.mode}] {c.symbol} {r['exit']} x{r['mult']:.2f} pnl ${pnl:+.3f} wallet ${self.risk.wallet:.2f}"
                ))
                if self.risk.killed or self.risk.wallet < self.cfg.kill_wallet_usd:
                    if self.risk.paper_reset:
                        self.j.log("Paper wipeout reached; paper reset is enabled.")
                    else:
                        self.j.log("KILL SWITCH: wallet below minimum. No more entries.")
                        asyncio.get_event_loop().create_task(telegram("KILL SWITCH: wallet below minimum."))

    async def exec_buy(self, c: Coin, stake: float):
        try:
            c.live = await self.exec.buy(c, stake, self.prices.sol_usd)
        except Exception as e:
            c.live = {"ok": False, "err": f"{type(e).__name__}: {e}"}
        c.live["stake"] = stake
        c.live["lane"] = (
            c.engine.pending.info.get("lane") if c.engine.pending and c.engine.pending.kind == "enter"
            else (c.engine.pos.lane if c.engine.pos else "B")
        )
        self.j.write("exec", {"side": "buy", "mint": c.mint, "result": c.live})
        if not c.live.get("ok"):
            self.j.log(f"BUY FAILED {c.symbol}: {c.live.get('err')}")

    async def exec_sell(self, c: Coin, reason: str):
        try:
            r = await self.exec.sell(c, self.prices.sol_usd)
        except Exception as e:
            r = {"ok": False, "err": f"{type(e).__name__}: {e}"}
        self.j.write("exec", {"side": "sell", "mint": c.mint, "reason": reason, "result": r})
        if self.cfg.mode == "live":
            try:
                w = await self.exec.wallet_usd(self.prices.sol_usd)
                pnl = w - self.risk.wallet
                lv = c.live or {}
                self.risk.on_close(lv.get("lane", "B"), pnl, pnl / max(1e-9, lv.get("stake", 1.0)))
            except Exception:
                self.risk.open = 0
        if not r.get("ok"):
            self.j.log(f"SELL FAILED {c.symbol}: {r.get('err')} -> check wallet manually")
            await telegram(f"SELL FAILED {c.symbol} {c.mint}: {r.get('err')}")

    async def ticker(self):
        last_day = eat_day()
        while not self.stop:
            await asyncio.sleep(1)
            now = time.time()
            for m, c in list(self.coins.items()):
                if c.shadow.pos or c.shadow.pending:
                    self.shadow_handle(c, c.shadow.on_tick(now))
                if c.engine.pos or c.engine.pending:
                    self.handle(c, c.engine.on_tick(now))
                retention = OBSERVATION_S if c.capture else UNTRADED_RETENTION_S
                if now - c.t0 >= retention:
                    self.finalize_coin(c)
                    if not c.engine.pos and not c.engine.pending and not c.shadow.pos and not c.shadow.pending:
                        self.coins.pop(m, None)
                        if not c.capture:
                            self.stats["coins_pruned_untraded"] += 1
            d = eat_day()
            if d != last_day:
                summary = daily_summary(self.j.read_trades(), last_day)
                with open(self.j.path(f"daily_{self.cfg.mode}_{last_day}.json"), "w") as f:
                    json.dump(summary, f, indent=1)
                self.j.log(f"DAILY {json.dumps(summary)}")
                await telegram(f"Daily {last_day} [{self.cfg.mode}]: {json.dumps(summary)}")
                last_day = d
            if int(now) % 15 == 0:
                self.j.status({
                    "risk": self.risk.state(), "coins_tracked": len(self.coins), "stats": self.stats,
                    "sol_usd": self.prices.sol_usd,
                    "feeds": {k: round(now - v, 1) for k, v in self.feed.last_msg.items()},
                    "open": [m for m, c in self.coins.items() if c.engine.pos],
                })
            observation_deadline = self.started_at + self.discovery_seconds + OBSERVATION_S
            if now >= observation_deadline and not self.coins:
                self.j.log("All coin observation windows finalized; stopping cleanly.")
                self.shutdown()
                return

    async def main(self):
        errs = self.cfg.validate()
        if errs:
            for e in errs:
                self.j.log("CONFIG ERROR: " + e)
            sys.exit(2)
        self.cfg.lanes = self.live_gate()
        if not self.cfg.lanes:
            self.j.log("No lane passed the live gate; refusing to trade live.")
            sys.exit(3)
        self.j.log(f"start mode={self.cfg.mode} lanes={self.cfg.lanes} config={json.dumps(self.cfg.public_dict(), default=str)}")
        if self.cfg.mode == "live":
            async with __import__("aiohttp").ClientSession() as session:
                await self.prices._sol(session)
            if not self.prices.sol_usd:
                self.j.log("no SOL/USD price; refusing to start live")
                sys.exit(4)
            self.risk.wallet = self.risk.day_start = self.risk.step_base = await self.exec.wallet_usd(self.prices.sol_usd)
            self.j.log(f"live wallet ${self.risk.wallet:.3f} (keep only what you can lose; no top-ups)")
            await telegram("pumpbot LIVE started")
        self.feed = Feed(self.cfg.feed_ws_urls, self.on_events, self.j.log)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.shutdown)
        if os.path.exists(self.j.path("STOP")):
            self.j.log("STOP file present in DATA_DIR: remove it to start")
            return
        await asyncio.gather(self.prices.run(), self.feed.run(), self.ticker(), self.watch_stop())

    async def watch_stop(self):
        while True:
            await asyncio.sleep(2)
            if os.path.exists(self.j.path("STOP")):
                self.j.log("STOP file found: no new entries; waiting for open positions to close")
                self.risk.killed = True
                while any(c.engine.pos or c.engine.pending for c in self.coins.values()):
                    await asyncio.sleep(1)
                self.shutdown()
                return

    def shutdown(self):
        if self.stop:
            return
        self.stop = True
        for c in list(self.coins.values()):
            if c.capture:
                self.finalize_coin(c)
        self.j.status({
            "risk": self.risk.state(), "coins_tracked": len(self.coins), "stats": self.stats,
            "shutdown": True,
            "observation_windows_finalized": self.stats["observation_windows_finalized"],
        })
        self.j.log("shutdown: finalized captured coin summaries and wrote final status")
        current = asyncio.current_task()
        for task in asyncio.all_tasks():
            if task is not current:
                task.cancel()


def main():
    cfg = Config.from_env()
    try:
        asyncio.run(Bot(cfg).main())
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    main()
