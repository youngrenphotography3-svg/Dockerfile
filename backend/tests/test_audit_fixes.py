# ruff: noqa: S101, T201  -- a test file asserts, and prints when run as a script
"""The 2026-11-18 audit fixes: correlation, ladder, validation, mutation, pyramids.

Plain asserts, so ``uv run pytest`` collects them and ``uv run python`` runs them as a script.
"""

import random
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np

from alpha_harness.brain.schemas import Alpha
from alpha_harness.labs import ga
from alpha_harness.labs.fastexpr import OperatorInfo, parse, validate
from alpha_harness.labs.objectives import LADDER_PROXY, constraints, ladder_proxy
from alpha_harness.labs.power_pool import memory_text
from alpha_harness.tools import pyramid_planner as pp
from alpha_harness.tools import submit_advisor as sa
from alpha_harness.vault.yields import held_out_ok, verdict

TABLE = {
    "ts_rank": OperatorInfo("ts_rank", "Time Series", 2, 3, ("x", "d", "constant = 0")),
    "rank": OperatorInfo("rank", "Cross Sectional", 1, 2, ("x", "rate = 2")),
}
NAMES = {"close", "open", "volume"}


def series(seed: int, base: np.ndarray | None = None, mix: float = 0.0) -> ga.Series:
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=600)
    values = noise if base is None else mix * base + (1 - mix) * noise
    start = date(2020, 1, 1)
    return ga.Series(
        np.array([start + timedelta(days=i) for i in range(600)], dtype="datetime64[D]"),
        values,
    )


# --- production correlation now gates ------------------------------------------------


def test_prod_correlation_fail_refuses() -> None:
    checks = [
        {"name": "LOW_SHARPE", "result": "PASS"},
        {"name": "PROD_CORRELATION", "result": "FAIL"},
    ]
    assert verdict(checks) == "refused"
    checks[1]["result"] = "PASS"
    assert verdict(checks) == "submittable"
    checks[1]["result"] = "PENDING"
    assert verdict(checks) == "pending"


# --- correlation selection fails closed where it must ---------------------------------


def test_unknown_series_is_kept_only_when_asked() -> None:
    series_by_id: dict[str, ga.Profile | None] = {"A": {"USA": series(1)}, "B": None}
    assert ga.independent(["A", "B"], series_by_id, 5) == ["A", "B"]
    assert ga.independent(["A", "B"], series_by_id, 5, keep_unknown=False) == ["A"]


def test_book_blocks_a_parent_that_repeats_a_submission() -> None:
    submitted = series(10)
    near = series(11, submitted.values, 0.95)
    far = series(12)
    book = {"book:S": {"USA": submitted}}
    got = ga.independent(
        ["NEAR", "FAR"], {"NEAR": {"USA": near}, "FAR": {"USA": far}}, 5, book=book
    )
    assert got == ["FAR"]


# --- the ladder's first iteration ---------------------------------------------------------


def test_held_out_needs_the_ladder_floor() -> None:
    assert not held_out_ok({"test_sharpe": 1.2, "train_sharpe": 2.4, "delay": 1})
    assert held_out_ok({"test_sharpe": 1.7, "train_sharpe": 2.4, "delay": 1})
    assert not held_out_ok({"test_sharpe": 2.0, "train_sharpe": 2.4, "delay": 0})


def test_ladder_proxy_is_a_sampler_constraint_only() -> None:
    alpha = Alpha.model_validate(
        {
            "id": "X",
            "settings": {
                "instrumentType": "EQUITY",
                "region": "USA",
                "universe": "TOP3000",
                "delay": 1,
            },
            "is": {"checks": [{"name": "LOW_SHARPE", "result": "PASS"}]},
            "test": {"sharpe": 1.2},
        }
    )
    proxy = ladder_proxy(alpha)
    assert proxy is not None and abs(proxy - 0.39) < 1e-9
    violations, detail = constraints(alpha)
    assert violations[LADDER_PROXY] > 0
    assert all(d["name"] != LADDER_PROXY for d in detail)


# --- validation catches what BRAIN refuses -------------------------------------------------


