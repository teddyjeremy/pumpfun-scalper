"""Predeclared, research-only rules for deciding which coins get full tape retention.

These predicates never authorize a paper or live entry. They only select coins
whose ten-minute tape is useful for evaluating independent entry strategies.
"""
from __future__ import annotations


def candidate_capture_reasons(features: dict) -> list[str]:
    """Return every candidate family matched by a baseline K/B signal."""
    reasons = []
    dip = features.get("dip")
    high_age = features.get("secs_high")
    if dip is not None and high_age is not None:
        if 0.05 <= float(dip) <= 0.20 and 3 <= int(high_age) <= 120:
            reasons.append("pullback_5_20")

    if (
        bool(features.get("steady"))
        and int(features.get("nw30") or 0) >= 15
        and float(features.get("uniq60") or 0) >= 0.45
        and float(features.get("b10") or 0) >= float(features.get("s10") or 0)
    ):
        reasons.append("participation")

    ret5, ret10 = features.get("ret5"), features.get("ret10")
    ret20, ret30 = features.get("ret20"), features.get("ret30")
    if None not in (ret5, ret10, ret20, ret30):
        if (
            float(ret5) > 0
            and float(ret10) > 0
            and float(ret20) >= -0.10
            and float(ret30) >= -0.15
            and float(features.get("uniq60") or 0) >= 0.40
            and float(features.get("b10") or 0) >= float(features.get("s10") or 0)
        ):
            reasons.append("multi_window_recovery")
    return reasons
