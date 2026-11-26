#!/usr/bin/env python3
"""Causal, paper-only wallet-history ledger and signal-cohort diagnostics."""
from __future__ import annotations
import argparse, csv, json, os, statistics
from collections import defaultdict
from datetime import datetime, timezone

VERSION = 1
HORIZONS = (60, 120)
MIN_OUTCOMES = 5
SHRINKAGE_N = 5


def read_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except json.JSONDecodeError:
                    pass
    return rows


def read_ledger(path):
    if not path or not os.path.exists(path):
        return {"outcomes": [], "activity": {}}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != VERSION:
        raise ValueError(f"Unsupported wallet ledger version: {data.get('version')}")
    return {"outcomes": data.get("outcomes", []), "activity": data.get("activity", {})}


def build_outcomes(tape, summaries):
    complete = {x.get("mint") for x in summaries if x.get("observation_complete") is True}
    by_mint = defaultdict(list)
    for row in tape:
        if row.get("mint") in complete and row.get("trader") and row.get("bt") is not None and row.get("price_usd"):
            by_mint[row["mint"]].append(row)
    outcomes = []
    for mint, rows in by_mint.items():
        rows.sort(key=lambda x: (int(x["bt"]), int(x.get("slot") or 0)))
        prices = [(int(x["bt"]), float(x["price_usd"])) for x in rows]
        first_buys = {}
        for row in rows:
            if row.get("side") == "buy":
                first_buys.setdefault(row["trader"], (int(row["bt"]), float(row["price_usd"])))
        for wallet, (buy_bt, buy_px) in first_buys.items():
            if buy_px <= 0:
                continue
            for horizon in HORIZONS:
                future = next(((bt, px) for bt, px in prices if bt >= buy_bt + horizon), None)
                if future:
                    matured_bt, matured_px = future
                    outcomes.append({"wallet": wallet, "mint": mint, "horizon_s": horizon,
                                     "first_buy_bt": buy_bt, "matured_bt": matured_bt,
                                     "return": matured_px / buy_px - 1.0})
    return outcomes


