# ruff: noqa: S101, T201  -- a test file asserts, and prints when run as a script
"""Seed sources: dataset and kind choices for Evolution Lab's Auto Select.

Plain asserts, so ``uv run pytest`` collects them and ``uv run python`` runs them as a script.
"""

import asyncio
import json
from typing import Any

from alpha_harness.labs.seed_sources import Source, allowed, sources_of

FIELDS = {"eps": "fundamental6", "sales": "fundamental6", "close": "pv1", "snt": "sentiment1"}


class Catalog:
    async def query(self, _sql: str, params: list[Any]) -> list[dict[str, Any]]:
        asked = set(params[3:])
        return [{"field_id": f, "dataset_id": d} for f, d in FIELDS.items() if f in asked]


class Queries:
    async def sole_datasets(self, _field_ids: list[str]) -> dict[str, str]:
        return {}


class State:
    catalog = Catalog()
    queries = Queries()


def row(alpha_id: str, expression: str, **over: object) -> dict[str, Any]:
    return {"alpha_id": alpha_id, "expression": expression, "sharpe": 1.5} | over


def run(rows: list[dict[str, Any]]) -> dict[str, Source]:
    return asyncio.run(sources_of(State(), rows, "USA", 1, "TOP3000"))


def test_one_dataset_is_atom() -> None:
    got = run([row("a", "rank(eps / sales)", checks=json.dumps([{"name": "LOW_SHARPE"}]))])
    assert got["a"].datasets == {"fundamental6"}
    assert got["a"].kinds == {"atom"}


def test_two_datasets_is_regular() -> None:
    got = run([row("a", "rank(eps) + rank(snt)", checks=json.dumps([{"name": "LOW_SHARPE"}]))])
    assert got["a"].datasets == {"fundamental6", "sentiment1"}
    assert got["a"].kinds == {"regular"}


def test_brain_power_pool_check_wins() -> None:
    power = json.dumps([{"name": "POWER_POOL_CORRELATION", "result": "PENDING"}])
    got = run([row("a", "rank(eps) + rank(snt)", checks=power)])
    assert got["a"].kinds == {"power_pool"}
    both = run([row("b", "rank(eps)", checks=power)])
    assert both["b"].kinds == {"power_pool", "atom"}


def test_without_checks_the_structural_limits_decide() -> None:
    small = run([row("a", "rank(eps) + rank(snt)")])
    assert "power_pool" in small["a"].kinds
    weak = run([row("b", "rank(eps) + rank(snt)", sharpe=0.5)])
    assert "power_pool" not in weak["b"].kinds
    wide = "rank(eps) + rank(sales) + rank(close) + rank(snt)"
    assert "power_pool" not in run([row("c", wide)])["c"].kinds


def test_pv1_operators_count_as_a_dataset() -> None:
    got = run([row("a", "rank(inst_pnl(eps))", checks="[]")])
    assert got["a"].datasets == {"fundamental6", "pv1"}
    assert "atom" not in got["a"].kinds


def test_unreadable_expression_is_regular() -> None:
    got = run([row("a", "rank((", checks=json.dumps([{"name": "LOW_SHARPE"}]))])
    assert got["a"].kinds == {"regular"}
    assert got["a"].datasets == frozenset()


def test_allowed() -> None:
    src = Source(frozenset({"fundamental6"}), frozenset({"atom"}))
    assert allowed(src, [], [])
    assert allowed(src, ["fundamental6", "pv1"], ["atom", "regular"])
    assert not allowed(src, ["pv1"], [])
    assert not allowed(src, [], ["regular"])
    # Every dataset it reads has to be chosen, not just one of them.
    multi = Source(frozenset({"fundamental6", "sentiment1"}), frozenset({"regular"}))
    assert not allowed(multi, ["fundamental6"], [])
    assert allowed(multi, ["fundamental6", "sentiment1"], [])
    # Nothing known about it passes only when nothing was asked.
    assert allowed(None, [], [])
    assert not allowed(None, ["pv1"], [])
    assert not allowed(Source(frozenset(), frozenset({"regular"})), ["pv1"], [])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
