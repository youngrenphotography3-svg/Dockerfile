# ruff: noqa: S101  -- a test file asserts
"""LLM Alpha Lab: the offline checks that decide what an LLM-written Alpha costs to try."""

import pytest

from alpha_harness.labs import fastexpr as fx
from alpha_harness.labs import power_pool as pp
from alpha_harness.labs.params import PowerPoolParams

DEFS = {
    "ts_backfill": "ts_backfill(x, lookback = d, k = 1)",
    "divide": "divide(x, y)",
    "add": "add(x, y, filter = false)",
    "subtract": "subtract(x, y, filter = false)",
    "multiply": "multiply(x, y, ..., filter = false)",
    "ts_rank": "ts_rank(x, d, constant = 0)",
    "ts_delta": "ts_delta(x, d)",
    "group_rank": "group_rank(x, group)",
    "group_zscore": "group_zscore(x, group)",
    "group_neutralize": "group_neutralize(x, group)",
    "winsorize": "winsorize(x, std = 4)",
    "ts_decay_linear": "ts_decay_linear(x, d, dense = false)",
    "rank": "rank(x, rate = 2)",
    "densify": "densify(x)",
}
TABLE = fx.operator_table([{"name": n, "definition": d, "category": "x"} for n, d in DEFS.items()])

REGULAR_OK = (
    "a = ts_backfill(fnd_a, 63); b = ts_backfill(fnd_b, 63); "
    "r = group_rank(ts_rank(divide(a, add(b, 0.0001)), 252), industry); "
    "s = ts_decay_linear(group_zscore(winsorize(r, std=4), industry), 5); "
    "group_neutralize(subtract(rank(s), 0.5), country)"
)


def ctx() -> pp.Context:
    field = lambda f: pp.Field(f, "MATRIX", 0.9, "", None)  # noqa: E731
    names = frozenset({"fnd_a", "fnd_b", "fnd_c", "fnd_d", "fnd_e", "close", "industry", "country"})
    return pp.Context(
        id="fundamental6",
        name="Fundamentals",
        category="Fundamental",
        description="",
        fields=tuple(field(f) for f in ("fnd_a", "fnd_b", "fnd_c", "fnd_d", "fnd_e")),
        basics=(field("close"),),
        groups=("industry", "country"),
        types=dict.fromkeys(names, "MATRIX"),
        own=frozenset({"fnd_a", "fnd_b", "fnd_c", "fnd_d", "fnd_e"}),
        names=names,
        held={},
    )


def run(**extra: object) -> PowerPoolParams:
    return PowerPoolParams(
        region="GLB", delay=1, universes=["TOP3000"], neutralizations=["COUNTRY"], **extra
    )


def test_group_times_a_number_is_the_unit_error_brain_gave() -> None:
    bad = "grp = densify(country) + densify(industry) * 100; group_neutralize(rank(close), grp)"
    problem = fx.group_unit_problem(fx.parse(bad))
    assert problem and "multiply" in problem
    assert fx.group_unit_problem(fx.parse("ind = densify(industry); multiply(ind, 2)"))
    fixed = "g = add(densify(country), densify(industry)); group_neutralize(rank(close), g)"
    assert fx.group_unit_problem(fx.parse(fixed)) is None


def test_distinct_operators_ignores_repeats_and_backfills() -> None:
    tree = fx.parse("rank(rank(ts_backfill(close, 5))) + rank(close) * 2")
    assert fx.distinct_operators(tree) == {"rank", "+", "*"}
    assert len(fx.distinct_operators(fx.parse(REGULAR_OK))) == 10


def test_regular_needs_the_floor_of_different_operators() -> None:
    expression, used = pp.check(REGULAR_OK, ctx(), TABLE, run(mode="regular"))
    assert used >= {"fnd_a", "fnd_b"}
    assert expression
    with pytest.raises(pp.Rejected, match="different operators"):
        pp.check("rank(fnd_a)", ctx(), TABLE, run(mode="regular"))
    # The floor is the researcher's to move.
    assert pp.check(REGULAR_OK, ctx(), TABLE, run(mode="regular", min_operators=10))
    with pytest.raises(pp.Rejected, match="needs 11"):
        pp.check(REGULAR_OK, ctx(), TABLE, run(mode="regular", min_operators=11))


def test_regular_ceilings_are_the_researchers() -> None:
    with pytest.raises(pp.Rejected, match="operators; the limit"):
        pp.check(REGULAR_OK, ctx(), TABLE, run(mode="regular", max_operators=9))
    wide = REGULAR_OK.replace("0.5)", "add(fnd_c, add(fnd_d, fnd_e)))")
    with pytest.raises(pp.Rejected, match="data fields"):
        pp.check(wide, ctx(), TABLE, run(mode="regular", max_fields=4))


def test_power_pool_is_still_held_to_eight() -> None:
    with pytest.raises(pp.Rejected, match="Power Pool allows 8"):
        pp.check(REGULAR_OK, ctx(), TABLE, run())
    assert pp.check("group_rank(ts_rank(fnd_a, 252), industry)", ctx(), TABLE, run())


