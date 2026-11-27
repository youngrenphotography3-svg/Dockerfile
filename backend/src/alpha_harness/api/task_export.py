"""A task's results as one compact file for an LLM (or a person) to analyse.

The Results page is for scrolling; this is for handing the whole picture over in one paste:
what the task was, why simulations failed, and every distinct Alpha it found, best first,
with the settings every row shares said once instead of on every row.
"""

import csv
import io
import math
from collections.abc import Sequence
from typing import Any, Literal

ExportFormat = Literal["md", "csv", "json"]

#: Settings that say nothing about the alpha and only add noise to an export.
_NOISE = frozenset({"language", "instrumentType", "visualization"})

#: Settings columns in the order they read best; any others follow alphabetically.
_ORDER = (
    "region",
    "universe",
    "delay",
    "decay",
    "neutralization",
    "truncation",
    "pasteurization",
    "nanHandling",
    "unitHandling",
    "maxTrade",
    "testPeriod",
)


def _key(row: Any) -> tuple[str, ...]:
    """What makes two rows the same Alpha: its id, else its expression and settings."""
    if row.alpha_id:
        return ("id", row.alpha_id)
    settings = sorted((row.settings or {}).items())
    return ("expr", " ".join((row.expression or "").split()), repr(settings))


def dedupe(rows: Sequence[Any]) -> tuple[list[Any], int]:
    """Rows with each Alpha once, keeping its first (best) appearance, and how many were dropped.

    The harness can simulate one expression more than once (the optimizer re-samples a grid
    point), and each comes back as a trial with the same alphaId.
    """
    seen: set[tuple[str, ...]] = set()
    out = []
    for row in rows:
        key = _key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out, len(rows) - len(out)


def split_settings(rows: Sequence[Any]) -> tuple[dict[str, Any], list[str]]:
    """Settings every row shares (said once), and the names of those that vary (a column each)."""
    names: set[str] = set()
    for row in rows:
        names.update(k for k in (row.settings or {}) if k not in _NOISE)
    ordered = [n for n in _ORDER if n in names] + sorted(names - set(_ORDER))
    shared: dict[str, Any] = {}
    varying: list[str] = []
    for name in ordered:
        values = {repr((row.settings or {}).get(name)) for row in rows}
        if len(values) == 1 and rows:
            shared[name] = (rows[0].settings or {}).get(name)
        else:
            varying.append(name)
    return shared, varying


def status_of(row: Any) -> str:
    if row.submitted:
        return "SUBMITTED"
    if row.errored:
        return "ERROR"
    if row.pending:
        return "PENDING"
    if row.submittable:
        return "QUICK-PASS" if row.quick else "PASS"
    return "REFUSED"


