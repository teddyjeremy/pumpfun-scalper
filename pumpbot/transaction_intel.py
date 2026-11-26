"""Transaction-complete Pump.fun research lane. Paper-only; never signs or sends transactions."""
from __future__ import annotations

from collections import defaultdict


def _spot_sol(ev: dict) -> float:
    vq = ev.get("virtual_sol_reserves") or 0
    vt = ev.get("virtual_token_reserves") or 0
    return (vq / 1e9) / (vt / 1e6) if vq > 0 and vt > 0 else 0.0


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_transaction_summaries(events, launches: dict, signature: str, slot: int | None):
    """Collapse all decoded trade events in one Solana transaction into final per-mint state."""
    grouped = defaultdict(list)
    for kind, ev in events:
        if kind == "trade" and ev.get("mint") in launches:
            grouped[ev["mint"]].append(ev)

    summaries = []
    for mint, rows in grouped.items():
        launch = launches[mint]
        timestamp = max(int(x.get("timestamp") or launch.created_at) for x in rows)
        t_plus = timestamp - launch.created_at
        if t_plus < 0:
            continue
        buyers, sellers = set(), set()
        buy_sol = sell_sol = buy_tokens = sell_tokens = 0.0
        creator_buy = creator_sell = False
        wallet_buy_sol = defaultdict(float)
        first_price = final_price = 0.0
        for ev in rows:
            trader = ev.get("user")
            is_buy = bool(ev.get("is_buy"))
            amount_sol = _number(ev.get("sol_amount")) / 1e9
            amount_tokens = _number(ev.get("token_amount")) / 1e6
            if is_buy:
                if trader and trader != launch.creator:
                    buyers.add(trader)
                    wallet_buy_sol[trader] += amount_sol
                buy_sol += amount_sol
                buy_tokens += amount_tokens
                creator_buy = creator_buy or bool(trader and trader == launch.creator)
            else:
                if trader and trader != launch.creator:
                    sellers.add(trader)
                sell_sol += amount_sol
                sell_tokens += amount_tokens
                creator_sell = creator_sell or bool(trader and trader == launch.creator)
            price = _spot_sol(ev)
            if price:
                if not first_price:
                    first_price = price
                final_price = price
        if launch.quote_mint != "SOL":
            continue
        summaries.append({
            "mint": mint, "symbol": launch.symbol, "creator": launch.creator,
            "signature": signature, "slot": int(slot or 0), "timestamp": timestamp,
            "t_plus": t_plus, "event_count": len(rows),
            "buyer_wallets": sorted(buyers), "seller_wallets": sorted(sellers),
            "buyer_count": len(buyers), "seller_count": len(sellers),
            "buy_count": sum(bool(x.get("is_buy")) for x in rows),
            "sell_count": sum(not bool(x.get("is_buy")) for x in rows),
            "buy_sol": round(buy_sol, 9), "sell_sol": round(sell_sol, 9),
            "net_buy_sol": round(buy_sol - sell_sol, 9),
            "buy_token_amount": round(buy_tokens, 6), "sell_token_amount": round(sell_tokens, 6),
            "creator_buy": creator_buy, "creator_sell": creator_sell,
            "wallet_buy_sol": {k: round(v, 9) for k, v in sorted(wallet_buy_sol.items())},
            "first_event_price_sol": first_price or None,
            "final_price_sol": final_price or None,
            "intra_transaction_price_move": round(final_price / first_price - 1.0, 6) if first_price and final_price else None,
        })
    return summaries


