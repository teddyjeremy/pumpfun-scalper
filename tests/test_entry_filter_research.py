from scripts.entry_filter_research import filter_a, filter_b, summarize


def test_pullback_filter_uses_dip_and_recent_high_age():
    assert filter_a({"dip": 0.12, "secs_high": 30})
    assert not filter_a({"dip": 0.02, "secs_high": 30})
    assert not filter_a({"dip": 0.12, "secs_high": 180})


def test_participation_filter_requires_all_conditions():
    good = {"steady": True, "nw30": 20, "uniq60": 0.55, "b10": 120.0, "s10": 100.0}
    assert filter_b(good)
    assert not filter_b({**good, "steady": False})
    assert not filter_b({**good, "nw30": 10})
    assert not filter_b({**good, "uniq60": 0.40})
    assert not filter_b({**good, "b10": 90.0})


def test_summary_counts_wins_and_losses_separately():
    result = summarize([
        {"net_per_1usd": 0.2},
        {"net_per_1usd": -0.1},
        {"net_per_1usd": -0.05},
    ])
    assert result["wins"] == 1
    assert result["losses"] == 2
    assert result["losses_below_wins"] is False
