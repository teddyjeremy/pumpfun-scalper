# Warning signals: what actually differs between losers and winners

Written 2026-11-23 13:51:09 Africa/Nairobi (UTC+3).

Research only. Cached tapes only (no RPC). Official backtest P&L is from `screen/backtest_2026-11-22.csv` and `screen/backtest_2026-11-21.csv` (30-minute analyze tapes). Paper P&L is the 1-second fill (`tp17_net_d1000`, `fade_17_net_d1000`) in `paper/logs/signals.csv`. Every feature below is measured at or before the entry print, with no lookahead, except one field that is explicitly marked as a 30-minute lookahead and is not a live filter.

## Sample and how wins are counted

- **22 strict v0 passers** on Sunday 22 Nov (21) and Saturday 21 Nov (1). PutInu (`GAZnEYKs…`) has no v1 entry on the 30-minute tape, so there are **21 simulated backtest trades**.
- **4 live paper signals** on Monday 23 Nov: POPDOG, Trench, BRAIN, Blue.
- A trade is a win if net on a $1 stake is above $0.005, after 1.25% fees each way plus about $0.0285 gas.
- **v1.1** skips the entry if the top-1 seller made more than 50% of the drop’s sell volume. A missing value does not skip. On these 30-minute tapes that skips **only PI**. (Earlier 5-minute-tape figures had skipped three Sunday losers; those seller-share numbers moved once the fuller tapes were used. This file uses the current official backtest files throughout, so the v1.1 baselines below will not match the older 17-trade / +$5.67 figures.)
- Headline totals are backtest-only. Paper is shown separately. Feature ranking is shown both ways.

**This is a small sample.** 21 backtest trades and 4 paper trades are not enough to freeze a new rule. Anything that looks clean here is only a candidate to test unchanged on Nov 20 / 19 / 18 / 17 / 16.

## Baseline totals (no new filters)

| Set | Exit | n | W/L/BE | Win % | Expectancy / $1 | Cum P&L | Max DD |
|---|---|---:|---|---:|---:|---:|---:|
| v0 (all 21 simulated) | 1.7x no-stop | 21 | 16/5/0 | 76.2% | $+0.43 | $+9.01 | $-0.96 |
| v0 (all 21 simulated) | 1.8x no-stop | 21 | 14/7/0 | 66.7% | $+0.32 | $+6.78 | $-1.42 |
| v0 (all 21 simulated) | volume-fade @ 1.7x | 21 | 16/5/0 | 76.2% | $+0.38 | $+7.97 | $-0.90 |
| v0 (all 21 simulated) | volume-fade @ 1.8x | 21 | 16/5/0 | 76.2% | $+0.37 | $+7.73 | $-0.90 |
| v1.1 (skips PI only) | 1.7x no-stop | 20 | 16/4/0 | 80.0% | $+0.49 | $+9.89 | $-0.96 |
| v1.1 (skips PI only) | 1.8x no-stop | 20 | 14/6/0 | 70.0% | $+0.38 | $+7.66 | $-0.96 |
| v1.1 (skips PI only) | volume-fade @ 1.7x | 20 | 15/5/0 | 75.0% | $+0.39 | $+7.89 | $-0.90 |
| v1.1 (skips PI only) | volume-fade @ 1.8x | 20 | 15/5/0 | 75.0% | $+0.38 | $+7.65 | $-0.90 |
| Paper (4 live fills) | 1.7x no-stop, 1s fill | 4 | 1/3/0 | 25.0% | $-0.39 | $-1.55 | $-1.55 |
| Paper (4 live fills) | 1.8x no-stop, 1s fill | 4 | 1/3/0 | 25.0% | $-0.39 | $-1.55 | $-1.55 |
| Paper (4 live fills) | volume-fade, 1s fill | 4 | 2/2/0 | 50.0% | $-0.04 | $-0.16 | $-0.77 |

**Backtest 1.7x winners (16):** Wave ($+0.67), Ely ($+0.82), Yume ($+0.24), кот ($+0.11), PUTIN ($+0.67), valor ($+0.66), Vodkat ($+0.81), HITLER ($+1.91), BEAR ($+0.67), VIRUS ($+0.66), OLA ($+0.68), Maxlong ($+0.12), ACC ($+2.09), BOT ($+0.69), FRANK ($+0.67), HACKERMAN ($+0.71).

**Backtest 1.7x losers (5):** PI ($-0.88), putinu ($-0.50), ANT ($-0.82), JAKEY ($-0.15), JAKEY ($-0.81).

**Paper 1.7x:** winner: BRAIN ($+0.08). losers: POPDOG ($-0.57), Trench ($-0.37), Blue ($-0.69).

v1.1 keeps FRANK and HACKERMAN even though their top-1 seller share is missing (the high and the entry share the same second, so there is no drop-seller window).

## The 1–3 things that actually differ

Jeremy is right about snipers and dev selling: they show up on winners too, including some of the best ones. Those two signals are documented in full below, but they are not what separates the groups. What actually moves is **where and when we buy**, and — for Blue specifically — **whether selling is already speeding up**.

1. **Higher market cap at entry, and buying later after the high (and later after launch), mark Sunday’s losers — but they would not have stopped Blue.** On the 21 backtest trades, losers were bought at a $16.3k median cap versus $8.5k for winners (AUC 0.81), 37 seconds after the high versus 13 seconds (AUC 0.75), and at t+109s versus t+66s (AUC 0.71). The already-discussed flags (skip if cap ≥ $12k, or if t+ ≥ 200s, or if secs-from-high > 28) each cut some remaining v1.1 losers, at the cost of a couple of winners (valor, VIRUS, Maxlong). All four of today’s paper trades, including Blue, were under $12k, before t+200s, and within 28 seconds of the high (Blue was only 8 seconds off the high). So this is a Sunday pattern, not a Blue pattern. It is still worth testing out of sample because it was already on the table before this study, not because of Blue.

2. **Selling speeding up into the dip is the one description that actually fits Blue, but the loose “crash vs dip” flag is too blunt for a hard rule.** On Blue, sells in the last 10 seconds were 26 versus 6 in the 10 seconds before that (a 4.3× jump), and sell volume already exceeded buy volume (USD buy/sell ratio 0.87). That is a cascade, not a quiet dip. The same loose flag (sell count up, and last-10s buy/sell USD ratio < 1) also fires on good winners such as HITLER, BOT, Vodkat, OLA and Yume, and it misses losers that faded without accelerating (ANT, putinu, PI, Trench). A tighter cut (sell count at least 2× the prior 10 seconds, and USD ratio < 1) still catches Blue and the second JAKEY, but it would also have skipped HITLER (+$1.91) and today’s only paper winner, BRAIN. Treat this as a candidate, not a settled rule.

