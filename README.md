# pumpfun-scalper

Research project: a Pump.fun (Solana bonding-curve) quick-flip study, plus a deployable bot that implements the rules.

* **Paper mode is the default.** `MODE=paper` uses the live feed and simulated fills, and sends no transactions.
* **No rule set has been shown profitable yet.** The best rule loses about $0.02 to $0.065 per $1 trade after fees,
  out of sample. Read [RULES.md](RULES.md) before doing anything else.
* Live trading needs `MODE=live` + `I_UNDERSTAND_RISK=yes` + `WALLET_PRIVATE_KEY` (a burner wallet holding only
  what you can lose). It also needs a positive paper record per lane, unless you override that explicitly.
* Hard limits: no entry before t+30 s or after t+5 min, max hold 120 s, $5k–$10k entry market cap, TP ≥ 1.5x.

## High-frequency development branch

The `feature/high-frequency-lane-k` branch adds Lane K, a 10-second buy-pressure entry setup, ahead of Lane B by default. It keeps the shared hard guards: launch-time window, $5k–$10k market-cap band, 300 cumulative trades, crash detection, creator-hold and create-slot-wallet rug checks. Lane K defaults to at least 4 trades and $25 buy volume in the last 10 seconds, with buy volume at least equal to sell volume. These are research parameters, not a claim of profitability. Paper mode remains the default; evaluate net results after fees, gas and fill delay before considering live use.

Layout:
- `pumpbot/`: the bot (feed, tape/features, signals, engine, risk, pump.fun instruction builder, execution, journal).
- `tests/`: unit tests. They use cached tapes when present and synthetic tapes otherwise.
- `scripts/`: layout verification (read-only) and a daily summary.
- `research/`: the research code and reports this rulebook came from. Data and tapes are not included.
- [DEPLOY.md](DEPLOY.md): cloud VM / Docker / systemd deployment, the paper → live checklist, and how to stop.

Quick start (paper):
```bash
pip install -r requirements.txt
cp .env.example .env    # fill in RPC endpoints (names only are listed)
python -m pumpbot.bot   # MODE=paper by default
```
Replay the rulebook numbers: `python -m pumpbot.replay --tapes <scalp_tapes dir> --paper-tapes <paper tapes dir>`

## Verification status (Mon Nov 23–24, 2026, EAT)
- **Unit tests:** 32 pass + 2 skip from a clean clone (the skipped ones need the tape cache); 39 pass with local fixtures (signals, guards, hard limits, risk/bankroll, instruction encoding, event decoding, config gating).
  With the full research tape cache (`TAPES_DIR=...`), the equivalence tests also pass. Streaming features match the research
  features on 206 tapes. Engine replay reproduces all 67 research Lane B trades (Fri/Sat/Sun) exactly.
- **Replay** (`--profile rulebook`): Fri 12 trades −$0.063, Sat 20 −$0.020, Sun 35 −$0.065. Those are the rulebook numbers.
- **Paper smoke** on Sunday's cached tapes (whole bot path): 33 shadow trades at −$0.074/trade. The $2 wallet wiped out on its first trade (−81% rug drain).
- **Live feed (paper):** decoding works on the public websocket (creates and trades seen).
- **Program layout:** `scripts/verify_layout.py` rebuilt the accounts of real mainnet `buy_exact_quote_in_v3` (2/2) and `sell_v3`
  (4/4: one used a non-ATA token account, which is allowed) and matched.
- **NOT tested:** signed live execution, `MODE=simulate` against a funded wallet, Jito sending, and the live-mode wallet
  bookkeeping. Do steps 4–5 of DEPLOY.md first.
