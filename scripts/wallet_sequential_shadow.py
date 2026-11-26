#!/usr/bin/env python3
"""Independent sequential portfolio shadow replay: baseline vs wallet-flow gate.

Research only. Replays recorded, selectively captured coin tapes through the
same CoinEngine fill/exit model. Each variant has one portfolio-wide position.
"""
from __future__ import annotations
import argparse, csv, json, os, statistics
from collections import defaultdict
from pumpbot.config import Config
from pumpbot.engine import CoinEngine
from pumpbot.tape import Tape


def read_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def wallet_gate(metrics):
    """Require positive-history cohort net buying and stronger net flow than negative cohort."""
    if not metrics:
        return False
    positive = float(metrics.get("positive_wallet_net_usd_30s") or 0)
    negative = float(metrics.get("negative_wallet_net_usd_30s") or 0)
    known_positive = int(metrics.get("positive_wallets_30s") or 0)
    return known_positive >= 1 and positive > 0 and positive > negative


class Portfolio:
    def __init__(self, variant):
        self.variant = variant
        self.active_mint = None
        self.trades = []
        self.signals = 0
        self.wallet_rejected = 0
        self.position_rejected = 0

    def gate_for(self, mint, scores):
        def gate(info):
            self.signals += 1
            if self.active_mint is not None:
                self.position_rejected += 1
                return False, "portfolio_position_open", 1.0
            if self.variant == "wallet_positive_net":
                key = (mint, info["lane"], int(info["t"]))
                metrics = scores.get(key)
                if not wallet_gate(metrics):
                    self.wallet_rejected += 1
                    return False, "wallet_net_flow_not_confirmed", 1.0
            self.active_mint = mint
            return True, "ok", 1.0
        return gate

    def consume(self, mint, events):
        for kind, payload in events:
            if kind == "exited":
                self.trades.append({
                    "variant": self.variant, "mint": mint, "lane": payload["lane"],
                    "entry_t_plus": payload["entry_t"], "exit_t_plus": payload["exit_bt"],
                    "hold_s": payload["hold_s"], "exit": payload["exit"],
                    "net_per_1usd": round(float(payload["net"]), 6),
                    "mfe": round(float(payload["mfe"]), 5), "mae": round(float(payload["mae"]), 5),
                })
                if self.active_mint == mint:
                    self.active_mint = None

    def finish_mint(self, mint):
        if self.active_mint == mint:
            self.active_mint = None


def summarize(portfolio):
    values = [float(x["net_per_1usd"]) for x in portfolio.trades]
    wins = sum(x > 0 for x in values)
    losses = sum(x < 0 for x in values)
    return {
        "variant": portfolio.variant, "signals": portfolio.signals,
        "completed_trades": len(values), "wins": wins, "losses": losses,
        "breakeven": len(values) - wins - losses,
        "win_rate": round(wins / len(values), 4) if values else None,
        "losses_below_wins": losses < wins if values else False,
        "net_per_1usd": round(sum(values), 6),
        "average_net_per_1usd": round(statistics.mean(values), 6) if values else None,
        "wallet_gate_rejections": portfolio.wallet_rejected,
        "position_open_rejections": portfolio.position_rejected,
    }


