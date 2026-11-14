"""Base Payment: what BRAIN's base-payment articles say to do, turned into figures from the vault.

BRAIN pays base fees daily from five components: Quantity (submissions per day), Quality
(good, diverse, theme-matching Alphas), Self-Growth (getting better over time), the Scaling of
the first two against other consultants, and the Value Factor (what an Alpha adds to a combo
out of sample). The articles give advice, not formulas, so nothing here predicts a payment.
Every number is a count, a share or an average read off the account's own submissions, set
against a threshold the articles name:

* Quantity: at most 4 Alphas, 1 SuperAlpha and (Delay 0) 5 Alphas a month.
* Concentration: "if 50%+ of your alphas share a single region, similar turnover, the same
  datafields, or high correlation, Value Factor may suffer".
* Value Factor hygiene: margin above 4 bps, turnover below ~30%, at least 70% of the Sharpe
  kept in the sub-universe.
* Osmosis: three scopes (region x delay), each with exactly 100,000 points over at least ten
  Alphas.

Pure functions over plain dicts, so each is testable without a database.
"""

import math
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

PLATFORM_TZ = ZoneInfo("America/New_York")

#: Daily and monthly submission allowances.
DAILY_ALPHAS = 4
DAILY_SUPER = 1
MONTHLY_D0 = 5

#: "Retain at least 70% of Sharpe in the sub-universe or super-universe".
MIN_SUBUNIVERSE_RETENTION = 0.70
#: "Keep turnover < 30% and margin > 4 bps".
SOFT_TURNOVER = 0.30
MIN_MARGIN = 0.0004
#: One share of the recent submissions above which Value Factor "may suffer".
CONCENTRATION = 0.50
#: A share below which a market is plainly under-used, and an Alpha there adds diversity.
UNDERUSED = 0.15
#: Window the diversity and growth reads treat as "recent".
RECENT_DAYS = 90
GROWTH_DAYS = 30

#: Osmosis.
OSMOSIS_POINTS = 100_000
OSMOSIS_MIN_ALPHAS = 10
OSMOSIS_MIN_SCOPES = 3
#: No single Alpha takes more than this share of a scope: "avoid frequent concentration".
OSMOSIS_MAX_SHARE = 0.15
OSMOSIS_MAX_ALPHAS = 50
OSMOSIS_STEP = 100
#: Two Alphas this correlated are one idea for the combo, so the weaker is dropped.
OSMOSIS_CORRELATION = 0.70
#: "Avoid alphas with turnover above 20% unless their investability-constrained Sharpe holds".
OSMOSIS_TURNOVER = 0.20

#: Weight changes the Submit Advisor applies on top of its 0-100 score, and no more: the
#: score ranks, it never overrides a check.
BONUS_THEME_MAX = 8.0
BONUS_DIVERSE = 3.0
PENALTY_MARGIN = 10.0
PENALTY_CONCENTRATED = 5.0
PENALTY_RETENTION = 8.0


# -- reading rows ---------------------------------------------------------


def num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def as_platform_time(value: Any) -> datetime | None:
    """A stored timestamp in platform time. A naive one is read as UTC, which is what the
    vault's TIMESTAMP column holds for the offset-bearing strings BRAIN sends."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(PLATFORM_TZ)


def scope_of(row: dict[str, Any]) -> str:
    return f"{row.get('region') or '?'}/D{0 if row.get('delay') == 0 else 1}"


def categories_of(row: dict[str, Any]) -> list[str]:
    """Dataset categories from the Alpha's pyramid names (``ASI/D1/OTHER`` -> ``OTHER``)."""
    return sorted({p.rsplit("/", 1)[-1] for p in row.get("pyramids") or [] if "/" in p})


def sub_universe_retention(checks: Sequence[dict[str, Any]], sharpe: float | None) -> float | None:
    """The sub-universe Sharpe over the Alpha's own, when BRAIN has reported it."""
    if not sharpe or sharpe <= 0:
        return None
    for check in checks:
        if str(check.get("name", "")).upper() == "LOW_SUB_UNIVERSE_SHARPE":
            value = num(check.get("value"))
            return None if value is None else value / sharpe
    return None