3. **Sniper load and a creator who has already sold out do not separate losers from winners.** By the entry print, the creator had already sold ≥95% of their buy on 17 of 21 backtest trades, including 14 winners (ACC, HITLER, BOT, FRANK, HACKERMAN, Ely, and more). The same is true of Blue, Trench, and BRAIN. Remaining sniper holdings from the first 3 seconds never reached 40% of supply on any trade; a 20% or 30% cut would have skipped winners only (ACC, BOT, HITLER, valor, OLA). The 30-minute “sniper supply %” figure from `summary.json` is gross buys, often over 40% on winners too, and can exceed 100% because it counts tokens that were later sold. Blue’s often-quoted “~42% in the first 3 seconds” is mostly the creator’s own launch buy (~27%) plus about 13% of gross sniper buys (only ~4% still held at entry). Filtering on that would not have been a sniper filter so much as a “the coin launched with a dev buy” filter, which almost all of these coins have.

A fourth candidate, **“the 1.7x target is above the all-time high so far,” fires on every single trade** in this set. That is mechanical: a 30% dip leaves the high at about 1.43× the entry, and 1.7 is always above that. No trade here dipped far enough (~41%+) for 1.7x to sit at or below the prior high. This is not a Blue-specific warning and should not be added as a rule.

## Per-trade snapshot

`Dev sold %` is creator tokens sold / bought by the entry print. `Sniper 3s net` is remaining holdings of non-dev wallets that bought in the first 3 seconds, as a % of supply, at entry. `Sell accel (vol)` is the stated crash flag: more sells in the last 10s than in the prior 10s, and last-10s buy/sell USD ratio < 1. `Acc. ratio` is last-10s sell count / prior-10s sell count. `1.7x > ATH` is whether entry cap × 1.7 is above the running high.

| Src | Coin | 1.7x | 1.8x | Fade 1.7x | t+ | Mcap | Dip % | Secs from high | 10s B/S $ | Acc. ratio | Sell accel (vol) | Dev sold % | Sniper 3s net % | 1.7x > ATH | Top-1 | v1.1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---|---:|---|
| BT | PI | $-0.88 | $-0.88 | $+0.08 | 50 | 16295 | 30.3 | 4 | 1.02 | 1.43 | no | 100% | 6.4 | yes | 0.99 | SKIP |
| BT | Wave | $+0.67 | $+0.79 | $+0.38 | 127 | 4863 | 30.4 | 11.0 | 0.26 | 1.40 | yes | 0% | 0.0 | yes | 0.26 |  |
| BT | Ely | $+0.82 | $+0.82 | $+0.82 | 50 | 6827 | 30.3 | 22.0 | 0.75 | 0.93 | no | 100% | 13.8 | yes | 0.07 |  |
| BT | Yume | $+0.24 | $+0.24 | $-0.29 | 95 | 11587 | 31.2 | 28.0 | 0.64 | 1.08 | yes | 100% | 0.1 | yes | 0.07 |  |
| BT | кот | $+0.11 | $+0.11 | $-0.60 | 83 | 9936 | 32.9 | 17.0 | 0.42 | 0.63 | no | 100% | 0.0 | yes | 0.14 |  |
| BT | PUTIN | $+0.67 | $+0.79 | $+0.67 | 34 | 10570 | 31.4 | 15.0 | 0.57 | 0.86 | no | 100% | 4.4 | yes | 0.14 |  |
| BT | valor | $+0.66 | $+0.76 | $+0.18 | 144 | 28080 | 30.1 | 5 | 0.51 | 0.80 | no | 100% | 24.5 | yes | 0.18 |  |
| BT | Vodkat | $+0.81 | $+0.81 | $+0.81 | 57 | 7353 | 30.6 | 11.0 | 0.47 | 2.67 | yes | 100% | 14.1 | yes | 0.34 |  |
| BT | putinu | $-0.50 | $-0.50 | $-0.17 | 225 | 9938 | 30.1 | 37.0 | 0.44 | 0.92 | no | 0% | 1.2 | yes | 0.17 |  |
| BT | HITLER | $+1.91 | $+1.91 | $+0.46 | 41 | 6991 | 30.2 | 7 | 0.43 | 2.07 | yes | 100% | 24.8 | yes | 0.24 |  |
| BT | BEAR | $+0.67 | $-0.70 | $+0.67 | 76 | 8760 | 33.7 | 20.0 | 0.29 | 0.48 | no | 100% | 13.8 | yes | 0.19 |  |
| BT | ANT | $-0.82 | $-0.82 | $-0.43 | 109 | 10531 | 30.2 | 73.0 | 0.09 | 0.93 | no | 16% | 7.4 | yes | 0.12 |  |
| BT | VIRUS | $+0.66 | $+0.43 | $+0.47 | 294 | 20638 | 31.0 | 37.0 | 0.59 | 0.50 | no | 100% | 1.2 | yes | 0.29 |  |
| BT | OLA | $+0.68 | $+0.76 | $+0.68 | 38 | 5907 | 32.1 | 19.0 | 0.51 | 1.30 | yes | 100% | 25.4 | yes | 0.09 |  |
| BT | Maxlong | $+0.12 | $+0.12 | $+0.07 | 129 | 10331 | 31.2 | 46.0 | 0.50 | 0.67 | no | 0% | 4.3 | yes | 0.12 |  |
| BT | ACC | $+2.09 | $+2.09 | $+2.09 | 45 | 10401 | 32.1 | 6 | 0.88 | 0.87 | no | 100% | 33.6 | yes | 0.06 |  |
| BT | BOT | $+0.69 | $+0.79 | $+0.69 | 45 | 6988 | 31.2 | 6 | 0.80 | 1.60 | yes | 100% | 31.7 | yes | 0.07 |  |
| BT | JAKEY | $-0.15 | $-0.15 | $-0.21 | 74 | 19943 | 31.6 | 21.0 | 0.68 | 1.43 | yes | 100% | 7.3 | yes | 0.08 |  |
| BT | JAKEY | $-0.81 | $-0.81 | $+0.24 | 216 | 20829 | 30.1 | 46.0 | 0.25 | 2.50 | yes | 100% | 0.5 | yes | 0.22 |  |
| BT | FRANK | $+0.67 | $+0.76 | $+0.67 | 85 | 8157 | 32.3 | 0 | 1.32 | 0.68 | no | 100% | 0.0 | yes | — |  |
| BT | HACKERMAN (Sat) | $+0.71 | $-0.54 | $+0.71 | 34 | 5380 | 31.5 | 0 | 1.12 | 1.26 | no | 100% | 10.8 | yes | — |  |
| P | POPDOG | $-0.57 | $-0.57 | $+0.44 | 125 | 5936 | 30.8 | 28.0 | 0.03 | 1.22 | yes | — | 15.6 | yes | 0.16 |  |
| P | Trench | $-0.37 | $-0.37 | $+0.17 | 53 | 7216 | 31.5 | 20.0 | 0.43 | 0.43 | no | 100% | 12.8 | yes | 0.07 |  |
| P | BRAIN | $+0.08 | $+0.08 | $-0.10 | 50 | 5328 | 30.6 | 8 | 0.77 | 2.67 | yes | 100% | 12.6 | yes | 0.28 |  |
| P | Blue | $-0.69 | $-0.69 | $-0.67 | 135 | 6116 | 30.7 | 8 | 0.87 | 4.33 | yes | 100% | 4.3 | yes | 0.28 |  |

