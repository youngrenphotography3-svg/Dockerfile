"""Tools: helpers that act on an Alpha the consultant already has.

The Settings Sampler re-runs one proven expression everywhere BRAIN will accept it. The
Correlation Breaker does the opposite — it holds the settings still and re-shapes the
expression, for an Alpha the platform says is already in the production pool. The
Submission Planner then decides which of the results are worth submitting, and in what order.
Neither simulates on its own: previews only read, and queueing hands work to the scheduler
like any lab.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal, Self

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select

from ..brain.schemas import REGION_AGNOSTIC_REGION
from ..catalog.pyramids import pyramid_grid
from ..db.models import Submission, Trial, TrialState, utcnow
from ..engine.packer import MAX_BATCH
from ..engine.slots import DEFAULT_SLOTS
from ..labs.launch import NO_NEUTRALIZATION, AddedTask, add_study
from ..labs.params import (
    CORRELATION_BREAKER,
    LEG_GATE,
    SETTINGS_SAMPLER,
    BreakerParams,
    LegGateParams,
    SettingsParams,
)
from ..schemas import Out
from ..tools import (
    base_payment,
    correlation_breaker,
    leg_gate,
    settings_sampler,
    submission_planner,
    submit_advisor,
)
from ..vault.store import json_list
from ..vault.yields import PLATFORM_ALPHA_URL, SUBMITTED, checks_of, verdict
from .deps import State, refuse
from .lab_tasks import submittable_alphas
from .vault import AlphaSettings

router = APIRouter(prefix="/api/tools", tags=["tools"])
log = structlog.get_logger(__name__)


class Pair(Out):
    max_trade: str
    max_position: str


class MarketRow(Out):
    region: str
    delay: int
    universe: str
    #: Of the thinnest data field the expression reads. BRAIN sometimes reports zero for
    #: markets that simulate fine (DEU, GBR), so it is shown, never used to exclude.
    coverage: float
    #: Simulations this market is worth: its neutralizations times its legal pairs.
    total: int
    #: What the Truncation Agent sets here.
    agent_truncation: float


class RegionPlan(Out):
    region: str
    delays: list[int]
    universes: list[str]
    neutralizations: list[str]
    pairs: list[Pair]
    #: Whether BRAIN accepts Max Position here.
    position_available: bool
    #: Simulations of the day's allowance one run here uses: 4 in All Regions, else 1.
    cost: int
    markets: list[MarketRow]
    total: int


class PlanTotals(Out):
    #: Of the day's allowance, All Regions counted at its own cost.
    total: int
    batches: int


class SourceSettings(Out):
    region: str | None
    universe: str | None
    delay: int | None
    neutralization: str | None
    decay: int | None
    truncation: float | None
    pasteurization: str
    nan_handling: str
    test_period: str
    max_trade: str
    max_position: str


class SettingsPlan(Out):
    alpha_id: str
    expression: str
    data_fields: list[str]
    #: Read by the expression but not counted as data by BRAIN, e.g. ``industry``.
    grouping_fields: list[str]
    settings: SourceSettings
    regions: list[RegionPlan]
    totals: PlanTotals
    #: Most concurrent slots one task may hold: the engine's own, so a lone sweep can use it all.
    max_cores: int
    problems: list[str]
    warnings: list[str]


class PreviewRequest(BaseModel):
    """An Alpha to read, or a bare expression with the decay and truncation to hold it at."""

    alpha_id: str = Field(default="", max_length=64, alias="alphaId")
    expression: str | None = Field(default=None, max_length=20_000)
    #: Each one left out keeps the source Alpha's own value, or the platform default when
    #: there is no Alpha.
    decay: int | None = Field(default=None, ge=0, le=512)
    truncation: float | None = Field(default=None, ge=0, le=1)
    pasteurization: Literal["ON", "OFF"] | None = None
    nan_handling: Literal["ON", "OFF"] | None = Field(default=None, alias="nanHandling")
    #: ``P{years}Y{months}M0D``, the shape BRAIN's own field takes; its bounds are
    #: ``P0Y0M0D`` to ``P6Y0M0D``.
    test_period: str | None = Field(
        default=None, alias="testPeriod", pattern=r"^P[0-6]Y(?:[0-9]|1[01])M0D$"
    )

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if bool(self.alpha_id.strip()) == bool((self.expression or "").strip()):
            raise ValueError("Give either an Alpha ID or an expression.")
        return self

    async def plan(self, state: Any) -> dict[str, Any]:
        return await settings_sampler.plan(
            state,
            self.alpha_id.strip(),
            expression=self.expression.strip() if self.expression else None,
            decay=self.decay,
            truncation=self.truncation,
            pasteurization=self.pasteurization,
            nan_handling=self.nan_handling,
            test_period=self.test_period,
        )


class MarketPick(BaseModel):
    region: str
    delay: int
    universe: str


class PairPick(BaseModel):
    max_trade: Literal["ON", "OFF"] = Field(alias="maxTrade")
    max_position: Literal["ON", "OFF"] = Field(alias="maxPosition")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _one_at_most(self) -> Self:
        # BRAIN refuses the pair outright; catching it here costs no round trip.
        if self.max_trade == "ON" and self.max_position == "ON":
            raise ValueError("Max Trade and Max Position cannot both be ON.")
        return self


class SampleRequest(PreviewRequest):
    """What to queue. An empty market or pair list means "everything the plan offers"; an
    empty neutralization list is refused, since nobody chose what to run."""

    markets: list[MarketPick] = Field(default_factory=list, max_length=500)
    neutralizations: list[str] = Field(default_factory=list, max_length=50)
    pairs: list[PairPick] = Field(default_factory=list, max_length=4)
    #: Bounded by the engine rather than by ``search.MAX_CORES``, which is the labs' own cap.
    #: The real ceiling is the engine's slot count, checked in the route where it is known.
    cores: int = Field(default=DEFAULT_SLOTS, ge=1)
    #: Drop the one combination that is not market neutral -- ``NONE`` neutralization with
    #: neither Max Trade nor Max Position. On by default: those simulations cost the same as
    #: any other and produce an Alpha carrying the market's own direction.
    market_neutral_only: bool = Field(default=True, alias="marketNeutralOnly")
    #: Truncation set per market by the Truncation Agent rather than held at one value.
    truncation_agent: bool = Field(default=False, alias="truncationAgent")


@router.post("/settings-sampler/preview")
async def preview(body: PreviewRequest, state: State) -> SettingsPlan:
    """Where one Alpha's expression could also run, and what that would cost.

    Reads the Alpha from BRAIN rather than the vault, because the vault stores no
    maxTrade/maxPosition and may not hold the Alpha at all.
    """
    found = await body.plan(state)
    return SettingsPlan.model_validate({**found, "maxCores": state.engine.slots})


@router.post("/settings-sampler/tasks", status_code=201)
async def add_task(body: SampleRequest, state: State) -> AddedTask:
    """Add the chosen settings as a background task."""
    if body.cores > state.engine.slots:
        raise refuse(
            422,
            "too_many_cores",
            f"The engine has {state.engine.slots} slots, so a task cannot hold {body.cores}.",
        )
    if not body.neutralizations:
        raise refuse(422, "no_neutralization", NO_NEUTRALIZATION)
    found = await body.plan(state)
    if found["problems"]:
        raise refuse(422, "settings_sampler_blocked", found["problems"][0])

    # Nothing chosen means every market, except All Regions: at four simulations a run it is
    # only swept when asked for by name.
    chosen = {(m.region, m.delay, m.universe) for m in body.markets} or {
        (str(m["region"]), int(m["delay"]), str(m["universe"]))
        for r in found["regions"]
        if r["region"] != REGION_AGNOSTIC_REGION
        for m in r["markets"]
    }
    source: dict[str, Any] = {**found["settings"], "expression": found["expression"]}
    requests = settings_sampler.expand(
        found["regions"],
        chosen,
        set(body.neutralizations),
        {(p.max_trade, p.max_position) for p in body.pairs},
        source,
        market_neutral_only=body.market_neutral_only,
        truncation_agent=body.truncation_agent,
    )
    if not requests:
        raise refuse(
            422,
            "no_simulations",
            "Nothing to run: choose a market, a neutralization and a "
            "Max Trade / Max Position pair.",
        )

    markets = len(chosen)
    return await add_study(
        state,
        now=utcnow(),
        sampler=SETTINGS_SAMPLER,
        params=SettingsParams(
            region=str(found["settings"]["region"] or ""),
            delay=int(found["settings"]["delay"] or 0),
            alpha_id=body.alpha_id,
            markets=markets,
            decay=int(found["settings"]["decay"] or 0),
            truncation=float(found["settings"]["truncation"] or 0.08),
            truncation_agent=body.truncation_agent,
            nan_handling=str(found["settings"]["nanHandling"] or "ON"),
            test_period=str(found["settings"]["testPeriod"] or ""),
            cores=body.cores,
        ),
        simulations=len(requests),
        # One batch more than the cores can run, so a finished batch is replaced from the
        # queue on the engine's next 2s tick instead of waiting out the scheduler's 5s poll.
        # Measured: without the spare, 29% of this task's slot-time sat idle.
        batch_size=(body.cores + 1) * MAX_BATCH,
        template_source=found["expression"],
        template_name=f"Settings Sampler · {body.alpha_id or 'Expression'}",
        seeds=settings_sampler.seed_trials(requests),
    )


# -- Submission Planner ---------------------------------------------------


class Pick(Out):
    alpha_id: str
    #: On its own, over the whole history. The portfolio beats every one of these.
    sharpe: float
    #: Already submitted on BRAIN, so it is carried rather than chosen.
    submitted: bool


class PlannedPortfolio(Out):
    #: Submittable Alphas the chosen tasks produced.
    candidates: int
    #: Of those, already submitted.
    locked: int
    #: Chosen but with no stored daily PnL, so they could not be judged.
    missing: list[str]
    size: int
    #: Submit in this order, strongest first.
    order: list[Pick]
    #: Combined Sharpe at each size over the training window; the peak sets ``size``.
    sizes: list[float]
    train_sharpe: float
    #: The same portfolio over the last fifth of history, which the search never saw.
    held_out_sharpe: float
    #: Combined Sharpe over the whole history.
    sharpe: float
    best_single: float
    #: ``None`` when no pair shared enough history to measure, which is not the same as zero.
    max_correlation: float | None
    #: The two Alphas that collision is between, empty when there is nothing to measure.
    worst_pair: list[str]
    #: Whether that pair is over the ceiling and admitted by BRAIN's 10% Sharpe rule.
    escape_used: bool
    days: int
    curve: list[float]
    dates: list[str]


class PlanRequest(BaseModel):
    # The screen plans over every task by default, and an account passes 50 within weeks. The
    # ids only fill an IN list, so the bound is SQLite's 32,766 parameters, not the search.
    task_ids: list[int] = Field(min_length=1, max_length=10_000, alias="taskIds")

    model_config = {"populate_by_name": True}


class SubmittedRequest(BaseModel):
    alpha_id: str = Field(min_length=1, max_length=64, alias="alphaId")
    submitted: bool

    model_config = {"populate_by_name": True}


async def _candidates(state: State, task_ids: list[int]) -> tuple[list[str], set[str], int]:
    """Every submittable Alpha those tasks produced, which are marked submitted, and how many
    BRAIN has not finished judging.

    Reads the frozen ``Trial.result`` rather than the vault: that is where a task's own view
    of its Alphas lives, and it is what the Tasks screen already shows.

    Judged the vault's way, not the Tasks screen's. ``labs.study.submittable`` answers "has
    anything refused this yet", which is right for a list that fills in as checks resolve --
    but a submission is permanent, and a check still ``PENDING`` is not agreement. So the
    strict reading is used here, and the ones still being judged are counted rather than
    dropped, because "not submittable" and "not judged yet" are different news.
    """
    async with state.db.session() as session:
        # Two columns, not whole rows: a sweep's trials carry their expression, settings and
        # distributions, and none of that is read here.
        rows = (
            await session.execute(
                select(Trial.alpha_id, Trial.result).where(
                    Trial.study_id.in_(task_ids),
                    Trial.state == TrialState.COMPLETE,
                    Trial.alpha_id.is_not(None),
                )
            )
        ).all()
        marked = {str(a) for a in (await session.scalars(select(Submission.alpha_id))).all()}

    # The vault holds the checks BRAIN has since finished; a trial's copy is frozen at
    # simulation time, still PENDING on the correlation checks, and only stands in for an
    # Alpha the vault does not hold.
    stored = await state.alphas.by_ids(list({str(a) for a, _ in rows}))
    found: list[str] = []
    seen: set[str] = set()
    pending = 0
    for alpha_id, result in rows:
        found_id = str(alpha_id)
        if found_id in seen:
            continue
        seen.add(found_id)
        row = stored.get(found_id) or {}
        checks = row.get("checks") or json.dumps((result or {}).get("checks") or [])
        judged = verdict(checks_of(checks), row.get("simulation_mode"))
        if judged == "submittable":
            found.append(found_id)
        elif judged == "pending":
            pending += 1
    return found, marked, pending


@router.post("/submission-planner/plan")
async def plan_submissions(body: PlanRequest, state: State) -> PlannedPortfolio:
    """Which Alphas to submit and in what order.

    The search is a few seconds of numpy over every pair, so it runs off the event loop.
    """
    alpha_ids, marked, pending = await _candidates(state, body.task_ids)
    if not alpha_ids:
        raise refuse(
            422,
            "no_candidates",
            f"BRAIN is still checking {pending} of those Alphas. Nothing is refused yet, so "
            "try again once the checks resolve."
            if pending
            else "Those tasks produced no submittable Alphas, so there is nothing to plan.",
        )
    # Every submission on the account, not just the ones these tasks produced. BRAIN measures
    # the ceiling against all of them, so a plan that ignores one is a plan it will refuse.
    locked_ids = sorted(marked)
    days = await state.alphas.daily_pnl(list(dict.fromkeys([*alpha_ids, *locked_ids])))
    found = await asyncio.to_thread(submission_planner.plan, days, alpha_ids, locked_ids=locked_ids)
    if not found["size"]:
        raise refuse(422, *_why(found, len(alpha_ids)))
    return PlannedPortfolio.model_validate(found)


def _why(found: dict[str, Any], candidates: int) -> tuple[str, str]:
    """Why no plan came back. Every one of these used to read as "nothing to plan yet"."""
    match found.get("reason"):
        case "short_history":
            return (
                "short_history",
                f"These Alphas share {found.get('days', 0)} trading days of PnL. Judging a "
                "portfolio needs 1250 -- a thousand to choose its size on and 250 held back to "
                "check that size against.",
            )
        case "nothing_legal":
            return (
                "nothing_legal",
                "Every candidate correlates at 0.50 or above with an Alpha you have already "
                "submitted, and none beats the one it collides with by the 10% BRAIN wants.",
            )
        case _:
            return (
                "too_few",
                f"{candidates} Alphas had stored daily PnL, and a portfolio needs at least two. "
                "Download their PnL in the Pool first.",
            )


@router.post("/submission-planner/submitted", status_code=204)
async def mark_submitted(body: SubmittedRequest, state: State) -> None:
    """Record that an Alpha was submitted on BRAIN, so later plans treat it as permanent."""
    async with state.db.session() as session:
        if body.submitted:
            await session.merge(Submission(alpha_id=body.alpha_id, submitted_at=utcnow()))
        else:
            await session.execute(delete(Submission).where(Submission.alpha_id == body.alpha_id))
        await session.commit()


# --- Submit Advisor -------------------------------------------------------


class AdvisedAlpha(Out):
    alpha_id: str
    #: ``recommended`` to submit now; ``similar`` repeats a better pick or a submission;
    #: ``held_back`` misses a bar or is beyond the top picks; ``needs_check`` is still being
    #: judged by BRAIN; ``waiting`` cannot be judged yet (Quick mode, or a check errored).
    status: submit_advisor.Status
    score: float
    #: 1-based place among the recommended; 0 for every other status.
    rank: int
    strengths: list[str]
    cautions: list[str]
    similar_to: str | None
    #: With ``similar_to``.
    correlation: float | None
    #: Worst PnL correlation with any better pick or submission, when measurable.
    max_correlation: float | None
    brain_url: str
    task_id: int
    task_name: str
    expression: str | None
    settings: dict[str, Any] | None
    sharpe: float | None
    fitness: float | None
    turnover: float | None
    returns: float | None
    drawdown: float | None
    margin: float | None
    after_cost_sharpe: float | None
    test_sharpe: float | None
    prod_correlation: float | None
    #: Pyramid multiplier on BRAIN's Quality Factor for this Alpha's data category, if known.
    multiplier: float | None = None


class SubmitAdvice(Out):
    #: Already submitted, so not a candidate.
    submitted: int
    recommended: int
    similar: int
    held_back: int
    needs_check: int
    waiting: int
    #: How many Alphas the advice was ranked from, apart from the submitted ones.
    considered: int
    #: Recommended first, in submit order, then the rest by how close they came.
    alphas: list[AdvisedAlpha]


async def _submitted_rows(state: State) -> list[dict[str, Any]]:
    """Every submitted Alpha with the columns the base-payment reads need."""
    # SUBMITTED is a module constant, not input.
    sql = (
        "SELECT a.alpha_id, a.sim_type, a.region, a.delay, a.universe, a.neutralization, "  # noqa: S608
        "a.sharpe, a.turnover, a.margin, a.status, a.date_submitted, a.pyramids, "
        "a.prod_correlation, a.self_correlation "
        f"FROM alpha a WHERE {SUBMITTED}"
    )
    rows = await state.alphas.catalog.query(sql)
    for row in rows:
        row["pyramids"] = json_list(row.get("pyramids"))
    return rows


async def _multipliers(state: State) -> dict[tuple[str, int, str], float]:
    """The pyramid multiplier of every (region, delay, category), or nothing if BRAIN is
    unreachable: a missing multiplier is never read as 1x, it just adds no bonus."""
    try:
        grid = await pyramid_grid(state.endpoints, state.alphas.catalog)
    except Exception:
        log.warning("base_payment.multipliers_failed", exc_info=True)
        return {}
    names = {c["id"]: c["name"] for c in grid.get("categories", [])}
    out: dict[tuple[str, int, str], float] = {}
    for cell in grid.get("cells", []):
        if cell.get("multiplier") is None:
            continue
        for label in {str(cell["categoryId"]), str(names.get(cell["categoryId"], ""))}:
            out[(cell["region"], int(cell["delay"]), label.upper())] = float(cell["multiplier"])
    return out


def _multiplier_of(pyramids: list[str], table: dict[tuple[str, int, str], float]) -> float | None:
    best: float | None = None
    for name in pyramids:
        parts = name.split("/")
        if len(parts) != 3 or not parts[1].startswith("D"):
            continue
        try:
            key = (parts[0], int(parts[1][1:]), parts[2].upper())
        except ValueError:
            continue
        if key in table and (best is None or table[key] > best):
            best = table[key]
    return best


async def _advice(state: State) -> tuple[list[AdvisedAlpha], dict[str, Any]]:
    """The advisor's output, with the base-payment levers applied, plus the figures they used."""
    found = await submittable_alphas(state)
    rows = {a.alpha_id: a.model_dump(by_alias=True) for a in found if a.alpha_id}
    async with state.db.session() as session:
        marked = {str(a) for a in (await session.scalars(select(Submission.alpha_id))).all()}
    submitted = await _submitted_rows(state)
    locked = sorted(marked | {str(r["alpha_id"]) for r in submitted})
    candidates = [r for r in rows.values() if not r.get("submitted")]
    days = await state.alphas.daily_pnl(
        list(dict.fromkeys([*(str(r["alphaId"]) for r in candidates), *locked]))
    )

    now = datetime.now(UTC)
    shares = base_payment.context(submitted, now)
    table = await _multipliers(state)
    ids = [str(r["alphaId"]) for r in candidates]
    stored = await state.alphas.by_ids(ids) if ids else {}
    adjustments: dict[str, tuple[float, list[str], list[str]]] = {}
    multipliers: dict[str, float | None] = {}
    for r in candidates:
        alpha_id = str(r["alphaId"])
        vault = stored.get(alpha_id) or {}
        multiplier = _multiplier_of(json_list(vault.get("pyramids")), table)
        multipliers[alpha_id] = multiplier
        retention = base_payment.sub_universe_retention(
            checks_of(vault.get("checks")),
            base_payment.num(r.get("sharpe")),
        )
        adjustments[alpha_id] = base_payment.value_adjustment(r, shares, multiplier, retention)

    advice = await asyncio.to_thread(
        submit_advisor.advise, candidates, days, locked, adjustments=adjustments
    )
    out: list[AdvisedAlpha] = []
    for item in advice:
        raw = rows[item.alpha_id]
        out.append(
            AdvisedAlpha.model_validate(
                raw
                | {
                    "alphaId": item.alpha_id,
                    "brainUrl": f"{PLATFORM_ALPHA_URL}{item.alpha_id}",
                    "status": item.status,
                    "score": item.score,
                    "rank": item.rank,
                    "strengths": item.strengths,
                    "cautions": item.cautions,
                    "similarTo": item.similar_to,
                    "correlation": item.correlation,
                    "maxCorrelation": item.max_correlation,
                    "multiplier": multipliers.get(item.alpha_id),
                }
            )
        )
    return out, {"rows": rows, "submitted": submitted, "shares": shares, "now": now}


