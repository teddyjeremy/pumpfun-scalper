# Pump.fun quick-flip rulebook (final consolidated, Mon Nov 23 2026)

> **Warning: no rule set here is profitable after fees out of sample.** The best version (Lane B) loses about
> **$0.02 to $0.065 per $1 trade** on every day tested. A $2 wallet running it gets wiped out (falls below $1.20) in
> about 96% of 7-day simulations, even with the bankroll rules. **Run paper mode only** until the paper journal shows
> at least 150 on-time trades with a positive average. The bot enforces this gate in live mode by default.

Costs used everywhere: 1.25% curve fee each way, plus $0.0285 gas per round trip. Fills are at the curve spot of the
first trade at or after trigger + 1 s, as in the research.

## Hard safety limits (in code, not configurable)
- **No sniping:** never enter before t+30 s after launch, and never after t+300 s.
- **No holding:** every position is closed by **120 s** at the latest. The configured cap can only be lower (default 90 s).
- **TP:** at least 1.5x (`TP_MULT` is clamped at 1.5 or higher).
- **Stake:** hard cap of $20. Max trades per day is capped at 50.

## Standing guards (always on, every lane)
1. Entry only at **t+30 s < t ≤ t+300 s**, with spot market cap **$5k–$10k**.
2. At least **300 cumulative trades** at entry. This is survivorship-safe, and it is also a liveness floor.
3. **Never buy a crash.** Skip if the last-10 s sell count is at least 2× the prior 10 s while sells > buys, or if last-10 s buy $ is under 60% of sell $.
4. **v1.1:** if price is at least 10% below the high, skip when the top-1 seller is more than 50% of sell $ since the high.
   **v1.3:** skip if the creator has sold since the high.
5. **Rug filter:** skip if the creator still holds more than 8% of supply. Skip if the create-slot wallets (buyers in the
   first 2 slots) still hold more than 25% (bot default; the rulebook replay profile leaves this off). The anti-wash rule is #7.

## Lane B: volume quick-flip, "steady + fresh" (primary, NOT profitable yet)
6. Steady curve: net buy $ inflow over both the last 30 s and the last 60 s, and no single buy over 35% of the last-30 s buy $.
7. At least 15 new unique buyers in the last 30 s. Unique wallets ÷ trades over 60 s must be at least 0.5 (anti-wash).
8. Exits are dynamic, with **no hard stop loss**:
   - **TP 1.5x.**
   - **Invalidation** if price breaks the 30 s swing low within 60 s.
   - **Sell surge** if, after 5 s, the 10 s sell count is at least 2× buys and at least 3.
   - **Time cap 90 s** (hard ≤ 120 s).
   - Optional fade exit (`FADE_EXIT=yes`).
9. At most 2 entries per coin. The second entry is only allowed after the first has exited.

## Lane A: quality dip, v1.1 → v1.3 (optional, off by default)
- Strict v0 dip: at least 30% below a mid-tape high (high made after t+10 s). The last 15 s must have at least 8 trades and at least $200.
  Cumulative totals must be at least $10k volume, 300 trades and 150 wallets.
- v1.1 skip top-1 seller > 50% of the drop's sells. v1.2 skip if high → dip took more than 28 s. v1.3 skip if the dev sold during the drop.
- Same exits as Lane B, with a 120 s cap. Restricted to the $5–10k band (Jeremy's quick-flip rule), so it fires rarely:
  0 / 0 / 0 / 1 / 21 passers Wed–Sun at $5–40k. Sunday's 21 were positive, but only in-sample and with a 5-min hold.

## Bankroll / no-wipeout layer
10. One open position at a time.
11. Stake ladder: $1 to start. Raise the stake ×1.2 each time the wallet grows 20% above the last step, and lower it again if the wallet shrinks.
    The stake is at most 35% of the wallet (floor $1), with a hard cap of $20. No top-ups.
12. Stop for the day at −25% of the start-of-day wallet. Max 50 trades a day.
13. After 3 losses in a row, skip the next 5 signals.
14. Kill switch: stop completely if the wallet falls below $1.20.
15. Lane gate: a lane is switched off while its last 100 trades average below $0. Live mode also refuses to start a lane
    unless the paper journal has at least 100 trades for it with a positive average (`LIVE_REQUIRE_PAPER_EDGE`).

## Honest numbers

Lane B, per $1 trade, replayed through this bot's engine (`python -m pumpbot.replay --profile rulebook`):

| Day | Trades | Win % | Avg $/trade | Worst | Best | Exit mix |
|---|---:|---:|---:|---:|---:|---|
| Fri Nov 20 | 12 | 33% | −0.063 | −0.78 | +0.47 | inval 4, surge 4, TP 3, cap 1 |
| Sat Nov 21 | 20 | 45% | −0.020 | −0.81 | +0.60 | surge 10, TP 6, inval 3, cap 1 |
| Sun Nov 22 | 35 | 26% | −0.065 | −0.81 | +0.61 | surge 21, inval 9, TP 3, cap 2 |
| Mon Nov 23 live tapes | 35* | 34% | −0.015 | −0.56 | +0.64 | surge 23, TP 6, inval 5, cap 1 |

*At writing time the rulebook had 31 trades for Monday (−0.018). The paper trader has saved more tapes since then.

The bot profile adds the create-slot filter, giving Fri +0.003 (11), Sat −0.020 (20), Sun −0.074 (33), Mon −0.008 (34).
Pooled, that is −$0.032 per trade over n = 98. Through the bankroll layer, the $2 wallet hit the $1.20 kill switch after 6 trades.

Walk-forward search (29 features × 7 exits): in-sample +$0.01 to +$0.30 became out-of-sample −$0.20 to +$0.04. That is overfitting,
not an edge. The full tables are in `research/screen/cache/rulebook_v2_daily.md`.

Wallet Monte Carlo (3,000 runs, 10–50 trades/day, wiped out = wallet below $1.20):

| Scenario | 7-day wipeout risk |
|---|---:|
| Lane B as measured, bankroll rules on | 95.8% |
| Hypothetical +$0.05/trade edge, rules on | 34.5% |
| Hypothetical +$0.10/trade edge, rules on | 10.4% |

**What would settle it:** unbiased full tapes of every coin that touches $5–10k, for 3–5 days. Those are now being recorded by
the paper trader's band tape capture. With them, a +$0.05/trade edge needs at least 150 out-of-sample trades to show at 2 SE.
