# Deploying pumpbot

> Read RULES.md first. **No rule set is profitable yet.** The default and recommended mode is `paper`.
> Live mode risks every lamport in the wallet you give it. Use a **brand-new burner wallet** holding only what you
> can lose ($2 + about 0.01 SOL for fees/rent). **Never** use your main wallet or reuse its key.

## 1. Pick a server close to Solana RPC
Solana validators and the big RPC providers (Helius, Triton, QuickNode, Jito block engines) are concentrated in
Frankfurt, Amsterdam, New York/Ashburn and Tokyo. Latency to the RPC matters more than CPU. Any 2 vCPU / 2–4 GB box is enough:

| Provider | Plan (approx.) | Region to pick |
|---|---|---|
| Hetzner Cloud | CPX21 / CX32, ~€6–8/mo | Falkenstein / Nuremberg (Frankfurt RPCs) or Ashburn |
| DigitalOcean | Basic 2 GB, ~$12/mo | FRA1 or NYC3 |
| AWS Lightsail | 2 GB, ~$10/mo | eu-central-1 (Frankfurt) or us-east-1 |

Pick the region your RPC provider tells you its websocket is served from. Then check it on the box with
`python scripts/verify_layout.py`, which also times the RPC.

## 2. Data endpoints (the feed decides everything)
- `FEED_WS_URLS`: comma-separated websocket URLs. Use **two providers** (for example Helius + QuickNode/Triton). The bot
  subscribes to the Pump program on all of them and de-duplicates. The free public endpoint lags 2–5 s, which is too late
  for 1–2 minute flips. Live mode refuses to run with only the public endpoint.
- `RPC_HTTP_URL`: HTTP RPC for account reads, blockhash, simulation and sending.
- Optional: `SEND_RPC_URLS` (extra send endpoints, such as a staked/"sender" endpoint) and `JITO_URL` + `JITO_TIP_LAMPORTS`.
- The firehose of all Pump program logs is heavy (tens of GB per day). Check your plan's websocket bandwidth/credit limits.

## 3. Install (Docker, recommended)
```bash
ssh root@SERVER
apt-get update && apt-get install -y docker.io docker-compose-plugin git
adduser --disabled-password pumpbot && usermod -aG docker pumpbot && su - pumpbot
git clone https://github.com/teddyjeremy/pumpfun-scalper.git && cd pumpfun-scalper
cp .env.example .env && chmod 600 .env && nano .env      # fill FEED_WS_URLS, RPC_HTTP_URL; leave MODE=paper
docker compose run --rm pumpbot python -m pytest -q       # tests must pass
docker compose up -d --build
docker compose logs -f                                     # ENTER / EXIT lines, feed status
```

### systemd alternative (no Docker)
```bash
sudo adduser --system --group pumpbot
sudo mkdir -p /opt/pumpbot && sudo chown pumpbot: /opt/pumpbot
sudo -u pumpbot git clone https://github.com/teddyjeremy/pumpfun-scalper.git /opt/pumpbot
cd /opt/pumpbot && sudo -u pumpbot python3 -m venv venv && sudo -u pumpbot venv/bin/pip install -r requirements.txt
sudo -u pumpbot cp .env.example .env && sudo chmod 600 .env && sudo -u pumpbot mkdir -p data
# in .env set DATA_DIR=/opt/pumpbot/data
sudo cp pumpbot.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now pumpbot
journalctl -u pumpbot -f
```

## 4. Paper first (at least 5–7 days)
`MODE=paper` sends nothing. Every day, check:
```bash
python scripts/daily_summary.py paper      # docker: docker compose exec pumpbot python scripts/daily_summary.py paper
cat data/status.json                       # wallet sim, lane stats, feed age, open position
```
There are two records:
- `data/trades_paper.jsonl`: the $2 wallet run through the bankroll rules. Wipeouts are counted, and paper resets to $2 to keep going.
- `data/shadow_paper.jsonl`: every on-time signal at $1 (the research measurement). **This is the record the live gate reads.**