@router.get("/submit-advisor")
async def advise_submissions(state: State) -> SubmitAdvice:
    """Which Alphas from every task to submit, ranked, with the reason for each.

    Read-only: it spends no simulation and asks BRAIN nothing about the Alphas. It judges
    what the vault already holds, strictly -- a check still ``PENDING`` is not agreement --
    then nudges the score by the base-payment levers (theme multiplier, margin, sub-universe
    retention, concentration), which rank but never override a check.
    """
    out, ctx = await _advice(state)
    count = {s: sum(1 for a in out if a.status == s) for s in submit_advisor.STATUSES}
    return SubmitAdvice(
        submitted=sum(1 for r in ctx["rows"].values() if r.get("submitted")),
        considered=len(out),
        alphas=out,
        **count,
    )


# --- Base Payment ------------------------------------------------------------


@router.get("/base-payment")
async def base_payment_report(state: State) -> dict[str, Any]:
    """Everything the base-payment articles tell a consultant to watch, from the account's
    own submissions: today's allowance, diversity, growth, an Osmosis proposal, today's picks
    and a checklist. Read-only."""
    advised, ctx = await _advice(state)
    submitted: list[dict[str, Any]] = ctx["submitted"]
    now: datetime = ctx["now"]

    qty = base_payment.quantity(submitted, now)
    div = base_payment.diversity(submitted, now)
    grow = base_payment.growth(submitted, now)
    days = await state.alphas.daily_pnl([str(r["alpha_id"]) for r in submitted])
    osm = await asyncio.to_thread(base_payment.osmosis, submitted, days, now)

    recommended = [
        {
            "alphaId": a.alpha_id,
            "brainUrl": a.brain_url,
            "score": a.score,
            "rank": a.rank,
            "settings": a.settings,
            "multiplier": a.multiplier,
        }
        for a in advised
        if a.status == "recommended"
    ]
    plan = base_payment.daily_plan(recommended, qty)

    recent = base_payment.recent_rows(submitted, now, 30)
    table = await _multipliers(state)
    today = qty["today"]
    themed: bool | None = None
    if table:
        themed = any(
            (m := _multiplier_of(r["pyramids"], table)) is not None
            and m > 1.0
            and (md := base_payment.as_platform_time(r.get("date_submitted"))) is not None
            and md.date().isoformat() == today
            for r in submitted
        )
    return {
        "quantity": qty,
        "diversity": div,
        "growth": grow,
        "osmosis": osm,
        "plan": plan,
        "checklist": base_payment.checklist(qty, div, grow, osm, recent, themed),
        "multipliersKnown": bool(table),
    }


