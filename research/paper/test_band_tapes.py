"""Offline test of the band-tape recorder (no network). Synthetic SOL + quote-token coins."""
import gzip, json, sys, time, shutil
from pathlib import Path
sys.path.insert(0, '/workspace/pumpfun/paper')
import paper_trader as P
import band_tapes as BT
import quote_events as QE

out = Path('/tmp/bandtest'); shutil.rmtree(out, ignore_errors=True); out.mkdir()
P.LOGS = out  # append_jsonl etc.
T = P.Trader(); T.band.dir = out / 'band_tapes'; T.band.proc_start = 0
T.sol.px = 130.0
clock = [1_800_000_000.0]
BT.time.time = lambda: clock[0]
P.now = lambda: clock[0]
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
T.qp.get = lambda m: (1.0, 6) if m == USDC else None
T.qp.symbol = lambda m: 'SOL' if m in (None, QE.SOL) else 'USDC'

def mk(mint, t0, quote=None):
    c = P.Coin(mint, 'n', mint[:4], 'curve', 'creator', t0, t0 + 1, 1)
    T.coins[mint] = c
    return c

def ev(mint, ts, buy, vs_sol, quote=None):
    vt = 30 * 1.073e9 / vs_sol * 1e6   # constant product approx (token raw)
    e = {"mint": mint, "timestamp": ts, "is_buy": buy, "user": f"w{int(ts)%7}", "token_amount": 1e12,
         "sol_amount": int(0.5e9), "virtual_sol_reserves": int(vs_sol * 1e9), "virtual_token_reserves": int(vt),
         "fee_bps": 95, "creator_fee_bps": 30}
    if quote:
        e.update(quote_mint=quote, quote_amount=int(65e6), virtual_quote_reserves=int(vs_sol * 130 * 1e6), real_quote_reserves=0,
                 virtual_sol_reserves=0)
    else:
        e["quote_mint"] = QE.SOL
    return e

t0 = clock[0]
a = mk('AAAAsolBandCoin', t0)            # SOL coin: reaches band (~$5-10k) at t+60, dies later
b = mk('BBBBsolNeverBand', t0)           # never reaches band
q = mk('QQQQusdcBandCoin', t0)           # USDC-quoted coin reaching band
for s in range(0, 300, 5):
    clock[0] = t0 + s + 1
    vs = 30 + (s / 300) * 40   # a: 30 -> 70 SOL virtual => mcap ~ $3.6k -> $8.5k
    T.on_trade_ev = None
    T.feed_event(a, ev(a.mint, t0 + s, s % 3 != 0, vs), f"sa{s}", 100 + s, clock[0])
    T.feed_event(b, ev(b.mint, t0 + s, True, 30.5), f"sb{s}", 100 + s, clock[0])
    T.feed_event(q, ev(q.mint, t0 + s, True, vs, quote=USDC), f"sq{s}", 100 + s, clock[0] + (8 if s == 100 else 0))
print('state', {k[:4]: v for k, v in T.band.state.items()})
# finalize at t+308
clock[0] = t0 + 309
for c in (a, b, q):
    c.finalized = True
    T.band.on_finalize(c)
    del T.coins[c.mint]
print('tail', list(T.band.tail))
assert a.mint in T.band.tail and q.mint in T.band.tail and b.mint not in T.band.tail
# tail trades via on_trade path (raw decode bypassed: emulate the on_trade routing)
for s in range(310, 650, 10):
    clock[0] = t0 + s + 1
    for c, quote in ((a, None), (q, USDC)):
        e = ev(c.mint, t0 + s, False, 40, quote=quote)
        r = T.band.on_tail_trade(e, f"t{c.mint[:2]}{s}", 500 + s, clock[0]); assert r == (s <= 610), (s, r)
    T.band.add_gap(t0 + 400, t0 + 412) if s == 420 else None
    T.band.tick()
print('status', T.band.status())
for p in sorted((out / 'band_tapes').rglob('*.gz')):
    lines = gzip.open(p, 'rt').read().splitlines()
    meta = json.loads(lines[0])['_meta']; rows = [json.loads(x) for x in lines[1:]]
    print(p.name, 'n', len(rows), 'quote', meta['quote_symbol'], 'touch_t', meta['band_touch_t'], 'end', meta['end_reason'],
          'gap_s', meta['gap_overlap_s'], 'on_time', meta['on_time'], 'flags', meta['flags'], 'last_t', rows[-1]['t'],
          'mcap0', round(rows[0]['mcap']), 'quote_amt', rows[0]['quote'], 'n_late', meta['n_late'])
    assert rows[-1]['t'] >= 600
idx = list((out / 'band_tapes').rglob('index.jsonl'))
print('index lines', sum(len(i.read_text().splitlines()) for i in idx))
assert T.band.status()['saved'] == 2
print('OK')
