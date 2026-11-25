from pumpbot.config import Config
from pumpbot.signals import decide, guards, lane_a, lane_k

BASE = dict(t=100, mc=7000, cn=400, notcrash=True, dev_drop=False, top1=0.2, dip=0.05, creator_hold=0.01, bundle_hold=0.05,
            steady=True, nw30=20, uniq60=0.7, high_t=20, n15=10, v15=500, cv=20_000, cw=200, secs_high=10,
            n10=6, b10=120, s10=80)


def f(**kw):
    d = dict(BASE); d.update(kw); return d


def test_lane_b_fires_on_base():
    assert decide(f(), Config(), ["B"]) == ("B", "signal")


def test_lane_k_fires_on_moderate_buy_pressure():
    assert lane_k(f(steady=False, nw30=2, b10=100, s10=90), Config())


def test_lane_k_rejects_low_activity_or_sell_pressure():
    c = Config()
    assert not lane_k(f(n10=3), c)
    assert not lane_k(f(b10=80, s10=100), c)
    assert not lane_k(f(b10=10, s10=1), c)  # configurable minimum buy volume is available


def test_lane_k_respects_shared_hard_guards():
    c = Config()
    assert decide(f(notcrash=False), c, ["K"])[0] is None
    assert decide(f(creator_hold=0.09), c, ["K"])[0] is None
    assert decide(f(cn=299), c, ["K"])[0] is None


def test_lane_priority_is_explicit():
    assert decide(f(), Config(), ["K", "B"])[0] == "K"
    assert decide(f(), Config(), ["B", "K"])[0] == "B"


def test_guards():
    c = Config()
    assert guards(f(t=30), c) == "window_or_band"
    assert guards(f(t=301), c) == "window_or_band"
    assert guards(f(mc=10_500), c) == "window_or_band"
    assert guards(f(cn=299), c) == "cum_trades_lt_300"
    assert guards(f(notcrash=False), c) == "crash"
    assert guards(f(dev_drop=True), c) == "v1.3_dev_sold_since_high"
    assert guards(f(top1=0.6, dip=0.2), c) == "v1.1_top_seller_gt_50pct"
    assert guards(f(top1=0.6, dip=0.05), c) is None
    assert guards(f(creator_hold=0.09), c) == "rug_creator_hold"
    assert guards(f(bundle_hold=0.3), c) == "rug_create_slot_wallets"
    assert guards(f(bundle_hold=0.3), c, rug_bundle=False) is None


def test_lane_b_filters():
    c = Config()
    assert decide(f(steady=False), c, ["B"])[0] is None
    assert decide(f(nw30=14), c, ["B"])[0] is None
    assert decide(f(uniq60=0.4), c, ["B"])[0] is None


def test_lane_a_v1_rules():
    c = Config()
    assert lane_a(f(dip=0.35), c)
    assert not lane_a(f(dip=0.25), c)
    assert not lane_a(f(dip=0.35, secs_high=29), c)
    assert not lane_a(f(dip=0.35, top1=0.51), c)
    assert not lane_a(f(dip=0.35, high_t=5), c)
    assert not lane_a(f(dip=0.35, n15=7), c)