class RobustnessRequest(BaseModel):
    alpha_id: str = Field(min_length=1, max_length=64, alias="alphaId")
    cores: int = Field(default=DEFAULT_SLOTS, ge=1)

    model_config = {"populate_by_name": True}


#: ``rank`` and ``sign`` of the same Alpha: "confirm the alpha still performs under rank(Alpha)
#: and sign(Alpha)". An Alpha whose edge vanishes under either is leaning on the exact size of
#: its values, which the Value Factor's out-of-sample combination will not reward.
ROBUSTNESS_RECIPES = (
    correlation_breaker.Recipe(
        id="robust_rank",
        name="rank(Alpha)",
        why="Keeps only the ordering of the signal.",
        setup=(),
        tail="rank(alpha)",
        operators=("rank",),
    ),
    correlation_breaker.Recipe(
        id="robust_sign",
        name="sign(Alpha)",
        why="Keeps only the direction of the signal.",
        setup=(),
        tail="sign(alpha)",
        operators=("sign",),
    ),
)


@router.post("/base-payment/robustness", status_code=201)
async def robustness_task(body: RobustnessRequest, state: State) -> AddedTask:
    """Queue ``rank(Alpha)`` and ``sign(Alpha)`` at the Alpha's own settings: two simulations."""
    if body.cores > state.engine.slots:
        raise refuse(
            422,
            "too_many_cores",
            f"The engine has {state.engine.slots} slots, so a task cannot hold {body.cores}.",
        )
    found = await correlation_breaker.plan(state, body.alpha_id.strip())
    if found["problems"]:
        raise refuse(422, "robustness_blocked", found["problems"][0])
    compressed = correlation_breaker.compress(found["expression"])
    requests = correlation_breaker.requests(compressed, ROBUSTNESS_RECIPES, found["rawSettings"])
    settings = found["settings"]
    return await add_study(
        state,
        now=utcnow(),
        sampler=CORRELATION_BREAKER,
        params=BreakerParams(
            region=str(settings["region"] or ""),
            delay=int(settings["delay"] or 0),
            alpha_id=body.alpha_id,
            universe=str(settings["universe"] or ""),
            neutralization=str(settings["neutralization"] or ""),
            decay=int(settings["decay"] or 0),
            truncation=float(settings["truncation"] or 0.08),
            recipes=[r.id for r in ROBUSTNESS_RECIPES],
            cores=body.cores,
        ),
        simulations=len(requests),
        batch_size=(body.cores + 1) * MAX_BATCH,
        template_source=found["expression"],
        template_name=f"Robustness Check · {body.alpha_id}",
        seeds=settings_sampler.seed_trials(requests),
    )


