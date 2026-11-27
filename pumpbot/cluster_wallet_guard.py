"""Isolated wallet-loss guard shadow lane for the multi-buyer portfolio.

This lane mirrors the existing one-position multi-buyer transaction pattern, but
locks a signal wallet for the rest of the observation after its first losing
trade. It is research-only and does not change any existing lane.
"""
from __future__ import annotations


class ClusterPortfolioWalletGuard:
    """One-position multi-buyer lane with a per-session first-loss wallet lock."""

    def __init__(
        self, journal, *, stake_usd: float, take_profit: float, stop_loss: float,
        max_hold_seconds: int, confirm_seconds: int, entry_slippage: float,
        exit_slippage: float, fee_rate: float, gas_usd: float,
    ):
        self.j = journal
        self.stake_usd = stake_usd
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.max_hold_seconds = max_hold_seconds
        self.confirm_seconds = confirm_seconds
        self.entry_slippage = entry_slippage
        self.exit_slippage = exit_slippage
        self.fee_rate = fee_rate
        self.gas_usd = gas_usd
        self.pending: dict[str, dict] = {}
        self.position: dict | None = None
        self.locked_wallets: set[str] = set()

    @staticmethod
    def _kind(suffix: str) -> str:
        return f"sniper_cluster_wallet_guard_{suffix}"

    def process(self, launch, ev: dict, signature: str, price: float, t: int):
        side_buy = bool(ev.get("is_buy"))
        trader = ev.get("user")
        position = self.position

        if position and position["mint"] == launch.mint:
            if signature == position["entry_signature"]:
                return
            position["last_price_sol"] = price
            position["last_price_timestamp"] = int(ev["timestamp"])
            move = price / position["entry_price_sol"] - 1.0
            held = int(ev["timestamp"]) - position["entry_timestamp"]
            reason = (
                "take_profit" if move >= self.take_profit else
                "stop_loss" if move <= -self.stop_loss else
                "time_cap" if held >= self.max_hold_seconds else None
            )
            if reason:
                self._close(price, int(ev["timestamp"]), t, signature, reason, 0)
            return

        pending = self.pending.get(launch.mint)
        if pending:
            if signature == pending["signal_signature"]:
                if side_buy and trader and trader != launch.creator:
                    pending["signal_buyers"].add(trader)
                    pending["signal_buy_count"] += 1
                elif not side_buy:
                    pending["signal_sell_count"] += 1
                return

            delay = int(ev["timestamp"]) - pending["signal_timestamp"]
            if delay > self.confirm_seconds:
                self.pending.pop(launch.mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": launch.mint, "decision": "expired_no_following_transaction",
                    "delay_seconds": delay,
                    "signal_transaction_buyer_count": len(pending["signal_buyers"]),
                })
                return

            buyer_count = len(pending["signal_buyers"])
            if buyer_count < 2:
                self.pending.pop(launch.mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": launch.mint, "decision": "rejected_single_buyer_signal_transaction",
                    "signal_transaction_buyer_count": buyer_count,
                })
                return

            signal_wallet = pending["signal_wallet"]
            if signal_wallet in self.locked_wallets:
                self.pending.pop(launch.mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": launch.mint, "decision": "rejected_wallet_locked",
                    "signal_wallet": signal_wallet,
                    "reason": "previous_losing_trade_this_session",
                })
                return

            if self.position is not None:
                self.pending.pop(launch.mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": launch.mint, "decision": "rejected_portfolio_busy",
                    "signal_wallet": signal_wallet,
                    "occupied_mint": self.position["mint"],
                })
                return

            fill = {
                "mint": launch.mint, "symbol": launch.symbol,
                "signal_wallet": signal_wallet,
                "signal_signature": pending["signal_signature"],
                "signal_timestamp": pending["signal_timestamp"],
                "signal_transaction_buyer_count": buyer_count,
                "signal_transaction_buy_count": pending["signal_buy_count"],
                "signal_transaction_sell_count": pending["signal_sell_count"],
                "entry_signature": signature, "entry_timestamp": int(ev["timestamp"]),
                "entry_t_plus": t, "entry_delay_seconds": delay,
                "stake_usd": self.stake_usd,
                "entry_price_sol": price,
                "entry_fill_price_sol": price * (1.0 + self.entry_slippage),
                "last_price_sol": price,
                "last_price_timestamp": int(ev["timestamp"]),
            }
            self.pending.pop(launch.mint, None)
            self.position = fill
            self.j.write(self._kind("entries"), fill)
            self.j.write(self._kind("decisions"), {
                "mint": launch.mint, "decision": "accepted_wallet_guard_entry",
                "signal_wallet": signal_wallet,
                "signal_transaction_buyer_count": buyer_count,
            })
            return

        if side_buy and signature != launch.create_signature and launch.first_buy_signature is None:
            if trader and trader == launch.creator:
                return
            if trader and trader in self.locked_wallets:
                self.j.write(self._kind("decisions"), {
                    "mint": launch.mint, "decision": "rejected_wallet_locked",
                    "signal_wallet": trader,
                    "reason": "previous_losing_trade_this_session",
                })
                return
            self.pending[launch.mint] = {
                "signal_signature": signature,
                "signal_timestamp": int(ev["timestamp"]),
                "signal_wallet": trader,
                "signal_buyers": {trader} if trader else set(),
                "signal_buy_count": 1,
                "signal_sell_count": 0,
            }

    def _close(self, price: float, timestamp: int, t_plus: int, signature: str,
               reason: str, stale_seconds: int):
        position = self.position
        if not position:
            return
        exit_fill = price * (1.0 - self.exit_slippage)
        net_mult = (exit_fill / position["entry_fill_price_sol"]) * ((1.0 - self.fee_rate) ** 2)
        net_pnl = position["stake_usd"] * (net_mult - 1.0) - self.gas_usd
        wallet = position.get("signal_wallet")
        locked_after_exit = net_pnl <= 0 and bool(wallet)
        self.j.write(self._kind("trades"), {
            **position,
            "exit_price_sol": price, "exit_fill_price_sol": exit_fill,
            "exit_timestamp": timestamp, "exit_t_plus": t_plus,
            "exit_signature": signature, "exit_reason": reason,
            "hold_seconds": timestamp - position["entry_timestamp"],
            "market_move_at_exit": round(price / position["entry_price_sol"] - 1.0, 6),
            "net_pnl_usd": round(net_pnl, 6),
            "exit_price_stale_seconds": stale_seconds,
            "wallet_locked_after_exit": locked_after_exit,
        })
        self.position = None
        if locked_after_exit:
            self.locked_wallets.add(wallet)
            self.j.write(self._kind("decisions"), {
                "mint": position["mint"], "decision": "wallet_locked_after_loss",
                "signal_wallet": wallet, "net_pnl_usd": round(net_pnl, 6),
                "locked_wallet_count": len(self.locked_wallets),
            })

    def timer_tick(self, now: float):
        for mint, pending in list(self.pending.items()):
            if now - pending["signal_timestamp"] > self.confirm_seconds:
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "decision": "expired_no_following_transaction",
                    "delay_seconds": int(now - pending["signal_timestamp"]),
                    "signal_transaction_buyer_count": len(pending["signal_buyers"]),
                })
        position = self.position
        if position and now - position["entry_timestamp"] >= self.max_hold_seconds and position.get("last_price_sol"):
            stale = max(0, int(now - position.get("last_price_timestamp", now)))
            self._close(position["last_price_sol"], int(now), 0, "timer", "time_cap_timer", stale)
