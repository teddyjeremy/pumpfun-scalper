# Non-SOL-quoted Pump.fun coins: census + backtest (Nov 16–23, 2026, EAT)

Research only (no trading, wallets or signing). Code: `screen/quote_coins/` (new files only; paper/, lib/parse.py and the running pipeline untouched). Generated 2026-11-23 17:41 EAT.

## How quote coins were identified

* Pump.fun bonding-curve accounts (166 bytes) store the **quote mint at byte offset 83** (System Program `111…1` = SOL). Verified on AGI (SPCX) and on a 100-coin random sample.
* Every launch whose create tx touched a non-WSOL mint (`extra_mints`) was checked with `getMultipleAccounts`; the quote vault always shows up there (100% of confirmed non-SOL curves had it). A ~3% random validation sample of launches with **no** extra mint was also checked: see the `val non-SOL` column (expect 0).
* Nov 18–22 launch lists come from the pipeline's enumerate cache; Nov 16, 17 and today (00:00–~16:20 EAT) were enumerated here via Helius `getTransactionsForAddress` on the mint-authority PDA.
* Quote tokens were identified with GeckoTerminal (`/tokens/multi`), USD price history = GeckoTerminal 15-min OHLCV of the quote's deepest pool (stables = $1).

## 1. How many launches are non-SOL quoted

| Day (EAT) | Launches | Non-SOL quoted | Share | Tokenized stocks | Stables | Pump memecoins as quote | Other tokens | val sample (non-SOL) | curve missing |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-11-16 Mon | 41,661 | 8,008 | 19% | 3502 | 490 | 845 | 3171 | 837 (0) | 44 |
| 2026-11-17 Tue | 48,447 | 8,278 | 17% | 2951 | 583 | 1785 | 2959 | 1149 (0) | 125 |
| 2026-11-18 Wed | 48,321 | 12,950 | 27% | 8075 | 463 | 2396 | 2016 | 1048 (0) | 53 |
| 2026-11-19 Thu | 41,476 | 9,946 | 24% | 6458 | 287 | 2003 | 1198 | 817 (0) | 43 |
| 2026-11-20 Fri | 39,373 | 7,019 | 18% | 3164 | 773 | 1568 | 1514 | 873 (0) | 59 |
| 2026-11-21 Sat | 37,178 | 5,642 | 15% | 2030 | 737 | 1157 | 1718 | 904 (0) | 40 |
| 2026-11-22 Sun | 29,885 | 4,053 | 14% | 1498 | 208 | 1280 | 1067 | 735 (0) | 50 |
| 2026-11-23 Mon (today, to ~16:20) | 23,626 | 2,926 | 12% | 760 | 233 | 723 | 1210 | 550 (0) | 23 |

Distinct quote mints over the period: **2519**. Top 25:

| Quote | Name | Type | Launches (period) | Mon 16 | Tue 17 | Wed 18 | Thu 19 | Fri 20 | Sat 21 | Sun 22 | Mon 23 | Price now | USD history source |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| TTWO `TTWofw…` | Take-Two Interactive Software - Backpa | tokenized_stock | 15961 | 1440 | 1170 | 6834 | 4601 | 1053 | 446 | 280 | 137 | $217.4 | TTWO / USDC (GT 15m) |
| PUMP `pumpCm…` | Pump | other_token | 4059 | 571 | 953 | 426 | 229 | 611 | 690 | 392 | 187 | $0.002534 | PUMP / USDC (GT 15m) |
| SPCX `SPCXxc…` | SpaceX - Backpack Securities | tokenized_stock | 3816 | 997 | 637 | 475 | 230 | 326 | 439 | 483 | 229 | $131.7 | SPCX / USDC (GT 15m) |
| USDC `EPjFWd…` | USD Coin | stable | 3774 | 490 | 583 | 463 | 287 | 773 | 737 | 208 | 233 | $0.9986 | pegged $1 |
| DCTY `9hjBLB…` | DacityCoin | pump_memecoin | 1614 | 0 | 0 | 629 | 913 | 54 | 5 | 7 | 6 | $0.002465 | DCTY / TTWO (GT 15m) |
| ANT `ANTBTZ…` | Anthropic - Backpack Securities | tokenized_stock | 1298 | 0 | 0 | 0 | 771 | 367 | 82 | 47 | 31 | $88.5 | ANT / USDC (GT 15m) |
| GTA6 `GTA6ot…` | GTA6 | other_token | 1148 | 0 | 548 | 527 | 47 | 21 | 4 | 1 | 0 | $1.005 | GT pool Hb5RbkVs… (not pulled) |
| NVDAx `Xsc9qv…` | NVIDIA xStock | tokenized_stock | 1128 | 302 | 297 | 219 | 47 | 93 | 59 | 62 | 49 | $136.3 | GT pool 49iMatQt… (not pulled) |
| ANTHRP `Pren1F…` | Anthropic PreStocks | other_token | 1030 | 53 | 108 | 381 | 381 | 57 | 14 | 20 | 16 | $607.3 | ANT / ANTHRP (GT 15m) |
| DJT `DJTu7v…` | Trump Media &amp; Technology Group Cor | tokenized_stock | 972 | 112 | 92 | 59 | 189 | 155 | 166 | 119 | 80 | $3.927 | DJT / USDC (GT 15m) |
| WBTC `3NZ9JM…` | Wrapped BTC (Wormhole) | other_token | 902 | 209 | 110 | 88 | 52 | 71 | 177 | 137 | 58 | $6.152e+04 | GT pool B5EwJVDu… (not pulled) |
| hSOL `he1ius…` | Helius Staked SOL | other_token | 792 | 82 | 21 | 14 | 14 | 50 | 102 | 97 | 412 | $85.42 | 1B / hSOL (GT 15m) |
| TSLAx `XsDoVf…` | Tesla xStock | tokenized_stock | 664 | 50 | 18 | 52 | 8 | 139 | 115 | 225 | 57 | $360.7 | TSLAx / USDC (GT 15m) |
| BP `BPxxfR…` | Backpack | other_token | 570 | 6 | 480 | 21 | 6 | 48 | 7 | 0 | 2 | $0.8422 | BP / USDC (GT 15m) |
| SQQQ `SQQQAa…` | ProShares UltraPro Short QQQ - Backpac | tokenized_stock | 549 | 13 | 18 | 4 | 9 | 214 | 266 | 15 | 10 | $52.35 | SQQQ / USDC (GT 15m) |
| DOGE `DoGEV7…` | Dogecoin | other_token | 532 | 118 | 127 | 56 | 20 | 30 | 98 | 56 | 27 | $0.05574 | DOGE / SOL (GT 15m) |
| GMEx `Xsf9mB…` | Gamestop xStock | tokenized_stock | 486 | 22 | 22 | 8 | 340 | 56 | 29 | 7 | 2 | $26.87 | GMEx / USDC (GT 15m) |
| FRONTIER `58QyXQ…` | AI Frontier | other_token | 475 | 72 | 34 | 43 | 55 | 49 | 81 | 58 | 83 | $0.7069 | FRONTIER / SOL (GT 15m) |
| ZEC `A7bdiY…` | Zcash | other_token | 385 | 80 | 55 | 29 | 60 | 53 | 59 | 22 | 27 | $794.9 | ZEC / USDC (GT 15m) |
| GTAVI `ERiM8N…` | Official Grand Theft Auto VI | pump_memecoin | 349 | 0 | 204 | 143 | 2 | 0 | 0 | 0 | 0 | $6.808e-06 | GT pool 9YbrZx4y… (not pulled) |
| MSFTx `XspzcW…` | Microsoft xStock | tokenized_stock | 312 | 60 | 122 | 37 | 50 | 10 | 28 | 0 | 5 | $503.2 | MSFTx / USDC (GT 15m) |
| AAPLx `XsbEhL…` | Apple xStock | tokenized_stock | 298 | 19 | 40 | 9 | 1 | 127 | 85 | 10 | 7 | $347.2 | AAPLx / USDC (GT 15m) |
| COINx `Xs7Zdz…` | Coinbase xStock | tokenized_stock | 253 | 28 | 146 | 36 | 8 | 14 | 9 | 11 | 1 | $106.1 | COINx / USDC (GT 15m) |
| FIONA `B34nXK…` | Fiona the Hippo | pump_memecoin | 252 | 0 | 0 | 0 | 0 | 207 | 33 | 12 | 0 | $0.001511 | FIONA / SOL (GT 15m) |
| OPENAI `PreweJ…` | OpenAI PreStocks | other_token | 250 | 31 | 64 | 33 | 23 | 42 | 34 | 5 | 18 | $1127 | GT pool 7Hvt1uyg… (not pulled) |

