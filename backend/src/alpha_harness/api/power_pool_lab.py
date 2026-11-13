"""LLM Alpha Lab: datasets or fields, a model, cores and simulations, then add the task.

Two kinds of Alpha: Power Pool (few operators, few fields) and Regular (a consultant's
full-size Alpha with a stated reason). Nothing here calls the LLM or simulates; the preview
shows the exact first prompt. Alphas written for review wait here until approved.
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..catalog.queries import FieldFilter
from ..db.models import Study, Trial, TrialState, utcnow
from ..labs import fastexpr, power_pool, search
from ..labs.launch import (
    NO_NEUTRALIZATION,
    NO_SIMULATIONS,
    OPERATORS_UNREAD,
    AddedTask,
    account_operators,
    add_study,
    choices,
    legal_choices,
    synced_universes,
)
from ..labs.params import POWER_POOL_SAMPLER, PowerPoolParams
from ..llm.text import estimate_tokens
from ..schemas import Out
from .deps import State, refuse

router = APIRouter(prefix="/api/power-pool-lab", tags=["power-pool-lab"])


class PowerPoolRequest(BaseModel):
    region: str
    delay: int = Field(ge=0, le=1)
    universe: str
    dataset_ids: list[str] = Field(default_factory=list, max_length=50)
    model: str | None = None
    #: What the LLM draws from. Empty is refused: see ``NO_NEUTRALIZATION``.
    neutralizations: list[str] = Field(default_factory=list, max_length=20)
    #: The universes each Alpha is drawn from. Empty means every downloaded one.
    universes: list[str] = Field(default_factory=list, max_length=20)
    cores: int = Field(default=search.MAX_CORES, ge=1, le=search.MAX_CORES)
    simulations: int = Field(default=0, ge=0, le=search.MAX_SIMULATIONS)
    #: The Data Explorer's filter the datasets were chosen under: only fields it shows are used.
    field_filter: FieldFilter | None = None
    #: ``power_pool`` or ``regular``.
    mode: str = Field(default="power_pool", pattern="^(power_pool|regular)$")
    #: The fields ticked by hand. Empty means every field of the chosen datasets.
    field_ids: list[str] = Field(default_factory=list, max_length=500)
    #: Regular only: the fewest different operators, the most in all, the most data fields.
    min_operators: int = Field(default=10, ge=9, le=40)
    max_operators: int = Field(default=30, ge=10, le=80)
    max_fields: int = Field(default=4, ge=1, le=12)
    #: What the researcher wants explored, in their words.
    focus: str = Field(default="", max_length=1000)
    #: Hold every written Alpha until the researcher approves it.
    review: bool = False


class PowerPoolModel(Out):
    id: str
    #: How the request names it: provider and id together.
    ref: str
    provider: str
    remaining_today: int


class PowerPoolOptions(Out):
    models: list[PowerPoolModel]
    default_model: str | None
    max_simulations: int


class PowerPoolPrompt(Out):
    system: str
    user: str
    tokens: int


class PowerPoolPreview(Out):
    fields: int
    universes: list[str]
    neutralizations: list[str]
    llm_calls: int
    prompt: PowerPoolPrompt | None
    problems: list[str]
    warnings: list[str]


async def _models(state: Any) -> list[dict[str, Any]]:
    """Set-up models whose provider has an enabled Key, most requests left today first."""
    keys = [k for k in await state.llm.keys.list_keys() if k.enabled]
    out = []
    for m in state.llm.registry.all():
        mine = [k for k in keys if k.provider == m.provider]
        if not mine:
            continue
        left = sum(
            [
                (await state.llm.ledger.headroom(k.id, m, cap=k.daily_limit)).daily_remaining
                for k in mine
            ]
        )
        out.append({"id": m.id, "ref": m.ref, "provider": m.provider, "remainingToday": left})
    return sorted(out, key=lambda m: -m["remainingToday"])


@router.get("/options")
async def options(state: State) -> PowerPoolOptions:
    models = await _models(state)
    return PowerPoolOptions.model_validate(
        {
            "models": models,
            "defaultModel": models[0]["ref"] if models else None,
            "maxSimulations": search.MAX_SIMULATIONS,
        }
    )


async def _plan(body: PowerPoolRequest, state: Any) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []
    operators = await account_operators(state, refresh=False)
    if not operators:
        problems.append(OPERATORS_UNREAD)
    dataset_ids = list(body.dataset_ids)
    if body.field_ids and not dataset_ids:
        # Fields ticked without a dataset: the datasets they belong to are the datasets.
        marks = ", ".join("?" for _ in body.field_ids)
        found = await state.catalog.query(
            "SELECT DISTINCT dataset_id FROM data_field WHERE region = ? AND delay = ? "  # noqa: S608
            f"AND field_id IN ({marks})",
            [body.region, body.delay, *body.field_ids],
        )
        dataset_ids = sorted(str(r["dataset_id"]) for r in found)
    if not dataset_ids:
        problems.append("Choose at least one dataset, or at least one field.")
    if body.mode == power_pool.REGULAR and body.min_operators > body.max_operators:
        problems.append("The fewest operators can't be more than the most.")
    models = {m["ref"]: m for m in await _models(state)}
    ref = body.model or next(iter(models), "")
    info = state.llm.registry.get(ref)
    if not ref:
        problems.append("No model is set up. Set one up under LLM Integration › Models.")
    elif info is None:
        problems.append(f"{ref} is not set up. Set it up under LLM Integration › Models.")
    elif info.ref not in models:
        problems.append(f"{info.id} can't run: no enabled Key for it. Add one in LLM Integration.")

    schema = await state.metadata.cached_settings_schema()
    legal = legal_choices(schema, body.region, body.delay)
    universes = await synced_universes(state, legal, body.region, body.delay, body.universe)
    if body.universes and universes:
        chosen = [u for u in universes if u in set(body.universes)]
        if not chosen:
            problems.append(
                f"None of the chosen universes is downloaded for {body.region} delay "
                f"{body.delay}. Sync it in the Data Explorer, or choose another."
            )
        universes = chosen
    # The LLM draws from whichever the reader chose, in BRAIN's order.
    offered = [str(n) for n in choices(legal, "neutralization") if n != "NONE"]
    wanted = set(body.neutralizations)
    neutralizations = [n for n in offered if n in wanted]
    if not universes:
        problems.append(
            f"No {body.region} delay {body.delay} market is downloaded. "
            "Sync it in the Data Explorer."
        )
    if not body.neutralizations:
        problems.append(NO_NEUTRALIZATION)
    elif not offered:
        problems.append("BRAIN's settings list is not loaded. Sign in again.")
    elif not neutralizations:
        problems.append(f"BRAIN offers none of the chosen neutralizations in {body.region}.")

    fields = 0
    prompt = None
    run = PowerPoolParams(
        region=body.region,
        delay=body.delay,
        universes=universes,
        neutralizations=neutralizations,
        mode=body.mode,
        field_ids=body.field_ids,
        min_operators=body.min_operators,
        max_operators=body.max_operators,
        max_fields=body.max_fields,
        focus=body.focus,
        review=body.review,
    )
    if universes:
        for dataset in dataset_ids:
            ctx = await power_pool.context_for(
                state.catalog,
                body.region,
                body.delay,
                universes,
                dataset,
                body.field_filter,
                frozenset(body.field_ids) or None,
            )
            if ctx is None:
                problems.append(
                    f"{dataset} is not in the downloaded {body.region} delay {body.delay} catalog."
                )
                continue
            if body.field_ids and not ctx.own:
                problems.append(f"None of the fields you chose are in {dataset}.")
                continue
            if body.field_filter and not ctx.fields:
                problems.append(
                    f"No field in {dataset} matches the Data Explorer filter. "
                    "Untick it, or loosen the filter."
                )
            fields += len(ctx.fields)
            if prompt is None and info is not None and operators:
                user, shown = power_pool.user_prompt(
                    ctx, operators, run, "None yet.", 0, info.prompt_tokens
                )
                system = power_pool.system_for(body.mode)
                prompt = {
                    "system": system,
                    "user": user,
                    "tokens": estimate_tokens(system + user),
                }
                if ctx.fields and shown < min(10, len(ctx.fields)):
                    problems.append(
                        f"Only {shown} of {dataset}'s fields fit in {info.id}'s "
                        f"{info.prompt_tokens:,} prompt tokens, too few to work with. Raise its "
                        "Max Prompt Tokens, or choose another dataset."
                    )
    calls = -(-body.simulations // power_pool.per_call(body.mode))
    if body.review:
        warnings.append(
            "Review is on: every Alpha the LLM writes waits in the review list until you "
            "approve it, and only approved Alphas are simulated."
        )
    left = models[info.ref]["remainingToday"] if info and info.ref in models else None
    if info is not None and left is not None and calls > left:
        warnings.append(
            f"About {calls:,} LLM calls; {info.id} has {left:,} left today, "
            f"so the task waits for its day to reset at midnight, {info.reset_timezone}."
        )
    return {
        "fields": fields,
        "universes": universes,
        "neutralizations": neutralizations,
        "llmCalls": calls,
        "prompt": prompt,
        "problems": problems,
        "warnings": warnings,
        "model": info.ref if info else ref,
        "datasetIds": dataset_ids,
    }


@router.post("/preview")
async def preview(body: PowerPoolRequest, state: State) -> PowerPoolPreview:
    """What a task would send. Free: no LLM call, no simulation."""
    return PowerPoolPreview.model_validate(await _plan(body, state))


@router.post("/tasks", status_code=201)
async def add_task(body: PowerPoolRequest, state: State) -> AddedTask:
    if body.simulations < 1:
        raise refuse(422, "no_simulations", NO_SIMULATIONS)
    plan = await _plan(body, state)
    if plan["problems"]:
        raise refuse(422, "power_pool_blocked", plan["problems"][0])
    return await add_study(
        state,
        now=utcnow(),
        sampler=POWER_POOL_SAMPLER,
        params=PowerPoolParams(
            region=body.region,
            delay=body.delay,
            universe=body.universe,
            universes=plan["universes"],
            neutralizations=plan["neutralizations"],
            dataset_ids=plan["datasetIds"],
            mode=body.mode,
            field_ids=body.field_ids,
            min_operators=body.min_operators,
            max_operators=body.max_operators,
            max_fields=body.max_fields,
            focus=body.focus,
            review=body.review,
            field_filter=(
                body.field_filter.model_dump(mode="json", exclude_defaults=True)
                if body.field_filter
                else None
            ),
            model=plan["model"],
            cores=body.cores,
            llm={"calls": 0},
        ),
        simulations=body.simulations,
        batch_size=body.cores * 10,
        template_source=(
            "# LLM Alpha Lab writes its expressions with an LLM; there is no template."
        ),
    )


# --- the review list -----------------------------------------------------------


class ReviewItem(Out):
    trial_id: int
    task_id: int
    task: str
    mode: str
    dataset: str
    expression: str
    hypothesis: str
    #: Different operators, as a Regular Alpha is counted.
    operators: int
    universe: str | None
    neutralization: str | None
    decay: int | None


class ReviewList(Out):
    items: list[ReviewItem]


class ReviewDecision(BaseModel):
    approve: list[int] = Field(default_factory=list, max_length=500)
    reject: list[int] = Field(default_factory=list, max_length=500)


class Reviewed(Out):
    approved: int
    rejected: int


def _different(expression: str) -> int:
    try:
        return len(fastexpr.distinct_operators(fastexpr.parse(expression)))
    except fastexpr.ParseError:
        return 0


@router.get("/review")
async def review_list(state: State) -> ReviewList:
    """Every Alpha the LLM wrote for a review-on task and nobody has decided yet."""
    async with state.db.session() as session:
        rows = (
            await session.execute(
                select(Trial, Study)
                .join(Study, Study.id == Trial.study_id)
                .where(Trial.message == power_pool.AWAITING, Study.sampler == POWER_POOL_SAMPLER)
                .order_by(Trial.study_id, Trial.number)
                .limit(1000)
            )
        ).all()
    items = []
    for trial, study in rows:
        settings = trial.settings or {}
        items.append(
            {
                "trialId": trial.id,
                "taskId": study.id,
                "task": study.label or study.name,
                "mode": str((study.sampler_params or {}).get("mode") or "power_pool"),
                "dataset": str((trial.params or {}).get("dataset") or ""),
                "expression": trial.expression or "",
                "hypothesis": str((trial.params or {}).get("idea") or ""),
                "operators": _different(trial.expression or ""),
                "universe": settings.get("universe"),
                "neutralization": settings.get("neutralization"),
                "decay": settings.get("decay"),
            }
        )
    return ReviewList.model_validate({"items": items})


@router.post("/review")
async def review_decide(body: ReviewDecision, state: State) -> Reviewed:
    """Approve (the cores take them) or reject (the LLM is told why) held Alphas."""
    wanted = {*body.approve, *body.reject}
    approved = rejected = 0
    if not wanted:
        return Reviewed.model_validate({"approved": 0, "rejected": 0})
    async with state.db.session() as session:
        studies = sorted(
            {
                int(i)
                for i in (
                    await session.scalars(select(Trial.study_id).where(Trial.id.in_(wanted)))
                ).all()
            }
        )
    for study_id in studies:
        async with state.optimizer.lock(study_id), state.db.session() as session:
            trials = (
                await session.scalars(
                    select(Trial).where(
                        Trial.study_id == study_id,
                        Trial.id.in_(wanted),
                        Trial.state == TrialState.PRUNED,
                        Trial.message == power_pool.AWAITING,
                    )
                )
            ).all()
            for trial in trials:
                if trial.id in body.reject:
                    trial.params = {**(trial.params or {}), "rejected": True}
                    trial.message = "Rejected by you in review."
                    trial.finished_at = utcnow()
                    rejected += 1
                else:
                    trial.message = power_pool.PROPOSED
                    approved += 1
    await state.optimizer.notify()
    return Reviewed.model_validate({"approved": approved, "rejected": rejected})
