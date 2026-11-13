"""Submit Advisor: which of the Alphas the Tasks produced are worth submitting, and why.

A submission is permanent, so this is fail-closed in the same way the Submission Planner is:

* **Ready** is the strict reading of BRAIN's checks -- every gating check ``PASS`` or
  ``WARNING``. A check still ``PENDING`` is not agreement, so those Alphas are never
  recommended as safe; they are listed apart as needing a BRAIN check first.
* Among the ready ones, each gets a **score** from figures BRAIN and the vault already hold:
  how far it clears the Sharpe and Fitness bars, whether that survives the held-out test
  years and after-cost, its turnover, and its Production Correlation. The score ranks; it
  never overrides a check.
* The ranking is then **de-duplicated on PnL correlation**. Two near-copies are one
  submission's worth of value, and BRAIN refuses the second at 0.7. The ceiling is measured
  the way the planner measures it (last four calendar years, days both traded), against the
  better picks already taken *and* against everything already submitted.

The score is a transparent weighted sum, listed in :data:`WEIGHTS`, not a model: every point
of it can be read back off the figures shown beside it.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from .submission_planner import correlations, grid, window_start

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

#: BRAIN's self-correlation rule for a regular Alpha: below this, or a Sharpe 10% higher than
#: the Alpha it collides with. (The planner's 0.5 is the stricter Power Pool ceiling.)
CEILING = 0.7
#: Consultant bars. Delay 0 is held to more.
SHARPE_BAR = {0: 2.69, 1: 1.58}
FITNESS_BAR = {0: 1.5, 1: 1.0}
TURNOVER_RANGE = (0.01, 0.70)
#: A figure BRAIN or the vault has not produced (after-cost, test years) is assumed to be this
#: fraction of the headline Sharpe, so an Alpha is never ranked higher for having less proof.
UNKNOWN_DISCOUNT = 0.75
#: Headroom over a bar that earns the whole component: 60% above it.
FULL_HEADROOM = 0.6
#: Turnover at or below this costs nothing in the score; the score falls to zero at the cap.
EASY_TURNOVER = 0.30
#: Most Alphas the advisor puts forward. BRAIN takes four a day, so ten is a few days' worth.
MAX_RECOMMENDED = 10

#: What each part of the score is worth, out of 100.
WEIGHTS = {
    "sharpe": 45,
    "fitness": 20,
    "stability": 15,
    "turnover": 10,
    "correlation": 10,
}

type Status = Literal["recommended", "similar", "held_back", "needs_check", "waiting"]
STATUSES: tuple[Status, ...] = ("recommended", "similar", "held_back", "needs_check", "waiting")


@dataclass(slots=True)
class Advice:
    """One Alpha's place in the advice, with the figures that put it there."""

    alpha_id: str
    status: Status
    score: float = 0.0
    #: The reasons it is worth submitting, strongest first.
    strengths: list[str] = field(default_factory=list)
    #: What to know before submitting it.
    cautions: list[str] = field(default_factory=list)
    #: For ``similar``: the Alpha it repeats and how strongly they correlate.
    similar_to: str | None = None
    correlation: float | None = None
    #: Worst correlation with any Alpha ahead of it or already submitted, when measurable.
    max_correlation: float | None = None
    #: Rank among the recommended, 1-based; 0 for the rest.
    rank: int = 0


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _num(value: Any) -> float | None:
    """A finite float, or ``None`` -- a missing figure is never read as zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if np.isfinite(value) else None


def _delay(alpha: dict[str, Any]) -> int:
    settings = alpha.get("settings") or {}
    return 0 if settings.get("delay") == 0 else 1


def score(alpha: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    """The 0-100 score of one ready Alpha, with what earned it and what to watch."""
    delay = _delay(alpha)
    sharpe_bar, fitness_bar = SHARPE_BAR[delay], FITNESS_BAR[delay]
    sharpe = _num(alpha.get("sharpe"))
    fitness = _num(alpha.get("fitness"))
    after_cost = _num(alpha.get("afterCostSharpe"))
    test = _num(alpha.get("testSharpe"))
    turnover = _num(alpha.get("turnover"))
    prod = _num(alpha.get("prodCorrelation"))
    strengths: list[str] = []
    cautions: list[str] = []

    # Sharpe is judged on the weakest honest measure, not the flattering one: an Alpha whose
    # Sharpe halves in the test years or once costs are charged has not earned its headline.
    # A measure that is missing is assumed weaker than the headline, never ignored.
    honest = [sharpe] if sharpe is not None else []
    if sharpe is not None:
        honest += [after_cost if after_cost is not None else sharpe * UNKNOWN_DISCOUNT]
        honest += [test if test is not None else sharpe * UNKNOWN_DISCOUNT]
    robust = min(honest) if honest else 0.0
    sharpe_part = _clip((robust / sharpe_bar - 1) / FULL_HEADROOM) if robust > 0 else 0.0
    if sharpe is not None and sharpe >= sharpe_bar * (1 + FULL_HEADROOM / 2):
        strengths.append(f"Sharpe {sharpe:.2f} clears the {sharpe_bar:.2f} bar by a wide margin")
    elif sharpe is not None:
        strengths.append(f"Sharpe {sharpe:.2f} against a {sharpe_bar:.2f} bar")
    if after_cost is not None and after_cost < sharpe_bar:
        cautions.append(f"After-cost Sharpe is {after_cost:.2f}, under the {sharpe_bar:.2f} bar")

    fitness_part = (
        _clip((fitness / fitness_bar - 1) / FULL_HEADROOM) if fitness and fitness > 0 else 0.0
    )

    if test is None:
        stability_part = 0.0
        cautions.append("No held-out test period, so out-of-sample strength is unknown")
    else:
        stability_part = _clip(test / sharpe) if sharpe and sharpe > 0 else 0.0
        if test >= sharpe_bar:
            strengths.append(f"Holds up in the test years (test Sharpe {test:.2f})")
        elif test <= 0:
            cautions.append(f"Test-year Sharpe is {test:.2f}: the edge may be fitted")
        else:
            cautions.append(f"Test-year Sharpe falls to {test:.2f}")

    if turnover is None:
        turnover_part = 0.0
    else:
        span = TURNOVER_RANGE[1] - EASY_TURNOVER
        turnover_part = (
            1.0 if turnover <= EASY_TURNOVER else _clip((TURNOVER_RANGE[1] - turnover) / span)
        )
        if turnover <= EASY_TURNOVER:
            strengths.append(f"Turnover {turnover:.0%} keeps costs low")
        else:
            cautions.append(f"Turnover {turnover:.0%} is high; costs bite")

    if prod is None:
        correlation_part = 0.5
        cautions.append("Production Correlation has not been checked yet")
    else:
        correlation_part = _clip((CEILING - prod) / CEILING)
        if prod >= CEILING:
            cautions.append(f"Production Correlation {prod:.2f} is at or over the {CEILING} limit")
        elif prod >= 0.5:
            cautions.append(f"Production Correlation {prod:.2f} is close to the {CEILING} limit")
        else:
            strengths.append(f"Distinct from production (correlation {prod:.2f})")

    parts = {
        "sharpe": sharpe_part,
        "fitness": fitness_part,
        "stability": stability_part,
        "turnover": turnover_part,
        "correlation": correlation_part,
    }
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    return round(total, 1), strengths, cautions


def _within_bars(alpha: dict[str, Any]) -> list[str]:
    """Bars BRAIN checks that the stored figures already show missed, for a ready Alpha."""
    problems: list[str] = []
    delay = _delay(alpha)
    sharpe, fitness = _num(alpha.get("sharpe")), _num(alpha.get("fitness"))
    turnover = _num(alpha.get("turnover"))
    if sharpe is None or sharpe < SHARPE_BAR[delay]:
        problems.append(f"Sharpe is under the {SHARPE_BAR[delay]:.2f} bar")
    if fitness is None or fitness < FITNESS_BAR[delay]:
        problems.append(f"Fitness is under the {FITNESS_BAR[delay]:.2f} bar")
    if turnover is None or not TURNOVER_RANGE[0] <= turnover <= TURNOVER_RANGE[1]:
        problems.append("Turnover is outside 1%-70%")
    return problems


def advise(
    alphas: Sequence[dict[str, Any]],
    days: dict[str, dict[date, float]],
    locked_ids: Sequence[str] = (),
    *,
    limit: int = MAX_RECOMMENDED,
    adjustments: dict[str, tuple[float, list[str], list[str]]] | None = None,
) -> list[Advice]:
    """Judge every candidate. ``alphas`` are camelCase rows as the Tasks screen reads them.

    ``days`` is the stored daily PnL by Alpha id; ``locked_ids`` are every Alpha already
    submitted on the account, which a new pick must not repeat.

    ``adjustments`` maps an Alpha id to ``(points, strengths, cautions)`` from the base-payment
    levers (``tools.base_payment.value_adjustment``): theme multiplier, margin, sub-universe
    retention and concentration. They move the score a few points and add their reasons; they
    never turn a held-back Alpha into a recommendation.
    """
    adjustments = adjustments or {}

    def adjusted(
        alpha_id: str, value: float, strengths: list[str], cautions: list[str]
    ) -> tuple[float, list[str], list[str]]:
        delta, more_good, more_bad = adjustments.get(alpha_id, (0.0, [], []))
        return (
            round(max(0.0, min(100.0, value + delta)), 1),
            [*strengths, *more_good],
            [*cautions, *more_bad],
        )

    out: list[Advice] = []
    ready: list[tuple[Advice, dict[str, Any]]] = []
    for alpha in alphas:
        alpha_id = str(alpha["alphaId"])
        if alpha.get("submitted"):
            continue
        if alpha.get("quick") or alpha.get("errored"):
            reason = (
                "BRAIN could not judge a check on it"
                if alpha.get("errored")
                else "Simulated in Quick mode: it must be simulated again in Full first"
            )
            out.append(Advice(alpha_id, "waiting", cautions=[reason]))
        elif alpha.get("pending"):
            value, strengths, cautions = score(alpha)
            value, strengths, cautions = adjusted(alpha_id, value, strengths, cautions)
            cautions = ["BRAIN has not finished its checks on it", *cautions]
            out.append(Advice(alpha_id, "needs_check", value, strengths, cautions))
        else:
            problems = _within_bars(alpha)
            prod = _num(alpha.get("prodCorrelation"))
            if prod is not None and prod >= CEILING:
                problems.append(f"Production Correlation {prod:.2f} is at or over {CEILING}")
            value, strengths, cautions = score(alpha)
            value, strengths, cautions = adjusted(alpha_id, value, strengths, cautions)
            if problems:
                out.append(Advice(alpha_id, "held_back", value, strengths, problems + cautions))
            else:
                item = Advice(alpha_id, "recommended", value, strengths, cautions)
                ready.append((item, alpha))

    # Best score first; Sharpe then id break ties, so the order never depends on input order.
    ready.sort(key=lambda p: (-p[0].score, -(_num(p[1].get("sharpe")) or 0.0), p[0].alpha_id))
    _diversify(ready, days, list(locked_ids), limit)
    out.extend(item for item, _ in ready)

    order = {"recommended": 0, "similar": 1, "held_back": 2, "needs_check": 3, "waiting": 4}
    out.sort(key=lambda a: (order[a.status], a.rank or 10**9, -a.score, a.alpha_id))
    return out


def _diversify(
    ready: list[tuple[Advice, dict[str, Any]]],
    days: dict[str, dict[date, float]],
    locked: list[str],
    limit: int,
) -> None:
    """Walk the ranking, keeping an Alpha only if it is distinct from every one kept so far."""
    ids = [a.alpha_id for a, _ in ready]
    pool = list(dict.fromkeys([*ids, *locked]))
    kept, dates, matrix = grid(days, pool)
    corr: np.ndarray | None = None
    col: dict[str, int] = {}
    if kept and dates:
        start = window_start(dates[-1])
        live = np.array([d >= start for d in dates])
        corr = correlations(matrix[live]) if live.any() else None
        col = {a: i for i, a in enumerate(kept)}

    def against(alpha_id: str, others: list[str]) -> tuple[float | None, str | None]:
        """The strongest correlation of ``alpha_id`` with any of ``others``, and with whom."""
        if corr is None or alpha_id not in col:
            return None, None
        worst: float | None = None
        who: str | None = None
        for other in others:
            if other == alpha_id or other not in col:
                continue
            c = float(corr[col[alpha_id], col[other]])
            if np.isnan(c):
                continue
            if worst is None or c > worst:
                worst, who = c, other
        return worst, who

    taken: list[str] = []
    for item, _ in ready:
        worst, who = against(item.alpha_id, [*taken, *locked])
        item.max_correlation = None if worst is None else round(worst, 3)
        if worst is not None and who is not None and worst >= CEILING:
            # BRAIN's 10%-better-Sharpe exception is deliberately not applied: it needs the
            # Sharpe of the Alpha already submitted, and a submission is permanent, so a
            # collision is treated as a repeat rather than guessed past.
            item.status = "similar"
            item.similar_to = who
            item.correlation = round(worst, 3)
            item.cautions.insert(
                0, f"Repeats {who} (PnL correlation {worst:.2f}); BRAIN refuses a second one"
            )
            continue
        if len(taken) >= limit:
            item.status = "held_back"
            item.cautions.insert(0, f"Beyond the top {limit}; submit the picks above first")
            continue
        if corr is None or item.alpha_id not in col:
            item.cautions.append("No stored daily PnL, so its overlap with the others is unchecked")
        elif worst is not None and worst >= 0.5:
            item.cautions.append(f"PnL correlation {worst:.2f} with {who}: close to the limit")
        taken.append(item.alpha_id)
        item.rank = len(taken)
