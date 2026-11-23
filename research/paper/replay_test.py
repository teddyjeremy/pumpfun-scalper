"""Offline replay of 30-min analyze tapes through the live signal + simulation code (sanity test)."""
import csv, sys, json, random
sys.path.insert(0, '/workspace/pumpfun/paper')
import paper_trader as P
K = 30_000_000_000 * 1_073_000_191_000_000
pre = {r['mint']: r for r in csv.DictReader(open('/workspace/pumpfun/screen/prescreen_2026-11-22.csv')) if r['pass_v0'] == '1'}
T = P.Trader(); P.LOGS = P.Path('/tmp/paper_replay'); P.LOGS.mkdir(exist_ok=True)
random.seed(1)
for mint in list(pre)[: int(sys.argv[1]) if len(sys.argv) > 1 else 21]:
    p = P.Path(f'/workspace/pumpfun/coins/{mint}/trades.csv')
    if not p.exists():
        continue
    rows = [r for r in csv.DictReader(p.open()) if r['venue'] == 'curve' and r.get('price_sol')]
    rows.sort(key=lambda r: (int(r['block_time']), int(r['slot'])))
    if not rows: continue
    t0 = int(rows[0]['block_time']) - int(rows[0]['t_plus_sec'])
    c = P.Coin(mint, pre[mint]['name'], pre[mint]['symbol'], 'curve', pre[mint]['creator'], t0, t0 + 1.5, 0)
    c.create_sig = rows[0]['signature']
    T.sol.px = float(rows[0]['price_usd']) / float(rows[0]['price_sol'])
    recv = t0 + 1.5
    for r in rows:
        bt = int(r['block_time']); recv = max(recv + 0.001, bt + 1.2 + random.random() * 0.8)
        pc = float(r['price_curve_sol'] or r['price_sol']); vs = (K * pc * 1e3) ** 0.5; vt = K / vs
        sol_usd = float(r['price_usd']) / float(r['price_sol'])
        T.seq += 1
        tr = {"signature": r['signature'], "seq": T.seq, "block_time": T.seq, "bt": bt, "slot": int(r['slot']),
              "t_plus_sec": int(r['t_plus_sec']), "side": r['side'], "trader": r['trader'],
              "sol_ui": float(r['sol_ui'] or 0), "token_ui": float(r['token_ui'] or 0), "price_sol": float(r['price_sol']),
              "price_usd": float(r['price_usd']), "spot_usd": (vs/1e9)/(vt/1e6)*sol_usd,
              "mcap_usd": (vs/1e9)/(vt/1e6)*1e9*sol_usd, "volume_usd": float(r['volume_usd'] or 0), "sol_usd": sol_usd,
              "vs": vs, "vt": vt, "fee_bps_total": 125, "recv": recv, "is_create_tx": int(r['signature'] == c.create_sig)}
        if tr['t_plus_sec'] > 640: break
        c.trades.append(tr); c.cum_vol += tr['volume_usd']; c.wallets.add(r['trader'])
        if c.signal is None and not c.decided: T.check_signal(c, tr)
    pr = pre[mint]
    if c.decision is None:
        print(f"{pr['symbol']:8s} NO DECISION live; prescreen entry t+{pr['entry_t_plus']}"); continue
    d = c.decision
    line = f"{pr['symbol']:8s} prescreen t+{pr['entry_t_plus']:>3} T{pr['entry_threshold']} mcap {float(pr['entry_mcap']):7.0f} top1 {pr['entry_top1_seller_share']:>6} | live t+{d['entry_t_plus']:>3} T{d['entry_threshold']} mcap {d['signal_mcap_usd']:7.0f} top1 {d['entry_top1_seller_share']} {d['status']} {d['skip_reasons']}"
    if c.signal:
        s = T.simulate(c)
        e = s['exits']
        line += f" | slip1s {s['fill_d1000_slip_vs_signal_spot_pct']}% tp17 {e['tp17']['exit']} net1s {e['tp17'].get('exit_d1000_net')} trig {e['tp17'].get('net_at_trigger') and round(e['tp17']['net_at_trigger'],3)} first {s['first_dynamic_exit']}"
    print(line)
