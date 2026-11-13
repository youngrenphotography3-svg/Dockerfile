"""Leg Gate: try a template of two or more legs without paying to simulate every combination.

A template built from several data legs (say ``fundamental6_leg`` and ``other335_leg``, summed
with weights) is tried like this by hand:

1. **Stage 1.** Simulate each leg alone, once per sign: ``legs x signs`` simulations.
2. **Gate.** Keep a leg only if one of its signs clears a Sharpe floor and is positive in every
   year that matters (e.g. 2022 and 2023).
3. **Stage 2.** Simulate the combination once, each kept leg at its winning sign, the strongest
   at the biggest weight. Judge it against Sharpe, last-two-year Sharpe and sub-universe margin.

Done by hand the combination is often simulated before it is known whether the legs work, or
once per sign pattern. Here stage 1 runs, the gates decide, and stage 2 is queued only when
enough legs passed: when they did not, the combination is never simulated.

Every number is a field of :class:`Gates` or the plan, none is fixed. The pure functions here
read plain data, so each step is testable without BRAIN; the I/O at the foot reads trials and
BRAIN's own figures for them.
"""

import asyncio
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import Field
from sqlalchemy import select

from ..brain.schemas import SimulationRequest, SimulationSettings
from ..db.models import Study, StudyStatus, Trial, TrialState
from ..engine.packer import MAX_BATCH
from ..labs.fastexpr import ParseError, parse
from ..labs.launch import add_study
from ..labs.params import LEG_GATE, LegGateParams
from ..schemas import Out
from ..tools.settings_sampler import seed_trials

log = structlog.get_logger(__name__)

#: Slots are written ``$name``, as in the Template Lab.
SLOT = re.compile(r"\$([A-Za-z_]\w*)")
ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)\s*=(?!=)\s*(.+)$", re.DOTALL)
IDENT = re.compile(r"[A-Za-z_]\w*")
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

MAX_LEGS = 6
SIGNS = (1, -1)
#: Cap on cached BRAIN reads of finished Alphas, whose in-sample figures never change.
CACHE_SIZE = 256
#: How often, and how long apart, to look again when BRAIN has not yet reported a finished
#: simulation's figures.
RETRIES = 6
RETRY_SECONDS = 20.0

#: Check names BRAIN reports.
SUB_UNIVERSE = "LOW_SUB_UNIVERSE_SHARPE"
TWO_YEAR = "LOW_2Y_SHARPE"


# -- the plan -------------------------------------------------------------


class Gates(Out):
    """Every threshold, with the defaults of the hand-run method. All adjustable."""

    #: Stage 1: a leg's Sharpe, alone, at its better sign.
    leg_min_sharpe: float = 1.2
    #: Stage 1: years in which the leg must earn (Sharpe above ``year_min_sharpe``).
    required_years: list[int] = Field(default_factory=lambda: [2022, 2023])
    year_min_sharpe: float = 0.0
    #: How many legs must pass before the combination is worth simulating.
    min_legs: int = 2
    #: Stage 2 weights, strongest leg first; the last repeats for any further leg.
    weights: list[float] = Field(default_factory=lambda: [1.5, 0.5])
    #: Stage 2: the combination's Sharpe and last-two-year Sharpe.
    combo_min_sharpe: float = 1.7
    combo_min_recent_sharpe: float = 1.8
    #: Stage 2: sub-universe Sharpe must clear BRAIN's cutoff by this fraction of the cutoff.
    sub_universe_margin: float = 0.10


class PlanSettings(Out):
    region: str
    delay: int
    universe: str
    neutralization: str = "NONE"
    decay: int = 0
    truncation: float = 0.08


class LegGatePlan(Out):
    """What the screen sends: the template, a value for every slot, and how to stage it."""

    template: str = Field(min_length=1, max_length=8000)
    #: A value for each ``$slot`` the staged expressions read. Typed as text, as written.
    values: dict[str, str] = Field(default_factory=dict)
    #: The assigned names that are legs, and the one assignment that sums them.
    legs: list[str] = Field(default_factory=list)
    combine: str | None = None
    #: The slot in the combine that carries each leg's signed weight.
    weight_slots: dict[str, str] = Field(default_factory=dict)
    signs: list[int] = Field(default_factory=lambda: list(SIGNS))
    settings: PlanSettings
    gates: Gates = Field(default_factory=Gates)
    #: Queue stage 2 by itself when stage 1 finishes and enough legs passed.
    auto_continue: bool = True


