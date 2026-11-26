"""Early-launch Pump.fun sniper research observer. Paper only; never signs or sends transactions."""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from .config import Config
from .events import SOL
from .feed import Feed
from .journal import Journal

DISCOVERY_SECONDS = int(os.environ.get("SNIPER_DISCOVERY_SECONDS", "3600"))
TAIL_SECONDS = int(os.environ.get("SNIPER_TAIL_SECONDS", "600"))
STAKE_USD = float(os.environ.get("SNIPER_STAKE_USD", "1.00"))
START_WALLET_USD = float(os.environ.get("SNIPER_START_WALLET_USD", "10.00"))
TAKE_PROFIT = float(os.environ.get("SNIPER_TAKE_PROFIT", "0.20"))
STOP_LOSS = float(os.environ.get("SNIPER_STOP_LOSS", "0.12"))
MAX_HOLD_SECONDS = int(os.environ.get("SNIPER_MAX_HOLD_SECONDS", "30"))
ENTRY_CONFIRM_SECONDS = int(os.environ.get("SNIPER_ENTRY_CONFIRM_SECONDS", "3"))
ENTRY_SLIPPAGE = float(os.environ.get("SNIPER_ENTRY_SLIPPAGE", "0.015"))
EXIT_SLIPPAGE = float(os.environ.get("SNIPER_EXIT_SLIPPAGE", "0.015"))
FEE_RATE = float(os.environ.get("SNIPER_FEE_RATE", "0.0125"))
ROUND_TRIP_GAS_USD = float(os.environ.get("SNIPER_GAS_USD", "0.0285"))


@dataclass
class Launch:
    mint: str
    created_at: int
    creator: str | None
    symbol: str
    name: str
    create_signature: str
    quote_mint: str = SOL
    first_buy_signature: str | None = None
    first_buy_timestamp: int | None = None
    entry_pending: bool = False
    entry_status: str = "watching"
    entry_price: float | None = None
    entry_timestamp: int | None = None
    entry_signature: str | None = None
    exit_status: str | None = None
    observed_trades: int = 0


