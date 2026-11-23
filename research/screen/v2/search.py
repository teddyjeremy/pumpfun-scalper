import pickle, statistics as st, collections, math, json, sys
X = pickle.load(open('/workspace/pumpfun/screen/v2/cands.pkl', 'rb'))
for r in X:
    r['bs10'] = r['b10'] / (r['s10'] + 1); r['bs30'] = r['b30'] / (r['s30'] + 1); r['bs60'] = r['b60'] / (r['s60'] + 1)
    r['sacc'] = r['ns10'] / (r['ns10p'] + 1)
EX = list(X[0]['ex'].keys())
BT = ['2026-11-20', '2026-11-21', '2026-11-22']; LIVE = '2026-11-23'
def G(r):  # Jeremy's standing rules: no crash, v1.1 whale, v1.3 dev-sold, rug creator>8%
    return r['cn'] >= 300 and r['notcrash'] and r['dev_drop'] == 0 and (r['top1'] <= 0.5 or r['dip'] < 0.1) and r['rf_creator_hold'] <= 0.08
XG = [r for r in X if G(r)]
FE = ['t','mc','dip','depth','recl','secs_high','low_age','ret10','ret30','ret60','swing_dd','bs10','bs30','bs60','sacc','nw10','nw30','n15','v15','big30','cv','cn','cw','top1','band_k','rf_bundle_hold','rf_uniq60','rf_top5_hold','rf_drain15']
bycoin = collections.defaultdict(list)
for r in XG: bycoin[(r['day'], r['mint'])].append(r)
for v in bycoin.values(): v.sort(key=lambda r: r['bt'])
def trades(conds, ex, days, reentry=True):
    out = []
    for (d, m), rows in bycoin.items():
        if d not in days: continue
        nxt = 0; k = 0
        for r in rows:
            if r['bt'] < nxt: continue
            if all((r[f] > th) if op == '>' else (r[f] <= th) for f, op, th in conds):
                e = r['ex'][ex]; out.append((r, e)); k += 1
                nxt = r['ebt'] + e[1] + 1
                if not reentry or k >= 2: break
    return out
def score(T, min_n):
    if len(T) < min_n: return -9
    n = [e[0] for _, e in T]
    return st.mean(n) - st.pstdev(n) / math.sqrt(len(n))
def search(train, min_n, maxc=3, beam=6):
    qs = {}
    rows = [r for r in XG if r['day'] in train]
    for f in FE:
        vs = sorted(r[f] for r in rows)
        qs[f] = sorted(set(vs[int(len(vs) * q / 10)] for q in range(1, 10)))
    best = []
    frontier = [((), ex) for ex in EX]
    scored = [(score(trades(c, ex, train), min_n), c, ex) for c, ex in frontier]
    scored.sort(key=lambda x: -x[0]); frontier = scored[:beam]; best = list(frontier)
    for depth in range(maxc):
        cand = []
        for s0, c, ex in frontier:
            used = {f for f, _, _ in c}
            for f in FE:
                if f in used: continue
                for th in qs[f]:
                    for op in ('>', '<='):
                        c2 = c + ((f, op, th),)
                        cand.append((score(trades(c2, ex, train), min_n), c2, ex))
        cand.sort(key=lambda x: -x[0])
        seen = set(); frontier = []
        for x in cand:
            key = (tuple(sorted(x[1])), x[2])
            if key in seen: continue
            seen.add(key); frontier.append(x)
            if len(frontier) >= beam: break
        best += frontier
    best.sort(key=lambda x: -x[0])
    return best[0]
def stats(T):
    if not T: return dict(n=0)
    n = [e[0] for _, e in T]
    return dict(n=len(n), wtd=round(sum(r['w'] for r, _ in T), 1), win=round(sum(x > 0 for x in n) / len(n), 2), avg=round(st.mean(n), 4), tot=round(sum(n), 3), worst=round(min(n), 3))
if __name__ == '__main__':
    res = []
    folds = [(['2026-11-21','2026-11-22'], '2026-11-20'), (['2026-11-20','2026-11-22'], '2026-11-21'), (['2026-11-20','2026-11-21'], '2026-11-22'), (['2026-11-20'], '2026-11-21'), (['2026-11-20','2026-11-21'], '2026-11-22'), (BT, LIVE)]
    for maxc in (1, 2, 3):
      for train, test in folds:
        mn = 10 * len(train)
        s, c, ex = search(train, mn, maxc=maxc)
        o = dict(maxc=maxc, train=train, test=test, conds=c, exit=ex, train_lcb=round(s, 4),
                 train_stats={d: stats(trades(c, ex, [d])) for d in train}, test_stats=stats(trades(c, ex, [test])),
                 live=stats(trades(c, ex, [LIVE])))
        print(json.dumps(o, default=str), flush=True); res.append(o)
    json.dump(res, open('/workspace/pumpfun/screen/v2/search_results.json', 'w'), default=str, indent=1)