class TransactionAwareLane:
    """One-position lane whose signals, fills and marks use complete transaction snapshots."""

    def __init__(self, journal, *, stake_usd, take_profit, stop_loss, max_hold_seconds,
                 confirm_seconds, entry_slippage, exit_slippage, fee_rate, gas_usd):
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
        self.pending = {}
        self.position = None

    def process(self, tx: dict):
        mint = tx["mint"]
        price = tx.get("final_price_sol")
        if not price or price <= 0:
            self.j.write("sniper_tx_decisions", {"mint": mint, "signature": tx["signature"], "decision": "ignored_missing_final_price"})
            return

        pos = self.position
        if pos and pos["mint"] == mint:
            if tx["signature"] == pos["entry_signature"]:
                return
            pos["last_price_sol"] = price
            pos["last_price_timestamp"] = tx["timestamp"]
            move = price / pos["entry_price_sol"] - 1.0
            held = tx["timestamp"] - pos["entry_timestamp"]
            reason = "take_profit" if move >= self.take_profit else "stop_loss" if move <= -self.stop_loss else "time_cap" if held >= self.max_hold_seconds else None
            if reason:
                self._close(price, tx["timestamp"], tx["t_plus"], tx["signature"], reason, 0)
            return

        pending = self.pending.get(mint)
        if pending:
            if tx["signature"] == pending["signal_signature"]:
                return
            delay = tx["timestamp"] - pending["signal_timestamp"]
            if delay < 0 or delay > self.confirm_seconds:
                self.pending.pop(mint, None)
                self.j.write("sniper_tx_decisions", {"mint": mint, "signature": tx["signature"], "decision": "expired_followup_window", "delay_seconds": delay})
                return
            if tx["buy_count"] < 1 or tx["net_buy_sol"] <= 0 or tx["creator_sell"]:
                self.pending.pop(mint, None)
                self.j.write("sniper_tx_decisions", {"mint": mint, "signature": tx["signature"], "decision": "rejected_followup_flow", "delay_seconds": delay, "net_buy_sol": tx["net_buy_sol"], "creator_sell": tx["creator_sell"]})
                return
            if self.position is not None:
                self.pending.pop(mint, None)
                self.j.write("sniper_tx_decisions", {"mint": mint, "signature": tx["signature"], "decision": "rejected_portfolio_busy", "occupied_mint": self.position["mint"]})
                return
            entry = {
                "mint": mint, "symbol": tx["symbol"], "creator": tx["creator"],
                "stake_usd": self.stake_usd,
                "signal_signature": pending["signal_signature"],
                "signal_timestamp": pending["signal_timestamp"],
                "signal_slot": pending["signal_slot"],
                "signal_buyer_wallets": pending["signal_buyer_wallets"],
                "signal_buyer_count": pending["signal_buyer_count"],
                "signal_seller_count": pending["signal_seller_count"],
                "signal_buy_sol": pending["signal_buy_sol"],
                "signal_sell_sol": pending["signal_sell_sol"],
                "signal_net_buy_sol": pending["signal_net_buy_sol"],
                "signal_creator_sell": pending["signal_creator_sell"],
                "cohort_id": pending["cohort_id"],
                "entry_signature": tx["signature"], "entry_timestamp": tx["timestamp"],
                "entry_slot": tx["slot"], "entry_t_plus": tx["t_plus"],
                "entry_delay_seconds": delay, "entry_event_count": tx["event_count"],
                "entry_buyer_count": tx["buyer_count"], "entry_seller_count": tx["seller_count"],
                "entry_buy_sol": tx["buy_sol"], "entry_sell_sol": tx["sell_sol"],
                "entry_net_buy_sol": tx["net_buy_sol"],
                "entry_intra_transaction_price_move": tx["intra_transaction_price_move"],
                "entry_price_sol": price, "entry_fill_price_sol": price * (1.0 + self.entry_slippage),
                "last_price_sol": price, "last_price_timestamp": tx["timestamp"],
            }
            self.pending.pop(mint, None)
            self.position = entry
            self.j.write("sniper_tx_entries", entry)
            self.j.write("sniper_tx_decisions", {"mint": mint, "signature": tx["signature"], "decision": "accepted_transaction_complete_entry", "cohort_id": entry["cohort_id"]})
            return

        if tx["buyer_count"] < 2:
            decision = "rejected_fewer_than_two_distinct_buyers"
        elif tx["net_buy_sol"] <= 0:
            decision = "rejected_non_positive_net_buy_flow"
        elif tx["creator_sell"]:
            decision = "rejected_creator_selling_in_signal_transaction"
        else:
            decision = None
        if decision:
            self.j.write("sniper_tx_decisions", {"mint": mint, "signature": tx["signature"], "decision": decision, "buyer_count": tx["buyer_count"], "net_buy_sol": tx["net_buy_sol"], "creator_sell": tx["creator_sell"]})
            return

        buyers = tx["buyer_wallets"]
        cohort_id = "|".join(buyers)
        self.pending[mint] = {
            "signal_signature": tx["signature"], "signal_timestamp": tx["timestamp"],
            "signal_slot": tx["slot"], "signal_buyer_wallets": buyers,
            "signal_buyer_count": tx["buyer_count"], "signal_seller_count": tx["seller_count"],
            "signal_buy_sol": tx["buy_sol"], "signal_sell_sol": tx["sell_sol"],
            "signal_net_buy_sol": tx["net_buy_sol"], "signal_creator_sell": tx["creator_sell"],
            "cohort_id": cohort_id,
        }
        self.j.write("sniper_tx_decisions", {
            "mint": mint, "signature": tx["signature"], "decision": "multi_buyer_transaction_signal",
            "cohort_id": cohort_id, "buyer_wallets": buyers, "buyer_count": tx["buyer_count"],
            "buy_sol": tx["buy_sol"], "sell_sol": tx["sell_sol"], "net_buy_sol": tx["net_buy_sol"],
            "creator_sell": tx["creator_sell"], "intra_transaction_price_move": tx["intra_transaction_price_move"],
        })

    def _close(self, price, timestamp, t_plus, signature, reason, stale_seconds):
        p = self.position
        if not p:
            return
        exit_fill = price * (1.0 - self.exit_slippage)
        net_mult = (exit_fill / p["entry_fill_price_sol"]) * ((1.0 - self.fee_rate) ** 2)
        net_pnl = p["stake_usd"] * (net_mult - 1.0) - self.gas_usd
        self.j.write("sniper_tx_trades", {
            **p, "exit_price_sol": price, "exit_fill_price_sol": exit_fill,
            "exit_timestamp": timestamp, "exit_t_plus": t_plus, "exit_signature": signature,
            "exit_reason": reason, "hold_seconds": timestamp - p["entry_timestamp"],
            "market_move_at_exit": round(price / p["entry_price_sol"] - 1.0, 6),
            "net_pnl_usd": round(net_pnl, 6), "exit_price_stale_seconds": stale_seconds,
        })
        self.position = None

    def timer_tick(self, now):
        for mint, pending in list(self.pending.items()):
            if now - pending["signal_timestamp"] > self.confirm_seconds:
                self.pending.pop(mint, None)
                self.j.write("sniper_tx_decisions", {"mint": mint, "decision": "expired_followup_window_timer", "delay_seconds": int(now - pending["signal_timestamp"]), "cohort_id": pending["cohort_id"]})
        p = self.position
        if p and now - p["entry_timestamp"] >= self.max_hold_seconds and p.get("last_price_sol"):
            stale = max(0, int(now - p.get("last_price_timestamp", now)))
            self._close(p["last_price_sol"], int(now), None, "timer", "time_cap_timer", stale)
