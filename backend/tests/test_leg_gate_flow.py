# ruff: noqa: S101, T201  -- a test file asserts, prints, and reaches into what it fakes
"""Leg Gate end to end on a real database: stage 1 finishes, the gates judge, stage 2 follows.

BRAIN is replaced by a table of the figures it would report for each Alpha.
"""

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from test_leg_gate import TEMPLATE, plan

from alpha_harness.db.models import Study, StudyStatus, Trial, TrialState
from alpha_harness.db.sqlite import Database
from alpha_harness.labs import launch
from alpha_harness.labs.params import LEG_GATE, LegGateParams
from alpha_harness.tools import leg_gate as lg
from alpha_harness.tools import settings_sampler


class Optimizer:
    async def notify(self) -> None:
        return None


class Endpoints:
    def __init__(self, table: dict[str, dict[str, Any]]) -> None:
        self.table = table

    async def alpha_body(self, alpha_id: str) -> dict[str, Any]:
        f = self.table[alpha_id]
        return {
            "is": {
                "sharpe": f["sharpe"],
                "fitness": 1.0,
                "turnover": 0.1,
                "checks": [
                    {"name": "LOW_2Y_SHARPE", "result": "PASS", "value": f.get("recent", 2.0)},
                    {
                        "name": "LOW_SUB_UNIVERSE_SHARPE",
                        "result": "PASS",
                        "value": 1.5,
                        "limit": 1.2,
                    },
                ],
            }
        }

    async def recordset_body(self, alpha_id: str, _name: str) -> dict[str, Any]:
        f = self.table[alpha_id]
        return {
            "schema": {"properties": [{"name": "year"}, {"name": "sharpe"}]},
            "records": [["2022", f["y22"]], ["2023", f["y23"]]],
        }


class State:
    def __init__(self, db: Database, table: dict[str, dict[str, Any]]) -> None:
        self.db = db
        self.endpoints = Endpoints(table)
        self.optimizer = Optimizer()


async def stage_one(state: State, **over: object) -> int:
    p = plan(**over)
    stmts, problems = lg.check(p)
    assert not problems
    sims = lg.stage_one(p, stmts)
    added = await launch.add_study(
        state,
        now=datetime.now(UTC),
        sampler=LEG_GATE,
        params=LegGateParams(
            region="EUR",
            delay=1,
            cores=1,
            stage=1,
            plan=p.model_dump(by_alias=False),
            trial_map=[{"leg": s.leg, "sign": s.sign} for s in sims],
            auto_continue=p.auto_continue,
        ),
        simulations=len(sims),
        batch_size=4,
        template_source=TEMPLATE,
        seeds=settings_sampler.seed_trials(lg.build_stage_one_requests(p, sims)),
    )
    async with state.db.session() as session:
        trials = (
            await session.scalars(
                select(Trial).where(Trial.study_id == added.id).order_by(Trial.number)
            )
        ).all()
        for t in trials:
            t.state = TrialState.COMPLETE
            t.alpha_id = f"A{t.number}"
        study = await session.get(Study, added.id)
        assert study is not None
        study.status = StudyStatus.COMPLETE
    return added.id


GOOD = {
    "A0": {"sharpe": -1.0, "y22": -0.5, "y23": -0.2},
    "A1": {"sharpe": 1.5, "y22": 0.4, "y23": 0.9},
    "A2": {"sharpe": 1.9, "y22": 0.8, "y23": 0.7},
    "A3": {"sharpe": -1.9, "y22": -0.8, "y23": -0.7},
}
BAD = GOOD | {"A1": {"sharpe": 1.4, "y22": 0.4, "y23": -0.1}}


async def with_state(table: dict[str, dict[str, Any]]) -> tuple[State, Database]:
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    await db.create_all()
    lg._cache.clear()  # the cache is keyed by Alpha id, which these tests reuse
    launch.scheduler.start_waiting = _noop  # type: ignore[assignment]
    return State(db, table), db


async def _noop(*_a: object, **_k: object) -> None:
    return None


async def children(state: State) -> list[Study]:
    async with state.db.session() as session:
        return list((await session.scalars(select(Study).where(Study.sampler == LEG_GATE))).all())


async def good_run() -> None:
    state, _ = await with_state(GOOD)
    first = await stage_one(state)
    found = await lg.evaluate(state, first)
    assert found["judgement"]["go"] and found["canQueueStage2"]
    added = await lg.queue_stage_two(state, first)
    both = await children(state)
    assert len(both) == 2
    child = next(s for s in both if s.id == added.id)
    assert child.status == StudyStatus.QUEUED
    params = LegGateParams.model_validate(child.sampler_params)
    assert params.stage == 2 and params.parent == first
    # fundamental6_leg wins at -1 (1.5), other335_leg at +1 (1.9): the stronger gets 1.5.
    assert "multiply(1.5, other335_leg)" in child.template_source
    assert "multiply(-0.5, fundamental6_leg)" in child.template_source
    try:
        await lg.queue_stage_two(state, first)
    except lg.NotReady as exc:
        assert "already queued" in str(exc)
    else:
        raise AssertionError("a second stage 2 was queued")
    assert len(await children(state)) == 2


async def bad_run() -> None:
    state, _ = await with_state(BAD)
    first = await stage_one(state)
    found = await lg.evaluate(state, first)
    assert not found["judgement"]["go"] and not found["canQueueStage2"]
    try:
        await lg.queue_stage_two(state, first)
    except lg.NotReady as exc:
        assert "stage 2 is not run" in str(exc)
    else:
        raise AssertionError("stage 2 was queued past a failed gate")
    assert len(await children(state)) == 1


async def auto_follows() -> None:
    state, _ = await with_state(GOOD)
    first = await stage_one(state)
    async with state.db.session() as session:
        study = await session.get(Study, first)
    assert study is not None
    await lg.on_complete(state, study)
    both = await children(state)
    assert len(both) == 2
    said = next(s for s in both if s.id == first).message
    assert "Queued as task" in said


async def auto_off_waits() -> None:
    state, _ = await with_state(GOOD)
    first = await stage_one(state, auto_continue=False)
    async with state.db.session() as session:
        study = await session.get(Study, first)
    assert study is not None
    await lg.on_complete(state, study)
    both = await children(state)
    assert len(both) == 1
    assert "worth simulating" in (both[0].message or "")


def test_a_passing_run_queues_one_combination() -> None:
    asyncio.run(good_run())


def test_a_failed_gate_never_simulates_the_combination() -> None:
    asyncio.run(bad_run())


def test_auto_continue_queues_stage_two() -> None:
    asyncio.run(auto_follows())


def test_auto_continue_off_stops_after_judging() -> None:
    asyncio.run(auto_off_waits())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
