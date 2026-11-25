"""Counterfactual exit research. This module never changes the trading engine."""
from __future__ import annotations


def _sell_surge(rows, bt: int) -> bool:
    window = [r for r in rows if bt - 10 < int(r.get("bt", 0)) <= bt]
    sells = sum(1 for r in window if r.get("side") == "sell")
    buys = len(window) - sells
    return sells >= 3 and sells >= 2 * max(1, buys)


def _net_per_1usd(entry: float, exit_price: float, fee: float, gas: float) -> float:
    if entry <= 0:
        return 0.0
    return (1 - fee) ** 2 * exit_price / entry - 1 - gas


def simulate_exit_variants(trade: dict, tape_rows: list[dict], fee: float = 0.0125,
                           gas: float = 0.0285) -> list[dict]:
    """Compare two hypothetical sell-surge exits from the actual fill.

    Fills use the first observed trade at or after trigger+1 second. If no such
    trade exists, use the last observed price at/before that target and mark it
    as a fallback. No-trigger outcomes are marked-to-market only, not exits.
    """
    entry_bt = int(trade.get("entry_bt") or 0)
    entry_px = float(trade.get("ent_px") or 0)
    if not entry_bt or entry_px <= 0:
        return []
    rows = sorted((r for r in tape_rows if int(r.get("bt", 0)) > entry_bt),
                  key=lambda r: int(r.get("bt", 0)))
    if not rows:
        return []
    variants = {
        "confirmed_sell_surge_2s": {"trigger": None, "start": None},
        "price_confirmed_sell_surge": {"trigger": None, "start": None},
    }
    for i, row in enumerate(rows):
        bt = int(row.get("bt", 0))
        px = float(row.get("price_usd") or 0)
        surge = _sell_surge(rows[:i + 1], bt)
        state = variants["confirmed_sell_surge_2s"]
        if state["trigger"] is None:
            if surge:
                if state["start"] is None:
                    state["start"] = bt
                elif bt - state["start"] >= 2:
                    state["trigger"] = bt
            else:
                state["start"] = None
        state = variants["price_confirmed_sell_surge"]
        if state["trigger"] is None and surge and px < entry_px:
            state["trigger"] = bt

    out = []
    for name, state in variants.items():
        trigger = state["trigger"]
        if trigger is None:
            last = rows[-1]
            out.append({
                "variant": name, "status": "no_trigger_observation_end_mark",
                "trigger_bt": None, "exit_bt": int(last["bt"]),
                "exit_price_usd": float(last["price_usd"]),
                "net_per_1usd": round(_net_per_1usd(entry_px, float(last["price_usd"]), fee, gas), 5),
                "executable_exit": False,
            })
            continue
        target = trigger + 1
        fill = next((r for r in rows if target <= int(r["bt"]) <= target + 2), None)
        fallback = False
        if fill is None:
            eligible = [r for r in rows if int(r["bt"]) <= target]
            fill = eligible[-1] if eligible else rows[0]
            fallback = True
        exit_px = float(fill["price_usd"])
        out.append({
            "variant": name, "status": "filled_fallback" if fallback else "filled",
            "trigger_bt": trigger, "exit_bt": int(fill["bt"]), "exit_price_usd": exit_px,
            "net_per_1usd": round(_net_per_1usd(entry_px, exit_px, fee, gas), 5),
            "executable_exit": not fallback,
        })
    return out