class Sniper:
    """First-buy/early-block paper sniper with full launch tape capture."""

    def __init__(self, cfg: Config):
        if cfg.mode != "paper":
            raise ValueError("SNIPER is paper-only; MODE must be paper")
        self.cfg = cfg
        self.started = time.time()
        self.j = Journal(cfg.data_dir, "sniper")
        self.launches: dict[str, Launch] = {}
        self.wallet = START_WALLET_USD
        self.open_mint: str | None = None
        self.position: dict | None = None
        self.shadow_pending: dict[str, dict] = {}
        self.shadow_positions: dict[str, dict] = {}
        self.stats = {
            "creates": 0, "sol_launches": 0, "trades_recorded": 0,
            "first_buy_signals": 0, "entries_filled": 0, "entries_rejected_busy": 0,
            "entries_expired": 0, "exits": 0, "wins": 0, "losses": 0,
            "skips": {},
        }

    @staticmethod
    def spot_sol(ev: dict) -> float:
        vq, vt = ev.get("virtual_sol_reserves") or 0, ev.get("virtual_token_reserves") or 0
        return (vq / 1e9) / (vt / 1e6) if vq > 0 and vt > 0 else 0.0

    def _skip(self, reason: str):
        self.stats["skips"][reason] = self.stats["skips"].get(reason, 0) + 1

    def on_events(self, events, signature, slot, received_at):
        for kind, ev in events:
            if kind == "create":
                mint = ev["mint"]
                if mint in self.launches or received_at > self.started + DISCOVERY_SECONDS:
                    continue
                c = Launch(
                    mint=mint, created_at=int(ev["timestamp"]), creator=ev.get("creator"),
                    symbol=ev.get("symbol") or mint[:8], name=ev.get("name") or "",
                    create_signature=signature, quote_mint=ev.get("quote_mint") or SOL,
                )
                self.launches[mint] = c
                self.stats["creates"] += 1
                if c.quote_mint == SOL:
                    self.stats["sol_launches"] += 1
                self.j.write("sniper_launches", {
                    "mint": mint, "symbol": c.symbol, "name": c.name, "creator": c.creator,
                    "created_at": c.created_at, "create_signature": signature, "slot": slot or 0,
                    "quote_mint": c.quote_mint, "decision": "watching" if c.quote_mint == SOL else "observe_only_non_sol_quote",
                })
            elif kind == "trade":
                c = self.launches.get(ev["mint"])
                if c is None:
                    continue
                t = int(ev["timestamp"]) - c.created_at
                if t < 0 or t > TAIL_SECONDS:
                    continue
                price = self.spot_sol(ev) if c.quote_mint == SOL else 0.0
                c.observed_trades += 1
                trader = ev.get("user")
                side = "buy" if ev.get("is_buy") else "sell"
                self.stats["trades_recorded"] += 1
                self.j.write("sniper_tape", {
                    "mint": c.mint, "symbol": c.symbol, "t_plus": t, "block_timestamp": int(ev["timestamp"]),
                    "slot": slot or 0, "signature": signature, "side": side, "trader": trader,
                    "creator": ev.get("creator"), "quote_mint": ev.get("quote_mint") or c.quote_mint,
                    "price_sol": price or None, "sol_amount_raw": ev.get("sol_amount"),
                    "token_amount_raw": ev.get("token_amount"), "virtual_sol_reserves_raw": ev.get("virtual_sol_reserves"),
                    "virtual_token_reserves_raw": ev.get("virtual_token_reserves"),
                    "fee_bps": ev.get("fee_bps"), "creator_fee_bps": ev.get("creator_fee_bps"),
                })
                if c.quote_mint != SOL or not price:
                    continue
                self._shadow_process(c, ev, signature, price, t)
                self._trade_launch(c, ev, signature, price, t)
            elif kind == "complete":
                c = self.launches.get(ev["mint"])
                if c:
                    self.j.write("sniper_launch_events", {
                        "mint": c.mint, "event": "bonding_curve_complete",
                        "t_plus": int(ev.get("timestamp") or c.created_at) - c.created_at,
                        "signature": signature, "slot": slot or 0,
                    })

    def _trade_launch(self, c: Launch, ev: dict, signature: str, price: float, t: int):
        side_buy = bool(ev.get("is_buy"))
        if self.position and self.position["mint"] == c.mint:
            self._manage_position(c, ev, signature, price, t)
            return
        if c.entry_pending and c.entry_price is None:
            if signature == c.first_buy_signature:
                return
            delay = int(ev["timestamp"]) - int(c.first_buy_timestamp or ev["timestamp"])
            if delay > ENTRY_CONFIRM_SECONDS:
                c.entry_pending = False
                c.entry_status = "expired_no_following_trade"
                self.stats["entries_expired"] += 1
                self.j.write("sniper_decisions", {"mint": c.mint, "decision": c.entry_status, "delay_seconds": delay})
                return
            if self.open_mint is not None:
                c.entry_pending = False
                c.entry_status = "rejected_portfolio_busy"
                self.stats["entries_rejected_busy"] += 1
                self._skip("portfolio_busy")
                self.j.write("sniper_decisions", {"mint": c.mint, "decision": c.entry_status, "delay_seconds": delay})
                return
            self._enter(c, ev, signature, price, t, delay)
            return
        if c.first_buy_signature is None and side_buy:
            if signature == c.create_signature:
                return
            if ev.get("user") and ev.get("user") == c.creator:
                c.entry_status = "creator_first_buyer_skipped"
                self._skip("creator_first_buyer")
                return
            c.first_buy_signature = signature
            c.first_buy_timestamp = int(ev["timestamp"])
            c.entry_pending = True
            c.entry_status = "first_buy_signal"
            self.stats["first_buy_signals"] += 1
            self.j.write("sniper_decisions", {
                "mint": c.mint, "decision": "first_buy_signal", "signal_t_plus": t,
                "signal_signature": signature, "signal_wallet": ev.get("user"), "signal_price_sol": price,
            })

    def _enter(self, c: Launch, ev: dict, signature: str, price: float, t: int, delay: int):
        entry_fill = price * (1.0 + ENTRY_SLIPPAGE)
        c.entry_price, c.entry_timestamp, c.entry_signature = price, int(ev["timestamp"]), signature
        c.entry_pending = False
        c.entry_status = "filled_paper"
        self.open_mint = c.mint
        self.position = {
            "mint": c.mint, "symbol": c.symbol, "stake_usd": STAKE_USD,
            "entry_price_sol": price, "entry_fill_price_sol": entry_fill,
            "entry_timestamp": int(ev["timestamp"]), "entry_t_plus": t,
            "entry_signature": signature, "signal_signature": c.first_buy_signature,
            "entry_delay_seconds": delay,
            "last_price_sol": price, "last_price_timestamp": int(ev["timestamp"]),
        }
        self.stats["entries_filled"] += 1
        self.j.write("sniper_entries", {**self.position, "mode": "paper"})
        self.j.log(f"SNIPER PAPER ENTRY {c.symbol} {c.mint[:8]} t+{t}s delay={delay}s")

    def _manage_position(self, c: Launch, ev: dict, signature: str, price: float, t: int):
        p = self.position
        if not p or signature == p["entry_signature"]:
            return
        p["last_price_sol"] = price
        p["last_price_timestamp"] = int(ev["timestamp"])
        move = price / p["entry_price_sol"] - 1.0
        held = int(ev["timestamp"]) - p["entry_timestamp"]
        reason = None
        if move >= TAKE_PROFIT:
            reason = "take_profit"
        elif move <= -STOP_LOSS:
            reason = "stop_loss"
        elif held >= MAX_HOLD_SECONDS:
            reason = "time_cap"
        if reason:
            exit_fill = price * (1.0 - EXIT_SLIPPAGE)
            net_mult = (exit_fill / p["entry_fill_price_sol"]) * ((1.0 - FEE_RATE) ** 2)
            gross_pnl = p["stake_usd"] * (net_mult - 1.0)
            net_pnl = gross_pnl - ROUND_TRIP_GAS_USD
            self.wallet += net_pnl
            rec = {
                **p, "exit_price_sol": price, "exit_fill_price_sol": exit_fill,
                "exit_timestamp": int(ev["timestamp"]), "exit_t_plus": t,
                "exit_signature": signature, "exit_reason": reason, "hold_seconds": held,
                "gross_return": round(net_mult - 1.0, 6), "net_pnl_usd": round(net_pnl, 6),
                "wallet_after_usd": round(self.wallet, 6), "market_move_at_exit": round(move, 6),
                "entry_slippage": ENTRY_SLIPPAGE, "exit_slippage": EXIT_SLIPPAGE,
                "fee_rate_each_side": FEE_RATE, "round_trip_gas_usd": ROUND_TRIP_GAS_USD,
            }
            self.j.write("sniper_trades", rec)
            self.stats["exits"] += 1
            if net_pnl > 0:
                self.stats["wins"] += 1
            else:
                self.stats["losses"] += 1
            self.j.log(f"SNIPER PAPER EXIT {c.symbol} {reason} net {net_pnl:+.4f} wallet {self.wallet:.4f}")
            c.exit_status = reason
            self.position = None
            self.open_mint = None


    def _shadow_process(self, c: Launch, ev: dict, signature: str, price: float, t: int):
        """Independent per-mint shadow lane; never blocked by the baseline portfolio."""
        side_buy = bool(ev.get("is_buy"))
        pending = self.shadow_pending.get(c.mint)
        pos = self.shadow_positions.get(c.mint)
        if pos:
            if signature == pos["entry_signature"]:
                return
            pos["last_price_sol"] = price
            pos["last_price_timestamp"] = int(ev["timestamp"])
            move = price / pos["entry_price_sol"] - 1.0
            held = int(ev["timestamp"]) - pos["entry_timestamp"]
            reason = "take_profit" if move >= TAKE_PROFIT else "stop_loss" if move <= -STOP_LOSS else "time_cap" if held >= MAX_HOLD_SECONDS else None
            if reason:
                self._close_shadow(c.mint, price, int(ev["timestamp"]), t, signature, reason, 0)
            return
        if pending:
            if signature == pending["signal_signature"]:
                return
            delay = int(ev["timestamp"]) - pending["signal_timestamp"]
            if delay > ENTRY_CONFIRM_SECONDS:
                self.shadow_pending.pop(c.mint, None)
                self.j.write("sniper_shadow_decisions", {"mint": c.mint, "decision": "expired_no_following_trade", "delay_seconds": delay})
                return
            fill = {
                "mint": c.mint, "symbol": c.symbol, "signal_wallet": pending["signal_wallet"],
                "signal_signature": pending["signal_signature"], "signal_timestamp": pending["signal_timestamp"],
                "entry_signature": signature, "entry_timestamp": int(ev["timestamp"]),
                "entry_t_plus": t, "entry_delay_seconds": delay, "stake_usd": STAKE_USD,
                "entry_price_sol": price, "entry_fill_price_sol": price * (1.0 + ENTRY_SLIPPAGE),
                "last_price_sol": price, "last_price_timestamp": int(ev["timestamp"]),
            }
            self.shadow_pending.pop(c.mint, None)
            self.shadow_positions[c.mint] = fill
            self.j.write("sniper_shadow_entries", fill)
            return
        if side_buy and signature != c.create_signature and c.first_buy_signature is None:
            trader = ev.get("user")
            if trader and trader == c.creator:
                return
            self.shadow_pending[c.mint] = {
                "signal_signature": signature, "signal_timestamp": int(ev["timestamp"]),
                "signal_wallet": trader,
            }
            self.j.write("sniper_shadow_decisions", {
                "mint": c.mint, "decision": "first_buy_signal",
                "signal_signature": signature, "signal_wallet": trader,
                "signal_timestamp": int(ev["timestamp"]), "signal_t_plus": t,
            })

    def _close_shadow(self, mint: str, price: float, timestamp: int, t_plus: int,
                      signature: str, reason: str, stale_seconds: int):
        p = self.shadow_positions.pop(mint, None)
        if not p:
            return
        exit_fill = price * (1.0 - EXIT_SLIPPAGE)
        net_mult = (exit_fill / p["entry_fill_price_sol"]) * ((1.0 - FEE_RATE) ** 2)
        net_pnl = p["stake_usd"] * (net_mult - 1.0) - ROUND_TRIP_GAS_USD
        self.j.write("sniper_shadow_trades", {
            **p, "exit_price_sol": price, "exit_fill_price_sol": exit_fill,
            "exit_timestamp": timestamp, "exit_t_plus": t_plus, "exit_signature": signature,
            "exit_reason": reason, "hold_seconds": timestamp - p["entry_timestamp"],
            "market_move_at_exit": round(price / p["entry_price_sol"] - 1.0, 6),
            "net_pnl_usd": round(net_pnl, 6), "exit_price_stale_seconds": stale_seconds,
        })

    def timer_tick(self, now: float | None = None):
        """Apply max-hold exits even if a token receives no further transactions."""
        now = time.time() if now is None else now
        if self.position:
            p = self.position
            if now - p["entry_timestamp"] >= MAX_HOLD_SECONDS and p.get("last_price_sol"):
                self._close_baseline_time_cap(max(0, int(now - p.get("last_price_timestamp", now))), int(now))
        for mint, p in list(self.shadow_positions.items()):
            if now - p["entry_timestamp"] >= MAX_HOLD_SECONDS and p.get("last_price_sol"):
                stale = max(0, int(now - p.get("last_price_timestamp", now)))
                self._close_shadow(mint, p["last_price_sol"], int(now), 0, "timer", "time_cap_timer", stale)

    def _close_baseline_time_cap(self, stale_seconds: int, timestamp: int):
        p = self.position
        if not p:
            return
        price = p["last_price_sol"]
        exit_fill = price * (1.0 - EXIT_SLIPPAGE)
        net_mult = (exit_fill / p["entry_fill_price_sol"]) * ((1.0 - FEE_RATE) ** 2)
        net_pnl = p["stake_usd"] * (net_mult - 1.0) - ROUND_TRIP_GAS_USD
        self.wallet += net_pnl
        rec = {**p, "exit_price_sol": price, "exit_fill_price_sol": exit_fill,
               "exit_timestamp": timestamp, "exit_t_plus": None, "exit_signature": "timer",
               "exit_reason": "time_cap_timer", "hold_seconds": int(timestamp - p["entry_timestamp"]),
               "gross_return": round(net_mult - 1.0, 6), "net_pnl_usd": round(net_pnl, 6),
               "wallet_after_usd": round(self.wallet, 6), "market_move_at_exit": round(price / p["entry_price_sol"] - 1.0, 6),
               "exit_price_stale_seconds": stale_seconds, "entry_slippage": ENTRY_SLIPPAGE,
               "exit_slippage": EXIT_SLIPPAGE, "fee_rate_each_side": FEE_RATE,
               "round_trip_gas_usd": ROUND_TRIP_GAS_USD}
        self.j.write("sniper_trades", rec)
        self.stats["exits"] += 1
        self.stats["wins" if net_pnl > 0 else "losses"] += 1
        self.j.log(f"SNIPER PAPER TIMER EXIT {p['symbol']} net {net_pnl:+.4f}; last quote stale {stale_seconds}s")
        self.position = None
        self.open_mint = None

    def prune_expired_launches(self):
        """Bound memory: keep each launch through its ten-minute tape window."""
        now = int(time.time())
        for mint, c in list(self.launches.items()):
            if c.entry_pending and c.first_buy_timestamp is not None:
                if now - c.first_buy_timestamp > ENTRY_CONFIRM_SECONDS:
                    c.entry_pending = False
                    c.entry_status = "expired_no_following_trade"
                    self.stats["entries_expired"] += 1
                    self.j.write("sniper_decisions", {
                        "mint": mint, "decision": c.entry_status,
                        "delay_seconds": now - c.first_buy_timestamp,
                    })
            if mint != self.open_mint and now - c.created_at > TAIL_SECONDS:
                self.launches.pop(mint, None)

    async def run(self):
        self.j.log("SNIPER V1 PAPER ONLY: launch-first-buy, next-transaction fill; no signed or live transactions.")
        self.j.log(f"config discovery={DISCOVERY_SECONDS}s tail={TAIL_SECONDS}s fixed_paper_stake={STAKE_USD:.2f} TP={TAKE_PROFIT:.1%} SL={STOP_LOSS:.1%} max_hold={MAX_HOLD_SECONDS}s; no loss-based shutdown")
        feed = Feed(self.cfg.feed_ws_urls, self.on_events, self.j.log)
        feed_task = asyncio.create_task(feed.run())
        deadline = self.started + DISCOVERY_SECONDS + TAIL_SECONDS
        try:
            while time.time() < deadline:
                await asyncio.sleep(1)
                self.timer_tick()
                self.prune_expired_launches()
                self.j.status({
                    "strategy": "pumpfun_launch_sniper_v1", "mode": "paper",
                    "elapsed_seconds": round(time.time() - self.started, 1),
                    "discovery_seconds": DISCOVERY_SECONDS, "tail_seconds": TAIL_SECONDS,
                    "wallet_usd": round(self.wallet, 6), "open_mint": self.open_mint,
                    "stats": self.stats, "feeds_last_message": feed.last_msg,
                })
        finally:
            feed_task.cancel()
            await asyncio.gather(feed_task, return_exceptions=True)
            self.j.status({
                "strategy": "pumpfun_launch_sniper_v1", "mode": "paper", "wallet_usd": round(self.wallet, 6),
                "stats": self.stats, "shutdown": True, "launches_tracked": len(self.launches),
                "open_position_at_shutdown": self.position,
                "shadow_positions_at_shutdown": list(self.shadow_positions.values()),
            })
            self.j.log("SNIPER shutdown; all observed launch trades have been written to the research tape.")


def main():
    cfg = Config.from_env()
    asyncio.run(Sniper(cfg).run())


if __name__ == "__main__":
    main()
