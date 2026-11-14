# ruff: noqa: S101, T201  -- a test file asserts, and prints when run as a script
"""Leg Gate: building the staged expressions and gating them, on the two-leg EUR template."""

from alpha_harness.labs.fastexpr import parse
from alpha_harness.tools import leg_gate as lg

TEMPLATE = """
fundamental6_leg = group_rank(ts_zscore(ts_backfill($fundamental6, $bf_slow), $win_slow), $group);
other335_leg = group_rank(ts_zscore(ts_backfill($other335, $bf_fast), $win_fast), $group);
raw = add(multiply($w_other335, other335_leg),
  multiply($w_fundamental6, fundamental6_leg), filter=true);
group_neutralize(ts_decay_linear(truncate(raw, maxPercent=$trunc), $decay), $country)
"""

VALUES = {
    "fundamental6": "fnd6_newa1_emp",
    "other335": "oth335_example",
    "bf_slow": "63",
    "win_slow": "252",
    "bf_fast": "5",
    "win_fast": "63",
    "group": "sector",
    "trunc": "0.08",
    "decay": "5",
    "country": "country",
}


def plan(**over: object) -> lg.LegGatePlan:
    base: dict[str, object] = {
        "template": TEMPLATE,
        "values": VALUES,
        "legs": ["fundamental6_leg", "other335_leg"],
        "combine": "raw",
        "weight_slots": {"fundamental6_leg": "w_fundamental6", "other335_leg": "w_other335"},
        "settings": {"region": "EUR", "delay": 1, "universe": "TOP800"},
    }
    return lg.LegGatePlan.model_validate(base | over)


def test_statements_and_slots() -> None:
    stmts = lg.statements(TEMPLATE)
    assert [s.name for s in stmts] == ["fundamental6_leg", "other335_leg", "raw", None]
    assert lg.slots(TEMPLATE)[:3] == ["fundamental6", "bf_slow", "win_slow"]
    assert lg.detect_combine(stmts, ["fundamental6_leg", "other335_leg"]) == ["raw"]


def test_semicolon_inside_brackets_and_comments() -> None:
    stmts = lg.statements("/* note; here */ a = f(1, 2); b = a")
    assert [s.name for s in stmts] == ["a", "b"]
    assert stmts[0].body == "f(1, 2)"


def test_solo_keeps_only_its_leg_and_the_final_line() -> None:
    p = plan()
    stmts, problems = lg.check(p)
    assert not problems
    sims = lg.stage_one(p, stmts)
    assert [(s.leg, s.sign) for s in sims] == [
        ("fundamental6_leg", 1),
        ("fundamental6_leg", -1),
        ("other335_leg", 1),
        ("other335_leg", -1),
    ]
    one = sims[1].expression
    assert "multiply(-1, fundamental6_leg)" in one
    assert "other335" not in one and "oth335_example" not in one
    assert "fnd6_newa1_emp" in one
    assert one.splitlines()[-1].startswith("group_neutralize(ts_decay_linear(truncate(raw")
    for sim in sims:
        parse(sim.expression)


def test_missing_slots_only_count_what_a_stage_reads() -> None:
    p = plan(values={k: v for k, v in VALUES.items() if k != "other335"})
    stmts, _ = lg.check(p)
    assert lg.missing_slots(p, stmts) == ["other335"]
    # The solo expression for the fundamental leg does not need the other dataset.
    assert "$other335" not in lg.stage_one(p, stmts)[0].expression


def test_stage_two_uses_winning_signs_and_ranked_weights() -> None:
    p = plan()
    stmts, _ = lg.check(p)
    text = lg.stage_two(p, stmts, [("other335_leg", -1), ("fundamental6_leg", 1)])
    assert "multiply(-1.5, other335_leg)" in text
    assert "multiply(0.5, fundamental6_leg)" in text
    parse(text)
    # Weights are adjustable.
    p2 = plan(gates={"weights": [2, 1]})
    stmts2, _ = lg.check(p2)
    assert "multiply(2, fundamental6_leg)" in lg.stage_two(
        p2, stmts2, [("fundamental6_leg", 1), ("other335_leg", 1)]
    )


def test_a_dropped_leg_gets_weight_zero() -> None:
    p = plan()
    stmts, _ = lg.check(p)
    text = lg.stage_two(p, stmts, [("fundamental6_leg", 1)])
    assert "multiply(0, other335_leg)" in text


def test_check_reports_each_problem() -> None:
    assert lg.check(plan(legs=["fundamental6_leg"]))[1][0] == "Choose at least two legs."
    assert any("not assigned" in x for x in lg.check(plan(legs=["nope", "other335_leg"]))[1])
    assert lg.check(plan(combine=None))[1] == ["Choose the assignment that sums the legs."]
    assert any(
        "not read" in x
        for x in lg.check(
            plan(weight_slots={"fundamental6_leg": "zzz", "other335_leg": "w_other335"})
        )[1]
    )
    assert any(
        "weight slot" in x
        for x in lg.check(plan(weight_slots={"fundamental6_leg": "w_fundamental6"}))[1]
    )


