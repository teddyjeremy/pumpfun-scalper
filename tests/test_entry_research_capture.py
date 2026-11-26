from pumpbot.entry_research import candidate_capture_reasons


def test_candidate_capture_is_research_only_and_returns_matching_families():
    f = {
        "dip": 0.12, "secs_high": 30,
        "steady": True, "nw30": 20, "uniq60": 0.55, "b10": 120, "s10": 100,
        "ret5": 0.02, "ret10": 0.03, "ret20": -0.04, "ret30": -0.08,
    }
    assert candidate_capture_reasons(f) == [
        "pullback_5_20", "participation", "multi_window_recovery"
    ]


def test_deep_dip_and_weak_flow_are_not_research_candidates():
    f = {
        "dip": 0.55, "secs_high": 45, "steady": False,
        "nw30": 8, "uniq60": 0.30, "b10": 30, "s10": 60,
        "ret5": 0.04, "ret10": 0.02, "ret20": -0.20, "ret30": -0.25,
    }
    assert candidate_capture_reasons(f) == []


def test_missing_multi_window_returns_do_not_pass_recovery():
    f = {
        "steady": True, "nw30": 20, "uniq60": 0.50, "b10": 90, "s10": 80,
        "ret5": 0.02, "ret10": 0.01, "ret20": None, "ret30": -0.02,
    }
    assert candidate_capture_reasons(f) == ["participation"]