Not simulated: PutInu (`GAZnEYKs…`, `no_v1_entry_on_tape`).

## Full winners-vs-losers ranking

AUC is the chance a random loser looks “worse” than a random winner on that feature, in the direction shown. 0.50 is a coin flip. Below about 0.60 the feature barely separates the groups. Some features are just restatements of each other (dip size, room back to the high, and “1.7x vs ATH” all move together). The 30-minute dev-sold field is lookahead and is included only to show it should not be used live.

### Backtest only (16 wins / 5 losses at 1.7x)

| Rank | Feature | Losers are… | AUC | Win median | Loss median | Win n | Loss n |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | First-3s sniper wallet count (ex-dev) | lower | 0.84 | 28.5 | 13.0 | 16 | 5 |
| 2 | Market cap at entry ($) | higher | 0.81 | 8458 | 16295 | 16 | 5 |
| 3 | Run-up into the high (ATH / start mcap) | higher | 0.81 | 4.57 | 6.34 | 16 | 5 |
| 4 | Run-up into the high % | higher | 0.81 | 357 | 534 | 16 | 5 |
| 5 | Share of last-15s buy volume from new buyers | higher | 0.80 | 0.77 | 0.98 | 16 | 5 |
| 6 | Dip size % from running high | lower | 0.78 | 31.2 | 30.2 | 16 | 5 |
| 7 | ATH market cap at entry ($) | higher | 0.78 | 12631 | 23388 | 16 | 5 |
| 8 | ATH / entry mcap (room back to the high) | lower | 0.78 | 1.45 | 1.43 | 16 | 5 |
| 9 | 1.7x target / ATH at entry | higher | 0.78 | 1.17 | 1.19 | 16 | 5 |
| 10 | 1.7x target vs ATH gap % | higher | 0.78 | 16.9 | 18.7 | 16 | 5 |
| 11 | Summary dev sold % in 30m (LOOKAHEAD — not usable live) | lower | 0.77 | 14.5 | 3.42 | 16 | 5 |
| 12 | Seconds from running high to entry | higher | 0.75 | 13.0 | 37.0 | 16 | 5 |
| 13 | Prior-15s trade count | lower | 0.75 | 104 | 42.0 | 16 | 5 |
| 14 | Sells in the prior 10s (t-20 to t-10) | lower | 0.73 | 26.5 | 14.0 | 16 | 5 |
| 15 | Sell-count acceleration ratio (last/prior 10s) | higher | 0.72 | 0.90 | 1.43 | 16 | 5 |
| 16 | Cumulative wallets (screen, may be Chao1) | lower | 0.71 | 438 | 288 | 16 | 5 |
| 17 | Volume trend (last 10s / prior 10s) | higher | 0.71 | 0.78 | 1.06 | 16 | 5 |
| 18 | Entry t+ (seconds) | higher | 0.71 | 66.5 | 109 | 16 | 5 |
| 19 | Share of last-15s buyers who are new | higher | 0.71 | 0.73 | 0.91 | 16 | 5 |
| 20 | Cumulative trades (screen, may be scaled) | lower | 0.70 | 482 | 244 | 16 | 5 |
| 21 | Prior-15s buy count | lower | 0.70 | 54.5 | 25.0 | 16 | 5 |
| 22 | Sells in last 10s | lower | 0.69 | 29.0 | 20.0 | 16 | 5 |
| 23 | Prior-10s trade count | lower | 0.69 | 69.0 | 28.0 | 16 | 5 |
| 24 | Volume in 15s at entry / 15s at the high | lower | 0.69 | 0.61 | 0.24 | 16 | 5 |
| 25 | Buy/sell USD ratio during the drop | higher | 0.69 | 0.51 | 0.71 | 14 | 5 |
| 26 | Buy/sell count ratio during the drop | higher | 0.69 | 1.08 | 1.39 | 14 | 5 |
| 27 | Sell volume forming the drop ($) | higher | 0.68 | 1320 | 1733 | 16 | 5 |
| 28 | Summary sniper supply % (slot 0–3, 30m gross) | higher | 0.68 | 73.0 | 97.4 | 16 | 5 |
| 29 | Summary create-slot bundle % (30m gross) | higher | 0.66 | 58.6 | 71.2 | 16 | 5 |
| 30 | Prior-10s buy count | lower | 0.66 | 30.5 | 13.0 | 16 | 5 |
| 31 | Prior-15s buy/sell USD ratio | lower | 0.65 | 0.66 | 0.48 | 16 | 5 |
| 32 | Prior-10s buy/sell USD ratio | lower | 0.64 | 0.54 | 0.44 | 16 | 5 |
| 33 | Volume trend (last 15s / previous 15s) | higher | 0.64 | 0.59 | 0.68 | 16 | 5 |
| 34 | Creator had sold ≥95% of their buy by entry | lower | 0.64 | 1.00 | 1.00 | 16 | 5 |
| 35 | Creator had sold ≥80% of their buy by entry | lower | 0.64 | 1.00 | 1.00 | 16 | 5 |
| 36 | Creator had sold ≥50% of their buy by entry | lower | 0.64 | 1.00 | 1.00 | 16 | 5 |
| 37 | First-3s sniper net holdings % of supply at entry (ex-dev) | lower | 0.64 | 12.3 | 6.42 | 16 | 5 |
| 38 | New buyers in last 15s | lower | 0.64 | 30.0 | 9.00 | 16 | 5 |
| 39 | Seconds from launch to the high (build time) | higher | 0.63 | 51.0 | 53.0 | 16 | 5 |
| 40 | Seconds from launch to the high (tape) | higher | 0.63 | 51.0 | 53.0 | 16 | 5 |
| 41 | Sell-count acceleration (last 10s minus prior 10s) | higher | 0.63 | -2.00 | 3.00 | 16 | 5 |
| 42 | Starting market cap ($) | lower | 0.62 | 3077 | 2456 | 16 | 5 |
| 43 | Sell-volume acceleration ratio (last/prior 10s) | higher | 0.62 | 1.04 | 1.27 | 16 | 5 |
| 44 | Creator net holdings % of supply at entry | higher | 0.62 | 0.00 | 0.00 | 16 | 5 |
| 45 | Slot 0–3 sniper net holdings % at entry | lower | 0.62 | 7.97 | 3.96 | 16 | 5 |
| 46 | Top-2 seller share of drop sell volume | higher | 0.61 | 0.27 | 0.32 | 14 | 5 |
| 47 | Creator buy as % of supply | lower | 0.61 | 14.5 | 6.62 | 16 | 5 |
| 48 | Create-slot bundle net holdings % at entry | lower | 0.61 | 6.21 | 3.96 | 16 | 5 |
| 49 | Creator sold during the drop (1=yes) | higher | 0.60 | 0.00 | 0.00 | 16 | 5 |
| 50 | Creator tokens sold / bought, at entry | lower | 0.59 | 1.00 | 1.00 | 16 | 5 |
| 51 | First-3s sniper gross buy % of supply (ex-dev) | lower | 0.59 | 36.9 | 21.4 | 16 | 5 |
| 52 | Top-1 seller share of drop sell volume | higher | 0.59 | 0.14 | 0.17 | 14 | 5 |
| 53 | Cumulative USD volume at entry | higher | 0.57 | 19105 | 28953 | 16 | 5 |
| 54 | Create-slot bundle gross buy % of supply (ex-dev) | higher | 0.57 | 19.5 | 20.3 | 16 | 5 |
| 55 | Prior-15s USD volume | lower | 0.56 | 2139 | 627 | 16 | 5 |
| 56 | Prior-10s USD volume | lower | 0.56 | 1593 | 512 | 16 | 5 |
| 57 | Slot 0–3 sniper gross buy % of supply (ex-dev) | higher | 0.56 | 26.7 | 21.3 | 16 | 5 |
| 58 | Observed trades per wallet | lower | 0.55 | 2.22 | 2.31 | 16 | 5 |
| 59 | Observed wallets at entry | lower | 0.54 | 252 | 273 | 16 | 5 |
| 60 | Observed trades at entry | lower | 0.54 | 530 | 564 | 16 | 5 |
| 61 | Prior-10s buy/sell count ratio | lower | 0.54 | 1.05 | 1.08 | 16 | 5 |
| 62 | Sell fraction of last-10s trades | higher | 0.54 | 0.49 | 0.48 | 16 | 5 |
| 63 | Creator had sold any tokens by entry | lower | 0.54 | 1.00 | 1.00 | 16 | 5 |
| 64 | Sell-accel flag (count up AND buys < sells by count) | lower | 0.53 | 0.00 | 0.00 | 16 | 5 |
| 65 | Sell-accel flag (count up AND buy/sell USD ratio < 1) | higher | 0.51 | 0.00 | 0.00 | 16 | 5 |
| 66 | Sells forming the drop | higher | 0.50 | 34.0 | 42.0 | 16 | 5 |