## 2. Graduation (curve `complete` flag as of now)

| Day | Non-SOL graduated | Non-SOL rate | SOL graduated (est.) | SOL rate (est.) |
|---|---:|---:|---:|---:|
| 2026-11-16 Mon | 132 / 8008 | 2% | 661 / 33,609 | 2% |
| 2026-11-17 Tue | 95 / 8278 | 1% | 792 / 40,044 | 2% |
| 2026-11-18 Wed | 266 / 12950 | 2% | 804 / 35,318 | 2% |
| 2026-11-19 Thu | 162 / 9946 | 2% | 835 / 31,487 | 3% |
| 2026-11-20 Fri | 80 / 7019 | 1% | 810 / 32,295 | 3% |
| 2026-11-21 Sat | 38 / 5642 | 1% | 511 / 31,496 | 2% |
| 2026-11-22 Sun | 34 / 4053 | 1% | 481 / 25,782 | 2% |
| 2026-11-23 Mon (today, to ~16:20) | 29 / 2926 | 1% | 334 / 20,677 | 2% |

**Among ACTIVE coins** (≥150 trades in the first 5 min on Bitquery):

| Day | Non-SOL active | Non-SOL graduated | rate | SOL active | SOL graduated | rate |
|---|---:|---:|---:|---:|---:|---:|
| 2026-11-22 Sun | 291 | 20 | 7% | 2466 | 353 | 14% |
| 2026-11-21 Sat | 429 | 22 | 5% | 2520 | 489 | 19% |
| 2026-11-20 Fri | 475 | 58 | 12% | 2328 | 535 | 23% |
| 2026-11-19 Thu | 770 | 113 | 15% | 2006 | 537 | 27% |

SOL rate is weighted (SOL coins with an extra mint counted 1:1; the rest from the ~3% validation sample). Today's coins can still graduate.

## 3. Active universe, base rate and backtests

Universe = launches with ≥150 trades in their first 5 minutes on Bitquery's cached minute bars (0 new points), the same definition as the SOL scalp sample; stratified random sample by 5-min trade tier (150–300 / 300–500 / 500–1000 / 1000+), day counts are tier-weighted estimates. Tapes: full, unsampled curve tapes t0−2 s…t0+480 s (Helius getTransactionsForAddress, 100 tx/page, ≤15 pages/coin). Prices: quote reserve ÷ token reserve × quote USD. Rules identical to SOL: entry t+30…300 s, fill at the first trade ≥1 s after the signal, TP ≥1.5x, out by ≤120 s, 1.25% fee each way + $0.0285 gas; per $5 = 5×(mult×0.9875²−1) − $0.0285.

### Base rate: coins trading at $5k–$10k (t+30…300 s) whose +1 s fill hits 1.5x within 120 s