def _num(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    return f"{value:.{digits}f}"


def _pct(value: float | None, digits: int = 1) -> str:
    return "" if value is None else _num(value * 100, digits)


def _ratio(test: float | None, train: float | None) -> str:
    if test is None or not train:
        return ""
    return _num(test / train, 2)


def _flat(value: Any) -> str:
    return "" if value is None else str(value)


def record(row: Any, rank: int, varying: Sequence[str]) -> dict[str, str]:
    """One row as flat strings, in column order."""
    out = {
        "rank": str(rank),
        "alpha_id": row.alpha_id or "",
        "value": _num(row.value, 3),
        "sharpe": _num(row.sharpe),
        "fitness": _num(row.fitness),
        "turnover_pct": _pct(row.turnover),
        "returns_pct": _pct(row.returns),
        "drawdown_pct": _pct(row.drawdown),
        "margin_bps": "" if row.margin is None else _num(row.margin * 10_000, 1),
        "test_sharpe": _num(row.test_sharpe),
        "test_train": _ratio(row.test_sharpe, row.sharpe),
        "test_fitness": _num(row.test_fitness),
        "after_cost": _num(row.after_cost_sharpe),
        "prod_corr": _num(row.prod_correlation),
        "long": _flat(row.long_count),
        "short": _flat(row.short_count),
        "status": status_of(row),
        "refused_by": ";".join(row.refused_by),
        "failed_checks": ";".join(row.failed_checks),
    }
    for name in varying:
        out[name] = _flat((row.settings or {}).get(name))
    out["expression"] = " ".join((row.expression or "").split())
    return out


#: Markdown leaves out what its header already says or what rarely helps a read.
_MD_SKIP = frozenset({"failed_checks", "test_fitness", "long", "short"})


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _header(task: Any, rows: Sequence[Any], dropped: int, shared: dict[str, Any]) -> list[str]:
    title = task.name or task.template_name or task.lab_name
    counts: dict[str, int] = {}
    for row in rows:
        counts[status_of(row)] = counts.get(status_of(row), 0) + 1
    lines = [
        f"# Task {task.id} export: {title}",
        "",
        f"- Lab: {task.lab_name}"
        + (f" · template: {task.template_name}" if task.template_name else ""),
        f"- Status: {task.status.value if hasattr(task.status, 'value') else task.status}",
        f"- Ranked on: {task.objective_label} (column `value`)",
        f"- Simulations: {task.simulated}/{task.target} spent, {task.cached} cached, "
        f"{task.full_runs} Full re-runs, {task.failed} failed (no Alpha)",
        f"- Distinct Alphas exported: {len(rows)}"
        + (f" ({dropped} duplicate trials dropped)" if dropped else ""),
        "- Verdicts: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"),
    ]
    if task.started_at or task.finished_at:
        lines.append(f"- Ran: {task.started_at or '?'} → {task.finished_at or 'running'}")
    if task.failures:
        lines.append(
            "- Why simulations failed: "
            + "; ".join(f"{f.reason} x{f.count}" for f in task.failures)
        )
    if shared:
        lines.append("- Shared settings: " + ", ".join(f"{k}={v}" for k, v in shared.items()))
    skeleton = task.template or task.expression
    if skeleton:
        lines += ["", "Template / expression:", "```", skeleton.strip(), "```"]
    lines += [
        "",
        "Units: turnover/returns/drawdown in %, margin in bps; test_train = test Sharpe ÷ "
        "train Sharpe; status PASS = nothing BRAIN reported refuses it.",
        "",
    ]
    return lines


def render(task: Any, rows: Sequence[Any], fmt: ExportFormat) -> str | dict[str, Any]:
    """The export in ``fmt``. ``rows`` are RankedAlpha, best first."""
    distinct, dropped = dedupe(rows)
    shared, varying = split_settings(distinct)
    records = [record(r, i + 1, varying) for i, r in enumerate(distinct)]
    if fmt == "json":
        return {
            "task": task.model_dump(mode="json", by_alias=True),
            "sharedSettings": shared,
            "duplicatesDropped": dropped,
            "rows": records,
        }
    if fmt == "csv":
        buf = io.StringIO()
        columns = list(records[0]) if records else list(record_columns(varying))
        writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
        return buf.getvalue()
    lines = _header(task, distinct, dropped, shared)
    if not records:
        lines.append("No finished Alphas yet.")
        return "\n".join(lines) + "\n"
    columns = [c for c in records[0] if c not in _MD_SKIP]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join("---" for _ in columns) + "|")
    for rec in records:
        lines.append("| " + " | ".join(_cell(rec[c]) for c in columns) + " |")
    return "\n".join(lines) + "\n"


def record_columns(varying: Sequence[str]) -> list[str]:
    """Column names for an export with no rows, so an empty CSV still has its header."""
    base = [
        "rank",
        "alpha_id",
        "value",
        "sharpe",
        "fitness",
        "turnover_pct",
        "returns_pct",
        "drawdown_pct",
        "margin_bps",
        "test_sharpe",
        "test_train",
        "test_fitness",
        "after_cost",
        "prod_corr",
        "long",
        "short",
        "status",
        "refused_by",
        "failed_checks",
    ]
    return [*base, *varying, "expression"]
