#!/usr/bin/env python3
"""Summarise the live paper trader: execution metrics for entries/exits (not profit)."""
import json, sys, math
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

P = Path(__file__).resolve().parent
L = P / "logs"
NBO = timezone(timedelta(hours=3))
DELAYS = ("d400", "d1000", "d2000")


def q(v, p):
    v = sorted(x for x in v if isinstance(x, (int, float)))
    if not v:
        return None
    k = (len(v) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    return v[f] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def fmt(x, nd=2, suf=""):
    return "-" if x is None else f"{x:.{nd}f}{suf}"


LATE_S = 5.0


def is_late(r):
    if "late" in r and r["late"] not in (None, ""):
        return bool(int(r["late"]))
    return (r.get("detect_latency_s_vs_blocktime") or 0) > LATE_S


def is_adopted(r):
    try:
        return int(r.get("late_adopted") or 0) == 1
    except (TypeError, ValueError):
        return False


def excluded(r):
    """Main stats use only trades seen on time on coins we tracked from their create event."""
    return is_late(r) or is_adopted(r)


def rowtag(r):
    return ("A" if is_adopted(r) else " ") + ("L" if is_late(r) else " ")


def load(name):
    p = L / name
    if not p.exists():
        return []
    out = []
    for line in p.open():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "SIGNAL"  # SIGNAL | ALL (incl. v1.1-skipped shadows)
    st = json.loads((P / "status.json").read_text()) if (P / "status.json").exists() else {}
    hourly = json.loads((L / "hourly.json").read_text()) if (L / "hourly.json").exists() else {}
    sigs = load("signals.jsonl")
    decs = load("decisions.jsonl")
    if which != "ALL":
        sigs = [s for s in sigs if s.get("status") == "SIGNAL"]
    late_sigs = [s for s in sigs if excluded(s)]
    sigs = [s for s in sigs if not excluded(s)]
    print(f"== Paper trader summary ({datetime.now(NBO):%Y-%m-%d %H:%M} EAT) | set={which} ==")
    if st:
        f = st.get("feed", {})
        hp = st.get("helius_probe", {})
        print(f"process pid {st.get('pid')} up {st.get('uptime_min')} min | feed connected={f.get('connected')} "
              f"reconnects={f.get('reconnects')} last msg {f.get('last_msg_age_s')}s ago | tracking {st.get('coins_tracking')} coins, "
              f"{st.get('paper_positions_open')} open paper positions")
        print(f"feed lag vs block time: median {st.get('lag_s_vs_blocktime_med')}s p90 {st.get('lag_s_vs_blocktime_p90')}s "
              f"(block time has 1s resolution) | vs slot-seen median {st.get('lag_ms_vs_slot_seen_med')} ms")
        print(f"helius probe: {hp.get('active_subs')} subs active, {hp.get('subs_total')} total, {hp.get('mb_today')} MB today "
              f"(~{hp.get('est_credits_today')} credits)")
    # per-day counts
    days = defaultdict(Counter)
    for h, v in hourly.items():
        for k, n in v.items():
            days[h[:10]][k] += n
    print("\n-- per day --")
    for d, c in sorted(days.items()):
        print(f"{d}: launches {c['launches_seen']}, dip candidates {c['dip_candidates']}, "
              f"v1.1 SIGNALS {c['signals_v11']}, v1.1 top-1-seller skips {c['skip:v11_top1_seller']}, finalized signals {c['finalized_signals']}")
    sk = Counter()
    for d, c in days.items():
        for k, n in c.items():
            if k.startswith("skip:") or k.startswith("nodip:"):
                sk[k] += n
    print("\n-- skip reasons (all days; a dip candidate can fail several filters) --")
    for k, n in sk.most_common():
        print(f"  {k:32s} {n}")
    lat = [d.get("detect_latency_s_vs_blocktime") for d in decs]
    lat_slot = [d.get("detect_latency_ms_vs_slot_seen") for d in decs]
    print(f"\n-- detection latency at dip decisions (n={len(decs)}, late >{LATE_S:g}s: {sum(1 for d in decs if is_late(d))}) --")
    print(f"  vs on-chain block time: median {fmt(q(lat,.5),3)}s  p90 {fmt(q(lat,.9),3)}s  (1s block-time resolution => ~+0.5s bias)")
    print(f"  vs slot first seen:     median {fmt(q(lat_slot,.5),0)}ms p90 {fmt(q(lat_slot,.9),0)}ms")
    hl = [s.get("helius_signal_latency_s_vs_blocktime") for s in load("signals.jsonl")]
    hd = [s.get("helius_minus_public_med_ms") for s in load("signals.jsonl")]
    if any(x is not None for x in hl):
        print(f"  Helius probe on signal coins: latency median {fmt(q(hl,.5),3)}s; Helius earlier than public by median {fmt(q([-x for x in hd if x is not None],.5),0)}ms")
    n = len(sigs)
    print(f"\n-- paper positions finalized: {n} on-time (excluded: {sum(1 for s in late_sigs if is_late(s))} late >{LATE_S:g}s behind chain, "
          f"{sum(1 for s in late_sigs if is_adopted(s))} on late-adopted coins) --")
    if late_sigs:
        print("  EXCLUDED (A = late-adopted coin, L = late; not in stats below):")
        for s in late_sigs:
            e = s.get("exits", {}).get("tp17", {})
            print(f"   {rowtag(s)} {s.get('signal_recv_eat','')[:19]} [{s.get('quote_symbol','SOL')}] {s.get('symbol','')[:10]:10s} lat {s.get('detect_latency_s_vs_blocktime')}s "
                  f"feed lag {s.get('feed_lag_med10s_at_signal', s.get('feed_lag_minute_med_est'))}s | slip1s {s.get('fill_d1000_slip_pct', s.get('fill_d1000_slip_vs_signal_spot_pct'))}% "
                  f"tp17 {e.get('exit')} net1s {e.get('exit_d1000_net')} | fade net1s {s.get('exits',{}).get('fade_17',{}).get('exit_d1000_net')}")
    if not n:
        print("  (no on-time positions yet)")
        return
    for dl in DELAYS:
        s1 = [s.get(f"fill_{dl}_slip_vs_signal_spot_pct") for s in sigs]
        mv = [s.get(f"fill_{dl}_spot_move_pct") for s in sigs]
        im = [s.get(f"fill_{dl}_impact_pct") for s in sigs]
        nb = [s.get(f"fill_{dl}_trades_between") for s in sigs]
        dip = [s.get(f"fill_{dl}_still_in_dip") for s in sigs]
        nh = [s.get(f"fill_{dl}_new_high_before_fill") for s in sigs]
        print(f"  entry fill +{int(dl[1:])}ms: slippage vs signal median {fmt(q(s1,.5))}% p90 {fmt(q(s1,.9))}% | "
              f"price move median {fmt(q(mv,.5))}% | $1 curve impact median {fmt(q(im,.5),4)}% | trades in between median {fmt(q(nb,.5),0)} | "
              f"still in dip {sum(1 for x in dip if x)}/{n} | new high before fill {sum(1 for x in nh if x)}/{n}")
    for tag in ("17", "18"):
        hit = [s for s in sigs if s.get(f"tp{tag}_touched")]
        secs = [s.get(f"tp{tag}_touch_secs") for s in hit]
        print(f"  TP {tag[0]}.{tag[1]}x touched within 5 min: {len(hit)}/{n} ({100*len(hit)/n:.0f}%), median time-to-TP {fmt(q(secs,.5),0)}s p90 {fmt(q(secs,.9),0)}s")
    mfe = [s.get("mfe_pct") for s in sigs]
    mae = [s.get("mae_pct") for s in sigs]
    print(f"  MFE median {fmt(q(mfe,.5))}% | MAE median {fmt(q(mae,.5))}% p10 {fmt(q(mae,.1))}%")
    fd = Counter(s.get("first_dynamic_exit") for s in sigs)
    print(f"  first dynamic exit to fire: {dict(fd.most_common(6))}")
    print("\n-- exits (entry = +1s fill; exit fills at +0.4/1/2s after trigger) --")
    keys = ["tp17", "tp18", "fade_17", "stall60_n10_17", "stall90_n10_17", "stall90_flat_17", "stall120_n10_17",
            "be20_17", "be30_17", "mom50_25", "c123_17", "c1234"]
    print(f"  {'rule':16s} {'n':>3} {'exit kinds':38s} {'med secs':>8} {'slip0.4':>8} {'slip1s':>8} {'slip2s':>8} {'net@trig':>9} {'net@1s':>8}")
    for k in keys:
        rs = [s["exits"][k] for s in sigs if k in s.get("exits", {})]
        if not rs:
            continue
        kinds = Counter((r.get("exit") or "").replace("volume_fade_", "fade_").replace("take_profit_", "tp_") for r in rs)
        ks = ",".join(f"{a}:{b}" for a, b in kinds.most_common(3))[:38]
        tot_t = sum(r.get("net_at_trigger") or 0 for r in rs)
        tot_1 = sum(r.get("exit_d1000_net") or 0 for r in rs)
        print(f"  {k:16s} {len(rs):>3} {ks:38s} {fmt(q([r.get('secs') for r in rs],.5),0):>8} "
              f"{fmt(q([r.get('exit_d400_slip_pct') for r in rs],.5)):>8} {fmt(q([r.get('exit_d1000_slip_pct') for r in rs],.5)):>8} "
              f"{fmt(q([r.get('exit_d2000_slip_pct') for r in rs],.5)):>8} {tot_t:>9.3f} {tot_1:>8.3f}")
    print("\n  slip = median exit fill vs trigger price (%); net = total $ over all positions for $1 each (trigger price vs modelled +1s fills).")
    print("\n-- last positions --")
    for s in sigs[-10:]:
        e = s.get("exits", {}).get("tp17", {})
        print(f"  {s.get('signal_recv_eat','')[:19]} {s.get('status'):16s} {s.get('symbol','')[:10]:10s} t+{s.get('entry_t_plus')}s "
              f"T{s.get('entry_threshold')} mcap ${s.get('signal_mcap_usd',0):,.0f} lat {s.get('detect_latency_s_vs_blocktime')}s "
              f"slip1s {s.get('fill_d1000_slip_vs_signal_spot_pct')}% | tp17 {e.get('exit')} {e.get('secs')}s net1s {e.get('exit_d1000_net')}")


def shadow_section():
    rows = load("shadow_trades.jsonl")
    print("\n== SHADOW quick-flip strategies (scalp_setups.py; paper only) ==")
    print("  IC_reclaim = I+C steady reclaim, TP 1.5x, hold 120s, fade + invalidation | B_breakout = L2 breakout, TP 1.7x, 60s")
    print("  '+rug' = same strategy with the rug layer (creator>8% OR create-slot wallets>25% OR uniq60<0.35) applied at entry")
    st = json.loads((P / "status.json").read_text()) if (P / "status.json").exists() else {}
    if st:
        print(f"  open shadow positions {st.get('shadow_open')} | eval tick p50 {st.get('shadow_tick_ms_p50')}ms p99 {st.get('shadow_tick_ms_p99')}ms")
    if not rows:
        print("  (no finalized shadow trades yet)")
        return
    late_rows = [r for r in rows if excluded(r)]
    rows_all = rows
    rows = [r for r in rows if not excluded(r)]
    print(f"  stats below exclude {sum(1 for r in late_rows if is_late(r))} late shadow trades (seen >{LATE_S:g}s behind chain) "
          f"and {sum(1 for r in late_rows if is_adopted(r))} on late-adopted coins")
    if late_rows:
        print("  EXCLUDED (A = late-adopted coin, L = late):")
        for r in late_rows:
            print(f"   {rowtag(r)} [{r.get('quote_symbol','SOL')}] {(r.get('sig_recv_eat') or '')[:19]} {r.get('track',''):15s} {str(r.get('symbol',''))[:10]:10s} lat {r.get('detect_latency_s_vs_blocktime')}s "
                  f"feed lag {r.get('feed_lag_med10s_at_signal', r.get('feed_lag_minute_med_est'))}s | {r.get('exit_reason')} {r.get('hold_s_wall')}s "
                  f"net $1 {r.get('net_per1')} $3 {r.get('net_per3')} $5 {r.get('net_per5')}")
    tracks = sorted({r.get("track") for r in rows_all})
    for tk in tracks:
        rs = [r for r in rows if r.get("track") == tk and r.get("net_per1") is not None]
        if not rs:
            print(f"\n  [{tk}] no on-time trades yet")
            continue
        n = len(rs)
        w = sum(1 for r in rs if r["net_per1"] > 0)
        ex = Counter(r.get("exit_reason") for r in rs)
        days = Counter((r.get("sig_recv_eat") or "")[:10] for r in rs)
        print(f"\n  [{tk}] trades {n} ({', '.join(f'{d}: {k}' for d, k in sorted(days.items()))}) | win {w}/{n} ({100*w/n:.0f}%)")
        for stake in (1, 3, 5):
            v = [r[f"net_per{stake}"] for r in rs]
            print(f"    net per ${stake}: total {sum(v):+.3f}  mean {sum(v)/n:+.4f}  ({100*sum(v)/n/stake:+.2f}% of stake)  worst {min(v):+.3f}")
        print(f"    rule-model net per $1 (scalp_setups +1s block-time fill): total {sum(r.get('rule_net_per1') or 0 for r in rs):+.3f}")
        print(f"    exits: {dict(ex.most_common())} | hold median {fmt(q([r.get('hold_s_wall') for r in rs],.5),1)}s "
              f"p90 {fmt(q([r.get('hold_s_wall') for r in rs],.9),1)}s")
        for dl in ("d400", "d1000", "d2000"):
            es = [r.get(f"entry_{dl}_slip_pct") for r in rs]
            xs = [r.get(f"exit_{dl}_slip_pct") for r in rs]
            band = sum(1 for r in rs if r.get(f"entry_{dl}_in_band"))
            print(f"    +{int(dl[1:])}ms: entry slip median {fmt(q(es,.5))}% p90 {fmt(q(es,.9))}% (fill still in $5k-10k {band}/{n}) | "
                  f"exit slip median {fmt(q(xs,.5))}% p10 {fmt(q(xs,.1))}% | net$1 total {sum(r.get(f'net_{dl}_per1') or 0 for r in rs):+.3f}")
        fl = [r for r in rs if r.get("rug_flag_any")]
        if not tk.endswith("+rug"):
            print(f"    rug-flagged at entry: {len(fl)}/{n} (creator>8% {sum(r.get('flag_creator_gt8',0) for r in rs)}, "
                  f"bundle>25% {sum(r.get('flag_bundle_gt25',0) for r in rs)}, wash {sum(r.get('flag_wash_uniq60_lt35',0) for r in rs)}); "
                  f"flagged net$1 {sum(r['net_per1'] for r in fl):+.3f} vs unflagged {sum(r['net_per1'] for r in rs if not r.get('rug_flag_any')):+.3f}")
        lat = [r.get("detect_latency_s_vs_blocktime") for r in rs]
        dd = [r.get("decide_delay_ms") for r in rs]
        print(f"    detection latency median {fmt(q(lat,.5),2)}s p90 {fmt(q(lat,.9),2)}s | decision delay median {fmt(q(dd,.5),0)}ms")
    print("\n  last shadow trades (all; A = late-adopted coin, L = late):")
    for r in rows_all[-8:]:
        print(f"   {rowtag(r)} {(r.get('sig_recv_eat') or '')[:19]} {r.get('track',''):15s} {str(r.get('symbol',''))[:10]:10s} t+{r.get('sig_t_plus')}s "
              f"mcap ${r.get('sig_mcap_usd') or 0:,.0f} rug={r.get('rug_flag_any')} slip1s {r.get('entry_d1000_slip_pct')}% "
              f"{r.get('exit_reason')} {r.get('hold_s_wall')}s net $1 {r.get('net_per1')} $3 {r.get('net_per3')} $5 {r.get('net_per5')}")


def quote_section():
    """Coins per quote pairing (SOL vs USDC/USDT/SPCX/...), late adoption and backfill bookkeeping."""
    coins = load("coins.jsonl")
    sigs = load("signals.jsonl")
    sh = load("shadow_trades.jsonl")
    decs = load("decisions.jsonl")
    skipped = load("skipped_coins.jsonl")
    adopt = load("adoptions.jsonl")
    bfs = load("backfills.jsonl")
    print("\n== QUOTE PAIRINGS (coins finalized since the quote-aware trader started) ==")
    by = defaultdict(Counter)
    for c in coins:
        k = c.get("quote_symbol") or "SOL"
        by[k]["coins"] += 1
        by[k]["adopted"] += int(c.get("late_adopted") or 0)
        by[k]["decided"] += int(c.get("decided") or 0)
        by[k]["skipped_no_price"] += int(bool(c.get("coin_skip_reason")))
    for d in decs:
        if "quote_symbol" in d:
            by[d["quote_symbol"]]["dip_decisions"] += 1
    for s_ in sigs:
        if "quote_symbol" in s_:
            by[s_["quote_symbol"]]["v1.1_positions"] += 1
    for r in sh:
        if "quote_symbol" in r:
            by[r["quote_symbol"]]["shadow_trades"] += 1
    cols = ["coins", "adopted", "skipped_no_price", "decided", "dip_decisions", "v1.1_positions", "shadow_trades"]
    print(f"  {'quote':10s} " + " ".join(f"{c_:>16s}" for c_ in cols))
    for k, c in sorted(by.items(), key=lambda kv: -kv[1]["coins"]):
        print(f"  {k:10s} " + " ".join(f"{c[c_]:>16d}" for c_ in cols))
    if skipped:
        print("  coins skipped for no reliable quote price: " + ", ".join(f"{k} x{n}" for k, n in Counter(
            f"{r.get('quote_symbol')}:{(r.get('reason') or '').split(':')[-1]}" for r in skipped).most_common(8)))
    print(f"\n-- late adoption: {len(adopt)} coins adopted (create event missed) | backfills: "
          + ", ".join(f"{k} {n}" for k, n in Counter(b.get("reason", "?").split("_")[0] for b in bfs).most_common())
          + f" | backfill errors: {Counter(b.get('error') for b in bfs if b.get('error')).most_common(3)}")
    added = sum(int(b.get("added") or 0) for b in bfs)
    print(f"   history trades added by backfill: {added}; truncated (>=1000 txs) calls: {sum(int(b.get('truncated') or 0) for b in bfs)}")


if __name__ == "__main__":
    main()
    shadow_section()
    quote_section()