def test_a_group_unit_mistake_is_refused_in_both_modes() -> None:
    bad = "group_neutralize(rank(fnd_a), multiply(densify(industry), 100))"
    for mode in ("power_pool", "regular"):
        with pytest.raises(pp.Rejected, match="group"):
            pp.check(bad, ctx(), TABLE, run(mode=mode))


def test_a_regular_alpha_must_use_a_chosen_field() -> None:
    other = REGULAR_OK.replace("fnd_a", "close").replace("fnd_b", "close")
    with pytest.raises(pp.Rejected, match="Uses no field"):
        pp.check(other, ctx(), TABLE, run(mode="regular"))


def test_schema_asks_for_the_reason_only_in_regular() -> None:
    plain, regular = pp.schema("power_pool"), pp.schema("regular")
    assert plain["properties"]["alphas"]["maxItems"] == pp.PER_CALL
    item = regular["properties"]["alphas"]["items"]
    assert regular["properties"]["alphas"]["maxItems"] == pp.PER_CALL_REGULAR
    assert item["required"] == ["hypothesis", "expression"]
    assert plain == pp.SCHEMA


def test_prompt_carries_the_focus_and_the_regular_floor() -> None:
    params = run(mode="regular", focus="analyst disagreement vs growth", field_ids=["fnd_a"])
    operators = [{"name": n, "definition": d, "category": "x"} for n, d in DEFS.items()]
    text, shown = pp.user_prompt(ctx(), operators, params, "None yet.", 0, 100_000)
    assert "analyst disagreement vs growth" in text
    assert "at least 10 different operators" in text
    assert "CHOSEN FIELDS OF fundamental6" in text
    assert shown == 5
    assert pp.system_for("regular") is not pp.system_for("power_pool")


def test_parse_alphas_keeps_the_hypothesis() -> None:
    raw = '{"alphas": [{"hypothesis": "because", "expression": "rank(close)"}, {"x": 1}]}'
    assert pp.parse_alphas(raw) == [{"hypothesis": "because", "expression": "rank(close)"}]


# --- the review list, on a real database ------------------------------------------


def test_review_holds_then_approves_or_rejects() -> None:
    import asyncio
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path

    from sqlalchemy import select

    from alpha_harness.api import power_pool_lab as api
    from alpha_harness.db.models import Trial, TrialState
    from alpha_harness.db.sqlite import Database
    from alpha_harness.labs import launch
    from alpha_harness.labs.params import POWER_POOL_SAMPLER

    class Optimizer:
        def __init__(self) -> None:
            self.locks: dict[int, asyncio.Lock] = {}
            self.notified = 0

        def lock(self, study_id: int) -> asyncio.Lock:
            return self.locks.setdefault(study_id, asyncio.Lock())

        async def notify(self) -> None:
            self.notified += 1

    class State:
        def __init__(self, db: Database) -> None:
            self.db = db
            self.optimizer = Optimizer()

    async def noop(*_a: object, **_k: object) -> None:
        return None

    async def go() -> None:
        db = Database(Path(tempfile.mkdtemp()) / "t.db")
        await db.create_all()
        launch.scheduler.start_waiting = noop  # type: ignore[assignment]
        state = State(db)
        added = await launch.add_study(
            state,
            now=datetime.now(UTC),
            sampler=POWER_POOL_SAMPLER,
            params=run(mode="regular", review=True),
            simulations=10,
            batch_size=3,
            template_source="",
        )
        async with db.session() as session:
            for n, text in enumerate(("rank(close)", REGULAR_OK, "rank(volume)")):
                session.add(
                    Trial(
                        study_id=added.id,
                        number=n,
                        params={"dataset": "fundamental6", "idea": f"reason {n}"},
                        distributions={},
                        expression=text,
                        settings={"universe": "TOP3000", "decay": 5},
                        state=TrialState.PRUNED,
                        message=pp.AWAITING,
                    )
                )
        held = (await api.review_list(state)).items  # type: ignore[arg-type]
        assert [i.hypothesis for i in held] == ["reason 0", "reason 1", "reason 2"]
        assert held[1].operators == 10
        assert held[1].mode == "regular"

        before = state.optimizer.notified
        done = await api.review_decide(
            api.ReviewDecision(approve=[held[1].trial_id], reject=[held[0].trial_id]),
            state,  # type: ignore[arg-type]
        )
        assert (done.approved, done.rejected) == (1, 1)
        assert state.optimizer.notified == before + 1
        left = (await api.review_list(state)).items  # type: ignore[arg-type]
        assert [i.trial_id for i in left] == [held[2].trial_id]
        async with db.session() as session:
            rows = {
                t.number: t
                for t in (
                    await session.scalars(select(Trial).where(Trial.study_id == added.id))
                ).all()
            }
        assert rows[1].message == pp.PROPOSED  # the cores may take it now
        assert rows[0].params["rejected"] is True  # and the LLM is told this one was thrown away
        # A decision is made once: deciding it again changes nothing.
        again = await api.review_decide(api.ReviewDecision(approve=[held[0].trial_id]), state)  # type: ignore[arg-type]
        assert (again.approved, again.rejected) == (0, 0)

    asyncio.run(go())