### Backtest + paper (17 wins / 8 losses at 1.7x)

Adding Blue, POPDOG, Trench and BRAIN weakens the market-cap signal (all four paper trades were $5k–$7k) and leaves “fewer wallets / a thinner 15-second tape / longer after the high” at the top. Those still miss Blue on the cap and timing cuts. Treat this pooled ranking as exploratory: paper fills and backtest fills are not the same experiment.

| Rank | Feature | Losers are… | AUC | Win median | Loss median | Win n | Loss n |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | Cumulative wallets (screen, may be Chao1) | lower | 0.79 | 438 | 235 | 17 | 8 |
| 2 | Summary dev sold % in 30m (LOOKAHEAD — not usable live) | lower | 0.77 | 14.5 | 3.42 | 16 | 5 |
| 3 | First-3s sniper wallet count (ex-dev) | lower | 0.76 | 29.0 | 14.5 | 17 | 8 |
| 4 | Prior-15s trade count | lower | 0.74 | 102 | 52.5 | 17 | 8 |
| 5 | Seconds from running high to entry | higher | 0.72 | 11.0 | 24.5 | 17 | 8 |
| 6 | Sells in last 10s | lower | 0.72 | 30.0 | 17.5 | 17 | 8 |
| 7 | Entry t+ (seconds) | higher | 0.71 | 57.0 | 117 | 17 | 8 |
| 8 | Sells in the prior 10s (t-20 to t-10) | lower | 0.71 | 26.0 | 13.5 | 17 | 8 |
| 9 | New buyers in last 15s | lower | 0.71 | 30.0 | 11.5 | 17 | 8 |
| 10 | Prior-15s buy count | lower | 0.70 | 54.5 | 25.0 | 16 | 5 |
| 11 | Dip size % from running high | lower | 0.69 | 31.2 | 30.5 | 17 | 8 |
| 12 | Prior-10s trade count | lower | 0.69 | 69.0 | 28.0 | 16 | 5 |
| 13 | Create-slot bundle net holdings % at entry | lower | 0.68 | 5.12 | 0.23 | 17 | 8 |
| 14 | Share of last-15s buy volume from new buyers | higher | 0.68 | 0.78 | 0.97 | 17 | 8 |
| 15 | Sell volume forming the drop ($) | higher | 0.68 | 1320 | 1733 | 16 | 5 |
| 16 | Summary sniper supply % (slot 0–3, 30m gross) | higher | 0.68 | 73.0 | 97.4 | 16 | 5 |
| 17 | Slot 0–3 sniper net holdings % at entry | lower | 0.67 | 7.38 | 2.36 | 17 | 8 |
| 18 | Summary create-slot bundle % (30m gross) | higher | 0.66 | 58.6 | 71.2 | 16 | 5 |
| 19 | Prior-10s buy count | lower | 0.66 | 30.5 | 13.0 | 16 | 5 |
| 20 | Prior-10s buy/sell USD ratio | lower | 0.65 | 0.57 | 0.44 | 17 | 8 |
| 21 | First-3s sniper gross buy % of supply (ex-dev) | lower | 0.65 | 36.9 | 21.4 | 17 | 8 |
| 22 | Prior-15s buy/sell USD ratio | lower | 0.64 | 0.69 | 0.62 | 17 | 8 |
| 23 | Seconds from launch to the high (build time) | higher | 0.64 | 46.0 | 75.0 | 17 | 8 |
| 24 | Seconds from launch to the high (tape) | higher | 0.64 | 46.0 | 75.0 | 17 | 8 |
| 25 | Market cap at entry ($) | higher | 0.62 | 8157 | 10234 | 17 | 8 |
| 26 | ATH / entry mcap (room back to the high) | lower | 0.62 | 1.45 | 1.44 | 17 | 8 |
| 27 | 1.7x target / ATH at entry | higher | 0.62 | 1.17 | 1.18 | 17 | 8 |
| 28 | 1.7x target vs ATH gap % | higher | 0.62 | 16.9 | 17.9 | 17 | 8 |
| 29 | Cumulative trades (screen, may be scaled) | lower | 0.62 | 463 | 377 | 17 | 8 |
| 30 | Volume trend (last 10s / prior 10s) | higher | 0.62 | 0.80 | 1.01 | 17 | 8 |

