import pickle, statistics as st, collections, math
X = pickle.load(open('/workspace/pumpfun/screen/v2/cands.pkl', 'rb'))
for r in X:
    r['bs10'] = r['b10'] / (r['s10'] + 1); r['bs30'] = r['b30'] / (r['s30'] + 1); r['bs60'] = r['b60'] / (r['s60'] + 1)
    r['sacc'] = r['ns10'] / (r['ns10p'] + 1)
EX = list(X[0]['ex'].keys())
days = sorted(set(r['day'] for r in X))
def first_per_coin(rows):
    seen = {}; 
    for r in sorted(rows, key=lambda r: r['bt']):
        seen.setdefault((r['day'], r['mint']), r)
    return list(seen.values())
print("ALL candidates first-per-coin (band entry, no filter)")
for d in days:
    R = first_per_coin([r for r in X if r['day'] == d])
    print(d, len(R), {k: round(st.mean(r['ex'][k][0] for r in R), 3) for k in EX}, 'hit1.5', round(st.mean(r['ex']['X5_tp15_pure'][3] >= 0.5 for r in R), 2))
G = lambda r: r['notcrash'] and r['dev_drop'] == 0 and (r['top1'] <= 0.5 or r['dip'] < 0.1) and r['rf_creator_hold'] <= 0.08
print("guarded")
for d in days:
    R = first_per_coin([r for r in X if r['day'] == d and G(r)])
    print(d, len(R), {k: round(st.mean(r['ex'][k][0] for r in R), 3) for k in EX})
# univariate quintiles on pooled bt days for X1
feats = ['t','mc','dip','depth','recl','secs_high','low_age','ret10','ret30','ret60','swing_dd','bs10','bs30','bs60','sacc','nw10','nw30','n15','v15','big30','cv','cn','cw','top1','band_k','brk','steady','rf_creator_hold','rf_bundle_hold','rf_uniq60','rf_top5_hold','rf_drain15','rf_sell_accel']
for ex in ['X1_tp15_h120', 'X4_half13_trail']:
  print('==', ex)
  for fe in feats:
    out = []
    for d in days:
        R = [r for r in X if r['day'] == d and G(r)]
        vs = sorted(r[fe] for r in R); q = [vs[int(len(vs) * k / 4)] for k in (1, 2, 3)]
        b = collections.defaultdict(list)
        for r in R:
            b[sum(r[fe] > x for x in q)].append(r['ex'][ex][0])
        out.append(' '.join(f"{st.mean(b[k]):+.3f}" if b[k] else '  na  ' for k in range(4)))
    print(f"{fe:16s}", ' | '.join(out))
