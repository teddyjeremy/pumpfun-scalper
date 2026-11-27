"""Latency-aware wallet-guard research lanes. Paper-only; no transaction signing or sending."""
from __future__ import annotations


class LatencyAwareWalletGuard:
    """One-position multi-buyer shadow with delayed, later-slot entry and exit marks.

    Signal qualification intentionally mirrors the existing wallet guard: at least
    two distinct non-creator buyers in one transaction. The first buyer is the
    wallet locked after a losing close. Positive net flow is recorded, not gated,
    so this experiment isolates execution delay rather than changing the signal.
    """

    def __init__(
        self, journal, *, latency_seconds: float, stake_usd: float,
        take_profit: float, stop_loss: float, max_hold_seconds: int,
        confirm_seconds: float, entry_slippage: float, exit_slippage: float,
        fee_rate: float, gas_usd: float,
    ):
        self.j = journal
        self.latency_seconds = float(latency_seconds)
        self.prefix = f"sniper_latency_{int(round(self.latency_seconds * 1000)):04d}ms"
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

    def _write(self, kind: str, row: dict):
        self.j.write(f"{self.prefix}_{kind}", {
            "latency_seconds": self.latency_seconds, **row
        })

    def process(self, tx: dict):
        mint = tx.get("mint")
        price = tx.get("final_price_sol")
        observed = tx.get("observed_at_epoch")
        slot = int(tx.get("slot") or 0)
        if not mint or not price or price <= 0 or observed is None:
            return
        observed = float(observed)

        pos = self.position
        if pos:
            if pos["mint"] != mint:
                return
            if pos.get("pending_exit"):
                trigger = pos["pending_exit"]
                if (tx["signature"] != trigger["signature"]
                        and slot > trigger["slot"]
                        and observed >= trigger["observed_at_epoch"] + self.latency_seconds):
                    self._close(tx, trigger["reason"])
                return
            if tx["signature"] == pos["entry_signature"] or slot <= pos["entry_slot"]:
                return
            move = float(price) / pos["entry_price_sol"] - 1.0
            held = observed - pos["entry_observed_at_epoch"]
            reason = ("take_profit" if move >= self.take_profit else
                      "stop_loss" if move <= -self.stop_loss else
                      "time_cap" if held >= self.max_hold_seconds else None)
            if reason:
                pos["pending_exit"] = {
                    "reason": reason, "signature": tx["signature"], "slot": slot,
                    "observed_at_epoch": observed, "trigger_price_sol": float(price),
                }
                self._write("decisions", {
                    "mint": mint, "decision": "exit_triggered_waiting_for_latency",
                    "reason": reason, "trigger_signature": tx["signature"],
                    "trigger_slot": slot, "trigger_price_sol": float(price),
                })
            else:
                pos["last_price_sol"] = float(price)
                pos["last_observed_at_epoch"] = observed
                pos["last_signature"] = tx["signature"]
                pos["last_slot"] = slot
            return

        pending = self.pending.get(mint)
        if pending:
            elapsed = observed - pending["signal_observed_at_epoch"]
            if elapsed > self.confirm_seconds:
                self.pending.pop(mint, None)
                self._write("decisions", {
                    "mint": mint, "decision": "entry_expired_latency_window",
                    "elapsed_seconds": round(elapsed, 6),
                    "signal_signature": pending["signal_signature"],
                })
                return
            if (tx["signature"] == pending["signal_signature"]
                    or slot <= pending["signal_slot"]
                    or elapsed < self.latency_seconds):
                return
            if len(pending["signal_buyer_wallets"]) < 2:
                self.pending.pop(mint, None)
                self._write("decisions", {
                    "mint": mint, "decision": "rejected_single_buyer_signal_transaction",
                })
                return
            signal_wallet = pending["signal_wallet"]
            if signal_wallet and signal_wallet in self.locked_wallets:
                self.pending.pop(mint, None)
                self._write("decisions", {
                    "mint": mint, "decision": "rejected_wallet_locked",
                    "signal_wallet": signal_wallet,
                })
                return
            entry = {
                "mint": mint, "symbol": tx.get("symbol"), "creator": tx.get("creator"),
                "latency_seconds": self.latency_seconds,
                "signal_wallet": signal_wallet,
                "signal_buyer_wallets": pending["signal_buyer_wallets"],
                "signal_buyer_count": len(pending["signal_buyer_wallets"]),
                "signal_signature": pending["signal_signature"],
                "signal_slot": pending["signal_slot"],
                "signal_timestamp": pending["signal_timestamp"],
                "signal_observed_at_epoch": pending["signal_observed_at_epoch"],
                "entry_signature": tx["signature"], "entry_slot": slot,
                "entry_timestamp": tx["timestamp"],
                "entry_observed_at_epoch": observed,
                "entry_latency_seconds": round(observed - pending["signal_observed_at_epoch"], 6),
                "entry_t_plus": tx.get("t_plus"), "entry_buyer_count": tx.get("buyer_count", 0),
                "entry_buy_sol": tx.get("buy_sol", 0), "entry_sell_sol": tx.get("sell_sol", 0),
                "entry_net_buy_sol": tx.get("net_buy_sol", 0),
                "stake_usd": self.stake_usd, "entry_price_sol": float(price),
                "entry_fill_price_sol": float(price) * (1.0 + self.entry_slippage),
                "last_price_sol": float(price), "last_observed_at_epoch": observed,
                "last_signature": tx["signature"], "last_slot": slot,
            }
            self.pending.pop(mint, None)
            self.position = entry
            self._write("entries", entry)
            self._write("decisions", {
                "mint": mint, "decision": "accepted_latency_aware_entry",
                "signal_signature": pending["signal_signature"],
                "entry_signature": tx["signature"], "entry_latency_seconds": entry["entry_latency_seconds"],
            })
            return

        buyers = list(tx.get("buyer_wallets") or [])
        if len(buyers) < 1 or tx.get("creator_buy"):
            # The existing guard starts at the first non-creator buy; this avoids
            # treating creator-only transactions as a signal.
            if not buyers:
                return
        signal_wallet = tx.get("first_buyer_wallet") or buyers[0]
        if signal_wallet in self.locked_wallets:
            self._write("decisions", {
                "mint": mint, "decision": "rejected_wallet_locked",
                "signal_wallet": signal_wallet,
            })
            return
        self.pending[mint] = {
            "signal_signature": tx["signature"], "signal_slot": slot,
            "signal_timestamp": tx["timestamp"],
            "signal_observed_at_epoch": observed,
            "signal_wallet": signal_wallet, "signal_buyer_wallets": buyers,
            "signal_net_buy_sol": tx.get("net_buy_sol", 0),
            "signal_creator_sell": tx.get("creator_sell", False),
        }
        self._write("decisions", {
            "mint": mint, "decision": "multi_buyer_signal_observed",
            "signal_signature": tx["signature"], "signal_slot": slot,
            "signal_wallet": signal_wallet, "signal_buyer_wallets": buyers,
            "signal_buyer_count": len(buyers),
            "signal_net_buy_sol": tx.get("net_buy_sol", 0),
            "signal_creator_sell": tx.get("creator_sell", False),
        })

    def timer_tick(self, now: float):
        for mint, pending in list(self.pending.items()):
            elapsed = now - pending["signal_observed_at_epoch"]
            if elapsed > self.confirm_seconds:
                self.pending.pop(mint, None)
                self._write("decisions", {
                    "mint": mint, "decision": "entry_expired_latency_window",
                    "elapsed_seconds": round(elapsed, 6),
                    "signal_signature": pending["signal_signature"],
                })
        pos = self.position
        if pos and not pos.get("pending_exit"):
            if now - pos["entry_observed_at_epoch"] >= self.max_hold_seconds:
                pos["pending_exit"] = {
                    "reason": "time_cap", "signature": pos["last_signature"],
                    "slot": pos["last_slot"], "observed_at_epoch": now,
                    "trigger_price_sol": pos["last_price_sol"],
                }
                self._write("decisions", {
                    "mint": pos["mint"], "decision": "time_cap_triggered_waiting_for_latency",
                    "trigger_slot": pos["last_slot"],
                })

    def _close(self, tx: dict, reason: str):
        pos = self.position
        if not pos:
            return
        price = float(tx["final_price_sol"])
        exit_fill = price * (1.0 - self.exit_slippage)
        net_mult = (exit_fill / pos["entry_fill_price_sol"]) * ((1.0 - self.fee_rate) ** 2)
        net_pnl = self.stake_usd * (net_mult - 1.0) - self.gas_usd
        wallet = pos.get("signal_wallet")
        locked = net_pnl <= 0 and bool(wallet)
        trigger = pos["pending_exit"]
        self._write("trades", {
            **{k: v for k, v in pos.items() if k != "pending_exit"},
            "exit_signature": tx["signature"], "exit_slot": int(tx.get("slot") or 0),
            "exit_timestamp": tx["timestamp"], "exit_observed_at_epoch": tx["observed_at_epoch"],
            "exit_price_sol": price, "exit_fill_price_sol": exit_fill,
            "exit_reason": reason, "exit_trigger_signature": trigger["signature"],
            "exit_trigger_slot": trigger["slot"],
            "exit_trigger_price_sol": trigger["trigger_price_sol"],
            "exit_latency_seconds": round(float(tx["observed_at_epoch"]) - trigger["observed_at_epoch"], 6),
            "exit_latency_slots": int(tx.get("slot") or 0) - int(trigger["slot"]),
            "hold_seconds": round(float(tx["observed_at_epoch"]) - pos["entry_observed_at_epoch"], 6),
            "market_move_at_exit": round(price / pos["entry_price_sol"] - 1.0, 6),
            "net_pnl_usd": round(net_pnl, 6), "wallet_locked_after_exit": locked,
        })
        self.position = None
        if locked:
            self.locked_wallets.add(wallet)
            self._write("decisions", {
                "mint": pos["mint"], "decision": "wallet_locked_after_loss",
                "signal_wallet": wallet, "net_pnl_usd": round(net_pnl, 6),
            })