# --- Correlation Breaker ----------------------------------------------------


class BreakerRecipe(Out):
    id: str
    name: str
    why: str
    #: What it costs, when it costs something worth knowing before running it.
    caution: str
    #: Only the re-shape, written against ``alpha``; the binding is shown once, above.
    transform: str
    #: Why it cannot run here, empty when it can.
    blocked: str
    #: The whole program, counted the way Power Pool counts it.
    operators: int
    data_fields: int
    #: For a Power Pool Alpha, the limit this re-shape would cross; empty when none is.
    over_power_pool: str


class BreakerPlan(Out):
    alpha_id: str
    expression: str
    #: The source Alpha reduced to ``alpha``, shown once above the re-shapes.
    bound: str
    #: What every simulation runs at: the source Alpha's own, never varied.
    settings: AlphaSettings
    #: BRAIN's own production-correlation check, as it last reported it.
    correlation: dict[str, Any] | None
    #: BRAIN judges it as a Power Pool Alpha, so its re-shapes are held to the pool's limits.
    power_pool: bool
    #: The source expression's own counts; null when it could not be read.
    operators: int | None
    data_fields: int | None
    recipes: list[BreakerRecipe]
    problems: list[str]


class BreakerRequest(BaseModel):
    alpha_id: str = Field(min_length=1, max_length=64, alias="alphaId")
    #: Which recipes to run; empty means every one the plan offers.
    recipes: list[str] = Field(default_factory=list, max_length=50)
    cores: int = Field(default=DEFAULT_SLOTS, ge=1)

    model_config = {"populate_by_name": True}


