# ruff: noqa: S101  -- a test file asserts
"""Uniqueness and base-payment levers from the 2026-11-19 account review.

Themes are read from BRAIN's own MATCHES_THEMES check, the account's Value Factor inputs
are reported, the Submit Advisor weighs production correlation more, and the Search and
Evolution labs lean toward fields fewer Alphas already use.
"""

import random
from datetime import UTC, datetime, timedelta

from alpha_harness.labs import ga, search
from alpha_harness.tools import base_payment as bp
from alpha_harness.tools import submit_advisor as sa

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)

THEME_PASS = {"name": "MATCHES_THEMES", "result": "PASS", "multiplier": 3.05, "themes": []}
THEME_WARN = {"name": "MATCHES_THEMES", "result": "WARNING", "themes": []}
PYRAMID = {"name": "MATCHES_PYRAMID", "result": "PASS", "multiplier": 1.3}


def test_theme_multiplier_reads_only_a_passed_theme_check() -> None:
    assert bp.theme_multiplier([THEME_PASS, THEME_WARN, PYRAMID]) == 3.05
    assert bp.theme_multiplier([THEME_WARN, PYRAMID]) is None
    assert bp.theme_multiplier([{"name": "MATCHES_THEMES", "result": "PENDING"}]) is None
    # A pass without a figure still counts as a match.
    assert bp.theme_multiplier([{"name": "MATCHES_THEMES", "result": "PASS"}]) == 1.0


def test_matched_theme_outweighs_a_pyramid_multiplier() -> None:
    alpha = {"settings": {"region": "GLB", "delay": 1}, "margin": 0.001, "turnover": 0.2}
    themed, good, _ = bp.value_adjustment(alpha, None, None, None, 3.05)
    pyramid, _, _ = bp.value_adjustment(alpha, None, 1.3, None)
    assert themed == bp.BONUS_MATCHED_THEME > pyramid
    assert any("theme" in s for s in good)


def _rec(alpha_id: str, score: float, region: str, theme: float | None = None) -> dict:
    return {
        "alphaId": alpha_id,
        "score": score,
        "rank": 1,
        "settings": {"region": region, "delay": 1},
        "multiplier": None,
        "theme": theme,
    }


def test_daily_plan_puts_one_theme_alpha_in_the_day() -> None:
    qty = {"alphaRemaining": 2, "d0Remaining": 5, "superToday": 0, "superCap": 1}
    rec = [_rec("A", 90, "USA"), _rec("B", 85, "EUR"), _rec("T", 80, "GLB", 2.0)]
    ids = [p["alphaId"] for p in bp.daily_plan(rec, qty)["picks"]]
    assert ids == ["T", "A"]
    # Once a theme Alpha went in today, the plan goes back to the plain ranking.
    ids = [p["alphaId"] for p in bp.daily_plan(rec, qty, True)["picks"]]
    assert ids == ["A", "B"]


def _submitted(days_ago: int, *, theme: float | None = None, sim: str = "REGULAR") -> dict:
    return {
        "alpha_id": f"S{days_ago}-{theme}",
        "sim_type": sim,
        "date_submitted": NOW - timedelta(days=days_ago),
        "theme": theme,
    }


def test_account_health_flags_a_negative_combined_performance() -> None:
    summary = {
        "performance": {
            "current": {
                "combinedAlphaPerformance": -0.57,
                "combinedPowerPoolAlphaPerformance": 1.1,
            },
            "previous": {"combinedAlphaPerformance": 0.85},
        }
    }
    leader = {"valueFactor": 0.5, "meanProdCorrelation": 0.672, "meanSelfCorrelation": 0.43}
    rows = [_submitted(1, theme=3.0), _submitted(2), _submitted(2), _submitted(3)]
    day = lambda n: (NOW - timedelta(days=n)).astimezone(bp.PLATFORM_TZ).date().isoformat()  # noqa: E731
    payments = [(day(1), 1.9), (day(2), 1.5), (day(3), 1.3)]
    health = bp.account_health(summary, leader, payments, rows, NOW)

    assert health["combinedAlphaPerformance"] == -0.57
    assert health["submissionDays"] == 3
    joined = " ".join(health["flags"])
    assert "-0.57" in joined and "last quarter 0.85" in joined
    assert "0.5 default" in joined  # fewer than 20 submission days
    assert "0.67" in joined  # above the consultants' median
    assert "theme Alpha paid 1.90" in joined
    by_count = {b["alphas"]: b["average"] for b in health["payment"]["byCount"]}
    assert by_count == {1: 1.6, 2: 1.5}

    items = {i["label"]: i["status"] for i in bp.health_checklist(health)}
    assert items["Combined Alpha Performance positive this quarter"] == "warn"
    assert items["20+ submission days in 3 months"] == "todo"


def test_account_health_with_nothing_from_brain() -> None:
    health = bp.account_health(None, None, [], [], NOW)
    assert health["known"] is False and health["valueFactor"] is None and health["flags"] == []
    labels = [i["label"] for i in bp.health_checklist(health)]
    assert labels == ["20+ submission days in 3 months"]


