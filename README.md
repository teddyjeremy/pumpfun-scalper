# pumpfun-scalper

Research project: a Pump.fun (Solana bonding-curve) quick-flip study, plus a deployable bot that implements the rules.

* **Paper mode is the default.** `MODE=paper` uses the live feed and simulated fills, and sends no transactions.
* **No rule set has been shown profitable yet.** The best rule loses about $0.02 to $0.065 per $1 trade after fees,
  out of sample. Read [RULES.md](RULES.md) before doing anything else.
* Live trading needs `MODE=live` + `I_UNDERSTAND_RISK=yes` + `WALLET_PRIVATE_KEY` (a burner wallet holding only
  what you can lose). It also needs a positive paper record per lane, unless you override that explicitly.
* Hard limits: no entry before t+30 s or after t+5 min, max hold 120 s, $5k–$10k entry market cap, TP ≥ 1.5x.

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