@router.post("/correlation-breaker/preview")
async def breaker_preview(body: BreakerRequest, state: State) -> BreakerPlan:
    """The Alpha, the settings its re-shapes will hold, and every recipe's expression.

    Free: reads the Alpha and the catalog, simulates nothing.
    """
    return BreakerPlan.model_validate(await correlation_breaker.plan(state, body.alpha_id.strip()))


@router.post("/correlation-breaker/tasks")
async def breaker_task(body: BreakerRequest, state: State) -> AddedTask:
    """Queue one simulation per chosen recipe, every one at the Alpha's own settings."""
    if body.cores > state.engine.slots:
        raise refuse(
            422,
            "too_many_cores",
            f"The engine has {state.engine.slots} slots, so a task cannot hold {body.cores}.",
        )
    found = await correlation_breaker.plan(state, body.alpha_id.strip())
    if found["problems"]:
        raise refuse(422, "correlation_breaker_blocked", found["problems"][0])

    wanted = set(body.recipes)
    # A recipe the plan marked blocked is never queued, whether or not it was asked for.
    runnable = {r["id"] for r in found["recipes"] if not r["blocked"]}
    chosen = [r for r in found["recipeSet"] if r.id in runnable and (not wanted or r.id in wanted)]
    if not chosen:
        raise refuse(
            422,
            "no_simulations",
            "Nothing to run: every chosen re-shape needs an operator or a field this market "
            "does not have.",
        )

    compressed = correlation_breaker.compress(found["expression"])
    requests = correlation_breaker.requests(compressed, chosen, found["rawSettings"])
    settings = found["settings"]
    return await add_study(
        state,
        now=utcnow(),
        sampler=CORRELATION_BREAKER,
        params=BreakerParams(
            region=str(settings["region"] or ""),
            delay=int(settings["delay"] or 0),
            alpha_id=body.alpha_id,
            universe=str(settings["universe"] or ""),
            neutralization=str(settings["neutralization"] or ""),
            decay=int(settings["decay"] or 0),
            truncation=float(settings["truncation"] or 0.08),
            recipes=[r.id for r in chosen],
            cores=body.cores,
        ),
        simulations=len(requests),
        batch_size=(body.cores + 1) * MAX_BATCH,
        template_source=found["expression"],
        template_name=f"Correlation Breaker · {body.alpha_id}",
        seeds=settings_sampler.seed_trials(requests),
    )


