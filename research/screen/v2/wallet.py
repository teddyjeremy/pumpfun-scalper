exec(open('/workspace/pumpfun/screen/v2/prereg.py').read().split("import statistics as st\nres={}")[0])
import random, statistics as st, json
RULE = RULES['P2 steady+fresh']; EXK = 'X7_tp15_h90_inv'
def seqdd(xs):
    c=p=dd=0
    for x in xs: c+=x; p=max(p,c); dd=min(dd,c-p)
    return dd
table=[]; pool=[]
for d in BT+[LIVE]:
    Tt=sorted(T(RULE,EXK,d), key=lambda x:x[0]['bt'])
    n=[e[0] for _,e in Tt]; pool+=n
    est = sum(r['w'] for r,_ in Tt) if d!=LIVE else None
    table.append(dict(day=d, sampled=len(n), est_per_day=round(est) if est else None, win=round(sum(x>0 for x in n)/len(n),2),
        avg=round(st.mean(n),3), med_hold=sorted(e[1] for _,e in Tt)[len(Tt)//2], worst=round(min(n),3), best=round(max(n),3),
        day_total_capped50=round(st.mean(n)*min(50, est or len(n)),2), dd_sampled_seq=round(seqdd(n),3),
        exits=dict(__import__('collections').Counter(e[2] for _,e in Tt))))
for t in table: print(t)
print('pool n',len(pool),'avg',round(st.mean(pool),4),'gross no-gas',round(st.mean(pool)+0.0285,4))
GAS=0.0285
def sim(pool, shift=0.0, days=30, rules=True, seed=0, nper=(10,50)):
    rng=random.Random(seed); w=2.0; peak=2.0; mdd=0; worst_day=0; ruined=False; step_base=2.0; stake=1.0
    for d in range(days):
        n=rng.randint(*nper); start=w; streak=0; skip=0
        for k in range(n):
            if w < 1.0+0.2: ruined = True; break          # can't place a $1 trade safely
            if rules:
                if w <= start*0.75: break                   # daily loss cap -25%
                if skip>0: skip-=1; continue
            # sizing ladder: +20% stake each time wallet grows 20% over the last step; never > 35% of wallet (min $1)
            while w >= step_base*1.2: step_base*=1.2; stake*=1.2
            while w < step_base/1.2 and stake>1.0: step_base/=1.2; stake=max(1.0,stake/1.2)
            s = min(stake, max(1.0, 0.35*w)) if rules else stake
            r = rng.choice(pool)+shift
            pnl = s*(r+GAS) - GAS
            w += pnl; peak=max(peak,w); mdd=min(mdd,(w-peak)/peak)
            if pnl<0: streak+=1
            else: streak=0
            if rules and streak>=3: skip=5; streak=0          # pause: skip next 5 signals after 3 losses in a row
        worst_day=min(worst_day,w-start)
        if ruined: break
    return dict(w=w, ruined=ruined, mdd=mdd, worst_day=worst_day)
out={}
for lab,shift in (('near-miss as measured',0.0),('if edge were +$0.05/trade',0.05-st.mean(pool)),('if edge were +$0.10/trade',0.10-st.mean(pool))):
    for rules in (False,True):
        R=[sim(pool,shift,rules=rules,seed=s) for s in range(2000)]
        ws=sorted(x['w'] for x in R)
        o=dict(ruin=round(sum(x['ruined'] for x in R)/len(R),3), med_wallet_30d=round(ws[len(ws)//2],2), p10=round(ws[len(ws)//10],2), p90=round(ws[9*len(ws)//10],2),
               med_maxdd=round(sorted(x['mdd'] for x in R)[len(R)//2],2), med_worst_day=round(sorted(x['worst_day'] for x in R)[len(R)//2],2))
        out[f"{lab} | bankroll rules={'on' if rules else 'off'}"]=o; print(lab,rules,o)
json.dump(dict(table=table,sim=out,pool_avg=st.mean(pool)),open('wallet_results.json','w'),indent=1,default=str)
