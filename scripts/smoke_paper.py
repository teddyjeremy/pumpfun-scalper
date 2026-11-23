#!/usr/bin/env python3
"""Paper-mode smoke run of the full Bot (event handling, risk gate, journal) on cached tapes. No network.

Converts recorded trades back into decoded Pump events (create + trade), merges all coins in time order and feeds
them through Bot.on_events, with the wall clock replaced by block time.
  python scripts/smoke_paper.py --tapes ../screen/cache/scalp_tapes/2026-11-22 --data /tmp/pumpbot_smoke
"""
import argparse, glob, gzip, json, os, sys, heapq, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pumpbot.config import Config
from pumpbot import bot as B
from pumpbot import journal as J

SOL_USD = 140.0


def events_for(b):
    vt = 800_000_000 * 10 ** 6
    yield b["t0"], ("create", {"mint": b["mint"], "timestamp": b["t0"], "creator": b.get("creator"), "symbol": b.get("symbol"),
                               "bonding_curve": b.get("curve"), "quote_mint": "SOL"})
    for x in b["trades"]:
        if not x.get("spot"):
            continue
        vs = int(x["spot"] / SOL_USD * (vt / 1e6) * 1e9)
        yield x["bt"], ("trade", {"mint": b["mint"], "timestamp": x["bt"], "is_buy": x["side"] == "buy", "user": x.get("trader"),
                                  "sol_amount": int((x.get("usd") or 0) / SOL_USD * 1e9), "token_amount": int((x.get("tok") or 0) * 1e6),
                                  "virtual_sol_reserves": vs, "virtual_token_reserves": vt, "fee_bps": 95, "creator_fee_bps": 30,
                                  "quote_mint": "SOL", "_slot": x.get("slot")})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--tapes", required=True); ap.add_argument("--data", default="/tmp/pumpbot_smoke")
    a = ap.parse_args()
    shutil.rmtree(a.data, ignore_errors=True)
    os.environ["DATA_DIR"] = a.data; os.environ["MODE"] = "paper"
    cfg = Config.from_env(); cfg.data_dir = a.data
    bot = B.Bot(cfg)
    bot.prices.sol_usd = SOL_USD
    clock = {"now": 0}
    J.time.time = lambda: clock["now"]          # journal timestamps follow block time
    tapes = [json.load(gzip.open(p, "rt")) for p in sorted(glob.glob(os.path.join(a.tapes, "*.json.gz")))]
    streams = [events_for(b) for b in tapes]
    merged = heapq.merge(*streams, key=lambda e: e[0])
    import asyncio
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    B.telegram = lambda *a, **k: asyncio.sleep(0)
    last_tick = 0
    for bt, (kind, ev) in merged:
        clock["now"] = bt
        if bt > last_tick:
            for c in list(bot.coins.values()):
                if c.engine.pos or c.engine.pending:
                    bot.handle(c, c.engine.on_tick(bt - 1))
                if c.shadow.pos or c.shadow.pending:
                    bot.shadow_handle(c, c.shadow.on_tick(bt - 1))
            last_tick = bt
        bot.on_events([(kind, ev)], f"sig{bt}", ev.get("_slot"), bt)
        loop.run_until_complete(asyncio.sleep(0))
    for c in bot.coins.values():
        bot.handle(c, c.engine.finish()); bot.shadow_handle(c, c.shadow.finish())
    loop.run_until_complete(asyncio.sleep(0))
    T = bot.j.read_trades("paper")
    SH = bot.j.read_kind("shadow", "paper")
    import statistics as st
    print("shadow trades", len(SH), "avg/$1", round(st.mean(t["net_per_1usd"] for t in SH), 4) if SH else None)
    print(json.dumps({"coins": len(tapes), "stats": bot.stats, "paper_trades": len(T), "risk": bot.risk.state(),
                      "files": sorted(os.listdir(a.data))}, indent=1, default=str))
    assert all(t["hold_s"] <= 123 for t in T + SH) and all(t["entry_t_plus"] > 30 for t in T + SH)
    print("SMOKE OK")


if __name__ == "__main__":
    main()
