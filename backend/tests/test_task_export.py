# ruff: noqa: S101  -- a test file asserts
"""A task's results export: deduped, shared settings said once, every format renders."""

import csv
import io

from alpha_harness.api import task_export
from alpha_harness.api.lab_tasks import LabTask, RankedAlpha

_SETTINGS = {"region": "CHN", "universe": "TOP2000U", "delay": 1, "language": "FASTEXPR"}


def _row(trial: int, alpha: str | None, sharpe: float, decay: int, **extra: object) -> RankedAlpha:
    return RankedAlpha.model_validate(
        {
            "trialId": trial,
            "number": trial,
            "alphaId": alpha,
            "expression": "rank(a | b)\n + 1",
            "settings": {**_SETTINGS, "decay": decay},
            "value": sharpe,
            "sharpe": sharpe,
            "fitness": 1.1,
            "turnover": 0.158,
            "returns": None,
            "drawdown": None,
            "margin": 0.0005,
            "testSharpe": sharpe / 2,
            "feasible": True,
            "failedChecks": ["IS_LADDER_SHARPE"],
            "refusedBy": ["IS_LADDER_SHARPE"],
            "submittable": False,
        }
        | extra
    )


def _task() -> LabTask:
    return LabTask.model_validate(
        {
            "id": 431,
            "name": "CHN IPR",
            "lab": "template",
            "labName": "Template Lab",
            "templateName": "ipr",
            "template": "rank($x)",
            "status": "COMPLETE",
            "stopping": False,
            "message": None,
            "region": "CHN",
            "delay": 1,
            "universe": "TOP2000U",
            "seeds": 0,
            "population": None,
            "mutationRate": None,
            "decay": 3,
            "cores": 4,
            "datasetIds": [],
            "fields": 2,
            "target": 8,
            "simulated": 8,
            "cached": 0,
            "queued": 0,
            "running": 0,
            "failed": 1,
            "failures": [{"reason": "Unknown variable", "count": 1}],
            "best": 2.26,
            "objectiveLabel": "Sharpe",
            "createdAt": None,
            "startedAt": None,
            "finishedAt": None,
        }
    )


def _rows() -> list[RankedAlpha]:
    return [
        _row(1, "O06kwdng", 2.26, 3),
        _row(2, "O06kwdng", 2.26, 3),  # a re-sampled duplicate
        _row(3, "O06kwA2v", 2.16, 6, submittable=True),
    ]


def test_markdown_dedupes_and_says_shared_settings_once() -> None:
    out = task_export.render(_task(), _rows(), "md")
    assert isinstance(out, str)
    assert "Distinct Alphas exported: 2 (1 duplicate trials dropped)" in out
    assert "Shared settings: region=CHN, universe=TOP2000U, delay=1" in out
    assert "language" not in out
    assert "| decay |" in out  # it varies, so it is a column
    assert "rank(a \\| b) + 1" in out  # pipes escaped, whitespace flattened
    assert "Unknown variable x1" in out
    assert "PASS 1" in out and "REFUSED 1" in out


def test_csv_has_every_column() -> None:
    out = task_export.render(_task(), _rows(), "csv")
    assert isinstance(out, str)
    rows = list(csv.DictReader(io.StringIO(out)))
    assert [r["alpha_id"] for r in rows] == ["O06kwdng", "O06kwA2v"]
    assert rows[0]["turnover_pct"] == "15.8"
    assert rows[0]["margin_bps"] == "5.0"
    assert rows[0]["test_train"] == "0.50"
    assert rows[0]["failed_checks"] == "IS_LADDER_SHARPE"


def test_json_and_empty() -> None:
    out = task_export.render(_task(), _rows(), "json")
    assert isinstance(out, dict)
    assert out["duplicatesDropped"] == 1
    assert out["task"]["id"] == 431
    empty = task_export.render(_task(), [], "md")
    assert isinstance(empty, str) and "No finished Alphas yet." in empty
    header = task_export.render(_task(), [], "csv")
    assert isinstance(header, str) and header.startswith("rank,alpha_id")