| Day | Non-SOL tapes used | Non-SOL active (est./day) | Non-SOL in band (est./day) | **Non-SOL first-touch hits 1.5x** | any touch | 1.7x | median s | SOL active (est./day) | SOL in band (est.) | **SOL first-touch 1.5x** | SOL any | SOL 1.7x |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-11-22 Sun | 42 (skipped 1) | 291 | 187 | **48%** (29 sampled) | 69% | 38% | 26.0 | 2,758 | 1,249 | **36%** | 54% | 26% |
| 2026-11-21 Sat | 43 (skipped 3) | 429 | 343 | **46%** (35 sampled) | 66% | 37% | 27.5 | 2,949 | 1,110 | **50%** | 59% | 30% |
| 2026-11-20 Fri | 50 (skipped 0) | 475 | 440 | **51%** (45 sampled) | 76% | 42% | 36 | 2,803 | 1,158 | **43%** | 55% | 37% |
| 2026-11-19 Thu | 52 (skipped 0) | 770 | 646 | **40%** (43 sampled) | 70% | 26% | 46 | – | – | **–** | – | – |

Pooled non-SOL (unweighted, all sampled days): 152 band coins, first-touch 1.5x hit rate **46%**, any-touch 70%, 1.7x 36%, median 34.5 s.

By quote type (pooled, unweighted):

| Quote type | Band coins | First-touch 1.5x | Any touch | 1.7x |
|---|---:|---:|---:|---:|
| tokenized_stock | 90 | 41% | 68% | 33% |
| pump_memecoin | 17 | 41% | 76% | 24% |
| stable | 15 | 47% | 60% | 33% |
| other_token | 30 | 63% | 80% | 50% |

### Quick-flip setups (same code and parameters as screen/scalp_setups.py / scalp_run.py, rug layer on)

