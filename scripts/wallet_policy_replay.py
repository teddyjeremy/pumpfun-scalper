#!/usr/bin/env python3
"""Offline, paper-only paired wallet-loss policy replay. Existing lanes are untouched."""
import argparse, json, os
from collections import Counter, defaultdict
from statistics import median

DELAYS_MS = (500, 1000, 1500)
POLICIES = (("no_lock", 0, 0), ("lock_after_1_loss", 1, 0),
            ("lock_after_2_losses", 2, 0), ("cooldown_5m", 0, 300),
            ("cooldown_10m", 0, 600))


def read_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                    if isinstance(x, dict): rows.append(x)
                except json.JSONDecodeError: pass
    return rows


class WalletPolicy:
    def __init__(self, loss_limit=0, cooldown=0):
        self.loss_limit, self.cooldown = loss_limit, cooldown
        self.losses, self.until = defaultdict(int), {}

    def blocked(self, wallet, now):
        return bool(wallet and ((self.loss_limit and self.losses[wallet] >= self.loss_limit)
                    or now < self.until.get(wallet, 0)))

    def close(self, wallet, pnl, now):
        if wallet and pnl <= 0:
            self.losses[wallet] += 1
            if self.cooldown: self.until[wallet] = now + self.cooldown


def replay(rows, delay, policy_name, loss_limit, cooldown, flow_gate=False,
           stake=1.0, tp=.20, sl=.12, max_hold=30, confirm=3.0,
           entry_slip=.015, exit_slip=.015, fee=.0125, gas=.0285):
    pending, position = {}, None
    guard = WalletPolicy(loss_limit, cooldown)
    entries, trades, decisions = [], [], Counter()
    for tx in rows:
        mint, sig = tx.get("mint"), tx.get("signature")
        px, now = tx.get("final_price_sol"), tx.get("observed_at_epoch")
        if not mint or not px or float(px) <= 0 or now is None: continue
        px, now, slot = float(px), float(now), int(tx.get("slot") or 0)
        for m, c in list(pending.items()):
            if now - c["signal_time"] > confirm:
                pending.pop(m); decisions["expired"] += 1
        if position and not position.get("trigger") and now >= position["entry_time"] + max_hold:
            position["trigger"] = {"reason":"time_cap", "sig":position["last_sig"],
                "slot":position["last_slot"], "time":position["entry_time"]+max_hold,
                "price":position["last_px"]}
            decisions["time_cap_trigger"] += 1
        if position and position["mint"] == mint:
            trigger = position.get("trigger")
            if trigger:
                if sig != trigger["sig"] and slot > trigger["slot"] and now >= trigger["time"] + delay:
                    fill = px * (1-exit_slip)
                    pnl = stake * ((fill / position["entry_fill"]) * (1-fee)**2 - 1) - gas
                    trades.append({**position, "exit_sig":sig, "exit_slot":slot,
                        "exit_time":now, "exit_px":px, "exit_reason":trigger["reason"],
                        "exit_trigger_px":trigger["price"], "exit_latency":now-trigger["time"],
                        "exit_vsol_raw":tx.get("final_virtual_sol_reserves_raw"),
                        "exit_vtoken_raw":tx.get("final_virtual_token_reserves_raw"),
                        "net_pnl_usd":round(pnl,6)})
                    guard.close(position["wallet"], pnl, now)
                    decisions["closed"] += 1
                else: continue
                position = None
                continue
            if sig == position["entry_sig"] or slot <= position["entry_slot"]: continue
            move = px / position["entry_px"] - 1
            reason = "take_profit" if move >= tp else "stop_loss" if move <= -sl else None
            if reason:
                position["trigger"] = {"reason":reason,"sig":sig,"slot":slot,
                    "time":now,"price":px}
                decisions["exit_"+reason] += 1
            else:
                position.update(last_sig=sig,last_slot=slot,last_time=now,last_px=px)
            continue
        candidate = pending.get(mint)
        if candidate:
            elapsed = now-candidate["signal_time"]
            if sig == candidate["signal_sig"] or slot <= candidate["signal_slot"] or elapsed < delay: continue
            if len(candidate["buyers"]) < 2:
                pending.pop(mint); decisions["single_buyer_signal"] += 1; continue
            wallet = candidate["wallet"]
            if guard.blocked(wallet, now):
                pending.pop(mint); decisions["wallet_policy_block"] += 1; continue
            if position:
                pending.pop(mint); decisions["portfolio_busy"] += 1; continue
            if flow_gate and not (int(tx.get("buy_count") or 0)>0 and
                    float(tx.get("net_buy_sol") or 0)>0 and not tx.get("creator_sell")):
                pending.pop(mint); decisions["flow_gate_block"] += 1; continue
            entry = {"mint":mint,"symbol":tx.get("symbol"),"wallet":wallet,
                "wallet_policy":policy_name,"delay":delay,"signal_sig":candidate["signal_sig"],
                "signal_slot":candidate["signal_slot"],"signal_time":candidate["signal_time"],
                "buyers":candidate["buyers"],"entry_sig":sig,"entry_slot":slot,
                "entry_time":now,"entry_latency":elapsed,"entry_px":px,
                "entry_fill":px*(1+entry_slip),
                "entry_vsol_raw":tx.get("final_virtual_sol_reserves_raw"),
                "entry_vtoken_raw":tx.get("final_virtual_token_reserves_raw"),
                "last_sig":sig,"last_slot":slot,"last_time":now,"last_px":px}
            pending.pop(mint); entries.append(entry); position=entry
            decisions["entry"] += 1
            continue
        buyers = sorted(set(tx.get("buyer_wallets") or []))
        if not buyers: continue
        wallet = tx.get("first_buyer_wallet") or buyers[0]
        if guard.blocked(wallet, now):
            decisions["wallet_policy_block_signal"] += 1; continue
        pending[mint] = {"signal_sig":sig,"signal_slot":slot,"signal_time":now,
            "wallet":wallet,"buyers":buyers}
        decisions["signal"] += 1
    return {"delay":delay,"policy":policy_name,"entries":entries,"trades":trades,
            "open":position,"decisions":dict(decisions)}


