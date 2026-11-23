"""Engine replay (trade-by-trade, live code path) must reproduce the research pipeline's Lane B trades exactly."""
import bisect

import pytest

import scalp_setups as S
from conftest import fixture_tapes, load
from pumpbot.config import Config
from pumpbot.replay import run_coin

TAPES = fixture_tapes()
X7 = dict(tp=1.5, hold=90, fade=False, inval_low=True, inval_sec=60, inval_flow=True)


def research_lane_b(b):
    """Same logic as screen/v2/extract.py + search.G + prereg 'P2 steady+fresh' + T() with re-entry."""
    f = S.build_features(b["trades"]); f.creator = b.get("creator")
    if f.n < 20:
        return []
    out = []; nxt = 0; k = 0
    for i in range(f.n):
        t = f.t[i]
        if not (30 < t <= 300):
            continue
        if i + 1 < f.n and f.bt[i + 1] == f.bt[i]:
            continue
        mc = f.spot[i] * 1e9
        if not (5000 <= mc <= 10000) or f.bt[i] < nxt:
            continue
        H = f.high[i]; dip = 1 - f.spot[i] / H if H else 0
        rf = S.rug_feats(f, i)
        th = f.t_high[i]
        dev = any(f.trader[q] == f.creator and not f.side_buy[q] for q in range(bisect.bisect_left(f.bt, th, 0, i + 1), i + 1)) if f.creator else False
        G = (i + 1) >= 300 and S.not_crash(f, i) and not dev and (f.top_seller_share[i] <= 0.5 or dip < 0.1) and rf["creator_hold"] <= 0.08
        P2 = S.steady_curve(f, i) and S.win(f, i, 30)["new_buyers"] >= 15 and rf["uniq60"] >= 0.5
        if not (G and P2):
            continue
        lo30 = bisect.bisect_left(f.bt, f.bt[i] - 30, 0, i + 1)
        swing = min(f.spot[lo30:i + 1])
        ent_px, ei, ebt = S.fill_px(f, f.bt[i], i)
        x = dict(X7); x["inval_low"] = swing
        e = S.simulate_exit(f, ei, ebt, ent_px, S.win(f, i, 15)["vol"], x)
        out.append((f.bt[i], round(e["net"], 9), e["hold_s"]))
        k += 1; nxt = ebt + e["hold_s"] + 1
        if k >= 2:
            break
    return out


@pytest.mark.skipif(not TAPES, reason="no cached tapes (set TAPES_DIR or add fixtures/)")
def test_engine_equals_research():
    cfg = Config()
    n_trades = 0
    for p in TAPES:
        b = load(p)
        ref = research_lane_b(b)
        mine = [(r["ent_bt"], r["net"], r["hold_s"]) for r in run_coin(b, cfg, ["B"], rug_bundle=False)]
        assert len(mine) == len(ref), p
        for (ebt, net, h), (sbt, rnet, rh) in zip(mine, ref):
            assert ebt in (sbt + 1, sbt + 2, sbt + 3) or ebt >= sbt
            assert net == pytest.approx(rnet, abs=1e-6), p
            assert h == rh, p
        n_trades += len(ref)
    print("trades compared:", n_trades)