def turnover_bucket(turnover: float | None) -> str:
    if turnover is None:
        return "unknown"
    if turnover < 0.10:
        return "under 10%"
    if turnover < 0.20:
        return "10-20%"
    if turnover < SOFT_TURNOVER:
        return "20-30%"
    return "30% and over"


# -- Quantity -------------------------------------------------------------


def quantity(rows: Sequence[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Submissions against the allowance, the recent daily record and the streak."""
    today = now.astimezone(PLATFORM_TZ).date()
    by_day: Counter[date] = Counter()
    super_today = d0_month = 0
    for row in rows:
        moment = as_platform_time(row.get("date_submitted"))
        if moment is None:
            continue
        day = moment.date()
        if str(row.get("sim_type") or "").upper() == "SUPER":
            if day == today:
                super_today += 1
            continue
        by_day[day] += 1
        if row.get("delay") == 0 and (day.year, day.month) == (today.year, today.month):
            d0_month += 1

    submitted_today = by_day.get(today, 0)
    recent = [
        {
            "date": (today - timedelta(days=i)).isoformat(),
            "count": by_day.get(today - timedelta(days=i), 0),
        }
        for i in range(13, -1, -1)
    ]
    # A streak is alive if yesterday had a submission even when today has none yet.
    streak, cursor = 0, today if by_day.get(today) else today - timedelta(days=1)
    while by_day.get(cursor):
        streak += 1
        cursor -= timedelta(days=1)
    week = sum(by_day.get(today - timedelta(days=i), 0) for i in range(7))
    return {
        "today": today.isoformat(),
        "submittedToday": submitted_today,
        "alphaCap": DAILY_ALPHAS,
        "alphaRemaining": max(0, DAILY_ALPHAS - submitted_today),
        "superToday": super_today,
        "superCap": DAILY_SUPER,
        "d0Month": d0_month,
        "d0Cap": MONTHLY_D0,
        "d0Remaining": max(0, MONTHLY_D0 - d0_month),
        "streak": streak,
        "lastSevenDays": week,
        "recent": recent,
    }


# -- Quality / Value Factor: diversity -------------------------------------


def _shares(values: Sequence[str]) -> list[dict[str, Any]]:
    counts = Counter(values)
    total = sum(counts.values())
    return [
        {"name": name, "count": n, "share": n / total, "over": n / total > CONCENTRATION}
        for name, n in counts.most_common()
    ]


def dimensions(row: dict[str, Any]) -> dict[str, list[str]]:
    """The values one Alpha takes on every axis the articles ask to diversify."""
    return {
        "region": [str(row.get("region") or "?")],
        "delay": [f"D{0 if row.get('delay') == 0 else 1}"],
        "universe": [str(row.get("universe") or "?")],
        "neutralization": [str(row.get("neutralization") or "?")],
        "turnover": [turnover_bucket(num(row.get("turnover")))],
        "category": categories_of(row) or ["unlabelled"],
    }


def recent_rows(
    rows: Sequence[dict[str, Any]], now: datetime, days: int = RECENT_DAYS
) -> list[dict[str, Any]]:
    cutoff = now.astimezone(PLATFORM_TZ) - timedelta(days=days)
    out = []
    for row in rows:
        moment = as_platform_time(row.get("date_submitted"))
        if moment is not None and moment >= cutoff:
            out.append(row)
    return out


def diversity(rows: Sequence[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Share of recent submissions on each axis, with the ones past 50% flagged."""
    recent = recent_rows(rows, now)
    axes: dict[str, list[dict[str, Any]]] = {}
    for axis in ("region", "delay", "universe", "neutralization", "turnover", "category"):
        values = [v for row in recent for v in dimensions(row)[axis]]
        axes[axis] = _shares(values) if values else []
    return {
        "windowDays": RECENT_DAYS,
        "considered": len(recent),
        "axes": axes,
        "flags": [
            f"{item['share']:.0%} of the last {RECENT_DAYS} days' submissions "
            f"share {axis} {item['name']}"
            for axis, items in axes.items()
            for item in items
            if item["over"] and axis != "category"
        ],
    }


def context(rows: Sequence[dict[str, Any]], now: datetime) -> dict[str, dict[str, float]]:
    """Recent share per axis value: what the Advisor weighs a candidate's market against."""
    out: dict[str, dict[str, float]] = {}
    for axis, items in diversity(rows, now)["axes"].items():
        out[axis] = {i["name"]: i["share"] for i in items}
    return out


# -- Self-Growth ----------------------------------------------------------


def _mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _block(group: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def col(key: str) -> list[float]:
        return [v for r in group if (v := num(r.get(key))) is not None]

    return {
        "count": len(group),
        "sharpe": _mean(col("sharpe")),
        "prodCorrelation": _mean(col("prod_correlation")),
        "selfCorrelation": _mean(col("self_correlation")),
        "turnover": _mean(col("turnover")),
        "margin": _mean(col("margin")),
    }


def growth(rows: Sequence[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Per region: the last 30 days' submissions against the 30 before, "is quality trending up".

    Correlation falling counts as improving, as the articles treat uniqueness as quality.
    """
    now_pt = now.astimezone(PLATFORM_TZ)
    new_from, old_from = (
        now_pt - timedelta(days=GROWTH_DAYS),
        now_pt - timedelta(days=2 * GROWTH_DAYS),
    )
    regions: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for row in rows:
        moment = as_platform_time(row.get("date_submitted"))
        if moment is None or moment < old_from:
            continue
        pair = regions.setdefault(str(row.get("region") or "?"), ([], []))
        pair[0 if moment >= new_from else 1].append(row)

    out = []
    for region, (new, old) in sorted(regions.items()):
        a, b = _block(new), _block(old)
        trend = "new"
        if a["count"] and b["count"] and a["sharpe"] is not None and b["sharpe"] is not None:
            delta = a["sharpe"] - b["sharpe"]
            corr = (
                None
                if a["prodCorrelation"] is None or b["prodCorrelation"] is None
                else a["prodCorrelation"] - b["prodCorrelation"]
            )
            trend = (
                "improving"
                if delta > 0.1 or (corr is not None and corr < -0.05 and delta > -0.1)
                else (
                    "declining"
                    if delta < -0.1 or (corr is not None and corr > 0.05 and delta < 0.1)
                    else "flat"
                )
            )
        out.append({"region": region, "recent": a, "previous": b, "trend": trend})
    return out


# -- Osmosis --------------------------------------------------------------


def _next_lock(now: datetime) -> str:
    """The coming Sunday 23:59 platform time, when allocations lock."""
    local = now.astimezone(PLATFORM_TZ)
    ahead = (6 - local.weekday()) % 7
    lock = (local + timedelta(days=ahead)).replace(hour=23, minute=59, second=0, microsecond=0)
    if lock <= local:
        lock += timedelta(days=7)
    return lock.isoformat()


def _osmosis_weight(row: dict[str, Any]) -> float:
    """A transparent merit: Sharpe, discounted for the traits the Osmosis guide warns about."""
    weight = max(num(row.get("sharpe")) or 0.0, 0.1)
    turnover = num(row.get("turnover"))
    if turnover is not None and turnover > OSMOSIS_TURNOVER:
        weight *= 0.7
    margin = num(row.get("margin"))
    if margin is not None and margin < MIN_MARGIN:
        weight *= 0.7
    return weight


def _capped_shares(weights: list[float], cap: float) -> list[float]:
    """Proportional shares with none above ``cap``, the excess redistributed."""
    n = len(weights)
    cap = max(cap, 1.0 / n)
    shares = [0.0] * n
    free = set(range(n))
    budget = 1.0
    while free:
        total = sum(weights[i] for i in free)
        over = [i for i in free if budget * weights[i] / total > cap]
        if not over:
            for i in free:
                shares[i] = budget * weights[i] / total
            break
        for i in over:
            shares[i] = cap
            budget -= cap
            free.discard(i)
        if budget <= 0:
            break
    return shares


def _points(shares: list[float]) -> list[int]:
    """Whole multiples of 100 that sum to exactly 100,000, by largest remainder."""
    units = OSMOSIS_POINTS // OSMOSIS_STEP
    raw = [s * units for s in shares]
    out = [math.floor(r) for r in raw]
    leftover = units - sum(out)
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[:leftover]:
        out[i] += 1
    return [p * OSMOSIS_STEP for p in out]


def _decorrelate(
    ranked: list[dict[str, Any]], days: dict[str, dict[date, float]]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Walk best-first, dropping an Alpha that moves with one already kept."""
    from .submission_planner import correlations, grid

    ids = [str(r["alpha_id"]) for r in ranked]
    kept_ids, dates, matrix = grid(days, ids)
    if len(kept_ids) < 2 or not dates:
        return ranked, {}
    corr = correlations(matrix)
    col = {a: i for i, a in enumerate(kept_ids)}
    chosen: list[dict[str, Any]] = []
    dropped: dict[str, str] = {}
    for row in ranked:
        alpha_id = str(row["alpha_id"])
        worst, who = None, None
        if alpha_id in col:
            for other in chosen:
                other_id = str(other["alpha_id"])
                if other_id not in col:
                    continue
                c = float(corr[col[alpha_id], col[other_id]])
                if not np.isnan(c) and (worst is None or c > worst):
                    worst, who = c, other_id
        if worst is not None and worst >= OSMOSIS_CORRELATION:
            dropped[alpha_id] = f"Moves with {who} (PnL correlation {worst:.2f})"
        else:
            chosen.append(row)
    return chosen, dropped


def osmosis(
    rows: Sequence[dict[str, Any]],
    days: dict[str, dict[date, float]],
    now: datetime,
) -> dict[str, Any]:
    """A proposed 100,000-point split per scope, for every scope with enough Alphas.

    BRAIN takes the allocation only through its own Alphas table, so this proposes and the
    CSV is what to copy across; nothing is sent.
    """
    scopes: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        status = str(row.get("status") or "").upper()
        if status == "DECOMMISSIONED":
            continue
        scopes.setdefault(scope_of(row), []).append(row)

    out = []
    for scope, members in sorted(scopes.items()):
        ranked = sorted(members, key=lambda r: (-_osmosis_weight(r), str(r["alpha_id"])))
        entry: dict[str, Any] = {
            "scope": scope,
            "submitted": len(members),
            "ready": len(members) >= OSMOSIS_MIN_ALPHAS,
            "needed": max(0, OSMOSIS_MIN_ALPHAS - len(members)),
            "alphas": [],
            "dropped": [],
        }
        if entry["ready"]:
            kept, why = _decorrelate(ranked, days)
            # Never let de-duplication starve a scope below the ten BRAIN needs: refill with the
            # best of the dropped ones and say so.
            refilled: list[str] = []
            if len(kept) < OSMOSIS_MIN_ALPHAS:
                dropped_rows = [r for r in ranked if str(r["alpha_id"]) in why]
                for row in dropped_rows[: OSMOSIS_MIN_ALPHAS - len(kept)]:
                    kept.append(row)
                    refilled.append(str(row["alpha_id"]))
                kept.sort(key=lambda r: (-_osmosis_weight(r), str(r["alpha_id"])))
            kept = kept[:OSMOSIS_MAX_ALPHAS]
            weights = [_osmosis_weight(r) for r in kept]
            points = _points(_capped_shares(weights, OSMOSIS_MAX_SHARE))
            entry["alphas"] = [
                {
                    "alphaId": str(r["alpha_id"]),
                    "points": p,
                    "sharpe": num(r.get("sharpe")),
                    "turnover": num(r.get("turnover")),
                    "margin": num(r.get("margin")),
                    "note": "Kept to reach 10 Alphas despite correlation"
                    if str(r["alpha_id"]) in refilled
                    else "",
                }
                for r, p in zip(kept, points, strict=True)
            ]
            entry["dropped"] = [
                {"alphaId": a, "reason": reason}
                for a, reason in why.items()
                if a not in refilled and a not in {str(r["alpha_id"]) for r in kept}
            ]
            entry["total"] = sum(p for p in points)
        out.append(entry)

    ready = [s for s in out if s["ready"]]
    return {
        "scopes": out,
        "readyScopes": len(ready),
        "requiredScopes": OSMOSIS_MIN_SCOPES,
        "valid": len(ready) >= OSMOSIS_MIN_SCOPES,
        "pointsPerScope": OSMOSIS_POINTS,
        "minAlphas": OSMOSIS_MIN_ALPHAS,
        "nextLock": _next_lock(now),
    }


def osmosis_csv(plan: dict[str, Any]) -> str:
    lines = ["scope,alpha_id,points"]
    for scope in plan["scopes"]:
        lines += [f"{scope['scope']},{a['alphaId']},{a['points']}" for a in scope["alphas"]]
    return "\n".join(lines) + "\n"


# -- Advisor adjustments --------------------------------------------------


def value_adjustment(
    alpha: dict[str, Any],
    shares: dict[str, dict[str, float]] | None,
    multiplier: float | None,
    retention: float | None,
) -> tuple[float, list[str], list[str]]:
    """Points to add to an advisor score, with the strengths and cautions that explain them.

    Everything here is a lever the base-payment articles name: the theme or pyramid
    multiplier, margin, sub-universe Sharpe retention, and concentration.
    """
    delta = 0.0
    strengths: list[str] = []
    cautions: list[str] = []

    if multiplier is not None and multiplier > 1.0:
        gain = min(BONUS_THEME_MAX, (multiplier - 1.0) * BONUS_THEME_MAX)
        delta += gain
        strengths.append(f"Pyramid multiplier {multiplier:.1f}x lifts its Quality Factor")

    margin = num(alpha.get("margin"))
    turnover = num(alpha.get("turnover"))
    if margin is not None and margin < MIN_MARGIN:
        delta -= PENALTY_MARGIN
        cautions.append(
            f"Margin {margin * 10_000:.1f} bps is under the 4 bps the Value Factor wants"
        )
    elif margin is not None and turnover is not None and turnover > SOFT_TURNOVER:
        cautions.append(
            f"Turnover {turnover:.0%} is over 30%; margin {margin * 10_000:.1f} bps must carry it"
        )

    if retention is not None and retention < MIN_SUBUNIVERSE_RETENTION:
        delta -= PENALTY_RETENTION
        cautions.append(
            f"Only {retention:.0%} of its Sharpe survives in the sub-universe (want 70%+)"
        )

    if shares:
        row = dimensions(
            {
                "region": (alpha.get("settings") or {}).get("region"),
                "delay": (alpha.get("settings") or {}).get("delay"),
                "universe": (alpha.get("settings") or {}).get("universe"),
                "neutralization": (alpha.get("settings") or {}).get("neutralization"),
                "turnover": turnover,
            }
        )
        for axis in ("region", "delay", "neutralization"):
            share = shares.get(axis, {}).get(row[axis][0], 0.0)
            if share > CONCENTRATION:
                delta -= PENALTY_CONCENTRATED
                cautions.append(
                    f"{share:.0%} of recent submissions already share {axis} {row[axis][0]}"
                )
                break
        else:
            region_share = shares.get("region", {}).get(row["region"][0], 0.0)
            if shares.get("region") and region_share < UNDERUSED:
                delta += BONUS_DIVERSE
                strengths.append(
                    f"Adds diversity: only {region_share:.0%} of recent submissions "
                    f"are {row['region'][0]}"
                )
    return delta, strengths, cautions


# -- Today's plan -----------------------------------------------------------


def daily_plan(
    recommended: Sequence[dict[str, Any]],
    qty: dict[str, Any],
) -> dict[str, Any]:
    """Which of the Advisor's recommendations to submit today, within the allowance.

    The Advisor already ranks and de-duplicates; this only spreads the day's picks across
    markets, so four submissions are not four of one region/delay, and respects the D0 cap.
    ``recommended`` rows carry ``alphaId``, ``score``, ``settings`` and ``multiplier``.
    """
    slots = qty["alphaRemaining"]
    d0_left = qty["d0Remaining"]
    picks: list[dict[str, Any]] = []
    pool = list(recommended)
    taken_scopes: Counter[str] = Counter()
    while pool and len(picks) < slots:

        def adjusted(item: dict[str, Any]) -> float:
            settings = item.get("settings") or {}
            scope = f"{settings.get('region') or '?'}/D{0 if settings.get('delay') == 0 else 1}"
            return float(item["score"]) - 6.0 * taken_scopes[scope]

        pool.sort(key=lambda i: (-adjusted(i), int(i.get("rank") or 10**9), str(i["alphaId"])))
        item = pool.pop(0)
        settings = item.get("settings") or {}
        is_d0 = settings.get("delay") == 0
        if is_d0 and d0_left <= 0:
            continue
        scope = f"{settings.get('region') or '?'}/D{0 if settings.get('delay') == 0 else 1}"
        reasons = [f"Advisor score {float(item['score']):.0f}"]
        if item.get("multiplier") and item["multiplier"] > 1:
            reasons.append(f"{item['multiplier']:.1f}x pyramid multiplier")
        if taken_scopes[scope] == 0:
            reasons.append(f"first {scope} pick today")
        picks.append(
            {
                "alphaId": item["alphaId"],
                "brainUrl": item.get("brainUrl"),
                "scope": scope,
                "reasons": reasons,
                "score": item["score"],
            }
        )
        taken_scopes[scope] += 1
        if is_d0:
            d0_left -= 1

    short = max(0, slots - len(picks))
    tips: list[str] = []
    if short:
        tips = [
            "Port a submitted Alpha to another region or universe (Settings Sampler).",
            "Re-check Alphas still waiting on BRAIN's checks (Submit Advisor).",
            "Start a lab on a pyramid with a high multiplier (Pyramids).",
        ]
    return {
        "slots": slots,
        "picks": picks,
        "short": short,
        "tips": tips,
        "superAlphaOpen": qty["superToday"] < qty["superCap"],
    }


# -- Checklist ------------------------------------------------------------


def checklist(
    qty: dict[str, Any],
    div: dict[str, Any],
    growth_rows: Sequence[dict[str, Any]],
    osm: dict[str, Any],
    recent: Sequence[dict[str, Any]],
    themed_today: bool | None,
) -> list[dict[str, str]]:
    """The articles' one-page checklist, answered from the account's own figures."""

    def item(label: str, status: str, detail: str) -> dict[str, str]:
        return {"label": label, "status": status, "detail": detail}

    out: list[dict[str, str]] = []
    out.append(
        item(
            "4 Alphas submitted today",
            "ok" if qty["submittedToday"] >= DAILY_ALPHAS else "todo",
            f"{qty['submittedToday']} of {DAILY_ALPHAS} today; {qty['streak']}-day streak",
        )
    )
    out.append(
        item(
            "1 SuperAlpha submitted today",
            "ok" if qty["superToday"] >= DAILY_SUPER else "todo",
            f"{qty['superToday']} of {DAILY_SUPER}",
        )
    )
    out.append(
        item(
            "D0 within the monthly cap",
            "ok" if qty["d0Month"] <= MONTHLY_D0 else "warn",
            f"{qty['d0Month']} of {MONTHLY_D0} this month",
        )
    )
    if themed_today is not None:
        out.append(
            item(
                "At least one theme / multiplier Alpha today",
                "ok" if themed_today else "todo",
                "A pyramid multiplier above 1x applies" if themed_today else "None yet today",
            )
        )
    out.append(
        item(
            "No axis above 50% of recent submissions",
            "ok" if not div["flags"] else "warn",
            "Balanced" if not div["flags"] else div["flags"][0],
        )
    )
    margins = [m for r in recent if (m := num(r.get("margin"))) is not None]
    low = sum(1 for m in margins if m < MIN_MARGIN)
    out.append(
        item(
            "Recent submissions keep margin above 4 bps",
            "ok" if margins and low == 0 else ("warn" if low else "todo"),
            f"{low} of {len(margins)} recent are below 4 bps" if margins else "No margin recorded",
        )
    )
    high = sum(1 for r in recent if (t := num(r.get("turnover"))) is not None and t > SOFT_TURNOVER)
    out.append(
        item(
            "Mostly under 30% turnover",
            "ok" if recent and high <= len(recent) / 3 else "warn",
            f"{high} of {len(recent)} recent are over 30%",
        )
    )
    declining = [g["region"] for g in growth_rows if g["trend"] == "declining"]
    out.append(
        item(
            "Quality trending up per region",
            "ok" if not declining and growth_rows else ("warn" if declining else "todo"),
            f"Declining in {', '.join(declining)}" if declining else "No region is declining",
        )
    )
    out.append(
        item(
            "Osmosis active (3 scopes x 10 Alphas, 100,000 points each)",
            "ok" if osm["valid"] else "todo",
            f"{osm['readyScopes']} of {osm['requiredScopes']} scopes have 10 submitted Alphas",
        )
    )
    return out
