exec(open('/workspace/pumpfun/screen/v2/search.py').read().split("if __name__")[0])
def T(pred, ex, d, reentry=True):
    out=[]
    for (dd,m),rows in bycoin.items():
        if dd!=d: continue
        nxt=0;k=0
        for r in rows:
            if r['bt']<nxt: continue
            if pred(r):
                e=r['ex'][ex]; out.append((r,e)); k+=1; nxt=r['ebt']+e[1]+1
                if not reentry or k>=2: break
    return out
RULES = {
 'BASE band+guard': lambda r: True,
 'IC_reclaim': lambda r: r['depth']>=0.25 and r['low_age']>=10 and r['recl']>=0.5 and r['b10']>r['s10'] and (r['nb10']+r['ns10'])>=4 and r['steady'],
 'B_breakout': lambda r: r['brk'] and r['phage']>=10 and r['bs10']>=1.5 and r['b10']>=150,
 'P1 inflow+fresh': lambda r: r['bs30']>1.2 and r['nw30']>=15 and r['big30']<=0.35 and r['ret60']>0 and r['swing_dd']<=0.25,
 'P2 steady+fresh': lambda r: r['steady'] and r['nw30']>=15 and r['rf_uniq60']>=0.5,
 'P3 calm-dip': lambda r: 0.15<=r['dip']<=0.4 and r['sacc']<=1 and r['bs10']>=1 and r['top1']<=0.3,
}
import statistics as st
res={}
for name,p in RULES.items():
    for ex in ['X2_tp15_h60','X1_tp15_h120','X7_tp15_h90_inv','X4_half13_trail','X3_tp13_h120']:
        row=[]
        for d in BT+[LIVE]:
            s=stats(T(p,ex,d)); row.append(f"{s.get('n',0):3d} {s.get('avg',0):+.3f}")
        print(f"{name:18s} {ex:16s}", ' | '.join(row))