def replay_variant(variant, cfg, coin_rows, tape_rows, raw_rows, scores):
    complete = {x.get("mint") for x in coin_rows}
    meta = {x.get("mint"): x for x in coin_rows}
    raw_lookup = {}
    for r in raw_rows:
        key = (r.get("mint"), int(r.get("block_timestamp") or 0), r.get("trader"),
               r.get("side"), int(r.get("slot") or 0))
        raw_lookup[key] = r
    by_mint = defaultdict(list)
    for r in tape_rows:
        if r.get("mint") in complete and r.get("price_usd"):
            by_mint[r["mint"]].append(r)

    portfolio = Portfolio(variant)
    engines, last_bt = {}, {}
    events = []
    events = []
    engines, row_counts = {}, {}
    for mint, rows in by_mint.items():
        rows.sort(key=lambda x: (int(x.get("bt") or 0), int(x.get("slot") or 0)))
        info = meta[mint]
        t0 = int(info.get("created_at") or 0)
        # Start with an empty streaming tape. CoinEngine.on_trade appends each
        # recorded trade in global timestamp order; preloading would leak future data.
        tp = Tape(mint, t0, info.get("creator"), info.get("quote_mint") or "SOL")
        engines[mint] = CoinEngine(tp, cfg, gate=portfolio.gate_for(mint, scores))
        row_counts[mint] = len(rows)
        for index, r in enumerate(rows):
            bt = int(r.get("bt") or 0)
            raw_key = (mint, bt, r.get("trader"), r.get("side"), int(r.get("slot") or 0))
            raw_event = raw_lookup.get(raw_key, {})
            tok_raw = raw_event.get("token_amount_raw")
            tok = float(tok_raw) / 1e6 if tok_raw is not None else 0.0
            trade = {
                "bt": bt, "t": int(r.get("t_plus") or 0),
                "side": r.get("side"), "spot": float(r["price_usd"]),
                "usd": float(r.get("trade_usd") or 0), "trader": r.get("trader"),
                "tok": tok, "sol": 0.0, "slot": int(r.get("slot") or 0),
            }
            events.append((bt, int(r.get("slot") or 0), mint, index, trade))

    events.sort(key=lambda x: (x[0], x[1], x[2]))
    finished = set()
    for _, _, mint, index, trade in events:
        engine = engines[mint]
        portfolio.consume(mint, engine.on_trade(trade))
        if index == row_counts[mint] - 1 and mint not in finished:
            portfolio.consume(mint, engine.finish())
            portfolio.finish_mint(mint)
            finished.add(mint)
    return portfolio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--out", default="./report")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cfg = Config.from_env()
    cfg.mode = "paper"
    summaries = read_jsonl(os.path.join(args.data_dir, "coin_summaries_paper.jsonl"))
    complete = [x for x in summaries if x.get("observation_complete") is True]
    coins = read_jsonl(os.path.join(args.data_dir, "coins_paper.jsonl"))
    coin_meta = {x.get("mint"): x for x in coins}
    complete_meta = [coin_meta[m] for m in {x.get("mint") for x in complete} if m in coin_meta]
    tape = read_jsonl(os.path.join(args.data_dir, "coin_tape_paper.jsonl"))
    raw = read_jsonl(os.path.join(args.data_dir, "coin_raw_tape_paper.jsonl"))
    score_rows = read_csv(os.path.join(args.out, "wallet_signal_scores.csv"))
    scores = {}
    for row in score_rows:
        try:
            key = (row.get("mint"), row.get("lane"), int(row.get("t_plus") or 0))
            scores[key] = row
        except (TypeError, ValueError):
            continue

    results = []
    all_trades = []
    for variant in ("portfolio_baseline", "wallet_positive_net"):
        portfolio = replay_variant(variant, cfg, complete_meta, tape, raw, scores)
        results.append(summarize(portfolio))
        all_trades.extend(portfolio.trades)

    fields = ["variant", "mint", "lane", "entry_t_plus", "exit_t_plus", "hold_s",
              "exit", "net_per_1usd", "mfe", "mae"]
    with open(os.path.join(args.out, "wallet_sequential_shadow_trades.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_trades)
    report = {
        "mode": "paper_only_sequential_shadow",
        "tape_scope": "Complete selectively captured candidate/filled-entry coin tapes only.",
        "portfolio_constraints": "Each variant has one portfolio-wide position at a time. Same CoinEngine entry/exit/fill logic and $1 notional.",
        "variants": results,
        "limitations": [
            "This is a sequential replay of captured tapes, not a live strategy or actual paper-wallet trade.",
            "Coverage is selective: only complete tapes captured by the predeclared candidate rules or baseline filled entries are replayed.",
            "A baseline and wallet-gated portfolio are compared under the same one-open-position constraint; neither is identical to the current per-coin ungated shadow series.",
            "Wallet outcomes are 60-second mark-to-market proxies, not realized wallet P&L. Wallet gate uses only scores already matured by each signal timestamp.",
            "A wallet-gated strategy cannot take a coin that was not captured; results must not be generalized to all discovered coins.",
            "Token amounts are restored from raw tape where available; missing raw rows default token balance contribution to zero and may affect creator/bundle guard features.",
            "No wallet clusters, slippage stress test, or live execution are included. K/B actual decisions and paper wallet remain unchanged."
        ]
    }
    with open(os.path.join(args.out, "wallet_sequential_shadow_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(args.out, "wallet_sequential_shadow_report.md"), "w", encoding="utf-8") as f:
        f.write("# Wallet sequential shadow replay (paper only)\n\n")
        f.write("Independent portfolio replay; actual K/B decisions and the paper wallet are unchanged.\n\n")
        f.write("| Variant | Trades | Wins | Losses | Win rate | Net / $1 | Wallet rejects | Position rejects |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|---:|\n")
        for x in results:
            f.write(f"| {x['variant']} | {x['completed_trades']} | {x['wins']} | {x['losses']} | {x['win_rate']} | {x['net_per_1usd']} | {x['wallet_gate_rejections']} | {x['position_open_rejections']} |\n")
        f.write("\n## Limitations\n\n" + "".join(f"- {x}\n" for x in report["limitations"]))
    print("Wrote wallet_sequential_shadow_trades.csv and wallet_sequential_shadow_report.json/.md")


if __name__ == "__main__":
    main()