@dataclass(frozen=True)
class Statement:
    name: str | None
    body: str


def statements(template: str) -> list[Statement]:
    """The template's ``;``-separated statements, comments removed, brackets respected."""
    text = COMMENT.sub(" ", template)
    parts: list[str] = []
    depth, quote, start = 0, "", 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == ";" and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    out: list[Statement] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        found = ASSIGNMENT.match(part)
        out.append(
            Statement(found.group(1), found.group(2).strip()) if found else Statement(None, part)
        )
    return out


def slots(template: str) -> list[str]:
    """Every ``$slot`` the template reads, in order of first use."""
    return list(dict.fromkeys(SLOT.findall(COMMENT.sub(" ", template))))


def assigned(stmts: list[Statement]) -> list[str]:
    return [s.name for s in stmts if s.name]


def fill(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """``text`` with each ``$slot`` replaced, and the slots that had no value."""
    missing: list[str] = []

    def put(found: re.Match[str]) -> str:
        name = found.group(1)
        value = (values.get(name) or "").strip()
        if not value:
            missing.append(name)
            return found.group(0)
        return value

    return SLOT.sub(put, text), list(dict.fromkeys(missing))


def detect_combine(stmts: list[Statement], legs: list[str]) -> list[str]:
    """Assignments that read at least two legs: the candidates for the one that sums them."""
    wanted = set(legs)
    return [
        s.name
        for s in stmts
        if s.name and s.name not in wanted and len(wanted & set(IDENT.findall(s.body))) >= 2
    ]


def _reachable(stmts: list[Statement]) -> list[Statement]:
    """Statements the last one needs, in their written order."""
    by_name = {s.name: s for s in stmts if s.name}
    need: set[str] = set()
    stack = [stmts[-1]]
    while stack:
        for ident in IDENT.findall(stack.pop().body):
            if ident in by_name and ident not in need:
                need.add(ident)
                stack.append(by_name[ident])
    return [s for s in stmts[:-1] if s.name in need] + [stmts[-1]]


def _write(stmts: list[Statement]) -> str:
    lines = [f"{s.name} = {s.body};" if s.name else s.body for s in stmts[:-1]]
    last = stmts[-1]
    lines.append(f"{last.name} = {last.body}" if last.name else last.body)
    return "\n".join(lines)


def solo(stmts: list[Statement], combine: str, leg: str, sign: int) -> str:
    """The template with its combine reduced to one leg at one sign, unused legs dropped.

    The final line is the template's own, so the leg is judged under the same decay,
    truncation and neutralization the combination will use.
    """
    swapped = [
        Statement(s.name, f"multiply({sign}, {leg})") if s.name == combine else s for s in stmts
    ]
    return _write(_reachable(swapped))


def combined(stmts: list[Statement]) -> str:
    return _write(stmts)


def weight_values(plan: LegGatePlan, kept: list[tuple[str, int]]) -> dict[str, str]:
    """Each leg's weight slot set: ``kept`` is ``(leg, sign)`` strongest first, the rest zero."""
    weights = plan.gates.weights or [1.0]
    out = {plan.weight_slots[leg]: "0" for leg in plan.legs if leg in plan.weight_slots}
    for rank, (leg, sign) in enumerate(kept):
        weight = weights[min(rank, len(weights) - 1)]
        out[plan.weight_slots[leg]] = f"{sign * weight:g}"
    return out


def check(plan: LegGatePlan) -> tuple[list[Statement], list[str]]:
    """The parsed statements and every reason the plan cannot run, in words."""
    problems: list[str] = []
    stmts = statements(plan.template)
    if len(stmts) < 2:
        return stmts, ["The template needs at least the legs and a final line."]
    names = assigned(stmts)
    if len(plan.legs) < 2:
        problems.append("Choose at least two legs.")
    if len(plan.legs) > MAX_LEGS:
        problems.append(f"At most {MAX_LEGS} legs.")
    problems += [f"{leg} is not assigned in the template." for leg in plan.legs if leg not in names]
    if not plan.combine or plan.combine not in names:
        problems.append("Choose the assignment that sums the legs.")
    elif plan.combine in plan.legs:
        problems.append("The combine cannot be one of the legs.")
    if stmts[-1].name in plan.legs:
        problems.append("The last line is the Alpha: it cannot be a leg.")
    if not plan.signs or any(s not in SIGNS for s in plan.signs):
        problems.append("Signs are +1 and/or -1.")
    for leg in plan.legs:
        slot = plan.weight_slots.get(leg)
        if not slot:
            problems.append(f"Choose the weight slot for {leg}.")
        elif plan.combine in names:
            body = next(s.body for s in stmts if s.name == plan.combine)
            if slot not in SLOT.findall(body):
                problems.append(f"${slot} is not read by {plan.combine}.")
    if plan.gates.min_legs > len(plan.legs):
        problems.append("Gates: more legs must pass than there are legs.")
    if any(w <= 0 for w in plan.gates.weights):
        problems.append("Gates: weights must be above zero.")
    return stmts, problems


@dataclass(frozen=True)
class Sim:
    leg: str
    sign: int
    expression: str


def stage_one(plan: LegGatePlan, stmts: list[Statement]) -> list[Sim]:
    """Every leg alone at every sign, each filled with the plan's values."""
    out: list[Sim] = []
    for leg in plan.legs:
        for sign in plan.signs:
            text, _ = fill(solo(stmts, plan.combine or "", leg, sign), plan.values)
            out.append(Sim(leg, sign, text))
    return out


def stage_two(plan: LegGatePlan, stmts: list[Statement], kept: list[tuple[str, int]]) -> str:
    text, _ = fill(combined(stmts), plan.values | weight_values(plan, kept))
    return text


def missing_slots(plan: LegGatePlan, stmts: list[Statement]) -> list[str]:
    """Slots a staged expression reads that have no value: what to fill before queueing."""
    out: list[str] = []
    for leg in plan.legs:
        for sign in plan.signs[:1] or SIGNS[:1]:
            _, missing = fill(solo(stmts, plan.combine or "", leg, sign), plan.values)
            out += missing
    base = {plan.weight_slots[leg]: "1" for leg in plan.legs if leg in plan.weight_slots}
    _, missing = fill(combined(stmts), plan.values | base)
    out += missing
    return list(dict.fromkeys(out))


def parse_problems(expressions: list[str]) -> list[str]:
    out: list[str] = []
    for text in expressions:
        try:
            parse(text)
        except ParseError as exc:
            out.append(f"{exc}")
            break
    return out


def quota(plan: LegGatePlan) -> dict[str, int]:
    """Simulations each outcome costs, against trying every sign pattern of the combination."""
    legs, signs = len(plan.legs), len(plan.signs)
    first = legs * signs
    return {
        "stageOne": first,
        "stageTwo": 1,
        "mostSpent": first + 1,
        "leastSpent": first,
        "everySignPattern": signs**legs,
    }


# -- judging ----------------------------------------------------------------


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def year_sharpes(recordset: dict[str, Any]) -> dict[int, float]:
    """Per-year Sharpe from BRAIN's ``yearly-stats`` record set."""
    names = [str(p.get("name")) for p in (recordset.get("schema") or {}).get("properties") or []]
    if "year" not in names or "sharpe" not in names:
        return {}
    at_year, at_sharpe = names.index("year"), names.index("sharpe")
    out: dict[int, float] = {}
    for row in recordset.get("records") or []:
        if not row or at_year >= len(row) or at_sharpe >= len(row):
            continue
        try:
            year = int(str(row[at_year])[:4])
        except ValueError:
            continue
        value = _number(row[at_sharpe])
        if value is not None:
            out[year] = value
    return out


def check_of(checks: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for item in checks:
        if str(item.get("name", "")).upper() == name:
            return item
    return None


def judge_leg(result: dict[str, Any], gates: Gates) -> tuple[bool, list[str]]:
    """Whether one leg at one sign clears the stage-1 gate, with the reasons it does not."""
    reasons: list[str] = []
    sharpe = result.get("sharpe")
    if sharpe is None:
        return False, ["No Sharpe reported"]
    if sharpe < gates.leg_min_sharpe:
        reasons.append(f"Sharpe {sharpe:.2f} is under {gates.leg_min_sharpe:g}")
    years: dict[int, float] = result.get("years") or {}
    for year in gates.required_years:
        value = years.get(year)
        if value is None:
            reasons.append(f"No {year} row")
        elif value <= gates.year_min_sharpe:
            reasons.append(f"{year} Sharpe {value:.2f} is not above {gates.year_min_sharpe:g}")
    return not reasons, reasons


def judge_stage_one(rows: list[dict[str, Any]], plan: LegGatePlan) -> dict[str, Any]:
    """Per leg, its better passing sign; and whether enough legs passed to go on.

    ``rows`` carry ``leg``, ``sign``, ``state``, ``alphaId``, ``sharpe`` and ``years``.
    """
    gates = plan.gates
    waiting = any(r["state"] not in {"COMPLETE", "FAIL", "PRUNED"} for r in rows)
    legs: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for leg in plan.legs:
        mine = [r for r in rows if r["leg"] == leg]
        scored = []
        for r in mine:
            ok, reasons = (
                judge_leg(r, gates) if r["state"] == "COMPLETE" else (False, ["Not finished"])
            )
            scored.append(r | {"passed": ok, "reasons": reasons})
        winners = sorted((s for s in scored if s["passed"]), key=lambda s: -(s["sharpe"] or 0.0))
        entry = {"leg": leg, "results": scored, "winner": winners[0]["sign"] if winners else None}
        entry["verdict"] = "keep" if winners else ("waiting" if waiting else "drop")
        legs.append(entry)
        if winners:
            kept.append({"leg": leg, "sign": winners[0]["sign"], "sharpe": winners[0]["sharpe"]})
    kept.sort(key=lambda k: -(k["sharpe"] or 0.0))
    go = not waiting and len(kept) >= gates.min_legs
    first = len(plan.signs) * len(plan.legs)
    # The combination is the one simulation a failed gate saves.
    saved = 1 if not waiting and not go else 0
    if waiting:
        summary = "Stage 1 is still running."
    elif go:
        summary = f"{len(kept)} of {len(plan.legs)} legs passed: stage 2 is worth simulating."
    else:
        summary = (
            f"{len(kept)} of {len(plan.legs)} legs passed and {gates.min_legs} are needed: "
            f"stage 2 is not run. Spent {first} simulations."
        )
    return {
        "legs": legs,
        "kept": kept,
        "waiting": waiting,
        "go": go,
        "summary": summary,
        "avoidedSimulations": saved,
    }


def judge_stage_two(row: dict[str, Any], plan: LegGatePlan) -> dict[str, Any]:
    """The combination against the combo gates."""
    gates = plan.gates
    out: list[dict[str, Any]] = []

    def gate(name: str, ok: bool, detail: str) -> None:
        out.append({"gate": name, "ok": ok, "detail": detail})

    sharpe, recent = row.get("sharpe"), row.get("recentSharpe")
    gate(
        f"Sharpe at least {gates.combo_min_sharpe:g}",
        sharpe is not None and sharpe >= gates.combo_min_sharpe,
        "Not reported" if sharpe is None else f"{sharpe:.2f}",
    )
    gate(
        f"Last-2-year Sharpe at least {gates.combo_min_recent_sharpe:g}",
        recent is not None and recent >= gates.combo_min_recent_sharpe,
        "Not reported" if recent is None else f"{recent:.2f}",
    )
    sub = row.get("subUniverse")
    if sub is None:
        gate("Sub-universe passes with margin", False, "Not reported")
    else:
        value, limit = _number(sub.get("value")), _number(sub.get("limit"))
        passed = str(sub.get("result", "")).upper() == "PASS"
        if value is None or not limit:
            gate("Sub-universe passes with margin", passed, str(sub.get("result", "")))
        else:
            margin = value / limit - 1
            gate(
                f"Sub-universe passes with {gates.sub_universe_margin:.0%} margin",
                passed and margin >= gates.sub_universe_margin,
                f"{value:.2f} against {limit:.2f} ({margin:+.0%})",
            )
    ok = all(g["ok"] for g in out)
    return {"gates": out, "verdict": "pass" if ok else "fail"}


# -- reading trials and BRAIN ----------------------------------------------

_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()


async def _brain_figures(state: Any, alpha_id: str) -> dict[str, Any]:
    """BRAIN's own in-sample figures for one finished Alpha; fixed once simulated."""
    if alpha_id in _cache:
        _cache.move_to_end(alpha_id)
        return _cache[alpha_id]
    body, yearly = await asyncio.gather(
        state.endpoints.alpha_body(alpha_id),
        state.endpoints.recordset_body(alpha_id, "yearly-stats"),
    )
    stats = (body.get("is") or {}) if isinstance(body, dict) else {}
    checks = stats.get("checks") or []
    recent = check_of(checks, TWO_YEAR)
    figures = {
        "sharpe": _number(stats.get("sharpe")),
        "fitness": _number(stats.get("fitness")),
        "turnover": _number(stats.get("turnover")),
        "years": year_sharpes(yearly),
        "recentSharpe": _number(recent.get("value")) if recent else None,
        "subUniverse": check_of(checks, SUB_UNIVERSE),
    }
    # Only a finished read is worth keeping: an empty one means BRAIN was not ready.
    if figures["sharpe"] is not None:
        _cache[alpha_id] = figures
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return figures


async def _trials(state: Any, study_id: int) -> tuple[Study | None, list[Trial]]:
    async with state.db.session() as session:
        study = await session.get(Study, study_id)
        rows = (
            await session.scalars(
                select(Trial).where(Trial.study_id == study_id).order_by(Trial.number)
            )
        ).all()
    return study, list(rows)


async def evaluate(state: Any, study_id: int) -> dict[str, Any]:
    """What a Leg Gate task has found so far, with what to do next. Reads; spends nothing."""
    study, trials = await _trials(state, study_id)
    if study is None or study.sampler != LEG_GATE:
        raise LookupError(study_id)
    params = LegGateParams.model_validate(study.sampler_params or {})
    plan = LegGatePlan.model_validate(params.plan)
    mapping = params.trial_map

    figures = await asyncio.gather(
        *[
            _brain_figures(state, t.alpha_id)
            if t.alpha_id and t.state == TrialState.COMPLETE
            else _none()
            for t in trials
        ]
    )
    rows = []
    for t, f in zip(trials, figures, strict=True):
        info = mapping[t.number] if t.number < len(mapping) else {"leg": "?", "sign": 0}
        rows.append(
            {
                "leg": info["leg"],
                "sign": info["sign"],
                "state": str(t.state),
                "alphaId": t.alpha_id,
                "expression": t.expression,
                "sharpe": f.get("sharpe"),
                "fitness": f.get("fitness"),
                "turnover": f.get("turnover"),
                "years": f.get("years") or {},
                "recentSharpe": f.get("recentSharpe"),
                "subUniverse": f.get("subUniverse"),
            }
        )

    child = await _child_of(state, study_id)
    out: dict[str, Any] = {
        "id": study_id,
        "name": study.name,
        "stage": params.stage,
        "status": str(study.status),
        "parent": params.parent,
        "child": child,
        "gates": plan.gates.model_dump(by_alias=True),
        "rows": rows,
    }
    if params.stage == 1:
        verdict = judge_stage_one(rows, plan)
        out["judgement"] = verdict
        out["canQueueStage2"] = bool(verdict["go"] and child is None)
    else:
        row = rows[0] if rows else {}
        done = bool(row) and row["state"] == "COMPLETE"
        out["judgement"] = (
            judge_stage_two(row, plan) if done else {"gates": [], "verdict": "waiting"}
        )
        out["canQueueStage2"] = False
    return out


async def _none() -> dict[str, Any]:
    return {}


async def _child_of(state: Any, study_id: int) -> int | None:
    async with state.db.session() as session:
        rows = (
            await session.execute(
                select(Study.id, Study.sampler_params).where(Study.sampler == LEG_GATE)
            )
        ).all()
    for found, stored in rows:
        if (stored or {}).get("parent") == study_id:
            return int(found)
    return None


async def runs(state: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Recent Leg Gate tasks, newest first, for the screen's list."""
    async with state.db.session() as session:
        rows = (
            await session.scalars(
                select(Study)
                .where(Study.sampler == LEG_GATE)
                .order_by(Study.id.desc())
                .limit(limit)
            )
        ).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "stage": int((r.sampler_params or {}).get("stage") or 1),
            "parent": (r.sampler_params or {}).get("parent"),
            "status": str(r.status),
            "message": r.message,
        }
        for r in rows
    ]


class NotReady(Exception):
    """Stage 2 cannot be queued yet, or at all; the message says why."""


async def queue_stage_two(state: Any, study_id: int, *, cores: int | None = None) -> Any:
    """Queue the combination, once, when stage 1's gates say it is worth simulating."""
    study, _ = await _trials(state, study_id)
    if study is None or study.sampler != LEG_GATE:
        raise LookupError(study_id)
    params = LegGateParams.model_validate(study.sampler_params or {})
    if params.stage != 1:
        raise NotReady("Only a stage 1 task leads to stage 2.")
    if (existing := await _child_of(state, study_id)) is not None:
        raise NotReady(f"Stage 2 is already queued as task {existing}.")

    found = await evaluate(state, study_id)
    verdict = found["judgement"]
    if verdict["waiting"]:
        raise NotReady("Stage 1 has not finished.")
    if not verdict["go"]:
        raise NotReady(verdict["summary"])

    plan = LegGatePlan.model_validate(params.plan)
    stmts, problems = check(plan)
    if problems:
        raise NotReady(problems[0])
    kept = [(k["leg"], int(k["sign"])) for k in verdict["kept"]]
    expression = stage_two(plan, stmts, kept)
    if rest := parse_problems([expression]):
        raise NotReady(rest[0])

    request = SimulationRequest(
        settings=SimulationSettings(
            region=plan.settings.region,
            universe=plan.settings.universe,
            delay=plan.settings.delay,
            decay=plan.settings.decay,
            neutralization=plan.settings.neutralization,
            truncation=plan.settings.truncation,
        ),
        regular=expression,
    )
    from datetime import UTC, datetime

    held = cores or params.cores
    return await add_study(
        state,
        now=datetime.now(UTC),
        sampler=LEG_GATE,
        params=LegGateParams(
            region=plan.settings.region,
            delay=plan.settings.delay,
            cores=held,
            stage=2,
            parent=study_id,
            plan=plan.model_dump(by_alias=False),
            trial_map=[{"leg": "combined", "sign": 0}],
            auto_continue=plan.auto_continue,
        ),
        simulations=1,
        batch_size=(held + 1) * MAX_BATCH,
        template_source=expression,
        template_name="Leg Gate · combined",
        seeds=seed_trials([request]),
        run=True,
    )


async def _say(state: Any, study_id: int, text: str) -> None:
    async with state.db.session() as session:
        row = await session.get(Study, study_id)
        if row is not None:
            row.message = text[:500]
            await session.commit()
    await state.optimizer.notify()


async def on_complete(state: Any, study: Study) -> None:
    """Called when a Leg Gate task completes: judge it, and go on to stage 2 if told to.

    Never raises: a failure here must not disturb the scheduler that called it.
    """
    try:
        params = LegGateParams.model_validate(study.sampler_params or {})
        found = await evaluate(state, study.id)
        for _ in range(RETRIES - 1):
            # BRAIN's figures for a just-finished Alpha can lag the status by a moment.
            if not any(r["state"] == "COMPLETE" and r["sharpe"] is None for r in found["rows"]):
                break
            await asyncio.sleep(RETRY_SECONDS)
            found = await evaluate(state, study.id)

        if params.stage == 1:
            verdict = found["judgement"]
            await _say(state, study.id, verdict["summary"])
            if verdict["go"] and params.auto_continue and found["canQueueStage2"]:
                added = await queue_stage_two(state, study.id)
                await _say(state, study.id, f"{verdict['summary']} Queued as task {added.id}.")
        else:
            verdict = found["judgement"]
            passed = [g for g in verdict["gates"] if g["ok"]]
            await _say(
                state,
                study.id,
                f"Combination {verdict['verdict'].upper()}: "
                f"{len(passed)} of {len(verdict['gates'])} gates cleared.",
            )
    except NotReady as exc:
        await _say(state, study.id, str(exc))
    except Exception:
        log.exception("leg_gate.on_complete_failed", study_id=study.id)


def build_stage_one_requests(plan: LegGatePlan, sims: list[Sim]) -> list[SimulationRequest]:
    settings = SimulationSettings(
        region=plan.settings.region,
        universe=plan.settings.universe,
        delay=plan.settings.delay,
        decay=plan.settings.decay,
        neutralization=plan.settings.neutralization,
        truncation=plan.settings.truncation,
    )
    return [SimulationRequest(settings=settings.model_copy(), regular=s.expression) for s in sims]


#: A finished task's status, for callers deciding whether to look.
FINISHED = (StudyStatus.COMPLETE, StudyStatus.FAILED)
