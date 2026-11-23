"""Core study: timelines, dips, simulations, holders, chart, summary schema."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

from lib.constants import (
    TOTAL_SUPPLY_UI, TOKEN_DECIMALS, GRADUATION_SOL, WINDOW_SEC, PREFERRED_SEC,
    CURVE_FEE_BPS, DEFAULT_PRIORITY_SOL, DEFAULT_BASE_FEE_SOL,
    INITIAL_VIRTUAL_SOL, INITIAL_VIRTUAL_TOKEN, INITIAL_REAL_TOKEN,
    ACTIVE_LOOKBACK_SEC, ACTIVE_MIN_TRADES, ACTIVE_MIN_USD, SNIPER_SECONDS,
)
from lib.fetch import cache_dir, out_dir, _load_cached_txs, sol_price_fn
from lib.parse import parse_tx, parse_enhanced_tx, account_keys

EAT = timezone(timedelta(hours=3))
DATASET = Path("/workspace/pumpfun/dataset.csv")

DATASET_FIELDS = [
    "mint","name","symbol","launch_ts","launch_time_utc","launch_time_eat","launch_slot","create_sig","creator",
    "sol_usd_at_launch","migrated_within_30m","migrate_sec","n_ok_tx_30m","n_failed_early","n_trades_30m",
    "n_curve_trades","n_pumpswap_trades","tape_end_t_plus_sec",
    "n_buys","n_sells","unique_traders","unique_buyers","volume_sol_30m","volume_usd_30m","buy_vol_usd","sell_vol_usd",
    "start_mcap_usd","mcap_after_devbuy_usd","ath_mcap_usd","ath_price_sol","ath_t_plus_sec","end_30m_mcap_usd",
    "curve_progress_5m","curve_progress_30m","dev_buy_sol","dev_buy_supply_pct","dev_sol_pre","dev_sol_post_create",
    "dev_sold_supply_pct_30m","dev_realized_sol_30m","n_dips_25pct","n_dips_30pct","n_dips_40pct",
    "first_dip40_t_plus_sec","first_dip40_drop_pct","first_dip40_volume_active",
    "firstprint_hit_1_7","firstprint_hit_1_8","firstprint_hit_2_0","firstprint_secs_to_2x","firstprint_net_usd_2x","firstprint_dd_before_2x",
    "dip40_hit_1_7","dip40_hit_1_8","dip40_hit_2_0","dip40_secs_to_2x","dip40_net_usd_2x","dip40_dd_before_2x",
    "dip25_t_plus_sec","dip25_drop_pct","dip25_volume_active","dip25_hit_1_7","dip25_secs_1_7","dip25_net_1_7","dip25_dd_1_7","dip25_hit_1_8","dip25_secs_1_8","dip25_net_1_8","dip25_dd_1_8","dip25_hit_2_0","dip25_secs_2x","dip25_net_2x","dip25_dd_2x","dip25_time_stop_net","dip25_time_stop_mult","dip25_best_mult","dip25_best_net",
    "dip30_t_plus_sec","dip30_drop_pct","dip30_volume_active","dip30_hit_1_7","dip30_secs_1_7","dip30_net_1_7","dip30_dd_1_7","dip30_hit_1_8","dip30_secs_1_8","dip30_net_1_8","dip30_dd_1_8","dip30_hit_2_0","dip30_secs_2x","dip30_net_2x","dip30_dd_2x","dip30_time_stop_net","dip30_time_stop_mult","dip30_best_mult","dip30_best_net",
    "dip35_t_plus_sec","dip35_drop_pct","dip35_volume_active","dip35_hit_1_7","dip35_secs_1_7","dip35_net_1_7","dip35_dd_1_7","dip35_hit_1_8","dip35_secs_1_8","dip35_net_1_8","dip35_dd_1_8","dip35_hit_2_0","dip35_secs_2x","dip35_net_2x","dip35_dd_2x","dip35_time_stop_net","dip35_time_stop_mult","dip35_best_mult","dip35_best_net",
    "dip40t_t_plus_sec","dip40t_drop_pct","dip40t_volume_active","dip40t_hit_1_7","dip40t_secs_1_7","dip40t_net_1_7","dip40t_dd_1_7","dip40t_hit_1_8","dip40t_secs_1_8","dip40t_net_1_8","dip40t_dd_1_8","dip40t_hit_2_0","dip40t_secs_2x","dip40t_net_2x","dip40t_dd_2x","dip40t_time_stop_net","dip40t_time_stop_mult","dip40t_best_mult","dip40t_best_net",
    "best_entry_name","best_entry_net_usd_2x","sniper_wallets","sniper_supply_pct","sniper_sol",
    "bundle_wallets_create_slot","bundle_supply_pct","insider_wallets","insider_supply_pct",
    "top10_circ_pct_5m","top10_circ_pct_30m","top10_circ_pct_now",
    "median_priority_sol","median_jito_sol","curve_fee_bps","priority_sol_assumed","risk_verdict",
]


def eat(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(EAT)


def eat_str(ts: int) -> str:
    return eat(ts).strftime("%Y-%m-%d %H:%M:%S %Z")


def load_enhanced(mint: str) -> dict:
    ed = cache_dir(mint) / "enhanced"
    have = {}
    if not ed.exists():
        return have
    for part in ed.glob("part_*.json"):
        try:
            blob = json.loads(part.read_text())
            if isinstance(blob, dict):
                have.update(blob)
        except Exception:
            continue
    return have



def _is_usdc(mint: str | None) -> bool:
    return bool(mint) and mint.lower().startswith("epjfwdd5")

def _is_usdt(mint: str | None) -> bool:
    return bool(mint) and mint.lower().startswith("es9vmfrza")

def _is_wsol(mint: str | None) -> bool:
    return bool(mint) and mint.startswith("So11111111111111111111111111111111111111112")


def _xfer_accounts(tt: dict) -> set[str]:
    return {tt.get("fromUserAccount"), tt.get("toUserAccount")} - {None, ""}


def infer_quote_usd_points(enhanced: dict, quote_mint: str, sol_px) -> list[tuple[int, float]]:
    """Build (unix_ts, quote_usd) from the main same-tx hop (largest quote size) vs USDC/USDT/SOL.

    Naive totals are wrong on pump custom-quote buys: the user first buys the quote
    token, then spends it on the curve, so summing every VVV transfer double-counts
    and cuts the implied USD price roughly in half.
    """
    pts = []
    if not quote_mint or _is_wsol(quote_mint) or _is_usdc(quote_mint) or _is_usdt(quote_mint):
        return pts

    def best_hop_px(qts, bases, to_usd):
        hops = []
        for qtt in qts:
            qv = float(qtt.get("tokenAmount") or 0)
            if qv <= 0:
                continue
            qa = _xfer_accounts(qtt)
            for btt in bases:
                if _xfer_accounts(btt) & qa:
                    bv = float(btt.get("tokenAmount") or 0)
                    if bv > 0:
                        hops.append((qv, to_usd(bv) / qv))
        if not hops:
            return None
        hops.sort(key=lambda x: x[0], reverse=True)
        px = hops[0][1]
        if 0.0001 < px < 1_000_000:
            return px
        return None

    for item in enhanced.values():
        if item.get("transactionError"):
            continue
        ts = item.get("timestamp")
        if not ts:
            continue
        tts = item.get("tokenTransfers") or []
        qts = [tt for tt in tts if tt.get("mint") == quote_mint]
        if not qts:
            continue
        stables = [tt for tt in tts if _is_usdc(tt.get("mint")) or _is_usdt(tt.get("mint"))]
        wsols = [tt for tt in tts if _is_wsol(tt.get("mint"))]
        px = best_hop_px(qts, stables, lambda bv: bv)
        if px is None and wsols:
            try:
                sol = float(sol_px(ts))
            except Exception:
                sol = None
            if sol:
                px = best_hop_px(qts, wsols, lambda bv, sol=sol: bv * sol)
        if px:
            pts.append((int(ts), float(px)))
    pts.sort()
    if len(pts) >= 5:
        mid = sorted(p for _, p in pts)[len(pts)//2]
        pts = [(t, p) for t, p in pts if 0.5 * mid <= p <= 2.0 * mid]
    return pts


def quote_usd_fn(pts: list[tuple[int, float]], sol_px, quote_mint: str | None):
    """Return unix_s -> USD per 1 quote token."""
    if _is_wsol(quote_mint) or not quote_mint:
        return lambda ts: float(sol_px(ts))
    if _is_usdc(quote_mint) or _is_usdt(quote_mint):
        return lambda ts: 1.0
    def price(ts: int) -> float:
        if not pts:
            return float("nan")
        if ts <= pts[0][0]:
            return pts[0][1]
        if ts >= pts[-1][0]:
            return pts[-1][1]
        lo, hi = 0, len(pts) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if pts[mid][0] <= ts:
                lo = mid + 1
            else:
                hi = mid - 1
        # linear interpolate if possible
        if hi + 1 < len(pts):
            t0, p0 = pts[hi]
            t1, p1 = pts[hi + 1]
            if t1 != t0:
                a = (ts - t0) / (t1 - t0)
                return p0 + a * (p1 - p0)
        return pts[hi][1]
    return price


def build_trade_tape(mint: str, curve: str, creator: str, t0: int, sol_px, raws: dict, enhanced: dict, amm_pool: str | None = None) -> list[dict]:
    """Prefer raw TradeEvents; fill gaps from enhanced; append PumpSwap AMM fills after migrate."""
    def _priced_trades(row) -> bool:
        for tr in (row.get("_trades") or []):
            if (tr.get("sol_ui") or 0) > 0 or (tr.get("quote_ui") or 0) > 0:
                return True
            if (tr.get("price_sol") or 0) > 0 or (tr.get("price_quote") or 0) > 0:
                return True
        return False

    rows_by_sig = {}
    for sig, tx in raws.items():
        try:
            rows_by_sig[sig] = parse_tx(sig, tx, mint, curve)
            rows_by_sig[sig]["source"] = "raw"
        except Exception:
            continue
    for sig, item in enhanced.items():
        try:
            row = parse_enhanced_tx(item, mint, curve)
        except Exception:
            continue
        if sig not in rows_by_sig:
            rows_by_sig[sig] = row
            continue
        if (not _priced_trades(rows_by_sig[sig])) and row.get("_trades"):
            rows_by_sig[sig]["_trades"] = row["_trades"]
            rows_by_sig[sig]["source"] = "enhanced_fill"

    if amm_pool:
        for sig, item in enhanced.items():
            if _priced_trades(rows_by_sig.get(sig) or {}):
                continue
            try:
                row = parse_enhanced_tx(item, mint, amm_pool)
            except Exception:
                continue
            if not row.get("_trades"):
                continue
            for tr in row["_trades"]:
                tr["venue"] = "pumpswap"
            if sig not in rows_by_sig:
                row["source"] = "enhanced_pumpswap"
                rows_by_sig[sig] = row
            else:
                rows_by_sig[sig]["_trades"] = row["_trades"]
                rows_by_sig[sig]["source"] = "enhanced_pumpswap"

    trades = []
    for sig, row in rows_by_sig.items():
        for tr in row.get("_trades") or []:
            tr = dict(tr)
            tr["source"] = tr.get("source") or row.get("source")
            bt = tr.get("block_time")
            if bt is None:
                continue
            if bt - t0 > WINDOW_SEC or bt < t0:
                # allow exact t0
                if bt < t0:
                    continue
                if bt - t0 > WINDOW_SEC:
                    continue
            tr["t_plus_sec"] = bt - t0
            tr["time_eat"] = eat_str(bt)
            sol_usd = float(sol_px(bt))
            tr["sol_usd"] = sol_usd
            tr["price_usd"] = (tr["price_sol"] * sol_usd) if tr.get("price_sol") else None
            tr["mcap_usd"] = (tr["mcap_sol"] * sol_usd) if tr.get("mcap_sol") else None
            tr["volume_usd"] = (tr["sol_ui"] * sol_usd) if tr.get("sol_ui") else 0
            tr["is_dev"] = tr.get("trader") == creator
            tr["venue"] = tr.get("venue") or "curve"
            tr["tx_fee_sol"] = (tr.get("tx_fee_lamports") or 0) / 1e9
            tr["priority_sol"] = (tr.get("priority_lamports_est") or 0) / 1e9 if tr.get("priority_lamports_est") is not None else None
            tr["jito_tip_sol"] = (tr.get("jito_tip_lamports") or 0) / 1e9
            trades.append(tr)

    trades.sort(key=lambda x: (x.get("block_time") or 0, x.get("slot") or 0, 0 if x.get("is_create_tx") else 1, x.get("signature") or ""))

    # Custom-quote (non-SOL) USD pricing from same-tx hops when needed
    quote_mints = [t.get("quote_mint") for t in trades if t.get("quote_mint") and not t.get("is_sol_quote", True) and not _is_wsol(t.get("quote_mint"))]
    quote_mint = None
    if quote_mints:
        from collections import Counter as _Counter
        quote_mint = _Counter(quote_mints).most_common(1)[0][0]
        pts = infer_quote_usd_points(enhanced, quote_mint, sol_px)
        qfn = quote_usd_fn(pts, sol_px, quote_mint)
        for tr in trades:
            bt = tr.get("block_time")
            q_usd = float(qfn(bt)) if bt else float("nan")
            tr["quote_usd"] = q_usd
            pq = tr.get("price_quote")
            qu = tr.get("quote_ui")
            if pq and q_usd == q_usd:  # not NaN
                tr["price_usd"] = pq * q_usd
                tr["mcap_usd"] = tr["price_usd"] * TOTAL_SUPPLY_UI
                tr["mcap_sol"] = (tr["mcap_usd"] / tr["sol_usd"]) if tr.get("sol_usd") else None
                if not tr.get("price_sol"):
                    tr["price_sol"] = pq  # used for dip ratios; unit is quote, not SOL
            if qu and q_usd == q_usd:
                tr["volume_usd"] = qu * q_usd
        # stash for summaries
        if trades:
            trades[0]["_quote_mint"] = quote_mint
            trades[0]["_quote_usd_points"] = len(pts)
            if pts:
                trades[0]["_quote_usd_median"] = sorted(p for _, p in pts)[len(pts)//2]

    # Reconstruct curve reserves / mcap from flow when missing (SOL-quoted curves only)
    sol_quoted = not any(not t.get("is_sol_quote", True) and t.get("quote_mint") and not _is_wsol(t.get("quote_mint")) for t in trades[:50])
    real_sol = 0.0
    tokens_sold = 0.0
    real_quote = 0.0
    for tr in trades:
        qamt = tr.get("sol_ui") if sol_quoted else tr.get("quote_ui")
        qamt = qamt or 0
        if tr["side"] == "buy":
            real_sol += (tr.get("sol_ui") or 0) if sol_quoted else 0
            real_quote += qamt
            tokens_sold += tr["token_ui"]
        else:
            real_sol -= (tr.get("sol_ui") or 0) if sol_quoted else 0
            real_quote -= qamt
            tokens_sold -= tr["token_ui"]
        tr["real_sol_ui_recon"] = real_sol if sol_quoted else None
        tr["real_quote_ui_recon"] = real_quote
        tr["tokens_sold_recon"] = tokens_sold
        if sol_quoted:
            vsol = INITIAL_VIRTUAL_SOL / 1e9 + real_sol
            vtok = INITIAL_VIRTUAL_TOKEN / (10 ** TOKEN_DECIMALS) - tokens_sold
            if vtok > 0:
                price_curve = vsol / vtok
                mcap_sol = price_curve * TOTAL_SUPPLY_UI
            else:
                price_curve, mcap_sol = tr.get("price_curve_sol"), tr.get("mcap_sol")
            tr["price_curve_sol"] = tr.get("price_curve_sol") or price_curve
            tr["mcap_sol"] = tr.get("mcap_sol") or mcap_sol
            tr["mcap_usd"] = (tr["mcap_sol"] * tr["sol_usd"]) if tr.get("mcap_sol") else tr.get("mcap_usd")
            tr["curve_progress_pct"] = (real_sol / GRADUATION_SOL) * 100.0
        else:
            # execution-price mcap already set from quote USD; progress unknown (non-SOL graduation)
            if tr.get("price_usd"):
                tr["mcap_usd"] = tr["price_usd"] * TOTAL_SUPPLY_UI
            tr["curve_progress_pct"] = None
    return trades, rows_by_sig


def trades_df(trades: list[dict]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame(trades)


def bucket_timeline(trades: list[dict], t0: int, width: int, until: int) -> list[dict]:
    if not trades:
        return []
    rows = []
    t = 0
    i = 0
    n = len(trades)
    last_close = trades[0].get("price_sol")
    last_mcap = trades[0].get("mcap_usd")
    last_prog = trades[0].get("curve_progress_pct")
    while t < until:
        end = min(t + width, until)
        bucket = []
        while i < n and trades[i]["t_plus_sec"] < end:
            if trades[i]["t_plus_sec"] >= t:
                bucket.append(trades[i])
            i_next = i
            i += 1
        # rewind? no, trades are sorted; but t_plus could equal previous. already consumed
        # actually we may skip trades with t_plus < t at start of first bucket only
        buys = [x for x in bucket if x["side"] == "buy"]
        sells = [x for x in bucket if x["side"] == "sell"]
        prices = [x["price_sol"] for x in bucket if x.get("price_sol")]
        mcaps = [x["mcap_usd"] for x in bucket if x.get("mcap_usd")]
        close = prices[-1] if prices else last_close
        mcap_c = mcaps[-1] if mcaps else last_mcap
        prog = bucket[-1]["curve_progress_pct"] if bucket else last_prog
        buyers = {x["trader"] for x in buys}
        sellers = {x["trader"] for x in sells}
        rows.append({
            "t_plus_start": t,
            "t_plus_end": end,
            "time_eat": eat_str(t0 + t),
            "n_trades": len(bucket),
            "n_buys": len(buys),
            "n_sells": len(sells),
            "unique_buyers": len(buyers),
            "unique_sellers": len(sellers),
            "unique_traders": len(buyers | sellers),
            "buy_vol_sol": sum((x.get("sol_ui") or 0) for x in buys),
            "sell_vol_sol": sum((x.get("sol_ui") or 0) for x in sells),
            "buy_vol_usd": sum(x.get("volume_usd") or 0 for x in buys),
            "sell_vol_usd": sum(x.get("volume_usd") or 0 for x in sells),
            "open_price_sol": prices[0] if prices else last_close,
            "high_price_sol": max(prices) if prices else last_close,
            "low_price_sol": min(prices) if prices else last_close,
            "close_price_sol": close,
            "open_mcap_usd": mcaps[0] if mcaps else last_mcap,
            "high_mcap_usd": max(mcaps) if mcaps else last_mcap,
            "low_mcap_usd": min(mcaps) if mcaps else last_mcap,
            "close_mcap_usd": mcap_c,
            "curve_progress_pct": prog,
        })
        last_close, last_mcap, last_prog = close, mcap_c, prog
        t = end
    return rows


def volume_active_at(trades: list[dict], t_plus: int, lookback: int = ACTIVE_LOOKBACK_SEC) -> dict:
    lo = t_plus - lookback
    w = [x for x in trades if lo <= x["t_plus_sec"] <= t_plus]
    n = len(w)
    usd = sum(x.get("volume_usd") or 0 for x in w)
    buys = sum(1 for x in w if x["side"] == "buy")
    active = (n >= ACTIVE_MIN_TRADES) and (usd >= ACTIVE_MIN_USD)
    return {
        "active": active,
        "n_trades": n,
        "volume_usd": usd,
        "n_buys": buys,
        "definition": f">={ACTIVE_MIN_TRADES} trades AND >=${ACTIVE_MIN_USD:.0f} USD in the prior {lookback}s (inclusive)",
    }


def find_dips(trades: list[dict], min_drop: float = 0.30) -> list[dict]:
    """Swing dips: decline of min_drop from a local peak; trough = min before a 15% bounce or new high."""
    if not trades:
        return []
    dips = []
    peak = trades[0]
    trough = trades[0]
    in_dip = False
    for tr in trades[1:]:
        px = tr.get("price_sol")
        if not px:
            continue
        if not in_dip:
            if px >= (peak.get("price_sol") or 0):
                peak = tr
                trough = tr
                continue
            dd = 1 - px / peak["price_sol"]
            if px < (trough.get("price_sol") or px):
                trough = tr
            if dd >= min_drop:
                in_dip = True
                trough = tr if px <= (trough.get("price_sol") or px) else trough
        else:
            if px < (trough.get("price_sol") or px):
                trough = tr
            # recovery 15% off trough or new high ends dip
            if px >= peak["price_sol"] or px >= trough["price_sol"] * 1.15:
                drop = 1 - trough["price_sol"] / peak["price_sol"]
                mid = [x for x in trades if peak["t_plus_sec"] <= x["t_plus_sec"] <= trough["t_plus_sec"]]
                after = [x for x in trades if trough["t_plus_sec"] < x["t_plus_sec"] <= trough["t_plus_sec"] + 15]
                dips.append({
                    "peak_t_plus": peak["t_plus_sec"],
                    "peak_slot": peak.get("slot"),
                    "peak_sig": peak.get("signature"),
                    "peak_price_sol": peak["price_sol"],
                    "peak_mcap_usd": peak.get("mcap_usd"),
                    "trough_t_plus": trough["t_plus_sec"],
                    "trough_slot": trough.get("slot"),
                    "trough_sig": trough.get("signature"),
                    "trough_price_sol": trough["price_sol"],
                    "trough_mcap_usd": trough.get("mcap_usd"),
                    "drop_pct": drop * 100,
                    "n_trades_during": len(mid),
                    "vol_usd_during": sum(x.get("volume_usd") or 0 for x in mid),
                    "buy_usd_during": sum((x.get("volume_usd") or 0) for x in mid if x["side"] == "buy"),
                    "sell_usd_during": sum((x.get("volume_usd") or 0) for x in mid if x["side"] == "sell"),
                    "n_trades_after_15s": len(after),
                    "vol_usd_after_15s": sum(x.get("volume_usd") or 0 for x in after),
                })
                # new peak starts at current if recovered high, else current is a bounce
                if px >= peak["price_sol"]:
                    peak = tr
                else:
                    peak = tr  # local bounce high may continue; treat current as new local peak candidate
                trough = tr
                in_dip = False
    # close open dip at end of window
    if in_dip and trough["signature"] != peak["signature"]:
        drop = 1 - trough["price_sol"] / peak["price_sol"]
        mid = [x for x in trades if peak["t_plus_sec"] <= x["t_plus_sec"] <= trough["t_plus_sec"]]
        after = [x for x in trades if trough["t_plus_sec"] < x["t_plus_sec"] <= trough["t_plus_sec"] + 15]
        dips.append({
            "peak_t_plus": peak["t_plus_sec"],
            "peak_slot": peak.get("slot"),
            "peak_sig": peak.get("signature"),
            "peak_price_sol": peak["price_sol"],
            "peak_mcap_usd": peak.get("mcap_usd"),
            "trough_t_plus": trough["t_plus_sec"],
            "trough_slot": trough.get("slot"),
            "trough_sig": trough.get("signature"),
            "trough_price_sol": trough["price_sol"],
            "trough_mcap_usd": trough.get("mcap_usd"),
            "drop_pct": drop * 100,
            "n_trades_during": len(mid),
            "vol_usd_during": sum(x.get("volume_usd") or 0 for x in mid),
            "buy_usd_during": sum((x.get("volume_usd") or 0) for x in mid if x["side"] == "buy"),
            "sell_usd_during": sum((x.get("volume_usd") or 0) for x in mid if x["side"] == "sell"),
            "n_trades_after_15s": len(after),
            "vol_usd_after_15s": sum(x.get("volume_usd") or 0 for x in after),
            "open_at_end": True,
        })
    return dips


def simulate_trade(trades: list[dict], entry: dict, sol_usd_entry: float, priority_sol: float,
                   label: str, reason: str, extra: dict | None = None) -> dict:
    """$1 swap + gas each side. Gross multiples on execution price."""
    fee = CURVE_FEE_BPS / 10000.0
    gas = (priority_sol + DEFAULT_BASE_FEE_SOL) * 2  # buy+sell, in SOL
    gas_usd = gas * sol_usd_entry
    px = entry.get("price_sol")
    entry_price_usd = entry.get("price_usd")
    if not entry_price_usd and px and sol_usd_entry:
        entry_price_usd = px * sol_usd_entry
    # $1 into swap, priced in USD so custom-quote coins work
    tokens = (1.0 * (1.0 - fee)) / entry_price_usd if entry_price_usd else 0
    after = [t for t in trades if (t["t_plus_sec"] > entry["t_plus_sec"]) or (
        t["t_plus_sec"] == entry["t_plus_sec"] and t["signature"] != entry["signature"] and t["side"] == "sell"
    )]
    # only later trades for exits
    after = [t for t in trades if (t["block_time"], t.get("slot") or 0, t["signature"]) >
             (entry["block_time"], entry.get("slot") or 0, entry["signature"]) and (t.get("price_usd") or t.get("price_sol"))]

    def net_at(exit_trade_or_px, exit_sol_usd=None):
        if isinstance(exit_trade_or_px, dict):
            exit_usd = exit_trade_or_px.get("price_usd")
            if not exit_usd:
                ep = exit_trade_or_px.get("price_sol")
                es = exit_trade_or_px.get("sol_usd") or exit_sol_usd or sol_usd_entry
                exit_usd = (ep * es) if ep and es else None
        else:
            es = exit_sol_usd or sol_usd_entry
            exit_usd = (exit_trade_or_px * es) if exit_trade_or_px and es else None
        if not exit_usd:
            return None
        proceeds = tokens * exit_usd * (1.0 - fee)
        return proceeds - 1.0 - gas_usd

    out = {
        "entry_name": label,
        "reason": reason,
        "entry_sig": entry.get("signature"),
        "entry_slot": entry.get("slot"),
        "entry_t_plus_sec": entry.get("t_plus_sec"),
        "entry_time_eat": entry.get("time_eat"),
        "entry_side_print": entry.get("side"),
        "entry_price_sol": px,
        "entry_price_usd": entry.get("price_usd"),
        "entry_mcap_usd": entry.get("mcap_usd"),
        "entry_is_dev_print": bool(entry.get("is_dev")),
        "sol_usd": sol_usd_entry,
        "fee_bps": CURVE_FEE_BPS,
        "priority_sol_per_tx": priority_sol,
        "base_fee_sol_per_tx": DEFAULT_BASE_FEE_SOL,
        "roundtrip_gas_usd": gas_usd,
        "tokens_for_$1": tokens,
    }
    if extra:
        out.update(extra)

    def _px_usd(t):
        if t.get("price_usd"):
            return t["price_usd"]
        if t.get("price_sol") and t.get("sol_usd"):
            return t["price_sol"] * t["sol_usd"]
        return None

    def first_hit(mult):
        def _px_usd(t):
            if t.get("price_usd"):
                return t["price_usd"]
            if t.get("price_sol") and t.get("sol_usd"):
                return t["price_sol"] * t["sol_usd"]
            return None
        for t in after:
            tu = _px_usd(t)
            if entry_price_usd and tu and tu >= entry_price_usd * mult:
                # max dd between entry and this t
                window = [x for x in after if (x["block_time"], x.get("slot") or 0, x["signature"]) <=
                          (t["block_time"], t.get("slot") or 0, t["signature"])]
                min_u = min((_px_usd(x) or entry_price_usd) for x in window)
                dd = min(0.0, (min_u / entry_price_usd - 1) * 100) if entry_price_usd else None
                return {
                    "hit": True,
                    "t_plus_sec": t["t_plus_sec"],
                    "secs_after_entry": t["t_plus_sec"] - entry["t_plus_sec"],
                    "exit_sig": t["signature"],
                    "exit_price_sol": t.get("price_sol"),
                    "exit_mcap_usd": t.get("mcap_usd"),
                    "max_dd_pct_before": dd,
                    "net_usd": net_at(t),
                }
        # never hit: dd over rest of window
        us = [_px_usd(x) for x in after if _px_usd(x)]
        min_u = min(us) if us else entry_price_usd
        max_u = max(us) if us else entry_price_usd
        return {
            "hit": False,
            "t_plus_sec": None,
            "secs_after_entry": None,
            "exit_sig": None,
            "exit_price_sol": None,
            "exit_mcap_usd": None,
            "max_dd_pct_before": (min(0.0, (min_u / entry_price_usd - 1) * 100) if entry_price_usd and min_u else None),
            "net_usd": None,
            "max_mult_reached": (max_u / entry_price_usd) if entry_price_usd and max_u else None,
        }

    for name, mult in [("1_7", 1.7), ("1_8", 1.8), ("2_0", 2.0)]:
        h = first_hit(mult)
        for k, v in h.items():
            out[f"{name}_{k}"] = v

    # best realistic exit: max execution price after entry in window
    if after:
        best = max(after, key=lambda x: (_px_usd(x) or 0))
        out["best_exit_t_plus_sec"] = best["t_plus_sec"]
        out["best_exit_secs"] = best["t_plus_sec"] - entry["t_plus_sec"]
        out["best_exit_sig"] = best["signature"]
        out["best_exit_price_sol"] = best.get("price_sol")
        bu = _px_usd(best)
        out["best_exit_mult"] = (bu / entry_price_usd) if entry_price_usd and bu else None
        out["best_exit_net_usd"] = net_at(best)
        # simple rule: take 1.8x if hit else exit at end of 5 min from launch or 30m, whichever: 
        # rule: TP 1.8x, else time-stop at t+300s from launch if entry before 5m, else end of 30m
        if out["1_8_hit"]:
            out["rule_exit"] = "take_profit_1.8x"
            out["rule_net_usd"] = out["1_8_net_usd"]
            out["rule_t_plus_sec"] = out["1_8_t_plus_sec"]
        else:
            # last trade in preferred window after entry, else last in 30m
            pref = [x for x in after if x["t_plus_sec"] <= PREFERRED_SEC]
            ender = pref[-1] if pref else after[-1]
            out["rule_exit"] = "time_stop_no_tp"
            out["rule_net_usd"] = net_at(ender)
            out["rule_t_plus_sec"] = ender["t_plus_sec"]
    else:
        out["best_exit_mult"] = None
        out["best_exit_net_usd"] = None
        out["rule_exit"] = "no_later_trade"
        out["rule_net_usd"] = None
    # also net if we hit +$0.50 — implied multiple
    # already have nets at 1.7/1.8/2.0
    out["clears_0_50_at_1_7"] = bool(out.get("1_7_hit") and (out.get("1_7_net_usd") or 0) >= 0.50)
    out["clears_0_50_at_1_8"] = bool(out.get("1_8_hit") and (out.get("1_8_net_usd") or 0) >= 0.50)
    out["clears_0_50_at_2_0"] = bool(out.get("2_0_hit") and (out.get("2_0_net_usd") or 0) >= 0.50)

    # Explicit 5-minute time stop from entry, capped at the 30-min window (always recorded).
    stop_t = min((entry.get("t_plus_sec") or 0) + 300, WINDOW_SEC)
    stop_trades = [t for t in after if t["t_plus_sec"] <= stop_t] if after else []
    if stop_trades and px:
        st = stop_trades[-1]
        out["time_stop_5m_t_plus_sec"] = st["t_plus_sec"]
        out["time_stop_5m_secs"] = st["t_plus_sec"] - entry["t_plus_sec"]
        su = _px_usd(st)
        out["time_stop_5m_mult"] = (su / entry_price_usd) if entry_price_usd and su else None
        out["time_stop_5m_net_usd"] = net_at(st)
        out["time_stop_5m_sig"] = st["signature"]
        min_u = min((_px_usd(x) or entry_price_usd) for x in stop_trades)
        out["time_stop_5m_dd"] = min(0.0, (min_u / entry_price_usd - 1) * 100) if entry_price_usd else None
    else:
        out["time_stop_5m_t_plus_sec"] = None
        out["time_stop_5m_secs"] = None
        out["time_stop_5m_mult"] = None
        out["time_stop_5m_net_usd"] = None
        out["time_stop_5m_sig"] = None
        out["time_stop_5m_dd"] = None
    return out


def first_threshold_touches(trades: list[dict], thresholds=(0.25, 0.30, 0.35, 0.40)) -> dict:
    """First print where price is down >= T from the running local high (resets on a new high)."""
    found = {}
    if not trades:
        return found
    peak = None
    for tr in trades:
        px = tr.get("price_sol")
        if not px:
            continue
        if peak is None or px >= peak["price_sol"]:
            peak = tr
            continue
        dd = 1 - px / peak["price_sol"]
        for T in thresholds:
            if T not in found and dd >= T:
                va = volume_active_at(trades, tr["t_plus_sec"])
                found[T] = {
                    "trade": tr,
                    "peak": peak,
                    "drop_at_touch_pct": dd * 100,
                    "volume_active": va["active"],
                    "va": va,
                    "peak_t_plus": peak.get("t_plus_sec"),
                    "peak_mcap_usd": peak.get("mcap_usd"),
                }
    return found


def pick_entries(trades: list[dict], creator: str, dips: list[dict]) -> list[tuple[str, dict, str, dict]]:
    """Return candidate entries (label, trade, reason, extra).

    Threshold entries are first *touches* of -25/-30/-35/-40% from a running local high,
    so those triggers can be compared even when they fall on the same swing.
    Trough entries for the first completed >=25/30/40% dip are included when they
    differ from the corresponding touch.
    """
    out = []
    seen_sigs_labels = set()

    def add(label, trade, reason, extra):
        if not trade:
            return
        key = (label, trade.get("signature"))
        if key in seen_sigs_labels:
            return
        seen_sigs_labels.add(key)
        out.append((label, trade, reason, extra or {}))

    first_pub = next((t for t in trades if t["side"] == "buy" and not t.get("is_dev")), None)
    if first_pub:
        add("first_public_buy", first_pub,
            "First non-dev buy after create (retail first-print; you cannot have the dev-buy price).", {})
    if trades:
        add("first_print_incl_dev", trades[0],
            "First fill on the tape (usually the dev buy in the create tx; not a realistic retail entry).", {})

    touches = first_threshold_touches(trades)
    labels = {0.25: "dip25_touch", 0.30: "dip30_touch", 0.35: "dip35_touch", 0.40: "dip40_touch"}
    for T, lab in labels.items():
        hit = touches.get(T)
        if not hit:
            continue
        va = hit["va"]
        reason = (
            f"First touch of -{T*100:.0f}% from a running local high "
            f"(actual {hit['drop_at_touch_pct']:.1f}% from t+{hit['peak_t_plus']}s, "
            f"peak mcap ${hit['peak_mcap_usd'] or 0:.0f}, entry mcap ${hit['trade'].get('mcap_usd') or 0:.0f}). "
            f"Volume active? {va['active']} ({va['n_trades']} trades, ${va['volume_usd']:.0f} in prior {ACTIVE_LOOKBACK_SEC}s). "
            f"Definition: {va['definition']}"
        )
        add(lab, hit["trade"], reason, {
            "volume_active": va["active"],
            "threshold_pct": T * 100,
            "drop_at_touch_pct": hit["drop_at_touch_pct"],
            "entry_style": "threshold_touch",
            **{f"va_{k}": v for k, v in va.items() if k != "definition"},
        })

    for min_pct, lab in ((25, "dip25_trough"), (30, "dip30_trough"), (40, "dip40_trough")):
        cands = [d for d in dips if d["drop_pct"] >= min_pct]
        if not cands:
            continue
        d = cands[0]
        trough = next((tr for tr in trades if tr.get("signature") == d["trough_sig"]), None)
        if not trough:
            continue
        va = volume_active_at(trades, d["trough_t_plus"])
        touch_lab = {25: "dip25_touch", 30: "dip30_touch", 40: "dip40_touch"}[min_pct]
        touch = next((x for x in out if x[0] == touch_lab), None)
        if touch and touch[1].get("signature") == d["trough_sig"]:
            if min_pct == 40:
                add("first_dip40_volume_active" if va["active"] else "first_dip40_volume_quiet",
                    trough,
                    f"First ~40% dip trough ({d['drop_pct']:.1f}% from t+{d['peak_t_plus']}s). "
                    f"Same print as the -40% touch. Volume active? {va['active']}.",
                    {"volume_active": va["active"], "entry_style": "trough"})
            continue
        reason = (
            f"Trough of first completed dip that reached {min_pct}% "
            f"({d['drop_pct']:.1f}% from t+{d['peak_t_plus']}s mcap ${d.get('peak_mcap_usd') or 0:.0f} "
            f"to ${d.get('trough_mcap_usd') or 0:.0f}). Volume active? {va['active']} "
            f"({va['n_trades']} trades, ${va['volume_usd']:.0f}/15s)."
        )
        add(lab, trough, reason, {"volume_active": va["active"], "entry_style": "trough", "drop_pct": d["drop_pct"]})
        if min_pct == 40:
            add("first_dip40_volume_active" if va["active"] else "first_dip40_volume_quiet",
                trough, reason, {"volume_active": va["active"], "entry_style": "trough", "drop_pct": d["drop_pct"]})
    return out


def holders_snapshots(trades: list[dict], curve: str, t_points: list[int]) -> dict:
    bal = defaultdict(float)
    snaps = {}
    idx = 0
    n = len(trades)
    for tp in sorted(t_points):
        while idx < n and trades[idx]["t_plus_sec"] <= tp:
            tr = trades[idx]
            if tr["side"] == "buy":
                bal[tr["trader"]] += tr["token_ui"]
            else:
                bal[tr["trader"]] -= tr["token_ui"]
            idx += 1
        # exclude curve
        items = [(w, b) for w, b in bal.items() if w != curve and b > 1e-6]
        items.sort(key=lambda x: x[1], reverse=True)
        circulating = sum(b for _, b in items)
        top = items[:10]
        snaps[tp] = {
            "t_plus": tp,
            "n_holders": len(items),
            "circulating": circulating,
            "top10": [
                {"rank": i + 1, "wallet": w, "tokens": b, "pct_total_supply": b / TOTAL_SUPPLY_UI * 100,
                 "pct_circulating": (b / circulating * 100) if circulating else 0}
                for i, (w, b) in enumerate(top)
            ],
            "top10_pct_total": sum(b for _, b in top) / TOTAL_SUPPLY_UI * 100,
            "top10_pct_circ": (sum(b for _, b in top) / circulating * 100) if circulating else 0,
        }
    return snaps, bal


def classify_early(trades: list[dict], rows_by_sig: dict, creator: str, slot0: int, t0: int, curve: str) -> dict:
    sniper_slots = {slot0, slot0 + 1, slot0 + 2, slot0 + 3}
    snipers = {}
    bundle_create = {}
    first5 = {}
    for tr in trades:
        if tr["side"] != "buy" or tr.get("is_dev"):
            continue
        w = tr["trader"]
        rec = {
            "wallet": w,
            "first_sig": tr["signature"],
            "first_slot": tr["slot"],
            "first_t_plus": tr["t_plus_sec"],
            "sol": 0.0,
            "tokens": 0.0,
        }
        if tr["slot"] in sniper_slots:
            snipers.setdefault(w, rec)
            snipers[w]["sol"] += tr.get("sol_ui") or tr.get("quote_ui") or 0
            snipers[w]["tokens"] += tr["token_ui"]
        if tr["slot"] == slot0:
            bundle_create.setdefault(w, rec)
            bundle_create[w]["sol"] += tr.get("sol_ui") or tr.get("quote_ui") or 0
            bundle_create[w]["tokens"] += tr["token_ui"]
        if tr["t_plus_sec"] <= SNIPER_SECONDS:
            first5.setdefault(w, rec)
            first5[w]["sol"] += tr.get("sol_ui") or tr.get("quote_ui") or 0
            first5[w]["tokens"] += tr["token_ui"]

    # sells of those wallets in 30m
    def add_sells(group):
        for w, g in group.items():
            sells = [t for t in trades if t["trader"] == w and t["side"] == "sell"]
            g["sold_tokens"] = sum(t["token_ui"] for t in sells)
            g["sol_in_from_sells"] = sum((t.get("sol_ui") or 0) for t in sells)
            g["first_sell_t_plus"] = sells[0]["t_plus_sec"] if sells else None
            g["fully_sold"] = g["sold_tokens"] >= g["tokens"] * 0.95
            g["supply_pct"] = g["tokens"] / TOTAL_SUPPLY_UI * 100

    add_sells(snipers)
    add_sells(bundle_create)
    add_sells(first5)

    # same-slot multi-wallet buy clusters (slots with >=4 unique non-dev buyers)
    clusters = []
    by_slot = defaultdict(list)
    for tr in trades:
        if tr["side"] == "buy" and not tr.get("is_dev"):
            by_slot[tr["slot"]].append(tr)
    for slot, lst in by_slot.items():
        ws = {t["trader"] for t in lst}
        if len(ws) >= 4:
            clusters.append({
                "slot": slot,
                "t_plus": min(t["t_plus_sec"] for t in lst),
                "n_wallets": len(ws),
                "n_buys": len(lst),
                "sol": sum((t.get("sol_ui") or 0) for t in lst),
                "tokens": sum(t["token_ui"] for t in lst),
                "supply_pct": sum(t["token_ui"] for t in lst) / TOTAL_SUPPLY_UI * 100,
            })
    clusters.sort(key=lambda x: x["t_plus"])
    return {
        "snipers_slot0_3": snipers,
        "first5s": first5,
        "bundle_create_slot": bundle_create,
        "multi_buy_slots": clusters,
    }


def make_chart(trades, dips, sims, t0, name, symbol, mint, path: Path, ath=None):
    if not trades:
        return
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})
    ax, axv = axes
    ts = [eat(t["block_time"]) for t in trades]
    mcaps = [t.get("mcap_usd") or 0 for t in trades]
    ax.plot(ts, mcaps, color="#1f4e79", lw=1.0, label="Mcap (USD, post-trade curve)")
    # dips
    for i, d in enumerate(dips):
        pk = eat(t0 + int(d["peak_t_plus"]))
        tr = eat(t0 + int(d["trough_t_plus"]))
        ax.axvspan(pk, tr, color="#f4cccc", alpha=0.45, label="≥30% dip" if i == 0 else None)
        ax.scatter([tr], [d["trough_mcap_usd"]], color="#cc0000", s=30, zorder=5)
        if d["drop_pct"] >= 38:
            ax.annotate(f"-{d['drop_pct']:.0f}%", (tr, d["trough_mcap_usd"]),
                        textcoords="offset points", xytext=(6, -12), fontsize=8, color="#990000")
    if ath:
        ax.axhline(ath["mcap_usd"], color="#e69138", ls="--", lw=0.8, label=f"ATH ${ath['mcap_usd']:.0f}")
        ax.scatter([eat(ath["block_time"])], [ath["mcap_usd"]], color="#e69138", s=40, zorder=6)
    colors = {"first_public_buy": "#3d85c6", "first_print_incl_dev": "#999999",
              "first_dip40_volume_active": "#38761d", "first_dip40_volume_quiet": "#bf9000",
              "dip25_touch": "#a64d79", "dip30_touch": "#674ea7", "dip35_touch": "#e69138",
              "dip40_touch": "#38761d", "dip25_trough": "#c27ba0", "dip30_trough": "#8e7cc3",
              "dip40_trough": "#6aa84f"}
    for s in sims:
        lab = s["entry_name"]
        et = eat(t0 + int(s["entry_t_plus_sec"]))
        em = s.get("entry_mcap_usd")
        c = colors.get(lab, "#674ea7")
        ax.scatter([et], [em], marker="^", s=70, color=c, zorder=7, label=lab)
        if s.get("2_0_hit"):
            xt = eat(t0 + int(s["2_0_t_plus_sec"]))
            ax.scatter([xt], [s.get("2_0_exit_mcap_usd")], marker="v", s=50, color=c, zorder=7)
            ax.annotate("2x", (xt, s.get("2_0_exit_mcap_usd") or 0), textcoords="offset points",
                        xytext=(4, 8), fontsize=7, color=c)
        elif s.get("1_7_hit"):
            xt = eat(t0 + int(s["1_7_t_plus_sec"]))
            ax.scatter([xt], [s.get("1_7_exit_mcap_usd")], marker="v", s=50, color=c, zorder=7)
            ax.annotate("1.7x", (xt, s.get("1_7_exit_mcap_usd") or 0), textcoords="offset points",
                        xytext=(4, 8), fontsize=7, color=c)
    ax.set_ylabel("Market cap (USD)")
    ax.set_title(f"{name} ({symbol}) — first 30 minutes  |  launch {eat_str(t0)}")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    # volume bars 15s
    width = 15
    t = 0
    xs, buys, sells = [], [], []
    while t < WINDOW_SEC:
        end = t + width
        b = sum(x.get("volume_usd") or 0 for x in trades if t <= x["t_plus_sec"] < end and x["side"] == "buy")
        s = sum(x.get("volume_usd") or 0 for x in trades if t <= x["t_plus_sec"] < end and x["side"] == "sell")
        xs.append(eat(t0 + t))
        buys.append(b)
        sells.append(-s)
        t = end
    axv.bar(xs, buys, width=timedelta(seconds=15), color="#6aa84f", label="Buy USD")
    axv.bar(xs, sells, width=timedelta(seconds=15), color="#cc4125", label="Sell USD")
    axv.set_ylabel("Volume USD / 15s")
    axv.legend(loc="upper right", fontsize=7)
    axv.grid(True, alpha=0.3)
    axv.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=EAT))
    axv.set_xlabel("Time (Africa/Nairobi, UTC+3)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    if fieldnames is None:
        # union of keys, stable
        keys = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen and not str(k).startswith("_"):
                    keys.append(k); seen.add(k)
        fieldnames = keys
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})


def upsert_dataset(row: dict):
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if DATASET.exists() and DATASET.stat().st_size > 0:
        with DATASET.open() as f:
            rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get("mint") != row.get("mint")]
    # ensure all fields
    clean = {k: row.get(k, "") for k in DATASET_FIELDS}
    rows.append(clean)
    with DATASET.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DATASET_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in DATASET_FIELDS})