| Setup | Day | Non-SOL trades sampled | est. trades/day | est. wins/day | Win % | Exp per $1 (weighted) | per $1 plain | **Exp per $5** | Avg win | Avg loss | Worst | < −20% | Median hold s | SOL exp per $1 (same code) | SOL per $5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| reclaim (I+C recl25 L1) + G fast-inval, TP1.5, <=120s | 11-22 | 7 | 53 | 37 | 71% | +0.024 | +0.038 | **+0.235** | +0.112 | -0.148 | -0.189 | 0 | 24 | +0.100 | +0.613 |
| reclaim (I+C recl25 L1) + G fast-inval, TP1.5, <=120s | 11-21 | 3 | 33 | 0 | 0% | -0.202 | -0.189 | **-0.896** | – | -0.189 | -0.250 | 2 | 34 | +0.140 | +0.815 |
| reclaim (I+C recl25 L1) + G fast-inval, TP1.5, <=120s | 11-20 | 7 | 72 | 18 | 29% | -0.070 | -0.062 | **-0.236** | +0.100 | -0.127 | -0.191 | 0 | 20 | +0.118 | +0.704 |
| reclaim (I+C recl25 L1) + G fast-inval, TP1.5, <=120s | 11-19 | 3 | 37 | 0 | 0% | -0.196 | -0.220 | **-0.868** | – | -0.220 | -0.338 | 1 | 29 | – | – |
| **reclaim (I+C recl25 L1) + G fast-inval, TP1.5, <=120s** | **pooled** | 20 | | | 35% | | -0.070 | **-0.235** | +0.109 | -0.166 | -0.338 | 3 | 24.0 | | |
| breakout (B L2) + G fast-inval, TP1.7, <=60s | 11-22 | 7 | 45 | 8 | 29% | -0.019 | +0.037 | **+0.018** | +0.340 | -0.085 | -0.158 | 0 | 17 | +0.042 | +0.325 |
| breakout (B L2) + G fast-inval, TP1.7, <=60s | 11-21 | 3 | 42 | 0 | 0% | -0.103 | -0.098 | **-0.400** | – | -0.098 | -0.154 | 0 | 4 | +0.251 | +1.370 |
| breakout (B L2) + G fast-inval, TP1.7, <=60s | 11-20 | 7 | 93 | 13 | 14% | -0.064 | -0.064 | **-0.204** | +0.136 | -0.097 | -0.141 | 0 | 7 | -0.027 | -0.021 |
| breakout (B L2) + G fast-inval, TP1.7, <=60s | 11-19 | 5 | 75 | 0 | 0% | -0.126 | -0.117 | **-0.516** | – | -0.117 | -0.174 | 0 | 3 | – | – |
| **breakout (B L2) + G fast-inval, TP1.7, <=60s** | **pooled** | 22 | | | 14% | | -0.049 | **-0.129** | +0.272 | -0.099 | -0.174 | 0 | 7.0 | | |
| band entry (K r1 L1) TP1.5 <=120s fade | 11-22 | 27 | 166 | 60 | 48% | +0.024 | +0.120 | **+0.235** | +0.465 | -0.200 | -0.586 | 6 | 23 | -0.097 | -0.370 |
| band entry (K r1 L1) TP1.5 <=120s fade | 11-21 | 32 | 313 | 124 | 41% | +0.021 | +0.041 | **+0.221** | +0.364 | -0.179 | -0.753 | 4 | 30.5 | +0.047 | +0.349 |
| band entry (K r1 L1) TP1.5 <=120s fade | 11-20 | 39 | 392 | 174 | 46% | +0.021 | +0.033 | **+0.218** | +0.330 | -0.221 | -0.564 | 10 | 36 | -0.099 | -0.379 |
| band entry (K r1 L1) TP1.5 <=120s fade | 11-19 | 41 | 639 | 231 | 41% | -0.036 | -0.007 | **-0.067** | +0.266 | -0.201 | -0.512 | 9 | 31 | – | – |
| **band entry (K r1 L1) TP1.5 <=120s fade** | **pooled** | 139 | | | 44% | | +0.040 | **+0.313** | +0.348 | -0.201 | -0.753 | 29 | 31 | | |
| band entry (K r2 L2) TP1.7 <=120s stall20 | 11-22 | 23 | 145 | 26 | 26% | -0.101 | -0.067 | **-0.391** | +0.176 | -0.153 | -0.404 | 4 | 21 | +0.028 | +0.255 |
| band entry (K r2 L2) TP1.7 <=120s stall20 | 11-21 | 24 | 237 | 71 | 42% | -0.042 | +0.018 | **-0.094** | +0.299 | -0.183 | -0.490 | 4 | 30.0 | -0.036 | -0.065 |
| band entry (K r2 L2) TP1.7 <=120s stall20 | 11-20 | 31 | 334 | 122 | 42% | -0.020 | +0.035 | **+0.012** | +0.299 | -0.156 | -0.447 | 5 | 21 | -0.049 | -0.129 |
| band entry (K r2 L2) TP1.7 <=120s stall20 | 11-19 | 36 | 560 | 76 | 19% | -0.105 | -0.055 | **-0.411** | +0.447 | -0.176 | -0.569 | 10 | 21.0 | – | – |
| **band entry (K r2 L2) TP1.7 <=120s stall20** | **pooled** | 114 | | | 32% | | -0.018 | **+0.026** | +0.307 | -0.168 | -0.569 | 23 | 21.0 | | |
| band entry (K r1 L0) TP1.5 <=120s fade | 11-22 | 28 | 176 | 65 | 50% | +0.078 | +0.160 | **+0.502** | +0.499 | -0.179 | -0.418 | 6 | 29.5 | -0.098 | -0.374 |
| band entry (K r1 L0) TP1.5 <=120s fade | 11-21 | 32 | 313 | 132 | 44% | +0.025 | +0.037 | **+0.239** | +0.355 | -0.210 | -0.753 | 5 | 32.5 | +0.049 | +0.360 |
| band entry (K r1 L0) TP1.5 <=120s fade | 11-20 | 41 | 413 | 167 | 44% | +0.012 | +0.030 | **+0.175** | +0.319 | -0.197 | -0.564 | 8 | 34 | -0.099 | -0.379 |
| band entry (K r1 L0) TP1.5 <=120s fade | 11-19 | 42 | 647 | 220 | 40% | -0.039 | -0.014 | **-0.082** | +0.272 | -0.208 | -0.512 | 10 | 29.5 | – | – |
| **band entry (K r1 L0) TP1.5 <=120s fade** | **pooled** | 143 | | | 44% | | +0.044 | **+0.334** | +0.354 | -0.200 | -0.753 | 29 | 31 | | |