## The four Blue-inspired signals, in detail

For each signal: who it would skip on the 21 backtest trades (and on v1.1’s remaining 20), the effect on 1.7x / 1.8x / volume-fade totals, and what it does to the four paper trades. Missing values do not skip, same as v1.1.

### 1) Creator had already sold all or most of their buy before the dip

Thresholds of 50%, 80% and 95% sold skip the exact same 17 backtest trades in this sample, so only the ≥95% cut is tabulated. ANT had sold only 16%; putinu and Wave had sold nothing; POPDOG had no measurable creator buy. This filter is dominated by winners and should not be added.

- **All 21 backtest trades:** skips 17 — losers: PI ($-0.88), JAKEY ($-0.15), JAKEY ($-0.81); winners: Ely ($+0.82), Yume ($+0.24), кот ($+0.11), PUTIN ($+0.67), valor ($+0.66), Vodkat ($+0.81), HITLER ($+1.91), BEAR ($+0.67), VIRUS ($+0.66), OLA ($+0.68), ACC ($+2.09), BOT ($+0.69), FRANK ($+0.67), HACKERMAN ($+0.71).
- **v1.1 only (20 trades, PI already out):** skips 16 — losers: JAKEY ($-0.15), JAKEY ($-0.81); winners: Ely ($+0.82), Yume ($+0.24), кот ($+0.11), PUTIN ($+0.67), valor ($+0.66), Vodkat ($+0.81), HITLER ($+1.91), BEAR ($+0.67), VIRUS ($+0.66), OLA ($+0.68), ACC ($+2.09), BOT ($+0.69), FRANK ($+0.67), HACKERMAN ($+0.71).
- **Paper (4):** skips 3 — losers: Trench ($-0.37), Blue ($-0.69); winners: BRAIN ($+0.08).

| Set | Exit | n | W/L/BE | Win % | Expectancy / $1 | Cum P&L | Max DD |
|---|---|---:|---|---:|---:|---:|---:|
| v1.1 only (reference) | 1.7x no-stop | 20 | 16/4/0 | 80.0% | $+0.49 | $+9.89 | $-0.96 |
| v1.1 + this skip | 1.7x no-stop | 4 | 2/2/0 | 50.0% | $-0.13 | $-0.53 | $-1.32 |
| v1.1 only (reference) | 1.8x no-stop | 20 | 14/6/0 | 70.0% | $+0.38 | $+7.66 | $-0.96 |
| v1.1 + this skip | 1.8x no-stop | 4 | 2/2/0 | 50.0% | $-0.10 | $-0.40 | $-1.32 |
| v1.1 only (reference) | volume-fade @ 1.7x | 20 | 15/5/0 | 75.0% | $+0.39 | $+7.89 | $-0.90 |
| v1.1 + this skip | volume-fade @ 1.7x | 4 | 2/2/0 | 50.0% | $-0.04 | $-0.14 | $-0.60 |
| v1.1 only (reference) | volume-fade @ 1.8x | 20 | 15/5/0 | 75.0% | $+0.38 | $+7.65 | $-0.90 |
| v1.1 + this skip | volume-fade @ 1.8x | 4 | 2/2/0 | 50.0% | $-0.04 | $-0.14 | $-0.60 |
| Paper, unfiltered | 1.7x, 1s fill | 4 | 1/3/0 | 25.0% | $-0.39 | $-1.55 | $-1.55 |
| Paper + this skip | 1.7x, 1s fill | 1 | 0/1/0 | 0.0% | $-0.57 | $-0.57 | $-0.57 |
| Paper, unfiltered | volume-fade, 1s fill | 4 | 2/2/0 | 50.0% | $-0.04 | $-0.16 | $-0.77 |
| Paper + this skip | volume-fade, 1s fill | 1 | 1/0/0 | 100.0% | $+0.44 | $+0.44 | $-0.00 |

### 2) Heavy sniper ownership (first ~3s or create-slot bundle ≥ ~40%)

Two different measures, because they tell different stories:

- **Net holdings at entry** of wallets that bought in the first 3 seconds (the honest “who still owns what” number): nobody is at 40%. A 30% cut skips only ACC and BOT (both winners). A 20% cut skips five winners (valor, HITLER, OLA, ACC, BOT) and zero losers.
- **Gross buys in the first 3 seconds:** a 40% cut skips 6 winners and 1 loser (first JAKEY). A 30% cut skips 12 winners and 2 losers.
- **Create-slot bundle, net:** nobody at 40%. **Bundle, gross ≥ 40%:** only the first JAKEY.
- **The 30-minute summary “sniper supply %” ≥ 40%** (gross, and not strictly pre-entry): 17 of 21 backtest trades, including 12 winners. This is the figure that makes it look like “almost every coin is sniper-heavy.” It is the wrong number for a live filter.