def test_windows_must_be_positive_whole_days() -> None:
    for bad in ("ts_rank(close, 0)", "ts_rank(close, 0.5)", "ts_rank(close, -5)"):
        assert validate(parse(bad), TABLE, NAMES), bad
    assert validate(parse("ts_rank(close, 20)"), TABLE, NAMES) == []


def test_unused_statement_is_refused() -> None:
    assert validate(parse("a = rank(close); b = rank(open); a"), TABLE, NAMES)
    assert validate(parse("a = rank(close); ts_rank(a, 5)"), TABLE, NAMES) == []


# --- mutation keeps slow signals slow ------------------------------------------------------


def test_decay_and_window_move_one_rung() -> None:
    rng = random.Random(0)
    for _ in range(50):
        assert ga._neighbour(rng, ga.DECAYS, 30) in (20, 60)
        assert ga._neighbour(rng, ga.WINDOWS, 504) == 252
    assert ga._neighbour(rng, ga.DECAYS, 26) == 30


def test_sparse_fields_are_not_swapped_in() -> None:
    fields = [
        {"field_id": "dense", "dataset_id": "f1", "field_type": "MATRIX", "coverage": 0.9},
        {"field_id": "sparse", "dataset_id": "f1", "field_type": "MATRIX", "coverage": 0.2},
        {"field_id": "unknown", "dataset_id": "f1", "field_type": "MATRIX", "coverage": None},
    ]
    market = ga.build_market([], fields, ["MARKET"])
    assert set(market.dataset_of) == {"dense", "unknown"}
    assert "sparse" in market.names


# --- region bars and LLM memory ------------------------------------------------------------


def test_ind_turnover_cap_is_forty_percent() -> None:
    ind = {"settings": {"region": "IND"}}
    assert sa.max_turnover(ind) == 0.4
    assert sa.max_turnover({"settings": {"region": "USA"}}) == 0.7
    assert sa.max_turnover({}) == 0.7


def test_memory_shows_failed_checks_and_errors() -> None:
    done = SimpleNamespace(values=[1.4], expression="rank(x)", constraint={"LOW_FITNESS": 0.2})
    err = SimpleNamespace(expression="ts_rank(x, 0)", message="Got invalid value 0")
    text = memory_text([done], [], [], [], [err])
    assert "fails: LOW_FITNESS" in text
    assert "Got invalid value 0" in text


# --- pyramid planner -------------------------------------------------------------------------


def test_planner_ranks_short_well_paid_pyramids_first() -> None:
    grid = {
        "categories": [
            {"id": "fundamental", "name": "Fundamental"},
            {"id": "analyst", "name": "Analyst"},
        ],
        "cells": [
            {
                "categoryId": "fundamental",
                "region": "IND",
                "delay": 1,
                "multiplier": 1.2,
                "alphaCount": 1,
                "synced": True,
            },
            {
                "categoryId": "fundamental",
                "region": "GLB",
                "delay": 1,
                "multiplier": 1.4,
                "alphaCount": 0,
                "synced": True,
            },
            {
                "categoryId": "analyst",
                "region": "GBR",
                "delay": 1,
                "multiplier": 1.6,
                "alphaCount": 11,
                "synced": True,
            },
            {
                "categoryId": "analyst",
                "region": "USA",
                "delay": 1,
                "multiplier": 1.2,
                "alphaCount": 0,
                "synced": False,
            },
        ],
    }
    rows = [
        {
            "pyramids": '["IND/D1/FUNDAMENTAL"]',
            "checks": '[{"name": "LOW_SHARPE", "result": "PASS"}]',
        },
        {
            "pyramids": '["IND/D1/FUNDAMENTAL"]',
            "checks": '[{"name": "LOW_SHARPE", "result": "FAIL"}]',
        },
    ]
    tallies = pp.tally(rows)
    assert tallies[("IND", 1, "FUNDAMENTAL")] == (2, 1)
    got = pp.plan(grid, tallies)
    assert [(p.region, p.category_id) for p in got] == [
        ("GLB", "fundamental"),
        ("IND", "fundamental"),
    ]
    assert all(p.gap > 0 for p in got)
    lit = pp.plan(grid, tallies, include_lit=True, categories={"Analyst"})
    assert [(p.region, p.gap) for p in lit] == [("GBR", 0)]


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            test()
            print("ok", name)