def test_advisor_prefers_the_less_correlated_of_two_equal_alphas() -> None:
    base = {"sharpe": 2.0, "fitness": 1.3, "testSharpe": 1.9, "turnover": 0.15, "settings": {}}
    near, _, _ = sa.score({**base, "prodCorrelation": 0.65})
    far, _, _ = sa.score({**base, "prodCorrelation": 0.25})
    # 0.40 of correlation is worth more than 10 points now: (0.40 / 0.7) * 20.
    assert far - near > 10


def test_search_pool_orders_least_crowded_first_among_covered() -> None:
    seen = {
        "crowded": (0.95, 52_000),
        "quiet": (0.80, 40),
        "sparse_quiet": (0.30, 5),
        "unknown": (0.90, None),
        "middle": (0.70, 900),
    }
    assert search.by_crowding(seen) == ["quiet", "middle", "crowded", "unknown", "sparse_quiet"]


def test_field_swaps_lean_toward_untouched_fields() -> None:
    fields = [
        {"field_id": "seed", "dataset_id": "d", "field_type": "MATRIX", "coverage": 0.9},
        {
            "field_id": "crowded",
            "dataset_id": "d",
            "field_type": "MATRIX",
            "coverage": 0.9,
            "alpha_count": 10_000,
        },
        {
            "field_id": "quiet",
            "dataset_id": "d",
            "field_type": "MATRIX",
            "coverage": 0.9,
            "alpha_count": 0,
        },
    ]
    market = ga.build_market([], fields, ["MARKET"])
    assert market.crowd == {"crowded": 10_000, "quiet": 0}
    assert ga.crowding_weight(market, "quiet") > 90 * ga.crowding_weight(market, "crowded")
    node = ga.Node(kind="field", value="seed")
    rng = random.Random(7)
    picks = [ga._mutated(rng, node, "field", market) for _ in range(500)]
    assert picks.count("quiet") > 450


def test_daily_plan_does_not_lift_a_weak_theme_alpha() -> None:
    qty = {"alphaRemaining": 1, "d0Remaining": 5, "superToday": 0, "superCap": 1}
    rec = [_rec("A", 26, "USA"), _rec("T", 12.5, "GLB", 3.29)]
    ids = [p["alphaId"] for p in bp.daily_plan(rec, qty)["picks"]]
    assert ids == ["A"]


def test_osmosis_skips_alphas_brain_refuses_and_weighs_test_years() -> None:
    from datetime import date

    def sub(i: int, *, days_ago: int, sharpe: float, test: float | None) -> dict:
        return {
            "alpha_id": f"O{i:02d}",
            "region": "USA",
            "delay": 1,
            "status": "ACTIVE",
            "sharpe": sharpe,
            "test_sharpe": test,
            "turnover": 0.1,
            "margin": 0.001,
            "date_submitted": NOW - timedelta(days=days_ago),
        }

    rows = [sub(i, days_ago=10 + i, sharpe=2.0, test=2.0) for i in range(10)]
    rows += [sub(50, days_ago=200, sharpe=3.5, test=3.5)]  # before compensation began
    rows += [sub(51, days_ago=5, sharpe=3.0, test=0.2)]  # faded in the test years
    start = (NOW - timedelta(days=150)).date()
    plan = bp.osmosis(rows, {}, NOW, compensated_from=start)
    usa = next(s for s in plan["scopes"] if s["scope"] == "USA/D1")
    ids = {a["alphaId"] for a in usa["alphas"]}
    assert "O50" not in ids and "O51" not in ids and len(ids) == 10
    reasons = {d["alphaId"]: d["reason"] for d in usa["dropped"]}
    assert "compensation" in reasons["O50"] and "Test-years" in reasons["O51"]
    assert sum(a["points"] for a in usa["alphas"]) == bp.OSMOSIS_POINTS
    # The weaker of IS and test years counts, and heavy turnover is scaled down.
    assert bp._osmosis_weight({"sharpe": 3.4, "test_sharpe": 1.7}) == 1.7
    heavy = bp._osmosis_weight({"sharpe": 2.0, "test_sharpe": 2.0, "turnover": 0.4})
    assert abs(heavy - 1.0) < 1e-9
    assert bp.osmosis_eligible(rows[0], None) is None
    assert date(2026, 1, 1) < start


def test_operators_brain_refuses_bare_are_kept_out_of_batches() -> None:
    from alpha_harness.labs import fastexpr

    assert {"truncate", "ts_delta_limit"} <= search.EXCLUDED
    table = fastexpr.operator_table(
        [
            {"name": "truncate", "definition": "truncate(x,maxPercent=0.01)"},
            {"name": "rank", "definition": "rank(x, rate=2)"},
        ]
    )
    bare = fastexpr.validate(fastexpr.parse("truncate(rank(close))"), table, {"close"})
    assert any("maxPercent" in p for p in bare)
    named = fastexpr.parse("truncate(rank(close), maxPercent=0.05)")
    assert fastexpr.validate(named, table, {"close"}) == []
