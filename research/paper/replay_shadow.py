"""Offline check: stream saved live tapes through the shadow code; compare to scalp_setups.first_signal on the full tape."""
import glob, gzip, json, sys, types
sys.path.insert(0, '/workspace/pumpfun/paper')
import paper_trader as P
import scalp_shadow as SS
S = SS.S
clock = {"t": 0.0}
SS.time = types.SimpleNamespace(time=lambda: clock["t"], perf_counter=__import__("time").perf_counter)
T = P.Trader(); P.LOGS = P.Path('/tmp/paper_replay'); P.LOGS.mkdir(exist_ok=True)
sh = SS.ScalpShadow(T, lambda m: None, P.eat)
n_sig = 0; mism = 0
for fpath in sorted(glob.glob('logs/tapes/*.gz')):
    b = json.load(gzip.open(fpath, 'rt'))
    c = P.Coin(b['mint'], b['symbol'], b['symbol'], b.get('curve'), b['creator'], b['t0'], b['t0'], 0)
    T.coins[c.mint] = c
    for k, x in enumerate(b['trades']):
        x = dict(x); x['seq'] = k + 1; x['block_time'] = k + 1
        x['sol_usd'] = (x['spot_usd'] / ((x['vs'] / 1e9) / (x['vt'] / 1e6))) if x.get('vs') else 0
        c.trades.append(x); c.cum_vol += x['volume_usd'] or 0; c.wallets.add(x['trader'])
        clock["t"] = x['recv']
        SS.ScalpShadow.mark(c, x)
        if c.scalp and c.scalp.get('dirty'):
            c.scalp['last_eval'] = 0
            sh.tick()
    clock["t"] = c.trades[-1]['recv'] + 1000
    f, trs = SS.tape_of(c)
    for name, tk in SS.TRACKS.items():
        p = dict(tk['p'], rug=tk['rug'])
        off = S.first_signal(f, tk['fam'], p)
        live = c.scalp['pos'][name]['sig_idx'] if c.scalp and name in c.scalp['pos'] else None
        if off != live:
            mism += 1; print('MISMATCH', b['symbol'], name, off, live)
    rows = sh.finalize(c) if c.scalp else []
    for r in rows:
        n_sig += 1
        print(f"{r['track']:16s} {r['symbol'][:10]:10s} t+{r['sig_t_plus']} mcap {r['sig_mcap_usd']:.0f} rug={r['rug_flag_any']} "
              f"slip .4/1/2 {r.get('entry_d400_slip_pct')}/{r.get('entry_d1000_slip_pct')}/{r.get('entry_d2000_slip_pct')} "
              f"exit {r.get('exit_reason')} hold {r.get('hold_s_wall')} rule$ {r.get('rule_net_per1')} net$1 {r.get('net_per1')} $3 {r.get('net_per3')} $5 {r.get('net_per5')} xslip1 {r.get('exit_d1000_slip_pct')} {r.get('error','')}")
    del T.coins[c.mint]
print('signals', n_sig, 'mismatches', mism)