Tabulating the least-bad sounding cut, first-3s **gross** ≥ 40% — which is still a bad rule:

- **All 21 backtest trades:** skips 7 — losers: JAKEY ($-0.15); winners: Ely ($+0.82), PUTIN ($+0.67), Vodkat ($+0.81), BEAR ($+0.67), OLA ($+0.68), Maxlong ($+0.12).
- **v1.1 only:** skips 7 — losers: JAKEY ($-0.15); winners: Ely ($+0.82), PUTIN ($+0.67), Vodkat ($+0.81), BEAR ($+0.67), OLA ($+0.68), Maxlong ($+0.12).
- **Paper:** skips 1 — losers: —; winners: BRAIN ($+0.08).

| Set | Exit | n | W/L/BE | Win % | Expectancy / $1 | Cum P&L | Max DD |
|---|---|---:|---|---:|---:|---:|---:|
| v1.1 only (reference) | 1.7x no-stop | 20 | 16/4/0 | 80.0% | $+0.49 | $+9.89 | $-0.96 |
| v1.1 + sniper gross ≥40% | 1.7x no-stop | 13 | 10/3/0 | 76.9% | $+0.48 | $+6.27 | $-0.82 |
| v1.1 only (reference) | 1.8x no-stop | 20 | 14/6/0 | 70.0% | $+0.38 | $+7.66 | $-0.96 |
| v1.1 + sniper gross ≥40% | 1.8x no-stop | 13 | 9/4/0 | 69.2% | $+0.40 | $+5.21 | $-0.82 |
| v1.1 only (reference) | volume-fade @ 1.7x | 20 | 15/5/0 | 75.0% | $+0.39 | $+7.89 | $-0.90 |
| v1.1 + sniper gross ≥40% | volume-fade @ 1.7x | 13 | 9/4/0 | 69.2% | $+0.34 | $+4.38 | $-0.90 |
| Paper, unfiltered | 1.7x, 1s fill | 4 | 1/3/0 | 25.0% | $-0.39 | $-1.55 | $-1.55 |
| Paper + sniper gross ≥40% | 1.7x, 1s fill | 3 | 0/3/0 | 0.0% | $-0.54 | $-1.63 | $-1.63 |

For completeness: first-3s **net** ≥ 20% on v1.1 at 1.7x would skip only winners and would drop expectancy. It is not tabulated as a serious candidate.

### 3a) Sell acceleration at the dip — loose flag (sell count up, and 10s buy/sell USD ratio < 1)

This is the wording from the brief, using the existing USD buy/sell ratio (the same ratio already in the backtest files). A count-ratio version (buys fewer than sells by trade count) is worse on Blue: Blue had more buy *trades* than sells in that 10 seconds (count ratio 1.19) even though sell *volume* was higher, so the count version misses Blue. It also skips HITLER, BOT and HACKERMAN on the backtest. Only the USD-ratio version is tabulated.

- **All 21 backtest trades:** skips 8 — losers: JAKEY ($-0.15), JAKEY ($-0.81); winners: Wave ($+0.67), Yume ($+0.24), Vodkat ($+0.81), HITLER ($+1.91), OLA ($+0.68), BOT ($+0.69).
- **v1.1 only (20 trades, PI already out):** skips 8 — losers: JAKEY ($-0.15), JAKEY ($-0.81); winners: Wave ($+0.67), Yume ($+0.24), Vodkat ($+0.81), HITLER ($+1.91), OLA ($+0.68), BOT ($+0.69).
- **Paper (4):** skips 3 — losers: POPDOG ($-0.57), Blue ($-0.69); winners: BRAIN ($+0.08).

| Set | Exit | n | W/L/BE | Win % | Expectancy / $1 | Cum P&L | Max DD |
|---|---|---:|---|---:|---:|---:|---:|
| v1.1 only (reference) | 1.7x no-stop | 20 | 16/4/0 | 80.0% | $+0.49 | $+9.89 | $-0.96 |
| v1.1 + this skip | 1.7x no-stop | 12 | 10/2/0 | 83.3% | $+0.49 | $+5.86 | $-0.82 |
| v1.1 only (reference) | 1.8x no-stop | 20 | 14/6/0 | 70.0% | $+0.38 | $+7.66 | $-0.96 |
| v1.1 + this skip | 1.8x no-stop | 12 | 8/4/0 | 66.7% | $+0.28 | $+3.32 | $-2.01 |
| v1.1 only (reference) | volume-fade @ 1.7x | 20 | 15/5/0 | 75.0% | $+0.39 | $+7.89 | $-0.90 |
| v1.1 + this skip | volume-fade @ 1.7x | 12 | 9/3/0 | 75.0% | $+0.43 | $+5.14 | $-0.60 |
| v1.1 only (reference) | volume-fade @ 1.8x | 20 | 15/5/0 | 75.0% | $+0.38 | $+7.65 | $-0.90 |
| v1.1 + this skip | volume-fade @ 1.8x | 12 | 9/3/0 | 75.0% | $+0.39 | $+4.72 | $-0.60 |
| Paper, unfiltered | 1.7x, 1s fill | 4 | 1/3/0 | 25.0% | $-0.39 | $-1.55 | $-1.55 |
| Paper + this skip | 1.7x, 1s fill | 1 | 0/1/0 | 0.0% | $-0.37 | $-0.37 | $-0.37 |
| Paper, unfiltered | volume-fade, 1s fill | 4 | 2/2/0 | 50.0% | $-0.04 | $-0.16 | $-0.77 |
| Paper + this skip | volume-fade, 1s fill | 1 | 1/0/0 | 100.0% | $+0.17 | $+0.17 | $-0.00 |

### 3b) Sell acceleration — tighter candidate (sell count ≥ 2× the prior 10s, and USD buy/sell ratio < 1)

This is the only Blue-inspired cut that is even close to usable, and it is still messy. It catches Blue (4.3×) and the second JAKEY (2.5×). It also skips HITLER (2.07×, +$1.91) and BRAIN (2.67×, today’s only paper winner), plus Vodkat on the backtest. Overfit risk is high: the 2× cutoff was read off this same small sample.

- **All 21 backtest trades:** skips 3 — losers: JAKEY ($-0.81); winners: Vodkat ($+0.81), HITLER ($+1.91).
- **v1.1 only (20 trades, PI already out):** skips 3 — losers: JAKEY ($-0.81); winners: Vodkat ($+0.81), HITLER ($+1.91).
- **Paper (4):** skips 2 — losers: Blue ($-0.69); winners: BRAIN ($+0.08).

