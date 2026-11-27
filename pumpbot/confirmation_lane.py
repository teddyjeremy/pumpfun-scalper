"""High-conviction, execution-lagged Pump.fun paper research lane.

This lane is isolated from the baseline, K/B scalper, and existing research lanes.
It requires a multi-buyer signal, a strong multi-buyer confirmation transaction,
then a separate positive-flow execution transaction. Entry and exit marks use
conservative within-transaction prices.
"""
from __future__ import annotations


def _valid_prices(tx: dict) -> list[float]:
    values = []
    for key in ("first_event_price_sol", "final_price_sol"):
        try:
            value = float(tx.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            values.append(value)
    return values


def _buy_mark(tx: dict) -> float:
    """Worst observed price for a simulated buy within the execution transaction."""
    prices = _valid_prices(tx)
    return max(prices) if prices else 0.0


def _sell_mark(tx: dict) -> float:
    """Worst observed price for a simulated sell within the exit transaction."""
    prices = _valid_prices(tx)
    return min(prices) if prices else 0.0


class HighConvictionConfirmationLane:
    """One-position lane with signal -> confirmation -> later execution sequencing."""

    def __init__(
        self, journal, *, stake_usd: float, take_profit: float, stop_loss: float,
        max_hold_seconds: int, confirm_seconds: int, entry_slippage: float,
        exit_slippage: float, fee_rate: float, gas_usd: float,
        min_confirmation_buyers: int = 2, min_confirmation_buy_sol: float = 0.5,
        min_confirmation_move: float = 0.05,
        event_prefix: str = "sniper_v2",
        max_entry_age_seconds: int | None = None,
        one_entry_per_mint: bool = False,
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
        self.min_confirmation_buyers = min_confirmation_buyers
        self.min_confirmation_buy_sol = min_confirmation_buy_sol
        self.min_confirmation_move = min_confirmation_move
        self.event_prefix = event_prefix
        self.max_entry_age_seconds = max_entry_age_seconds
        self.one_entry_per_mint = one_entry_per_mint
        self.used_mints: set[str] = set()
        self.pending: dict[str, dict] = {}
        self.position: dict | None = None

    def _kind(self, suffix: str) -> str:
        return f"{self.event_prefix}_{suffix}"

    def process(self, tx: dict):
        mint = tx["mint"]
        signature = tx["signature"]
        timestamp = int(tx["timestamp"])
        sequence = int(tx.get("observation_sequence") or 0)

        if self.position and self.position["mint"] == mint:
            pos = self.position
            if signature == pos["entry_signature"]:
                return
            price = _sell_mark(tx)
            if not price:
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "ignored_exit_missing_price",
                })
                return
            pos["last_price_sol"] = price
            pos["last_price_timestamp"] = timestamp
            pos["last_price_sequence"] = sequence
            move = price / pos["entry_price_sol"] - 1.0
            held = timestamp - pos["entry_timestamp"]
            reason = (
                "take_profit" if move >= self.take_profit else
                "stop_loss" if move <= -self.stop_loss else
                "time_cap" if held >= self.max_hold_seconds else None
            )
            if reason:
                self._close(price, timestamp, tx.get("t_plus"), signature, reason, 0, sequence)
            return

        pending = self.pending.get(mint)
        if pending and pending["stage"] == "signal":
            if signature == pending["signal_signature"]:
                return
            delay = timestamp - pending["signal_timestamp"]
            if sequence and pending.get("signal_sequence") and sequence <= pending["signal_sequence"]:
                return
            if delay < 0 or delay > self.confirm_seconds:
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "expired_confirmation_window", "delay_seconds": delay,
                })
                return

            move = tx.get("intra_transaction_price_move")
            try:
                move = float(move) if move is not None else None
            except (TypeError, ValueError):
                move = None
            failures = []
            if int(tx.get("buyer_count") or 0) < self.min_confirmation_buyers:
                failures.append("too_few_confirmation_buyers")
            if float(tx.get("buy_sol") or 0) < self.min_confirmation_buy_sol:
                failures.append("confirmation_buy_volume_too_low")
            if float(tx.get("net_buy_sol") or 0) <= 0:
                failures.append("confirmation_net_flow_not_positive")
            if tx.get("creator_sell"):
                failures.append("creator_selling_in_confirmation")
            if move is None or move < self.min_confirmation_move:
                failures.append("confirmation_momentum_too_weak")
            if failures:
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "rejected_confirmation_quality",
                    "reasons": failures, "buyer_count": int(tx.get("buyer_count") or 0),
                    "buy_sol": float(tx.get("buy_sol") or 0),
                    "net_buy_sol": float(tx.get("net_buy_sol") or 0),
                    "intra_transaction_price_move": move,
                })
                return

            pending.update({
                "stage": "confirmed",
                "confirmation_signature": signature,
                "confirmation_timestamp": timestamp,
                "confirmation_sequence": sequence,
                "confirmation_slot": int(tx.get("slot") or 0),
                "confirmation_buyer_count": int(tx.get("buyer_count") or 0),
                "confirmation_buy_sol": float(tx.get("buy_sol") or 0),
                "confirmation_net_buy_sol": float(tx.get("net_buy_sol") or 0),
                "confirmation_intra_transaction_price_move": move,
            })
            self.j.write(self._kind("decisions"), {
                "mint": mint, "signature": signature,
                "decision": "confirmation_passed_waiting_for_later_execution",
                "buyer_count": pending["confirmation_buyer_count"],
                "buy_sol": pending["confirmation_buy_sol"],
                "intra_transaction_price_move": move,
            })
            return

        if pending and pending["stage"] == "confirmed":
            if signature == pending["confirmation_signature"]:
                return
            delay = timestamp - pending["confirmation_timestamp"]
            if sequence and pending.get("confirmation_sequence") and sequence <= pending["confirmation_sequence"]:
                return
            if delay < 0 or delay > self.confirm_seconds:
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "expired_execution_window", "delay_seconds": delay,
                })
                return
            if (
                int(tx.get("buyer_count") or 0) < 1
                or int(tx.get("buy_count") or 0) < 1
                or float(tx.get("net_buy_sol") or 0) <= 0
                or tx.get("creator_sell")
            ):
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "rejected_execution_flow",
                    "buyer_count": int(tx.get("buyer_count") or 0),
                    "net_buy_sol": float(tx.get("net_buy_sol") or 0),
                    "creator_sell": bool(tx.get("creator_sell")),
                })
                return
            entry_age = tx.get("t_plus")
            if self.max_entry_age_seconds is not None and (
                entry_age is None or int(entry_age) > self.max_entry_age_seconds
            ):
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "rejected_entry_age",
                    "entry_t_plus": entry_age,
                    "max_entry_age_seconds": self.max_entry_age_seconds,
                })
                return
            if self.one_entry_per_mint and mint in self.used_mints:
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "rejected_mint_already_traded",
                })
                return
            if self.position is not None:
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "rejected_portfolio_busy",
                    "occupied_mint": self.position["mint"],
                })
                return

            price = _buy_mark(tx)
            if not price:
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint, "signature": signature,
                    "decision": "rejected_execution_missing_price",
                })
                return
            exit_mark = _sell_mark(tx) or price
            entry = {
                "mint": mint, "symbol": tx.get("symbol") or mint[:8],
                "creator": tx.get("creator"), "stake_usd": self.stake_usd,
                "signal_signature": pending["signal_signature"],
                "signal_timestamp": pending["signal_timestamp"],
                "signal_sequence": pending.get("signal_sequence"),
                "signal_slot": pending.get("signal_slot"),
                "signal_buyer_wallets": pending["signal_buyer_wallets"],
                "signal_buyer_count": pending["signal_buyer_count"],
                "signal_buy_sol": pending["signal_buy_sol"],
                "signal_net_buy_sol": pending["signal_net_buy_sol"],
                "confirmation_signature": pending["confirmation_signature"],
                "confirmation_timestamp": pending["confirmation_timestamp"],
                "confirmation_sequence": pending.get("confirmation_sequence"),
                "confirmation_slot": pending["confirmation_slot"],
                "confirmation_buyer_count": pending["confirmation_buyer_count"],
                "confirmation_buy_sol": pending["confirmation_buy_sol"],
                "confirmation_net_buy_sol": pending["confirmation_net_buy_sol"],
                "confirmation_intra_transaction_price_move": pending["confirmation_intra_transaction_price_move"],
                "entry_signature": signature, "entry_timestamp": timestamp,
                "entry_sequence": sequence, "entry_slot": int(tx.get("slot") or 0),
                "entry_t_plus": tx.get("t_plus"),
                "entry_delay_from_confirmation_seconds": delay,
                "entry_event_count": int(tx.get("event_count") or 0),
                "entry_buyer_count": int(tx.get("buyer_count") or 0),
                "entry_buy_sol": float(tx.get("buy_sol") or 0),
                "entry_net_buy_sol": float(tx.get("net_buy_sol") or 0),
                "entry_first_event_price_sol": tx.get("first_event_price_sol"),
                "entry_final_price_sol": tx.get("final_price_sol"),
                "entry_price_basis": "max_first_and_final_event_price",
                "entry_price_sol": price,
                "entry_fill_price_sol": price * (1.0 + self.entry_slippage),
                "last_price_sol": exit_mark,
                "last_price_timestamp": timestamp,
                "last_price_sequence": sequence,
            }
            self.pending.pop(mint, None)
            self.position = entry
            self.used_mints.add(mint)
            self.j.write(self._kind("entries"), entry)
            self.j.write(self._kind("decisions"), {
                "mint": mint, "signature": signature,
                "decision": "accepted_high_conviction_entry",
                "signal_signature": entry["signal_signature"],
                "confirmation_signature": entry["confirmation_signature"],
                "entry_price_sol": price,
            })
            return

        if pending:
            return

        if int(tx.get("buyer_count") or 0) < 2:
            return
        if float(tx.get("net_buy_sol") or 0) <= 0 or tx.get("creator_sell"):
            self.j.write(self._kind("decisions"), {
                "mint": mint, "signature": signature,
                "decision": "rejected_signal_flow",
                "buyer_count": int(tx.get("buyer_count") or 0),
                "net_buy_sol": float(tx.get("net_buy_sol") or 0),
                "creator_sell": bool(tx.get("creator_sell")),
            })
            return
        self.pending[mint] = {
            "stage": "signal",
            "signal_signature": signature,
            "signal_timestamp": timestamp,
            "signal_sequence": sequence,
            "signal_slot": int(tx.get("slot") or 0),
            "signal_buyer_wallets": list(tx.get("buyer_wallets") or []),
            "signal_buyer_count": int(tx.get("buyer_count") or 0),
            "signal_buy_sol": float(tx.get("buy_sol") or 0),
            "signal_net_buy_sol": float(tx.get("net_buy_sol") or 0),
        }
        self.j.write(self._kind("decisions"), {
            "mint": mint, "signature": signature,
            "decision": "multi_buyer_signal_waiting_for_confirmation",
            "buyer_count": int(tx.get("buyer_count") or 0),
            "buy_sol": float(tx.get("buy_sol") or 0),
            "net_buy_sol": float(tx.get("net_buy_sol") or 0),
        })

    def _close(self, price, timestamp, t_plus, signature, reason, stale_seconds, sequence):
        p = self.position
        if not p:
            return
        exit_fill = price * (1.0 - self.exit_slippage)
        net_mult = (exit_fill / p["entry_fill_price_sol"]) * ((1.0 - self.fee_rate) ** 2)
        net_pnl = p["stake_usd"] * (net_mult - 1.0) - self.gas_usd
        self.j.write(self._kind("trades"), {
            **p, "exit_price_sol": price, "exit_fill_price_sol": exit_fill,
            "exit_timestamp": timestamp, "exit_t_plus": t_plus,
            "exit_signature": signature, "exit_sequence": sequence,
            "exit_price_basis": "min_first_and_final_event_price",
            "exit_reason": reason, "hold_seconds": timestamp - p["entry_timestamp"],
            "market_move_at_exit": round(price / p["entry_price_sol"] - 1.0, 6),
            "net_pnl_usd": round(net_pnl, 6),
            "exit_price_stale_seconds": stale_seconds,
        })
        self.position = None

    def timer_tick(self, now):
        for mint, pending in list(self.pending.items()):
            start = pending["signal_timestamp"] if pending["stage"] == "signal" else pending["confirmation_timestamp"]
            if now - start > self.confirm_seconds:
                self.pending.pop(mint, None)
                self.j.write(self._kind("decisions"), {
                    "mint": mint,
                    "decision": "expired_confirmation_timer" if pending["stage"] == "signal" else "expired_execution_timer",
                    "delay_seconds": int(now - start),
                })
        p = self.position
        if p and now - p["entry_timestamp"] >= self.max_hold_seconds and p.get("last_price_sol"):
            stale = max(0, int(now - p.get("last_price_timestamp", now)))
            self._close(p["last_price_sol"], int(now), None, "timer", "time_cap_timer", stale, None)
