#!/usr/bin/env python3
"""Offline execution-quality ablation replay for the Pump.fun sniper.

Replays the exact transaction-summary stream through isolated, one-position
paper portfolios. It does not alter or feed back into any live lane.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter

VARIANTS = {
    "control_existing_execution": {"min_buy_sol": 0.0, "min_buyers": 1},
    "execution_buy_0_5_sol": {"min_buy_sol": 0.5, "min_buyers": 1},
    "execution_buy_1_sol": {"min_buy_sol": 1.0, "min_buyers": 1},
    "execution_buy_2_sol": {"min_buy_sol": 2.0, "min_buyers": 1},
    "execution_buy_0_5_sol_and_2_buyers": {"min_buy_sol": 0.5, "min_buyers": 2},
}


def number(row, key):
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def buy_mark(tx):
    prices = [number(tx, k) for k in ("first_event_price_sol", "final_price_sol")]
    prices = [x for x in prices if x > 0]
    return max(prices) if prices else 0.0


def sell_mark(tx):
    prices = [number(tx, k) for k in ("first_event_price_sol", "final_price_sol")]
    prices = [x for x in prices if x > 0]
    return min(prices) if prices else 0.0


class ExecutionFilterReplay:
    def __init__(self, name, rules, *, stake=1.0, tp=0.20, sl=0.12,
                 max_hold=30, confirm_seconds=3, entry_slippage=0.015,
                 exit_slippage=0.015, fee_rate=0.0125, gas=0.0285):
        self.name, self.rules = name, rules
        self.stake, self.tp, self.sl = stake, tp, sl
        self.max_hold, self.confirm_seconds = max_hold, confirm_seconds
        self.entry_slippage, self.exit_slippage = entry_slippage, exit_slippage
        self.fee_rate, self.gas = fee_rate, gas
        self.pending = {}
        self.position = None
        self.entries, self.trades = [], []
        self.decisions = Counter()

    def _decision(self, name):
        self.decisions[name] += 1

    def _close(self, price, timestamp, reason, stale=0, signature=None):
        p = self.position
        if not p or price <= 0:
            return
        exit_fill = price * (1 - self.exit_slippage)
        net_mult = (exit_fill / p["entry_fill_price_sol"]) * ((1 - self.fee_rate) ** 2)
        pnl = self.stake * (net_mult - 1) - self.gas
        self.trades.append({
            **p, "exit_timestamp": timestamp, "exit_signature": signature,
            "exit_price_sol": price, "exit_fill_price_sol": exit_fill,
            "exit_reason": reason, "hold_seconds": timestamp - p["entry_timestamp"],
            "exit_price_stale_seconds": stale,
            "market_move_at_exit": price / p["entry_price_sol"] - 1,
            "net_pnl_usd": round(pnl, 6),
        })
        self.position = None

    def _timer_before(self, timestamp):
        p = self.position
        if p and timestamp - p["entry_timestamp"] >= self.max_hold:
            close_at = p["entry_timestamp"] + self.max_hold
            stale = max(0, close_at - p["last_price_timestamp"])
            self._close(p["last_price_sol"], close_at, "time_cap_timer", stale, "timer")
        for mint, pending in list(self.pending.items()):
            start = pending["signal_timestamp"] if pending["stage"] == "signal" else pending["confirmation_timestamp"]
            if timestamp - start > self.confirm_seconds:
                self.pending.pop(mint, None)
                self._decision("expired_" + pending["stage"] + "_window")

    def process(self, tx):
        ts = int(tx.get("timestamp") or 0)
        self._timer_before(ts)
        mint, signature = tx["mint"], tx["signature"]

        if self.position and self.position["mint"] == mint:
            if signature == self.position["entry_signature"]:
                return
            price = sell_mark(tx)
            if not price:
                self._decision("ignored_exit_missing_price")
                return
            self.position["last_price_sol"] = price
            self.position["last_price_timestamp"] = ts
            move = price / self.position["entry_price_sol"] - 1
            held = ts - self.position["entry_timestamp"]
            reason = "take_profit" if move >= self.tp else "stop_loss" if move <= -self.sl else "time_cap" if held >= self.max_hold else None
            if reason:
                self._close(price, ts, reason, 0, signature)
            return

        pending = self.pending.get(mint)
        if pending and pending["stage"] == "signal":
            if signature == pending["signal_signature"]:
                return
            delay = ts - pending["signal_timestamp"]
            if delay < 0 or delay > self.confirm_seconds:
                self.pending.pop(mint, None)
                self._decision("expired_confirmation_window")
                return
            move = tx.get("intra_transaction_price_move")
            try:
                move = float(move) if move is not None else None
            except (TypeError, ValueError):
                move = None
            if (int(tx.get("buyer_count") or 0) < 2
                    or number(tx, "buy_sol") < 0.5
                    or number(tx, "net_buy_sol") <= 0
                    or tx.get("creator_sell")
                    or move is None or move < 0.05):
                self.pending.pop(mint, None)
                self._decision("rejected_confirmation_quality")
                return
            pending.update({
                "stage": "confirmed", "confirmation_signature": signature,
                "confirmation_timestamp": ts,
                "confirmation_buyer_count": int(tx.get("buyer_count") or 0),
                "confirmation_buy_sol": number(tx, "buy_sol"),
                "confirmation_net_buy_sol": number(tx, "net_buy_sol"),
                "confirmation_intra_transaction_price_move": move,
            })
            self._decision("confirmation_passed")
            return

        if pending and pending["stage"] == "confirmed":
            if signature == pending["confirmation_signature"]:
                return
            delay = ts - pending["confirmation_timestamp"]
            if delay < 0 or delay > self.confirm_seconds:
                self.pending.pop(mint, None)
                self._decision("expired_execution_window")
                return
            buyers = int(tx.get("buyer_count") or 0)
            buy_sol = number(tx, "buy_sol")
            if (buyers < 1 or int(tx.get("buy_count") or 0) < 1
                    or number(tx, "net_buy_sol") <= 0 or tx.get("creator_sell")):
                self.pending.pop(mint, None)
                self._decision("rejected_execution_flow")
                return
            if buyers < self.rules["min_buyers"]:
                self.pending.pop(mint, None)
                self._decision("rejected_execution_buyer_count")
                return
            if buy_sol < self.rules["min_buy_sol"]:
                self.pending.pop(mint, None)
                self._decision("rejected_execution_buy_volume")
                return
            if self.position is not None:
                self.pending.pop(mint, None)
                self._decision("rejected_portfolio_busy")
                return
            price = buy_mark(tx)
            if not price:
                self.pending.pop(mint, None)
                self._decision("rejected_execution_missing_price")
                return
            entry = {
                "mint": mint, "symbol": tx.get("symbol") or mint[:8],
                "signal_signature": pending["signal_signature"],
                "confirmation_signature": pending["confirmation_signature"],
                "entry_signature": signature, "entry_timestamp": ts,
                "entry_t_plus": tx.get("t_plus"),
                "entry_buyer_count": buyers, "entry_buy_sol": buy_sol,
                "entry_net_buy_sol": number(tx, "net_buy_sol"),
                "entry_sell_count": int(tx.get("sell_count") or 0),
                "entry_seller_count": int(tx.get("seller_count") or 0),
                "entry_creator_sell": bool(tx.get("creator_sell")),
                "entry_event_count": int(tx.get("event_count") or 0),
                "confirmation_buy_sol": pending["confirmation_buy_sol"],
                "confirmation_buyer_count": pending["confirmation_buyer_count"],
                "confirmation_intra_transaction_price_move": pending["confirmation_intra_transaction_price_move"],
                "entry_price_sol": price,
                "entry_fill_price_sol": price * (1 + self.entry_slippage),
                "last_price_sol": sell_mark(tx) or price,
                "last_price_timestamp": ts,
            }
            self.pending.pop(mint, None)
            self.position = entry
            self.entries.append(entry.copy())
            self._decision("accepted_entry")
            return

        if pending:
            return
        if (int(tx.get("buyer_count") or 0) < 2
                or number(tx, "net_buy_sol") <= 0 or tx.get("creator_sell")):
            if int(tx.get("buyer_count") or 0) >= 2:
                self._decision("rejected_signal_flow")
            return
        self.pending[mint] = {
            "stage": "signal", "signal_signature": signature,
            "signal_timestamp": ts,
        }
        self._decision("multi_buyer_signal")

    def finish(self, last_timestamp):
        self._timer_before(int(last_timestamp))
        if self.position and int(last_timestamp) - self.position["entry_timestamp"] >= self.max_hold:
            p = self.position
            close_at = p["entry_timestamp"] + self.max_hold
            self._close(p["last_price_sol"], close_at, "time_cap_timer",
                        max(0, close_at - p["last_price_timestamp"]), "timer")

    def report(self):
        pnls = [float(x["net_pnl_usd"]) for x in self.trades]
        wins = sum(x > 0 for x in pnls)
        gross_profit = sum(x for x in pnls if x > 0)
        gross_loss = abs(sum(x for x in pnls if x < 0))
        equity = peak = drawdown = 0.0
        for trade in sorted(self.trades, key=lambda x: (x["exit_timestamp"], x.get("exit_signature") or "")):
            equity += float(trade["net_pnl_usd"])
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)
        return {
            "variant": self.name, **self.rules,
            "entries": len(self.entries), "closed_trades": len(self.trades),
            "unclosed_entries": len(self.entries) - len(self.trades),
            "wins": wins, "losses": len(pnls) - wins,
            "win_rate": wins / len(pnls) if pnls else None,
            "net_pnl_usd": round(sum(pnls), 6),
            "average_net_pnl_usd": round(sum(pnls) / len(pnls), 6) if pnls else None,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
            "max_drawdown_usd": round(drawdown, 6),
            "exit_reasons": dict(Counter(x["exit_reason"] for x in self.trades)),
            "decisions": dict(self.decisions),
            "entries_detail": self.entries,
            "trades_detail": self.trades,
        }


def replay(transactions, **kwargs):
    variants = {
        name: ExecutionFilterReplay(name, rules, **kwargs)
        for name, rules in VARIANTS.items()
    }
    ordered = sorted(enumerate(transactions), key=lambda pair: (
        int(pair[1].get("observation_sequence") or 0),
        int(pair[1].get("timestamp") or 0), pair[0]))
    for _, tx in ordered:
        for lane in variants.values():
            lane.process(tx)
    last_ts = max((int(x.get("timestamp") or 0) for x in transactions), default=0)
    for lane in variants.values():
        lane.finish(last_ts)
    return {
        "method": "offline_chronological_replay_of_identical_transaction_summaries",
        "paper_only": True,
        "variants": [lane.report() for lane in variants.values()],
        "limitations": [
            "All variants replay the same captured transaction-summary stream; they are not independent market samples.",
            "Each variant has its own one-position paper portfolio and fixed $1 notional.",
            "Entry and exit use conservative first/final reserve-derived marks, not guaranteed executable fills.",
            "Timer exits use the last observed price and may be stale.",
            "The existing high-conviction signal and confirmation rules are held constant; only execution buy-volume and buyer-count gates vary.",
            "Results are exploratory and do not establish profitability or predict live execution.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out", default="./report")
    args = parser.parse_args()
    source = os.path.join(args.data_dir, "sniper_transactions_sniper.jsonl")
    rows = []
    if os.path.exists(source):
        with open(source, encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    result = replay(rows)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "execution_filter_ablation.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    lines = [
        "# Execution-quality ablation (paper replay)", "",
        f"- Transaction summaries replayed: **{len(rows)}**",
        "- All variants use the same signal and confirmation rules, fixed $1 paper stake, conservative price marks, and the same cost assumptions.",
        "- The only differences are minimum buy SOL and distinct buyer count in the later execution transaction.", "",
        "| Variant | Exec buy min | Exec buyers min | Entries | Closed | Wins | Losses | Win rate | Net P&L | PF | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["variants"]:
        rate = f'{row["win_rate"]:.1%}' if row["win_rate"] is not None else "n/a"
        pf = f'{row["profit_factor"]:.2f}' if row["profit_factor"] is not None else "n/a"
        lines.append(
            f'| {row["variant"]} | {row["min_buy_sol"]:.2f} SOL | {row["min_buyers"]} | '
            f'{row["entries"]} | {row["closed_trades"]} | {row["wins"]} | {row["losses"]} | '
            f'{rate} | ${row["net_pnl_usd"]:.4f} | {pf} | ${row["max_drawdown_usd"]:.4f} |'
        )
    lines += ["", "## Per-variant decisions"]
    for row in result["variants"]:
        lines.append(f'- **{row["variant"]}:** {row["decisions"]}; exits {row["exit_reasons"]}; unclosed {row["unclosed_entries"]}')
    lines += ["", "## Limitations"] + [f"- {item}" for item in result["limitations"]]
    with open(os.path.join(args.out, "execution_filter_ablation.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "variants"} |
                     {"variants": [{k: v for k, v in x.items() if k not in ("entries_detail", "trades_detail")} for x in result["variants"]]}, indent=2))


if __name__ == "__main__":
    main()