### Strict v0 / v1.1 (annotated v1.2, v1.3) on non-SOL coins

| Day | v0 signals (sampled) | est./day | v1.1 keeps | v1.2 keeps | v1.3 keeps | entries in $5–10k | TP1.5/120 s fade: net per $1 each | TP1.7: net per $1 each |
|---|---:|---:|---:|---:|---:|---:|---|---|
| 2026-11-22 Sun | 8 | 27 | 8 | 5 | 5 | 3 | -0.70, -0.49, +0.69, -0.05, +0.10, -0.10, +0.68, +0.20 | -0.70, -0.49, +0.69, -0.05, +0.10, -0.10, +0.68, +0.20 |
| 2026-11-21 Sat | 13 | 96 | 13 | 7 | 7 | 8 | -0.26, -0.67, -0.52, -0.25, -0.14, +0.16, +0.09, +0.43, -0.04, -0.01, +0.50, -0.23, +0.10 | -0.26, -0.67, -0.52, -0.25, -0.14, +0.16, +0.09, +0.69, -0.04, -0.01, +0.48, -0.23, +0.10 |
| 2026-11-20 Fri | 17 | 116 | 17 | 6 | 6 | 5 | +0.23, -0.23, -0.04, -0.27, +0.16, +0.16, -0.44, -0.18, +0.59, +0.60, -0.21, -0.19, -0.53, -0.08, -0.60, -0.51, +0.13 | +0.23, -0.23, -0.04, -0.27, +0.16, +0.16, -0.44, -0.18, +0.71, +0.60, -0.21, -0.19, -0.53, -0.08, -0.60, -0.51, +0.13 |
| 2026-11-19 Thu | 13 | 174 | 13 | 6 | 6 | 8 | +0.13, -0.25, -0.54, -0.57, -0.04, +0.17, +0.08, -0.15, +0.44, +0.17, -0.21, -0.52, -0.34 | +0.13, -0.25, -0.54, -0.57, -0.04, +0.17, +0.08, -0.15, +0.74, +0.17, -0.21, -0.52, -0.34 |

* 2026-11-22: sampled tapes by quote type {'tokenized_stock': 21, 'pump_memecoin': 9, 'stable': 2, 'other_token': 10}; skipped {'no_price_history': 1}; graduated inside 480 s: 1; truncated tapes (>1,500 txs before t+480): 9; results dropped because they needed data past a truncated tape's end: {'base': 3, 'base17': 4, 'band entry (K r1 L1) TP1.5 <=120s fade': 1, 'band entry (K r1 L0) TP1.5 <=120s fade': 1, 'v0': 1}.
* 2026-11-21: sampled tapes by quote type {'stable': 12, 'other_token': 7, 'tokenized_stock': 22, 'pump_memecoin': 2}; skipped {'no_price_history': 3}; graduated inside 480 s: 0; truncated tapes (>1,500 txs before t+480): 18; results dropped because they needed data past a truncated tape's end: {'base': 3, 'base17': 3}.
* 2026-11-20: sampled tapes by quote type {'pump_memecoin': 6, 'tokenized_stock': 24, 'other_token': 17, 'stable': 3}; skipped {}; graduated inside 480 s: 2; truncated tapes (>1,500 txs before t+480): 15; results dropped because they needed data past a truncated tape's end: {'base': 1, 'base17': 1}.
* 2026-11-19: sampled tapes by quote type {'pump_memecoin': 7, 'tokenized_stock': 42, 'other_token': 3}; skipped {}; graduated inside 480 s: 5; truncated tapes (>1,500 txs before t+480): 17; results dropped because they needed data past a truncated tape's end: {'base': 3, 'base17': 3, 'v0': 2}.

