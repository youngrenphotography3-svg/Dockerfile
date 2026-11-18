"""Pyramid Planner: which pyramids to research next, and how much each is worth.

A pyramid (region x delay x dataset category) counts toward Genius once three Alphas are
submitted in it in a quarter (``catalog.pyramids.LIT_AT``). Past three, another Alpha in the
same pyramid adds nothing to that count, so the next simulation is worth most where a pyramid
is short of three, pays a high multiplier, and has turned simulations into clean Alphas before.

The priority, in full::

    priority = gap x multiplier x hit_rate

* ``gap`` is how many submissions the pyramid still needs this quarter (0 once lit).
* ``multiplier`` is BRAIN's dynamic pyramid multiplier; one BRAIN does not offer counts as 1.
* ``hit_rate`` is the share of the vault's Alphas in that pyramid that nothing BRAIN reported
  refuses, smoothed toward a 10% prior so an untried pyramid is neither skipped nor favoured:
  ``(clean + 1) / (tried + 10)``.

Only pyramids whose fields are downloaded are ranked: nothing can be researched in the rest.
Read-only: it reads the grid BRAIN already serves and the vault, and simulates nothing.
"""

from dataclasses import dataclass
from typing import Any

from ..catalog.pyramids import LIT_AT
from ..vault.store import json_list
from ..vault.yields import CLEAN, checks_of, verdict

#: The smoothing prior: ``PRIOR_CLEAN`` clean Alphas out of ``PRIOR_TRIED`` before any data.
PRIOR_CLEAN = 1
PRIOR_TRIED = 10


@dataclass(frozen=True, slots=True)
class Plan:
    region: str
    delay: int
    category_id: str
    category: str
    alpha_count: int
    gap: int
    multiplier: float | None
    tried: int
    clean: int
    hit_rate: float
    priority: float


def pyramid_key(region: str, delay: int, category: str) -> tuple[str, int, str]:
    return region.upper(), int(delay), category.upper()


def tally(rows: list[dict[str, Any]]) -> dict[tuple[str, int, str], tuple[int, int]]:
    """``(tried, clean)`` per pyramid from vault rows carrying ``pyramids`` and ``checks``.

    A pyramid name reads ``REGION/D1/CATEGORY``. An Alpha in several pyramids counts in each,
    as BRAIN counts it.
    """
    found: dict[tuple[str, int, str], list[int]] = {}
    for row in rows:
        good = verdict(checks_of(row.get("checks")), row.get("simulation_mode")) in CLEAN
        for name in json_list(row.get("pyramids")):
            parts = str(name).split("/")
            if len(parts) != 3 or not parts[1].upper().startswith("D"):
                continue
            try:
                key = pyramid_key(parts[0], int(parts[1][1:]), parts[2])
            except ValueError:
                continue
            counts = found.setdefault(key, [0, 0])
            counts[0] += 1
            counts[1] += int(good)
    return {k: (v[0], v[1]) for k, v in found.items()}


def plan(
    grid: dict[str, Any],
    tallies: dict[tuple[str, int, str], tuple[int, int]],
    *,
    regions: set[str] | None = None,
    categories: set[str] | None = None,
    include_lit: bool = False,
) -> list[Plan]:
    """Every researchable pyramid, highest priority first.

    ``regions`` and ``categories`` narrow it (category by id or name, any case); lit pyramids
    are left out unless ``include_lit``, since another Alpha there no longer adds a pyramid.
    """
    names = {str(c["id"]): str(c.get("name") or c["id"]) for c in grid.get("categories", [])}
    wanted = {c.upper() for c in categories} if categories else None
    out: list[Plan] = []
    for cell in grid.get("cells", []):
        region, delay = str(cell["region"]), int(cell["delay"])
        category_id = str(cell["categoryId"])
        name = names.get(category_id, category_id)
        if not cell.get("synced"):
            continue
        if regions and region.upper() not in {r.upper() for r in regions}:
            continue
        if wanted and category_id.upper() not in wanted and name.upper() not in wanted:
            continue
        count = int(cell.get("alphaCount") or 0)
        gap = max(0, LIT_AT - count)
        if gap == 0 and not include_lit:
            continue
        tried, clean = tallies.get(
            pyramid_key(region, delay, category_id),
            tallies.get(pyramid_key(region, delay, name), (0, 0)),
        )
        hit_rate = (clean + PRIOR_CLEAN) / (tried + PRIOR_TRIED)
        multiplier = cell.get("multiplier")
        weight = float(multiplier) if multiplier is not None else 1.0
        out.append(
            Plan(
                region=region,
                delay=delay,
                category_id=category_id,
                category=name,
                alpha_count=count,
                gap=gap,
                multiplier=None if multiplier is None else float(multiplier),
                tried=tried,
                clean=clean,
                hit_rate=round(hit_rate, 4),
                priority=round(gap * weight * hit_rate, 4),
            )
        )
    out.sort(key=lambda p: (-p.priority, -p.gap, p.region, p.category))
    return out
