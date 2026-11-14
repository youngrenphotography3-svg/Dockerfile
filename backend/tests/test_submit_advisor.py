# ruff: noqa: S101, T201  -- a test file asserts, and prints when run as a script
"""Submit Advisor: ranking, de-duplication and fail-closed handling.

Plain asserts, so ``uv run pytest`` collects them and ``uv run python`` runs them as a script.
"""

from datetime import date, timedelta

import numpy as np

from alpha_harness.tools import submit_advisor as sa


def alpha(alpha_id: str, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
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
    }
    return base | over


def steady(alpha_id: str, sharpe: float, **over: object) -> dict[str, object]:
    """An Alpha whose after-cost and test Sharpe track its headline, so Sharpe orders the score."""
    figures = {"sharpe": sharpe, "afterCostSharpe": sharpe * 0.9, "testSharpe": sharpe * 0.9}
    return alpha(alpha_id, **(figures | over))


def series(seed: int, base: np.ndarray | None = None, mix: float = 0.0) -> dict[date, float]:
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=1400)
    values = noise if base is None else mix * base + (1 - mix) * noise
    start = date(2022, 1, 3)
    return {start + timedelta(days=i): float(v) for i, v in enumerate(values)}


def by_id(advice: list[sa.Advice]) -> dict[str, sa.Advice]:
    return {a.alpha_id: a for a in advice}


def test_statuses_and_failing_closed() -> None:
    rows = [
        alpha("GOOD"),
        alpha("PEND", pending=True),
        alpha("QUICK", quick=True),
        alpha("ERR", errored=True),
        alpha("LOWSHARPE", sharpe=1.2),
        alpha("HIGHTVR", turnover=0.85),
        alpha("PRODCORR", prodCorrelation=0.75),
        alpha("DONE", submitted=True),
    ]
    got = by_id(sa.advise(rows, {}))
    assert got["GOOD"].status == "recommended" and got["GOOD"].rank == 1
    assert got["PEND"].status == "needs_check"
    assert got["QUICK"].status == "waiting" and got["ERR"].status == "waiting"
    for held in ("LOWSHARPE", "HIGHTVR", "PRODCORR"):
        assert got[held].status == "held_back", held
    assert "DONE" not in got


def test_delay_zero_is_held_to_its_higher_bar() -> None:
    d0 = alpha("D0", settings={"delay": 0}, sharpe=2.0, fitness=1.6)
    assert sa.advise([d0], {})[0].status == "held_back"


def test_missing_figures_are_not_read_as_good() -> None:
    bare = alpha("BARE", sharpe=None, fitness=None, turnover=None)
    assert sa.advise([bare], {})[0].status == "held_back"


def test_less_proof_never_ranks_higher() -> None:
    proven = alpha("PROVEN")
    bare = alpha("BARE", testSharpe=None, afterCostSharpe=None)
    got = [a.alpha_id for a in sa.advise([bare, proven], {}) if a.status == "recommended"]
    assert got[0] == "PROVEN"
    assert sa.score(bare)[0] < sa.score(proven)[0]


def test_better_score_ranks_first() -> None:
    weak = steady("WEAK", 1.7, fitness=1.1)
    strong = steady("STRONG", 3.0, fitness=2.2)
    got = sa.advise([weak, strong], {})
    assert [a.alpha_id for a in got if a.status == "recommended"] == ["STRONG", "WEAK"]


def test_near_duplicate_is_marked_similar_not_dropped() -> None:
    first = series(1)
    base = np.array(list(first.values()))
    days = {"A": first, "B": series(2, base, 0.95), "C": series(3)}
    rows = [steady("A", 3.0), steady("B", 2.5), steady("C", 2.2)]
    got = by_id(sa.advise(rows, days))
    assert got["A"].status == "recommended"
    assert got["B"].status == "similar" and got["B"].similar_to == "A"
    assert got["B"].correlation is not None and got["B"].correlation >= sa.CEILING
    assert got["C"].status == "recommended" and got["C"].rank == 2


def test_collision_with_an_already_submitted_alpha() -> None:
    locked = series(10)
    base = np.array(list(locked.values()))
    days = {"NEW": series(11, base, 0.9), "LOCKED": locked}
    got = sa.advise([steady("NEW", 3.5)], days, ["LOCKED"])
    assert got[0].status == "similar" and got[0].similar_to == "LOCKED"


def test_no_stored_pnl_is_recommended_but_flagged() -> None:
    got = sa.advise([alpha("NOPNL")], {})
    assert got[0].status == "recommended"
    assert any("daily PnL" in c for c in got[0].cautions)


def test_limit_caps_the_picks() -> None:
    rows = [steady(f"A{i}", 2.0 + i / 10) for i in range(5)]
    days = {f"A{i}": series(100 + i) for i in range(5)}
    got = sa.advise(rows, days, limit=3)
    assert sum(a.status == "recommended" for a in got) == 3
    assert sum(a.status == "held_back" for a in got) == 2


def test_order_is_independent_of_input_order() -> None:
    rows = [steady(f"A{i}", 2.0 + (i % 3) / 10) for i in range(6)]
    days = {f"A{i}": series(200 + i) for i in range(6)}
    one = [(a.alpha_id, a.status) for a in sa.advise(rows, days)]
    two = [(a.alpha_id, a.status) for a in sa.advise(list(reversed(rows)), days)]
    assert one == two


def test_score_is_bounded() -> None:
    top = sa.score(alpha("T", sharpe=9, fitness=9, testSharpe=9, turnover=0.05, prodCorrelation=0))
    low = sa.score(alpha("L", sharpe=1.58, fitness=1.0, testSharpe=0.1, turnover=0.7))
    assert 0 <= low[0] < top[0] <= 100


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
