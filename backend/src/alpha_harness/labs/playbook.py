"""Region playbook: the settings a built-in template family starts from in each region.

Read off 48 submitted USA Alphas (BRAIN screenshots, Apr-May 2025). What they share is not
clever formulas but a method: one or two fields from a dataset with an economic story, dropped
into a small proven shape, then settings sized to how fast the signal moves. A fast reversal
ran at decay 0-4 and truncation 0.01-0.07; option skew and news at decay 4-20; slow recency
and fundamentals at decay 10-300. Price-volume and buzz were neutralized by market, news and
fundamentals by sector, industry or subindustry.

The same families travel to every region with what BRAIN's own region guides add:

* multi-country regions (EUR, ASI, GLB, AMR) neutralize by country before anything else;
* KOR and HKG want turnover under 30%, TWN a margin of at least 10 bps, IND turnover under
  40%, so each runs its families one speed slower;
* in CHN, KOR and TWN ``-alpha`` is not the mirror of ``alpha`` (price limits and circuit
  breakers), so a family's sign is searched rather than assumed.

Only a start: the user changes any of it before adding a task.
"""

from dataclasses import dataclass
from typing import Any, Final, Literal

Speed = Literal["fast", "medium", "slow"]
SPEEDS: Final[tuple[Speed, ...]] = ("fast", "medium", "slow")


@dataclass(frozen=True, slots=True)
class Pace:
    """Decay and truncation for a family moving at one speed."""

    decay: int
    truncation: float


#: Middle of the ranges the submitted USA Alphas used at each speed.
PACES: Final[dict[Speed, Pace]] = {
    "fast": Pace(decay=2, truncation=0.02),
    "medium": Pace(decay=8, truncation=0.08),
    "slow": Pace(decay=26, truncation=0.1),
}


@dataclass(frozen=True, slots=True)
class Region:
    """What a region adds to a family's settings."""

    #: Neutralizations to search, the first the one its guide puts first.
    neutralizations: tuple[str, ...]
    #: Speeds slower than the family's own, for regions whose costs or caps punish turnover.
    slower: int
    #: Whether ``-alpha`` is the opposite of ``alpha`` there.
    symmetric: bool
    #: Turnover above this fails the region's test or its costs.
    max_turnover: float
    #: The region's own guide, in a line.
    note: str


_SINGLE: Final = ("SUBINDUSTRY", "INDUSTRY", "SECTOR", "MARKET")
_MULTI: Final = ("COUNTRY", "INDUSTRY", "SECTOR", "MARKET")

REGIONS: Final[dict[str, Region]] = {
    "USA": Region(
        _SINGLE, 0, True, 0.7, "Most mined region: production correlation is the hard test."
    ),
    "GLB": Region(
        _MULTI, 0, True, 0.7, "Country first; ratios, never raw prices, across currencies."
    ),
    "EUR": Region(
        _MULTI,
        0,
        True,
        0.7,
        "Country x industry; PV, analyst, fundamental, option, short interest.",
    ),
    "ASI": Region(_MULTI, 0, True, 0.7, "Country first; ideas that work here also seed JPN-KOR."),
    "AMR": Region(_MULTI, 0, True, 0.7, "Country first; keep PnL off one sector (materials)."),
    "JPN": Region(_SINGLE, 0, True, 0.7, "Weak momentum: reversal and value (model77) families."),
    "CHN": Region(_SINGLE, 1, False, 0.7, "Price limits: search the sign. Robust universe >= 40%."),
    "KOR": Region(_SINGLE, 2, False, 0.3, "Max Trade on, turnover under 30%; try analyst25."),
    "TWN": Region(_SINGLE, 2, False, 0.7, "Max Trade on; margin of at least 10 bps."),
    "HKG": Region(_SINGLE, 2, True, 0.3, "Max Trade on, turnover under 30%; model25, other85."),
    "IND": Region(_SINGLE, 1, True, 0.4, "Turnover under 40%; Robust Universe Sharpe >= 1."),
    "GBR": Region(_SINGLE, 0, True, 0.7, "Standard tests."),
    "DEU": Region(_SINGLE, 0, True, 0.7, "Harder than GBR; worth it for combined performance."),
}
_DEFAULT: Final = Region(_SINGLE, 0, True, 0.7, "")


def pace(region: str, speed: Speed) -> Pace:
    """A family's decay and truncation in a region: slower where the region wants it."""
    slower = REGIONS.get(region, _DEFAULT).slower
    return PACES[SPEEDS[min(SPEEDS.index(speed) + slower, len(SPEEDS) - 1)]]


def playbook() -> dict[str, Any]:
    """Every region's starting settings at every speed, as the Template Lab reads them."""
    return {
        name: {
            "neutralizations": list(region.neutralizations),
            "symmetric": region.symmetric,
            "maxTurnover": region.max_turnover,
            "note": region.note,
            "paces": {
                speed: {
                    "decay": pace(name, speed).decay,
                    "truncation": pace(name, speed).truncation,
                }
                for speed in SPEEDS
            },
        }
        for name, region in REGIONS.items()
    }


if __name__ == "__main__":
    import sys

    checks = {
        "a fast family in USA keeps its own pace": pace("USA", "fast") == PACES["fast"],
        "KOR runs a fast family as a slow one": pace("KOR", "fast") == PACES["slow"],
        "nothing is slower than slow": pace("TWN", "slow") == PACES["slow"],
        "an unknown region keeps the family's pace": pace("XYZ", "medium") == PACES["medium"],
        "multi-country regions neutralize by country first": all(
            REGIONS[r].neutralizations[0] == "COUNTRY" for r in ("EUR", "ASI", "GLB", "AMR")
        ),
        "every region is in the playbook": set(playbook()) == set(REGIONS),
    }
    if failed := [check for check, ok in checks.items() if not ok]:
        raise SystemExit(f"playbook self-check failed: {'; '.join(failed)}")
    sys.stdout.write(f"{len(REGIONS)} regions, e.g. KOR fast = {pace('KOR', 'fast')}\n")