| Set | Exit | n | W/L/BE | Win % | Expectancy / $1 | Cum P&L | Max DD |
|---|---|---:|---|---:|---:|---:|---:|
| v1.1 only (reference) | 1.7x no-stop | 20 | 16/4/0 | 80.0% | $+0.49 | $+9.89 | $-0.96 |
| v1.1 + this skip | 1.7x no-stop | 17 | 14/3/0 | 82.4% | $+0.47 | $+7.97 | $-0.82 |
| v1.1 only (reference) | 1.8x no-stop | 20 | 14/6/0 | 70.0% | $+0.38 | $+7.66 | $-0.96 |
| v1.1 + this skip | 1.8x no-stop | 17 | 12/5/0 | 70.6% | $+0.34 | $+5.74 | $-1.25 |
| v1.1 only (reference) | volume-fade @ 1.7x | 20 | 15/5/0 | 75.0% | $+0.39 | $+7.89 | $-0.90 |
| v1.1 + this skip | volume-fade @ 1.7x | 17 | 12/5/0 | 70.6% | $+0.38 | $+6.38 | $-0.90 |
| v1.1 only (reference) | volume-fade @ 1.8x | 20 | 15/5/0 | 75.0% | $+0.38 | $+7.65 | $-0.90 |
| v1.1 + this skip | volume-fade @ 1.8x | 17 | 12/5/0 | 70.6% | $+0.36 | $+6.14 | $-0.90 |
| Paper, unfiltered | 1.7x, 1s fill | 4 | 1/3/0 | 25.0% | $-0.39 | $-1.55 | $-1.55 |
| Paper + this skip | 1.7x, 1s fill | 2 | 0/2/0 | 0.0% | $-0.47 | $-0.94 | $-0.94 |
| Paper, unfiltered | volume-fade, 1s fill | 4 | 2/2/0 | 50.0% | $-0.04 | $-0.16 | $-0.77 |
| Paper + this skip | volume-fade, 1s fill | 2 | 2/0/0 | 100.0% | $+0.30 | $+0.61 | $-0.00 |

### 4) The 1.7x target sits above the all-time high at entry

This flag is **yes on all 21 backtest trades and all 4 paper trades**. Every coin was a 30–33% dip, so the running high was about 1.43–1.50× the entry, and 1.7 is always above that. The target would only fall at or below the prior high after a dip of about 41% or more, which this screen almost never takes. Adding the flag would skip the entire book. It is not a filter; it is a restatement of “we buy 30% dips and target 1.7x.”

## Combinations with v1.1 and the cap / timing flags

These flags were already on the table from the Sunday review (skip if entry cap ≥ $12k, skip if t+ ≥ 200s), plus seconds-from-high > 28s, which the walk-forward learner had also surfaced and not frozen into v1.1. They are re-tested here unchanged. They help Sunday. They do nothing to today’s paper trades, including Blue.

Who each combination skips, among v1.1’s 20 trades (1.7x):

- **v1.1 + skip cap ≥ $12k:** skips 4 — losers: JAKEY ($-0.15), JAKEY ($-0.81); winners: valor ($+0.66), VIRUS ($+0.66).
- **v1.1 + skip t+ ≥ 200s:** skips 3 — losers: putinu ($-0.50), JAKEY ($-0.81); winners: VIRUS ($+0.66).
- **v1.1 + skip cap ≥ $12k OR t+ ≥ 200s:** skips 5 — losers: putinu ($-0.50), JAKEY ($-0.15), JAKEY ($-0.81); winners: valor ($+0.66), VIRUS ($+0.66).
- **v1.1 + skip secs-from-high > 28s:** skips 5 — losers: putinu ($-0.50), ANT ($-0.82), JAKEY ($-0.81); winners: VIRUS ($+0.66), Maxlong ($+0.12).
- **v1.1 + skip cap ≥ $12k OR t+ ≥ 200s OR secs > 28s:** skips 7 — losers: putinu ($-0.50), ANT ($-0.82), JAKEY ($-0.15), JAKEY ($-0.81); winners: valor ($+0.66), VIRUS ($+0.66), Maxlong ($+0.12).
- **v1.1 + tight sell-accel (ratio ≥ 2 and USD ratio < 1):** skips 3 — losers: JAKEY ($-0.81); winners: Vodkat ($+0.81), HITLER ($+1.91).
- **v1.1 + cap/timing OR tight sell-accel:** skips 7 — losers: putinu ($-0.50), JAKEY ($-0.15), JAKEY ($-0.81); winners: valor ($+0.66), Vodkat ($+0.81), HITLER ($+1.91), VIRUS ($+0.66).

And on paper: cap, t+ and secs-from-high skip **nobody**. Tight sell-accel skips Blue (loss) and BRAIN (win).

### Effect on v1.1 totals