# --- Leg Gate ---------------------------------------------------------------


class LegGateStart(leg_gate.LegGatePlan):
    cores: int = Field(default=DEFAULT_SLOTS, ge=1)


def _leg_gate_view(plan: leg_gate.LegGatePlan) -> dict[str, Any]:
    """The staged simulations the plan would run, and every reason it cannot yet."""
    stmts, problems = leg_gate.check(plan)
    view: dict[str, Any] = {
        "problems": problems,
        "assigned": leg_gate.assigned(stmts),
        "slots": leg_gate.slots(plan.template),
        "combineCandidates": leg_gate.detect_combine(stmts, plan.legs) if stmts else [],
        "sims": [],
        "combined": None,
        "missing": [],
        "quota": leg_gate.quota(plan) if plan.legs and plan.signs else None,
    }
    if problems:
        return view
    sims = leg_gate.stage_one(plan, stmts)
    kept = [(leg, plan.signs[0]) for leg in plan.legs]
    combined = leg_gate.stage_two(plan, stmts, kept)
    view["sims"] = [{"leg": s.leg, "sign": s.sign, "expression": s.expression} for s in sims]
    view["combined"] = combined
    view["missing"] = leg_gate.missing_slots(plan, stmts)
    if not view["missing"]:
        view["problems"] = leg_gate.parse_problems([s.expression for s in sims] + [combined])
    return view


