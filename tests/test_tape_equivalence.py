"""The live engine's streaming features must equal the research batch features (scalp_setups) on real tapes."""
import bisect
import math

import pytest

import scalp_setups as S
from conftest import fixture_tapes, load
from pumpbot.tape import Tape

TAPES = fixture_tapes()


@pytest.mark.skipif(not TAPES, reason="no cached tapes (set TAPES_DIR or add fixtures/)")
@pytest.mark.parametrize("path", TAPES[:40])
def test_features_match_research(path):
    b = load(path)
    f = S.build_features(b["trades"]); f.creator = b.get("creator")
    tp = Tape(b["mint"], b["t0"], b.get("creator"))
    for x in b["trades"]:
        tp.append(x)
    assert tp.n == f.n
    if f.n == 0:
        return
    checked = 0
    for i in range(0, f.n, max(1, f.n // 60)):
        g = tp.features(i)
        assert g["top1"] == pytest.approx(f.top_seller_share[i])
        assert g["steady"] == S.steady_curve(f, i)
        assert g["notcrash"] == S.not_crash(f, i)
        w30 = S.win(f, i, 30)
        assert g["nw30"] == w30["new_buyers"]
        rf = S.rug_feats(f, i)
        assert g["creator_hold"] == pytest.approx(rf["creator_hold"], abs=1e-12)
        assert g["bundle_hold"] == pytest.approx(rf["bundle_hold"], abs=1e-12)
        assert g["uniq60"] == pytest.approx(rf["uniq60"])
        lo30 = bisect.bisect_left(f.bt, f.bt[i] - 30, 0, i + 1)
        assert g["swing30"] == min(f.spot[lo30:i + 1])
        th = f.t_high[i]
        dev = any(f.trader[k] == f.creator and not f.side_buy[k] for k in range(bisect.bisect_left(f.bt, th, 0, i + 1), i + 1)) if f.creator else False
        assert g["dev_drop"] == dev
        checked += 1
    assert checked > 0