def summarize(r):
    ts=r["trades"]; wins=[x for x in ts if x["net_pnl_usd"]>0]
    losses=[x for x in ts if x["net_pnl_usd"]<=0]
    gw=sum(x["net_pnl_usd"] for x in wins); gl=abs(sum(x["net_pnl_usd"] for x in losses))
    streak=maximum=0
    for x in sorted(ts,key=lambda z:z["exit_time"]):
        streak=0 if x["net_pnl_usd"]>0 else streak+1; maximum=max(maximum,streak)
    return {"delay":r["delay"],"policy":r["policy"],"entries":len(r["entries"]),
        "closed":len(ts),"wins":len(wins),"losses":len(losses),
        "win_rate":len(wins)/len(ts) if ts else None,
        "net_pnl_usd":round(sum(x["net_pnl_usd"] for x in ts),6),
        "profit_factor":round(gw/gl,4) if gl else None,"max_loss_streak":maximum,
        "unique_mints":len({x["mint"] for x in ts}),"open":bool(r["open"]),
        "open_mint":r["open"]["mint"] if r["open"] else None,
        "median_entry_delay":median([x["entry_latency"] for x in r["entries"]]) if r["entries"] else None,
        "decisions":r["decisions"]}


def build_report(data_dir, out_dir):
    rows=read_jsonl(os.path.join(data_dir,"sniper_transactions_sniper.jsonl"))
    rows.sort(key=lambda x:int(x.get("observation_sequence") or 0))
    runs=[]
    for ms in DELAYS_MS:
        for name,limit,cooldown in POLICIES:
            runs.append(replay(rows,ms/1000,name,limit,cooldown))
    results=[summarize(x) for x in runs]
    os.makedirs(out_dir,exist_ok=True)
    with open(os.path.join(out_dir,"wallet_policy_replay_details.json"),"w",encoding="utf-8") as f:
        json.dump({"lanes":[{"delay":x["delay"],"policy":x["policy"],"entries":x["entries"],
            "trades":x["trades"],"open_position":x["open"],"decisions":x["decisions"]} for x in runs]},f)
    with open(os.path.join(out_dir,"wallet_policy_replay.json"),"w",encoding="utf-8") as f:
        json.dump({"input_transactions":len(rows),"lanes":results},f,indent=2)
    lines=["# Wallet-loss policy ablation (paper research only)","",
      f"Input transaction summaries: {len(rows)}","",
      "Paired policies: no lock; permanent lock after one loss; permanent lock after two losses; 5-minute cooldown after a loss; 10-minute cooldown after a loss. Each is replayed at 0.5s, 1.0s, and 1.5s assumed delay.",
      "","| Delay | Policy | Entries | Closed | W/L | Win rate | Net P&L | PF | Max loss streak | Unique mints | Open |",
      "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for x in results:
        wr=f'{x["win_rate"]:.1%}' if x["win_rate"] is not None else "n/a"
        pf=f'{x["profit_factor"]:.2f}' if x["profit_factor"] is not None else "n/a"
        lines.append(f'| {x["delay"]:.1f}s | {x["policy"]} | {x["entries"]} | {x["closed"]} | {x["wins"]}/{x["losses"]} | {wr} | ${x["net_pnl_usd"]:.4f} | {pf} | {x["max_loss_streak"]} | {x["unique_mints"]} | {"yes" if x["open"] else "no"} |')
    lines += ["","## Limitations",
      "- Offline paired replay only. Existing runtime lanes, K/B decisions, and live execution paths are unchanged.",
      "- Prices remain reserve-derived transaction marks with configured slippage, fees, and gas, not executable sell quotes or confirmed fills.",
      "- Detailed JSON retains raw virtual SOL/token reserve snapshots at entry and delayed exit for later curve-quote modelling.",
      "- The lock/cooldown applies only to the first buyer wallet in the signal transaction, not the entire buyer cohort.",
      "- Cooldown starts at losing trade close. The 5/10-minute periods are exploratory, not optimized.",
      "- Variants overlap on the same market stream; P&Ls are paired comparisons, not independent or additive.",
      "- No policy should be promoted from a single session."]
    with open(os.path.join(out_dir,"wallet_policy_replay.md"),"w",encoding="utf-8") as f:f.write("\n".join(lines)+"\n")
    return {"input_transactions":len(rows),"lanes":results}


def main():
    p=argparse.ArgumentParser();p.add_argument("--data-dir",default="./data");p.add_argument("--out",default="./report")
    a=p.parse_args();print(json.dumps(build_report(a.data_dir,a.out),indent=2))


if __name__=="__main__": main()