| Filter | Exit | n | W/L/BE | Win % | Expectancy / $1 | Cum P&L | Max DD |
|---|---|---:|---|---:|---:|---:|---:|
| v1.1 only (reference) | 1.7x no-stop | 20 | 16/4/0 | 80.0% | $+0.49 | $+9.89 | $-0.96 |
| v1.1 only (reference) | 1.8x no-stop | 20 | 14/6/0 | 70.0% | $+0.38 | $+7.66 | $-0.96 |
| v1.1 only (reference) | volume-fade @ 1.7x | 20 | 15/5/0 | 75.0% | $+0.39 | $+7.89 | $-0.90 |
| v1.1 + skip cap ≥ $12k | 1.7x no-stop | 16 | 14/2/0 | 87.5% | $+0.60 | $+9.53 | $-0.82 |
| v1.1 + skip cap ≥ $12k | 1.8x no-stop | 16 | 12/4/0 | 75.0% | $+0.46 | $+7.43 | $-0.82 |
| v1.1 + skip cap ≥ $12k | volume-fade @ 1.7x | 16 | 12/4/0 | 75.0% | $+0.45 | $+7.22 | $-0.90 |
| v1.1 + skip t+ ≥ 200s | 1.7x no-stop | 17 | 15/2/0 | 88.2% | $+0.62 | $+10.54 | $-0.82 |
| v1.1 + skip t+ ≥ 200s | 1.8x no-stop | 17 | 13/4/0 | 76.5% | $+0.50 | $+8.54 | $-0.82 |
| v1.1 + skip t+ ≥ 200s | volume-fade @ 1.7x | 17 | 13/4/0 | 76.5% | $+0.43 | $+7.36 | $-0.90 |
| v1.1 + skip cap ≥ $12k OR t+ ≥ 200s | 1.7x no-stop | 15 | 14/1/0 | 93.3% | $+0.67 | $+10.03 | $-0.82 |
| v1.1 + skip cap ≥ $12k OR t+ ≥ 200s | 1.8x no-stop | 15 | 12/3/0 | 80.0% | $+0.53 | $+7.93 | $-0.82 |
| v1.1 + skip cap ≥ $12k OR t+ ≥ 200s | volume-fade @ 1.7x | 15 | 12/3/0 | 80.0% | $+0.49 | $+7.39 | $-0.90 |
| v1.1 + skip secs-from-high > 28s | 1.7x no-stop | 15 | 14/1/0 | 93.3% | $+0.75 | $+11.23 | $-0.15 |
| v1.1 + skip secs-from-high > 28s | 1.8x no-stop | 15 | 12/3/0 | 80.0% | $+0.62 | $+9.24 | $-0.70 |
| v1.1 + skip secs-from-high > 28s | volume-fade @ 1.7x | 15 | 12/3/0 | 80.0% | $+0.51 | $+7.71 | $-0.90 |
| v1.1 + skip cap ≥ $12k OR t+ ≥ 200s OR secs > 28s | 1.7x no-stop | 13 | 13/0/0 | 100.0% | $+0.82 | $+10.72 | $-0.00 |
| v1.1 + skip cap ≥ $12k OR t+ ≥ 200s OR secs > 28s | 1.8x no-stop | 13 | 11/2/0 | 84.6% | $+0.66 | $+8.63 | $-0.70 |
| v1.1 + skip cap ≥ $12k OR t+ ≥ 200s OR secs > 28s | volume-fade @ 1.7x | 13 | 11/2/0 | 84.6% | $+0.60 | $+7.74 | $-0.90 |
| v1.1 + tight sell-accel (ratio ≥ 2 and USD ratio < 1) | 1.7x no-stop | 17 | 14/3/0 | 82.4% | $+0.47 | $+7.97 | $-0.82 |
| v1.1 + tight sell-accel (ratio ≥ 2 and USD ratio < 1) | 1.8x no-stop | 17 | 12/5/0 | 70.6% | $+0.34 | $+5.74 | $-1.25 |
| v1.1 + tight sell-accel (ratio ≥ 2 and USD ratio < 1) | volume-fade @ 1.7x | 17 | 12/5/0 | 70.6% | $+0.38 | $+6.38 | $-0.90 |
| v1.1 + cap/timing OR tight sell-accel | 1.7x no-stop | 13 | 12/1/0 | 92.3% | $+0.56 | $+7.31 | $-0.82 |
| v1.1 + cap/timing OR tight sell-accel | 1.8x no-stop | 13 | 10/3/0 | 76.9% | $+0.40 | $+5.21 | $-0.82 |
| v1.1 + cap/timing OR tight sell-accel | volume-fade @ 1.7x | 13 | 10/3/0 | 76.9% | $+0.47 | $+6.12 | $-0.90 |

Reading the 1.7x no-stop line: adding “cap ≥ $12k or t+ ≥ 200s” on top of v1.1 goes from 20 trades, 16W/4L, +$9.89, to fewer trades, fewer leftover losers (ANT is the one that still gets through), and a lower total because valor and VIRUS (real winners) are also cut. Adding secs-from-high > 28s instead catches ANT and putinu and the second JAKEY, misses the first JAKEY (−$0.15), and cuts VIRUS and Maxlong. Stacking cap, timing and secs-from-high wipes the four remaining losers and also valor, VIRUS and Maxlong — a cleaner Sunday, and a textbook overfit if we froze it from n=20. Stacking the tight sell-accel flag on the cap/timing cut would also have skipped HITLER.

## Features that look different but are easy to overfit

- **Bigger run-up into the high** (loser median 6.3× start versus 4.6×) is mostly the same fact as a higher market cap. A 6× cut would have skipped ACC (+$2.09), one of the best trades.
- **Thinner tape in the 15 seconds before entry** (42 trades vs 104) and **fewer cumulative wallets** look real on Sunday. On paper, Blue had 63 trades and 189 wallets, so a “too few observed wallets” cut would not have helped, and on the backtest `obs wallets < 150` only skips winners (Wave, HACKERMAN).
- **More of the last-15s buy volume coming from new buyers** is a weak, easy-to-fit pattern (AUC 0.80 on Sunday, weaker once paper is included) and should not be turned into a rule from this sample.
- **First-3s sniper wallet *count* is lower on Sunday losers** (13 vs 29). That is the opposite of “heavy snipers are bad,” and Trench (a live loss) had 49 such wallets. Do not use it.
- Dip size, “room back to the high,” and “1.7x vs ATH” are almost the same variable. The median gap is tiny (30.2% vs 31.2% dips) and is not a useful live cut.

## Recommendation

For the out-of-sample days (Nov 20, 19, 18, 17, 16), keep **v1.1 frozen** (skip if the top-1 seller is above 50% of the drop). Do **not** add a sniper filter, a “dev already sold” filter, or a “1.7x is above ATH” filter. Those either cut winners, or fire on everyone.

Carry these as **candidates only**, tested unchanged, and report them beside plain v1.1:

1. **Skip if entry market cap ≥ $12k, or if entry t+ ≥ 200s.** Already motivated by Sunday, before this Blue study. Will be judged by whether Friday–Monday still show higher-cap and late entries losing. Today’s paper book, including Blue, would have been untouched.
2. **Skip if seconds from the running high > 28s.** Same status: a pre-existing Sunday clue, not a Blue clue. Can be reported next to (1), not stacked with it as a new frozen rule.
3. **Optional, weaker: skip if sell count in the last 10s is at least 2× the prior 10s and the last-10s buy/sell USD ratio is under 1.** This is the only new filter that would have avoided Blue. It would also have skipped HITLER and BRAIN. If it is tested at all, test it alone, do not stack it onto (1) or (2) and then freeze the combo from this sample.

The loose “sells are up and buys < sells” flag is too noisy to test as a hard skip. Volume-fade as an *exit* is a separate question and is already being tracked on the paper trader; this file does not change that.

If Friday–Monday disagree with (1) or (2), drop them. If the tight sell-accel flag also cuts good trades on those days, drop it too. The sample behind this note is too small to deserve another frozen rulebook version on its own.

