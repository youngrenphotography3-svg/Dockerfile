# ruff: noqa: S101  -- a test file asserts
"""Simulations left today come from BRAIN's own count, not the x-ratelimit header."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from alpha_harness.api import today
from alpha_harness.brain.filters import PLATFORM_TZ


class _Tracker:
    def __init__(self, used: int, snapshot: Any) -> None:
        self._used, self._snapshot = used, snapshot

    async def used_today(self) -> int:
        return self._used

    async def latest_quota(self) -> Any:
        return self._snapshot


class _Endpoints:
    def __init__(self, days: list[tuple[str, int]]) -> None:
        self.days = days

    async def simulations_by_day(self) -> list[tuple[str, int]]:
        return self.days


class _Engine:
    async def status(self) -> dict[str, Any]:
        return {"queuedTotal": 0}


def _state(local: int, counted: int | None, header_remaining: int) -> Any:
    day = datetime.now(UTC).astimezone(PLATFORM_TZ).date().isoformat()
    snap = SimpleNamespace(
        limit_total=5000,
        remaining=header_remaining,
        observed_at=datetime.now(UTC),
    )
    days = [] if counted is None else [("2026-01-01", 9), (day, counted)]
    return SimpleNamespace(
        tracker=_Tracker(local, snap), endpoints=_Endpoints(days), engine=_Engine()
    )


def test_brain_count_beats_a_stale_header() -> None:
    today._activity.update(at=float("-inf"), day=None, count=None)
    sims = asyncio.run(
        today.simulations_today(_state(local=900, counted=1709, header_remaining=5000))
    )
    assert sims["remaining"] == 5000 - 1709 and sims["used"] == 1709 and sims["exact"]


def test_header_and_local_count_can_only_lower_it() -> None:
    today._activity.update(at=float("-inf"), day=None, count=None)
    sims = asyncio.run(
        today.simulations_today(_state(local=2000, counted=1709, header_remaining=2500))
    )
    assert sims["remaining"] == 2500  # header lower than 5000 - 2000
    today._activity.update(at=float("-inf"), day=None, count=None)
    sims = asyncio.run(
        today.simulations_today(_state(local=300, counted=None, header_remaining=5000))
    )
    assert sims["remaining"] == 4700 and not sims["exact"]