## 4. Verdict: are non-SOL-quoted coins better or worse scalp targets?

* **Big blind spot, now closed for research:** 12–27% of all launches each day are non-SOL quoted (Wed Nov 18 peak 27%, mostly TTWO-quoted (Take-Two, Backpack tokenized stock)). Our SOL-only decoder saw them as $0 volume, so the strict screen never judged them. On these tapes, strict v0 fires on an estimated ~30–170 non-SOL coins/day (51 sampled signals in 4 days), while the SOL screen found 22 in the whole week.
* **Not better as v0/v1.x targets:** the 51 sampled v0 signals averaged −$0.069 per $1 (TP 1.5x / ≤120 s / fade), 20 of 51 green. v1.1 (top-seller >50%) removed none of them. The v1.2 cut (≤28 s from the high) kept 24, which averaged +$0.025 (12 of 24 green). That's break-even, not an edge.
* **Base rate is the same or slightly higher:** 40–51% of non-SOL coins in the $5–10k band hit 1.5x within 2 min from the first touch (pooled 46%), against 36–50% for SOL coins. They get there more slowly (median 26–46 s vs 15–23 s). But there are ~10x fewer of them: ~190–650 band coins/day vs ~1,100–1,250 SOL.
* **Quick-flip setups:** the SOL favourites don't carry over. Steady reclaim was −$0.07 per $1 pooled (20 trades) and breakout −$0.05 (22 trades). Plain band entry (K, TP 1.5x) was the only family slightly positive: +$0.02 weighted on Sun/Sat/Fri, −$0.04 on Thu, pooled +$0.04 per $1 / +$0.31 per $5. Its losses are not minimal, though: 29 of 139 trades lost more than 20% and the worst lost 75%. On SOL coins the same rule lost about −$0.10 on 2 of 3 days.
* **Fewer graduations:** among active coins, 5–15% of non-SOL coins graduated vs 14–27% of SOL coins (Thu–Sun). Over all launches the non-SOL rate is also lower (table 2).
* **Bottom line:** non-SOL coins are *different*, not better. They make a worthwhile extra pool for a band-entry test in the paper trader, since their volume and wallet counts look farmed (about $40/trade, about 2 trades/wallet on v0 hits). They don't rescue the v0/v1.x rulebook.

## 5. Caveats

* Small samples: 187 priced tapes over 4 days (~40–50/day, tier-weighted). Setup rows have 3–40 trades/day. Treat every expectancy as ±$0.05–0.10 noise.
* 59 of the busiest tapes hit the 15-page cap before t+480 s. Any result that needed data past a tape's end was dropped and is listed per day above; that slightly favours early hits in the base rate.
* USD prices come from GeckoTerminal 15-minute closes of the quote's deepest pool, interpolated. Memecoin quotes can move within a 2-minute flip; the $5–10k band edges are ±a few % for them. 4 sampled coins were skipped (no usable quote price history). hSOL and a few others use today's price as a constant.
* Real execution on a non-SOL coin needs a SOL→quote swap in and quote→SOL out (or holding the quote). That's ~0.1–0.5% extra each way on deep stock/stable pools and more on memecoin quotes. It is **not** included above; at 0.3% each way it costs ~$0.006 per $1.
* Nov 16, 17 and 23 are counted and graduation-checked only. There are no Bitquery bars for those days and Nov 18's prescreen is unfinished, so no tapes were backtested for them. Today's coins can still graduate.
* Classification only checks launches whose create tx touched a non-WSOL mint. The random validation sample of the rest found 0 non-SOL curves in ~6,900 checked.