Go/no-go before any money:
1. At least 150 shadow trades for the lane, with **a positive average after fees** over the last 100 (the bot checks this, `LIVE_REQUIRE_PAPER_EDGE=yes`).
2. Feed age in `status.json` stays under ~1 s (`feeds` field). If it is 2 s+, the paper fills are optimistic.
3. Paper wipeouts are rare. If the $2 wallet wipes out every few days in paper, it will do the same live.

## 5. Dry-run the transactions (`MODE=simulate`)
Set `MODE=simulate` and `SIM_WALLET_PUBKEY=<your burner's PUBLIC address>`. No private key is needed.
The bot builds the real buy/sell transactions and calls `simulateTransaction` (unsigned, `sigVerify=false`). Nothing is sent.
Results go to `data/exec_simulate.jsonl`. You want `ok: true`, or at most an "insufficient funds" error for an empty wallet.
Any `AccountNotFound`, `ConstraintSeeds` or `InvalidAccountData` error means the program layout changed: **stop**.
Also run `python scripts/verify_layout.py` (read-only). It rebuilds the accounts of real mainnet v3 trades and compares them.

## 6. Live (only if 4 and 5 pass)
1. Create a NEW wallet on the server (`solana-keygen new -o burner.json`, or a fresh Phantom account used for nothing else).
   Fund it with $2 of SOL + ~0.01 SOL (token-account rent is refunded on each sell; ~0.0018 SOL one-time volume-accumulator rent).
2. Put the secret in `.env` only: `WALLET_PRIVATE_KEY=<base58 secret or JSON array>`. Then `chmod 600 .env`.
   Never paste it into chats, git, tickets or screenshots.
3. Set `MODE=live` and `I_UNDERSTAND_RISK=yes`, then `docker compose up -d` (or `systemctl restart pumpbot`).
4. The bot reads the real wallet balance. It refuses to start if no lane passes the paper gate, or if the feed is only the public endpoint.
5. Watch the first trades: `docker compose logs -f`, `data/exec_live.jsonl` (signatures), and Solscan for the burner address.

## 7. Monitoring
- `data/status.json` (updated every 15 s): wallet, stake, day P&L, lane averages, feed age, open position.
- `data/bot.log`, `data/trades_<mode>.jsonl`, `data/shadow_<mode>.jsonl`, `data/signals_<mode>.jsonl`, `data/exec_<mode>.jsonl`, `data/daily_<mode>_<day>.json`.
- Telegram (optional): set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for every exit, kill switch, sell failure and daily summary.

## 8. Stopping
- **Graceful:** `touch data/STOP`. That means no new entries; the open position (≤120 s) closes, then the bot exits.
  Remove the file to allow a restart.
- **Immediate:** `docker compose stop` (150 s grace) or `systemctl stop pumpbot`. If a position was open, check the wallet on Solscan.
  Sell leftovers on pump.fun manually.
- **Emergency:** move the SOL out of the burner with your own wallet app. The bot cannot trade without balance.
- The bot also stops itself in these cases: wallet < $1.20 (kill switch), −25% day (until the next EAT day), 50 trades/day,
  and lane average < 0 over the last 100.

## Caveats
- Execution uses the documented `buy_exact_quote_in_v3` / `sell_v3` (17 accounts, Nov 2026 IDL). It has **not been executed live by
  us**. Use MODE=simulate and verify_layout.py first, and again after any Pump program upgrade.
- Cashback coins are skipped (v3 does not support them). Non-SOL-quoted coins are tracked in paper/shadow but are not traded live
  unless you add their quote mint to `TRADEABLE_QUOTES` and hold that token.
- Paper fills assume the research convention (next trade ≥ 1 s after the trigger). Real fills depend on your latency and priority fee.
  Rug drains can still cost −80% on one trade.
