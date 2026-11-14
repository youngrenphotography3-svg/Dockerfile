"""Where Evolution Lab's seeds may come from: which datasets, and which kind of Alpha.

Three kinds, told apart the way BRAIN does:

* **Power Pool**: BRAIN attaches ``POWER_POOL_CORRELATION`` to an Alpha it judges Power Pool
  eligible (``vault.yields.is_power_pool``). An Alpha stored without any checks is judged from
  Power Pool's own structural limits instead: at most 8 operators and 3 data fields.
* **ATOM**: every data field comes from one dataset. ``inst_pnl`` and ``convert`` read pv1, so
  they count as that dataset too.
* **Regular**: neither of the above, an ordinary Alpha over several datasets.

An Alpha can be Power Pool and ATOM at once, and then it is both and not Regular.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import structlog

from ..vault.store import json_list
from ..vault.yields import checks_of, is_power_pool
from .fastexpr import (
    MAX_FIELDS,
    MAX_OPERATORS,
    ParseError,
    data_fields,
    operator_count,
    parse,
    walk,
)
from .template import PV1_OPERATORS

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

log = structlog.get_logger(__name__)

KINDS: Final = ("regular", "power_pool", "atom")

#: BRAIN's name for the classification it gives a submitted Power Pool Alpha.
POWER_POOL_CLASSIFICATION = "Power Pool Alpha"
#: Placeholders per query, well under what either database takes in one statement.
_CHUNK = 500


@dataclass(frozen=True, slots=True)
class Source:
    """What one stored Alpha is made of."""

    #: Datasets of the fields it reads; a field the catalog cannot place is simply absent.
    datasets: frozenset[str]
    kinds: frozenset[str]


def _chunks(items: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(items), _CHUNK):
        yield items[start : start + _CHUNK]


async def field_datasets(
    state: Any, region: str, delay: int, universe: str, fields: Collection[str]
) -> dict[str, str]:
    """Each field's dataset in this market, else where every market agrees on it."""
    wanted = sorted(set(fields))
    found: dict[str, str] = {}
    for part in _chunks(wanted):
        marks = ", ".join("?" for _ in part)
        rows = await state.catalog.query(
            f"""
            SELECT field_id, any_value(dataset_id) AS dataset_id FROM data_field
            WHERE instrument_type = 'EQUITY' AND region = ? AND delay = ? AND universe = ?
              AND field_id IN ({marks})
            GROUP BY field_id
            """,  # noqa: S608 - placeholders only
            [region, delay, universe, *part],
        )
        found.update({str(r["field_id"]): str(r["dataset_id"]) for r in rows if r["dataset_id"]})
    # A region-agnostic market has no fields of its own, and a field the market lacks may
    # still be one dataset everywhere.
    missing = [f for f in wanted if f not in found]
    for part in _chunks(missing):
        found.update(await state.queries.sole_datasets(part))
    return found


def _is_power_pool(row: dict[str, Any], tree_ops: int, fields: int) -> bool:
    checks = checks_of(row.get("checks"))
    if checks:
        return is_power_pool(checks)
    if POWER_POOL_CLASSIFICATION in json_list(row.get("classifications")):
        return True
    # Nothing from BRAIN to go on: Power Pool's own limits.
    sharpe = row.get("sharpe")
    return (
        tree_ops <= MAX_OPERATORS
        and 0 < fields <= MAX_FIELDS
        and sharpe is not None
        and sharpe >= 1.0
    )


async def sources_of(
    state: Any, rows: Iterable[dict[str, Any]], region: str, delay: int, universe: str
) -> dict[str, Source]:
    """Datasets and kinds of each row, by alpha id, in one pass over the catalog."""
    parsed: dict[str, tuple[list[str], int, bool]] = {}
    every: set[str] = set()
    kept = list(rows)
    for row in kept:
        alpha_id = str(row["alpha_id"])
        try:
            tree = parse(row.get("expression") or "")
        except ParseError:
            parsed[alpha_id] = ([], 0, False)
            continue
        fields = data_fields(tree)
        reads_pv1 = any(n.kind == "call" and n.value in PV1_OPERATORS for _, n in walk(tree))
        parsed[alpha_id] = (fields, operator_count(tree), reads_pv1)
        every.update(fields)

    where = await field_datasets(state, region, delay, universe, every)
    out: dict[str, Source] = {}
    for row in kept:
        alpha_id = str(row["alpha_id"])
        fields, ops, reads_pv1 = parsed[alpha_id]
        datasets = {where[f] for f in fields if f in where}
        if reads_pv1:
            datasets.add("pv1")
        # One source is one dataset whatever it is; several have to be known and the same.
        sources = len(fields) + (1 if reads_pv1 else 0)
        known = len([f for f in fields if f in where]) + (1 if reads_pv1 else 0)
        atom = sources > 0 and (sources == 1 or (known == sources and len(datasets) == 1))
        power_pool = _is_power_pool(row, ops, len(fields))
        kinds = {k for k, on in (("power_pool", power_pool), ("atom", atom)) if on}
        out[alpha_id] = Source(frozenset(datasets), frozenset(kinds or {"regular"}))
    return out


def allowed(source: Source | None, datasets: Collection[str], kinds: Collection[str]) -> bool:
    """Whether an Alpha passes the chosen datasets and kinds. Empty choices mean no limit.

    Datasets are met only when everything the Alpha reads is among them: one stray dataset
    makes it a different Alpha than the one asked for.
    """
    if source is None:
        return not datasets and not kinds
    if kinds and not source.kinds & set(kinds):
        return False
    if datasets:
        return bool(source.datasets) and source.datasets <= set(datasets)
    return True