def wallet_profile(wallet, outcomes, prior_activity):
    history = {x["mint"]: x for x in outcomes
               if x.get("wallet") == wallet and int(x.get("horizon_s", 0)) == 60}
    values = [max(-0.50, min(1.00, float(x["return"]))) for x in history.values()]
    n = len(values)
    mean = statistics.mean(values) if values else 0.0
    shrunk = n / (n + SHRINKAGE_N) * mean if n else 0.0
    hit = sum(v > 0 for v in values) / n if n else None
    activity = prior_activity.get(wallet, {})
    high_frequency = int(activity.get("events", 0)) >= 100 or len(activity.get("mints", [])) >= 20
    if high_frequency:
        category = "high_frequency"
    elif n < MIN_OUTCOMES:
        category = "unknown"
    elif shrunk >= 0.03 and hit >= 0.55:
        category = "positive_history"
    elif shrunk <= -0.03 and hit <= 0.45:
        category = "negative_history"
    else:
        category = "neutral"
    return {"category": category, "outcomes_60s": n,
            "hit_rate_60s": round(hit, 4) if hit is not None else None,
            "shrunk_mean_return_60s": round(shrunk, 5) if n else None}


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--out", default="./report")
    ap.add_argument("--history", default="")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    tape = read_jsonl(os.path.join(args.data_dir, "coin_tape_paper.jsonl"))
    summaries = read_jsonl(os.path.join(args.data_dir, "coin_summaries_paper.jsonl"))
    signals = read_jsonl(os.path.join(args.data_dir, "signals_paper.jsonl"))
    coins = read_jsonl(os.path.join(args.data_dir, "coins_paper.jsonl"))
    prior = read_ledger(args.history)
    previous, prior_activity = prior["outcomes"], prior["activity"]
    fresh = build_outcomes(tape, summaries)
    seen = {(x["wallet"], x["mint"], int(x["horizon_s"])) for x in previous}
    added = [x for x in fresh if (x["wallet"], x["mint"], int(x["horizon_s"])) not in seen]
    all_outcomes = previous + added

    activity = defaultdict(lambda: {"events": 0, "mints": set()})
    tape_by_mint = defaultdict(list)
    for row in tape:
        wallet, mint = row.get("trader"), row.get("mint")
        if wallet:
            activity[wallet]["events"] += 1
            if mint:
                activity[wallet]["mints"].add(mint)
        if mint:
            tape_by_mint[mint].append(row)
    merged_activity = {}
    for wallet in set(prior_activity) | set(activity):
        old, current = prior_activity.get(wallet, {}), activity.get(wallet, {})
        merged_activity[wallet] = {
            "events": int(old.get("events", 0)) + int(current.get("events", 0)),
            "mints": sorted(set(old.get("mints", [])) | set(current.get("mints", set())))
        }

    created = {x.get("mint"): int(x.get("created_at") or 0) for x in coins}
    signal_rows, categories = [], defaultdict(int)
    for signal in signals:
        mint = signal.get("mint")
        if mint not in created:
            continue
        signal_bt = created[mint] + int(signal.get("t_plus") or 0)
        buys, sells = defaultdict(float), defaultdict(float)
        for trade in tape_by_mint.get(mint, []):
            bt, wallet = int(trade.get("bt") or 0), trade.get("trader")
            if wallet and signal_bt - 30 <= bt <= signal_bt:
                target = buys if trade.get("side") == "buy" else sells
                target[wallet] += float(trade.get("trade_usd") or 0)
        buy_vol, sell_vol = defaultdict(float), defaultdict(float)
        counts = defaultdict(int)
        for wallet in set(buys) | set(sells):
            available = [x for x in all_outcomes if x.get("wallet") == wallet
                         and int(x.get("horizon_s", 0)) == 60
                         and int(x.get("matured_bt", 0)) <= signal_bt]
            category = wallet_profile(wallet, available, prior_activity)["category"]
            buy_vol[category] += buys.get(wallet, 0.0)
            sell_vol[category] += sells.get(wallet, 0.0)
            if buys.get(wallet, 0) > 0:
                counts[category] += 1
        known = counts["positive_history"] + counts["negative_history"] + counts["neutral"]
        total_buy, total_sell = sum(buys.values()), sum(sells.values())
        row = {
            "mint": mint, "symbol": signal.get("symbol"), "lane": signal.get("lane"),
            "t_plus": signal.get("t_plus"), "signal_bt": signal_bt, "baseline_taken": signal.get("taken"),
            "recent_buyers_30s": len(buys), "recent_sellers_30s": len(sells), "known_wallets_30s": known,
            "positive_wallets_30s": counts["positive_history"], "negative_wallets_30s": counts["negative_history"],
            "neutral_wallets_30s": counts["neutral"], "unknown_wallets_30s": counts["unknown"],
            "high_frequency_wallets_30s": counts["high_frequency"],
            "positive_wallet_buy_usd_30s": round(buy_vol["positive_history"], 4),
            "negative_wallet_buy_usd_30s": round(buy_vol["negative_history"], 4),
            "positive_wallet_sell_usd_30s": round(sell_vol["positive_history"], 4),
            "negative_wallet_sell_usd_30s": round(sell_vol["negative_history"], 4),
            "positive_wallet_net_usd_30s": round(buy_vol["positive_history"] - sell_vol["positive_history"], 4),
            "negative_wallet_net_usd_30s": round(buy_vol["negative_history"] - sell_vol["negative_history"], 4),
            "known_wallet_buy_volume_share": round((buy_vol["positive_history"] + buy_vol["negative_history"] + buy_vol["neutral"]) / total_buy, 4) if total_buy else 0,
            "positive_wallet_buy_volume_share": round(buy_vol["positive_history"] / total_buy, 4) if total_buy else 0,
            "negative_wallet_buy_volume_share": round(buy_vol["negative_history"] / total_buy, 4) if total_buy else 0,
            "positive_wallet_sell_volume_share": round(sell_vol["positive_history"] / total_sell, 4) if total_sell else 0,
            "negative_wallet_sell_volume_share": round(sell_vol["negative_history"] / total_sell, 4) if total_sell else 0,
        }
        signal_rows.append(row)
        for key, value in counts.items():
            categories[key] += value

    ledger = {"version": VERSION, "generated_utc": datetime.now(timezone.utc).isoformat(),
              "outcome_definition": "First buy per wallet/mint; first recorded price at or after +60s/+120s; mark-to-market proxy, not realized P&L.",
              "coverage": "Only complete, selectively captured candidate/filled-entry tapes; unknown is neutral and missing coverage is not a negative score.",
              "activity": merged_activity, "outcomes": all_outcomes}
    with open(os.path.join(args.out, "wallet_ledger.json"), "w", encoding="utf-8") as f:
        json.dump(ledger, f, separators=(",", ":"))
    fields = list(signal_rows[0]) if signal_rows else ["mint", "lane", "t_plus"]
    write_csv(os.path.join(args.out, "wallet_signal_scores.csv"), signal_rows, fields)
    write_csv(os.path.join(args.out, "wallet_outcomes_added.csv"), added,
              ["wallet", "mint", "horizon_s", "first_buy_bt", "matured_bt", "return"])
    report = {
        "mode": "paper_only_research", "prior_matured_outcomes": len(previous),
        "new_matured_outcomes_added": len(added), "ledger_outcomes_total": len(all_outcomes),
        "wallets_in_current_tape": len(activity), "signals_scored": len(signal_rows),
        "wallets_in_recent_buy_cohorts": dict(categories),
        "minimum_outcomes_for_classification": MIN_OUTCOMES, "shrinkage_prior_n": SHRINKAGE_N,
        "limitations": [
            "Outcomes are first-buy 60s/120s mark-to-market price-return proxies, not realized wallet P&L.",
            "Only complete, selectively captured candidate/filled-entry tapes contribute; coverage is biased.",
            "Positive/negative labels require five distinct matured 60s mint outcomes and shrink returns toward zero; unknown wallets are not penalized.",
            "At each signal, only outcomes with matured_bt <= signal timestamp are used. Current-run high-frequency activity is saved for the next run, not used to classify earlier signals.",
            "High-frequency is an activity label, not a smart-money or profitability label; counts reflect captured tapes only.",
            "Wallet clustering, independent sequential shadow execution, and trade gating are not implemented in this stage.",
            "K/B entry, exit, risk, stake, and paper-wallet decisions are unchanged."
        ]
    }
    with open(os.path.join(args.out, "wallet_intelligence_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(args.out, "wallet_intelligence_report.md"), "w", encoding="utf-8") as f:
        f.write("# Wallet intelligence research (paper only)\n\n")
        f.write("Descriptive research only; K/B decisions and the paper wallet are unchanged.\n\n")
        f.write(f"- Prior matured outcomes: {len(previous)}\n- New matured outcomes: {len(added)}\n")
        f.write(f"- Ledger outcomes after this run: {len(all_outcomes)}\n- Signals scored: {len(signal_rows)}\n\n")
        f.write("## Method\n\nFirst buy per wallet/mint is compared with the first recorded price at or after +60s/+120s. The 60s score is clipped and shrunk toward zero; at least five distinct matured mints are required for a positive/negative label. A signal uses only outcomes matured by its timestamp. High-frequency labels use activity saved by previous runs.\n\n")
        f.write("## Limitations\n\n" + "".join(f"- {x}\n" for x in report["limitations"]))
    print("Wrote wallet ledger, signal scores, new outcomes and wallet intelligence report")


if __name__ == "__main__":
    main()