def test_quota() -> None:
    assert lg.quota(plan()) == {
        "stageOne": 4,
        "stageTwo": 1,
        "mostSpent": 5,
        "leastSpent": 4,
        "everySignPattern": 4,
    }
    three = plan(legs=["fundamental6_leg", "other335_leg", "raw"])
    assert lg.quota(three)["everySignPattern"] == 8


def row(
    leg: str, sign: int, sharpe: float | None, y22: float, y23: float, state: str = "COMPLETE"
) -> dict:
    return {
        "leg": leg,
        "sign": sign,
        "state": state,
        "alphaId": f"{leg}{sign}",
        "sharpe": sharpe,
        "years": {2022: y22, 2023: y23},
    }


def test_both_legs_pass_and_the_better_sign_wins() -> None:
    rows = [
        row("fundamental6_leg", 1, -1.0, -0.5, -0.2),
        row("fundamental6_leg", -1, 1.5, 0.4, 0.9),
        row("other335_leg", 1, 1.9, 0.8, 0.7),
        row("other335_leg", -1, -1.9, -0.8, -0.7),
    ]
    v = lg.judge_stage_one(rows, plan())
    assert v["go"] and not v["waiting"]
    assert [(k["leg"], k["sign"]) for k in v["kept"]] == [
        ("other335_leg", 1),
        ("fundamental6_leg", -1),
    ]
    assert v["avoidedSimulations"] == 0


def test_a_negative_year_fails_the_leg_and_stops_the_run() -> None:
    rows = [
        row("fundamental6_leg", 1, 1.4, 0.5, -0.1),  # Sharpe fine, 2023 negative
        row("fundamental6_leg", -1, -1.4, -0.5, 0.1),
        row("other335_leg", 1, 1.9, 0.8, 0.7),
        row("other335_leg", -1, -1.9, -0.8, -0.7),
    ]
    v = lg.judge_stage_one(rows, plan())
    assert not v["go"] and v["avoidedSimulations"] == 1
    assert v["legs"][0]["verdict"] == "drop"
    assert any("2023" in r for r in v["legs"][0]["results"][0]["reasons"])
    assert "stage 2 is not run" in v["summary"]


def test_gates_are_adjustable() -> None:
    rows = [
        row("fundamental6_leg", 1, 1.0, 0.5, 0.2),
        row("fundamental6_leg", -1, -1.0, -0.5, -0.2),
        row("other335_leg", 1, 1.9, 0.8, 0.7),
        row("other335_leg", -1, -1.9, -0.8, -0.7),
    ]
    assert not lg.judge_stage_one(rows, plan())["go"]
    assert lg.judge_stage_one(rows, plan(gates={"legMinSharpe": 0.9}))["go"]
    assert lg.judge_stage_one(rows, plan(gates={"legMinSharpe": 0.9, "minLegs": 1}))["go"]


def test_unfinished_stage_one_is_waiting_not_failed() -> None:
    rows = [
        row("fundamental6_leg", 1, None, 0, 0, state="RUNNING"),
        row("fundamental6_leg", -1, 1.5, 0.4, 0.9),
        row("other335_leg", 1, 1.9, 0.8, 0.7),
        row("other335_leg", -1, -1.9, -0.8, -0.7),
    ]
    v = lg.judge_stage_one(rows, plan())
    assert v["waiting"] and not v["go"] and v["avoidedSimulations"] == 0


def test_missing_year_row_fails_the_year_gate() -> None:
    r = row("fundamental6_leg", 1, 1.5, 0.4, 0.9)
    r["years"] = {2022: 0.4}
    ok, why = lg.judge_leg(r, lg.Gates())
    assert not ok and why == ["No 2023 row"]


def test_year_sharpes_reads_the_record_set() -> None:
    record = {
        "schema": {"properties": [{"name": "year"}, {"name": "sharpe"}]},
        "records": [["2022-01-01", 0.5], ["2023", -0.2], ["x", 1]],
    }
    assert lg.year_sharpes(record) == {2022: 0.5, 2023: -0.2}
    assert lg.year_sharpes({}) == {}


def test_stage_two_gates() -> None:
    p = plan()
    good = {
        "sharpe": 1.9,
        "recentSharpe": 2.0,
        "subUniverse": {"result": "PASS", "value": 1.5, "limit": 1.2},
    }
    assert lg.judge_stage_two(good, p)["verdict"] == "pass"
    thin = good | {"subUniverse": {"result": "PASS", "value": 1.25, "limit": 1.2}}
    assert lg.judge_stage_two(thin, p)["verdict"] == "fail"  # 4% margin, wants 10%
    assert lg.judge_stage_two(thin, plan(gates={"subUniverseMargin": 0.0}))["verdict"] == "pass"
    weak = good | {"sharpe": 1.5}
    assert lg.judge_stage_two(weak, p)["verdict"] == "fail"
    assert lg.judge_stage_two(weak, plan(gates={"comboMinSharpe": 1.4}))["verdict"] == "pass"
    assert lg.judge_stage_two({}, p)["verdict"] == "fail"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