@router.post("/leg-gate/preview")
async def leg_gate_preview(body: leg_gate.LegGatePlan) -> dict[str, Any]:
    """What a multi-leg template would simulate, and what it would cost. Spends nothing."""
    return _leg_gate_view(body)


@router.post("/leg-gate/tasks", status_code=201)
async def leg_gate_task(body: LegGateStart, state: State) -> AddedTask:
    """Queue stage 1: each leg alone at each sign. Stage 2 follows only if the gates allow."""
    if body.cores > state.engine.slots:
        raise refuse(
            422,
            "too_many_cores",
            f"The engine has {state.engine.slots} slots, so a task cannot hold {body.cores}.",
        )
    plan = leg_gate.LegGatePlan.model_validate(body.model_dump(exclude={"cores"}))
    view = _leg_gate_view(plan)
    if view["problems"]:
        raise refuse(422, "leg_gate_invalid", view["problems"][0])
    if view["missing"]:
        raise refuse(
            422,
            "leg_gate_missing_slots",
            "Give a value for: " + ", ".join(f"${m}" for m in view["missing"]) + ".",
        )
    stmts, _ = leg_gate.check(plan)
    sims = leg_gate.stage_one(plan, stmts)
    requests = leg_gate.build_stage_one_requests(plan, sims)
    return await add_study(
        state,
        now=utcnow(),
        sampler=LEG_GATE,
        params=LegGateParams(
            region=plan.settings.region,
            delay=plan.settings.delay,
            cores=body.cores,
            stage=1,
            plan=plan.model_dump(by_alias=False),
            trial_map=[{"leg": s.leg, "sign": s.sign} for s in sims],
            auto_continue=plan.auto_continue,
        ),
        simulations=len(requests),
        batch_size=(body.cores + 1) * MAX_BATCH,
        template_source=plan.template,
        template_name="Leg Gate",
        seeds=settings_sampler.seed_trials(requests),
    )


@router.get("/leg-gate/runs")
async def leg_gate_runs(state: State) -> list[dict[str, Any]]:
    """Recent Leg Gate tasks, newest first."""
    return await leg_gate.runs(state)


@router.get("/leg-gate/{study_id}")
async def leg_gate_evaluation(study_id: int, state: State) -> dict[str, Any]:
    """The gates applied to a Leg Gate task's finished simulations so far. Reads only."""
    try:
        return await leg_gate.evaluate(state, study_id)
    except LookupError:
        raise refuse(
            404, "not_a_leg_gate_task", f"Task {study_id} is not a Leg Gate task."
        ) from None


@router.post("/leg-gate/{study_id}/stage-two", status_code=201)
async def leg_gate_stage_two(study_id: int, state: State) -> AddedTask:
    """Queue the combination now, if stage 1 finished and enough legs passed."""
    try:
        return await leg_gate.queue_stage_two(state, study_id)
    except LookupError:
        raise refuse(
            404, "not_a_leg_gate_task", f"Task {study_id} is not a Leg Gate task."
        ) from None
    except leg_gate.NotReady as exc:
        raise refuse(409, "stage_two_not_ready", str(exc)) from None
