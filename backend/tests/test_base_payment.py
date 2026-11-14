# ruff: noqa: S101, T201  -- a test file asserts, and prints when run as a script
"""Base Payment: allowance counting, concentration, Osmosis allocation, advisor levers."""

from datetime import UTC, date, datetime, timedelta

import numpy as np

from alpha_harness.tools import base_payment as bp
from alpha_harness.tools import submit_advisor as sa

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)  # a Saturday, 14:00 in New York


def row(i: int, *, days_ago: float = 0.0, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "alpha_id": f"A{i:03d}",
        "sim_type": "REGULAR",
        "region": "USA",
        "delay": 1,
        "universe": "TOP3000",
        "neutralization": "INDUSTRY",
        "sharpe": 2.0,
        "turnover": 0.15,
        "margin": 0.0008,
        "status": "ACTIVE",
        "date_submitted": NOW - timedelta(days=days_ago),
        "pyramids": ["USA/D1/ANALYST"],
        "prod_correlation": 0.3,
        "self_correlation": 0.3,
    }
    return base | over


def test_quantity_counts_today_streak_d0_and_super() -> None:
    rows = [
        row(1),
        row(2),
        row(3, delay=0),
        row(4, sim_type="SUPER"),
        row(5, days_ago=1),
        row(6, days_ago=2),
        row(7, days_ago=4),
    ]
    q = bp.quantity(rows, NOW)
    assert q["submittedToday"] == 3
    assert q["alphaRemaining"] == 1
    assert q["superToday"] == 1
    assert q["d0Month"] == 1
    assert q["d0Remaining"] == 4
    assert q["streak"] == 3  # today, yesterday, two days ago; the gap ends it


def test_streak_survives_an_empty_today() -> None:
    q = bp.quantity([row(1, days_ago=1), row(2, days_ago=2)], NOW)
    assert q["submittedToday"] == 0
    assert q["streak"] == 2


def test_diversity_flags_a_majority() -> None:
    rows = [row(i, days_ago=i) for i in range(6)] + [row(9, days_ago=3, region="EUR")]
    d = bp.diversity(rows, NOW)
    assert any("region USA" in f for f in d["flags"])
    assert d["considered"] == 7


def test_growth_trend_follows_sharpe() -> None:
    new = [row(i, days_ago=5 + i, sharpe=2.4) for i in range(3)]
    old = [row(10 + i, days_ago=40 + i, sharpe=1.8) for i in range(3)]
    g = bp.growth(new + old, NOW)
    assert g[0]["trend"] == "improving"
    worse = bp.growth([r | {"sharpe": 1.5} for r in new] + old, NOW)
    assert worse[0]["trend"] == "declining"


def test_osmosis_needs_ten_and_sums_to_100k() -> None:
    few = bp.osmosis([row(i) for i in range(9)], {}, NOW)
    assert not few["scopes"][0]["ready"] and few["scopes"][0]["needed"] == 1
    assert not few["valid"]

    rows = [row(i, sharpe=1.6 + i * 0.05) for i in range(14)]
    plan = bp.osmosis(rows, {}, NOW)
    scope = plan["scopes"][0]
    assert scope["ready"]
    points = [a["points"] for a in scope["alphas"]]
    assert sum(points) == 100_000
    assert all(p % 100 == 0 for p in points)
    assert max(points) <= 15_000 + 100
    # Best Sharpe gets the most.
    assert scope["alphas"][0]["alphaId"] == "A013"


def test_osmosis_drops_a_copy_but_keeps_ten() -> None:
    rng = np.random.default_rng(1)
    start = date(2022, 1, 3)
    base = rng.normal(size=300)
    days = {}
    for i in range(12):
        values = base if i in (0, 1) else rng.normal(size=300)
        days[f"A{i:03d}"] = {start + timedelta(days=d): float(v) for d, v in enumerate(values)}
    rows = [row(i, sharpe=3.0 - i * 0.1) for i in range(12)]
    scope = bp.osmosis(rows, days, NOW)["scopes"][0]
    ids = {a["alphaId"] for a in scope["alphas"]}
    assert "A000" in ids and "A001" not in ids
    assert any(d["alphaId"] == "A001" for d in scope["dropped"])
    assert len(ids) == 11


def test_osmosis_csv() -> None:
    plan = bp.osmosis([row(i) for i in range(10)], {}, NOW)
    lines = bp.osmosis_csv(plan).splitlines()
    assert lines[0] == "scope,alpha_id,points" and len(lines) == 11


def test_value_adjustment_levers() -> None:
    alpha = {"settings": {"region": "USA", "delay": 1}, "margin": 0.0002, "turnover": 0.4}
    delta, good, bad = bp.value_adjustment(alpha, {"region": {"USA": 0.8}}, 1.5, 0.5)
    assert any("4 bps" in c for c in bad)
    assert any("sub-universe" in c for c in bad)
    assert any("share region USA" in c for c in bad)
    assert any("multiplier" in s for s in good)
    # +4 theme, -10 margin, -8 retention, -5 concentration
    assert delta == 4.0 - 10.0 - 8.0 - 5.0

    clean = {"settings": {"region": "EUR", "delay": 1}, "margin": 0.001, "turnover": 0.1}
    delta, good, bad = bp.value_adjustment(clean, {"region": {"USA": 0.9, "EUR": 0.05}}, None, 0.9)
    assert delta == bp.BONUS_DIVERSE and not bad and good


def test_advisor_applies_adjustments_but_not_to_checks() -> None:
    def alpha(alpha_id: str, **over: object) -> dict[str, object]:
        return {
            "alphaId": alpha_id,
            "settings": {"delay": 1},
            "sharpe": 2.2,
            "fitness": 1.5,
            "turnover": 0.2,
            "afterCostSharpe": 2.0,
            "testSharpe": 2.0,
            "prodCorrelation": 0.3,
            "submitted": False,
            "pending": False,
            "quick": False,
            "errored": False,
        } | over

    rows = [alpha("A"), alpha("B"), alpha("LOW", sharpe=1.2)]
    plain = {a.alpha_id: a for a in sa.advise(rows, {})}
    boosted = {
        a.alpha_id: a
        for a in sa.advise(
            rows, {}, adjustments={"B": (8.0, ["theme"], []), "LOW": (8.0, ["theme"], [])}
        )
    }
    assert boosted["B"].score == min(100.0, plain["B"].score + 8.0)
    assert "theme" in boosted["B"].strengths
    assert boosted["B"].rank < boosted["A"].rank
    # A bonus can never rescue an Alpha that missed a bar.
    assert boosted["LOW"].status == "held_back"


def test_daily_plan_spreads_scopes_and_respects_d0() -> None:
    qty = {"alphaRemaining": 3, "d0Remaining": 0, "superToday": 0, "superCap": 1}
    rec = [
        {
            "alphaId": "U1",
            "score": 80,
            "rank": 1,
            "settings": {"region": "USA", "delay": 1},
            "multiplier": None,
        },
        {
            "alphaId": "U2",
            "score": 79,
            "rank": 2,
            "settings": {"region": "USA", "delay": 1},
            "multiplier": None,
        },
        {
            "alphaId": "E1",
            "score": 76,
            "rank": 3,
            "settings": {"region": "EUR", "delay": 1},
            "multiplier": 1.4,
        },
        {
            "alphaId": "D0",
            "score": 90,
            "rank": 0,
            "settings": {"region": "USA", "delay": 0},
            "multiplier": None,
        },
    ]
    plan = bp.daily_plan(rec, qty)
    ids = [p["alphaId"] for p in plan["picks"]]
    assert "D0" not in ids  # the monthly D0 allowance is spent
    assert ids[:2] == ["U1", "E1"]  # a second USA pick gives way to a new market
    assert plan["short"] == 0


def test_daily_plan_reports_a_shortfall() -> None:
    plan = bp.daily_plan(
        [], {"alphaRemaining": 4, "d0Remaining": 5, "superToday": 1, "superCap": 1}
    )
    assert plan["short"] == 4 and plan["tips"] and not plan["superAlphaOpen"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
