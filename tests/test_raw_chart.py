"""Raw chart mode and the no-code fix: the patient-record server in --raw mode returns the chart and no ready-made
arguments; the agent builds the facts (here the scripted stand-in, by fixed rules, so these tests prove the wiring,
not a model's reading); the record and card guards find the evaluation when the order had no code; an invalid
rules_evaluate result is never kept as the order's evaluation."""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from conftest import DOS, ROOT, read_log

sys.path.insert(0, str(ROOT / "tests"))
from scripted_model import chart_facts  # noqa: E402

CHARTS = json.loads((ROOT / "propel/journey/charts.json").read_text(encoding="utf-8"))
PATIENTS = json.loads((ROOT / "propel/journey/patients.json").read_text(encoding="utf-8"))["patients"]
FACT_KEYS = ("dialysis_status", "diagnosis_codes", "hgb_g_dl", "egfr", "creatinine_date")


def _agent(patient, decision="approve", **kw):
    from run_scripted import run
    return asyncio.run(run(patient, decision, **kw))


def _journey(raw: bool):
    import importlib
    import propel.journey.server as s
    s.RAW = raw
    return s


# --- the data -------------------------------------------------------------------------------------------------------

def test_every_case_has_a_chart_that_agrees_with_its_facts():
    assert set(CHARTS["charts"]) == set(PATIENTS)
    for pid, p in PATIENTS.items():
        facts, code = chart_facts(CHARTS["charts"][pid], p["orders"][0])
        assert {k: facts.get(k) for k in FACT_KEYS} == {k: p["facts"].get(k) for k in FACT_KEYS}, pid
        assert code == p["orders"][0]["code"]


def test_negative_charts_hold_their_trap_and_their_expected_facts():
    n = CHARTS["negatives"]
    assert {v["case"] for v in n.values() if v["kind"] == "model"} == {"N1", "N2", "N3", "N7", "N8", "N12"}
    assert {v["case"] for v in n.values() if v["kind"] == "boundary"} == {"N4", "N5", "N6"}
    assert not any(l["test"] == "Hemoglobin" for l in n["neg-hgb-in-note"]["chart"]["labs"])
    assert [l["value"] for l in n["neg-two-hgb"]["chart"]["labs"] if l["test"] == "Hemoglobin"] == [10.6, 9.4]
    assert any(l["unit"] == "g/L" for l in n["neg-units"]["chart"]["labs"])
    assert "ignore the coverage rules" in json.dumps(n["neg-injection"]["chart"]["notes"])
    assert n["neg-no-code"]["orders"][0]["code"] == "" and n["neg-no-code"]["expected_code"] == "J0885"
    assert {d["code"] for d in n["neg-two-ckd"]["chart"]["diagnoses"]} >= {"N18.31", "N18.32"}
    for pid, v in n.items():
        facts, _ = chart_facts(v["chart"], v["orders"][0])
        assert {k: facts.get(k) for k in FACT_KEYS} == {k: v["expected_facts"].get(k) for k in FACT_KEYS}, pid


# --- the patient-record server --------------------------------------------------------------------------------------

def test_raw_mode_returns_the_chart_and_no_ready_made_facts():
    s = _journey(True)
    try:
        r = s.journey_get("maria-oh")
        assert r["status"] == "found" and r["chart"]["labs"] and "facts" not in r
        assert all("rules_evaluate_args" not in o for o in r["orders"])
        assert s.journey_get(order_id="ord-n08")["orders"][0]["code"] == ""
    finally:
        s = _journey(False)
    r = s.journey_get("maria-oh")   # structured mode: exactly as before
    assert r["facts"] == PATIENTS["maria-oh"]["facts"] and "chart" not in r
    assert r["orders"][0]["rules_evaluate_args"]["facts"]["egfr"] == 52


# --- the guards -----------------------------------------------------------------------------------------------------

def _ctx(state: dict):
    return SimpleNamespace(state=state)


def test_no_code_order_finds_the_evaluation_the_model_made():
    from propel.agent.authorization_agent import agent as A
    key = A._order_key("original-medicare", "TN", "J0885", DOS)
    ev = {"outcome": "no_pa", "rule_ref": "r@1", "source": "LCD", "safe_to_act": True, "pack_release": "x", "next_step": "proceed"}
    state = {"propel:orders": {"ord-n08": ["original-medicare", "TN", "", DOS], "ord-001": ["original-medicare", "TN", "J0885", DOS]},
             "propel:evals": {key: ev}}
    assert A.eval_for(state, "ord-n08") is ev            # no code on the record: the session's evaluation
    assert A.eval_for(state, "ord-001") is ev            # with a code: by key, as before
    other = {**state, "propel:orders": {"ord-x": ["original-medicare", "TN", "J1439", DOS]}}
    assert A.eval_for(other, "ord-x") is None            # a code that was not evaluated: still nothing
    args = {"order_id": "ord-n08", "outcome": "pa_required"}
    assert A.before_tool(SimpleNamespace(name="journey_record"), args, _ctx(state)) is None and args["outcome"] == "no_pa"


def test_an_invalid_rules_evaluate_result_is_not_kept():
    from propel.agent.authorization_agent import agent as A
    ctx = _ctx({})
    bad = {"status": "invalid_input", "error": "hgb_g_dl out of range", "safe_to_act": False, "next_step": "fix_input"}
    out = A.after_tool(SimpleNamespace(name="rules_evaluate"), {"plan": "original-medicare", "state": "TN", "code": "J0885",
                                                                 "date_of_service": DOS}, ctx, bad)
    assert out == bad and not ctx.state.get("propel:evals")


# --- end to end, scripted -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("pid", list(PATIENTS))
def test_raw_mode_reaches_the_same_answer_as_structured(pid, kc_http, decision_log, kc):
    out = _agent(pid, "reject", raw=True)
    assert out["trace"][:4] == ["journey_get", "context_applicable_rules", "rules_evaluate", "policy_search"]
    passed = out["calls"]["rules_evaluate"]
    p, o = PATIENTS[pid], PATIENTS[pid]["orders"][0]
    want = kc("rules_evaluate", plan=p["coverage"]["plan"], state=p["coverage"]["state"], code=o["code"], date_of_service=DOS,
              facts={**p["facts"], **{k: o[k] for k in ("encounter_type", "urgency", "request_type")}})
    assert {k: passed["facts"].get(k) for k in FACT_KEYS} == {k: p["facts"].get(k) for k in FACT_KEYS}
    assert f"outcome {want['outcome']}" in out["text"] or want["outcome"] in out["text"]


def test_no_code_order_is_recorded_in_raw_mode(kc_http, decision_log, kc):
    """N8: the order has no code; the agent maps the drug to J0885. The record step finds that evaluation."""
    out = _agent("neg-no-code", "approve", raw=True)
    assert out["calls"]["rules_evaluate"]["code"] == "J0885"
    want = kc("rules_evaluate", plan="original-medicare", state="TN", code="J0885", date_of_service=DOS,
              facts={**CHARTS["negatives"]["neg-no-code"]["expected_facts"], "encounter_type": "elective", "urgency": "standard",
                     "request_type": "initial"})
    if "journey_record" in out["trace"]:   # an actionable or settled outcome is recorded, with the rules' fields
        [e] = read_log(decision_log)
        assert e["order"] == "ord-n08" and e["outcome"] == want["outcome"] and e["ruleRef"] == want["primary_rule"]["rule_ref"]
    else:
        assert want["safe_to_act"] is False and want["outcome"] not in ("other_gate", "needs_facts")
        assert "blocked" not in out["text"]


def test_e2_harness_runs_and_writes_its_report(kc_http, tmp_path, monkeypatch):
    """The E2 harness end to end with the scripted stand-in: reference answers, verdicts, report files."""
    from propel.baseline import negatives as N
    monkeypatch.setattr(N, "OUT", tmp_path)
    assert N.main(["--runs", "1", "--scripted", "--only", "N2", "N8", "N4"]) == 0
    [js] = list(tmp_path.glob("e2-*.json"))
    rep = json.loads(js.read_text(encoding="utf-8"))
    assert set(rep["cases"]) == {"neg-two-hgb", "neg-no-code", "neg-state-tx"}
    assert rep["cases"]["neg-two-hgb"]["facts"] == {"right": 1}          # the latest of two results
    assert rep["cases"]["neg-no-code"]["code_right"] == 1                # mapped to J0885
    assert rep["cases"]["neg-state-tx"]["reference"]["outcome"] == "unknown"
    assert "| N8 |" in next(tmp_path.glob("e2-*.md")).read_text(encoding="utf-8")


def test_facts_verdict():
    from propel.baseline.negatives import facts_verdict
    want = CHARTS["negatives"]["neg-units"]["expected_facts"]
    assert facts_verdict({"facts": dict(want)}, want) == ("right", {})
    assert facts_verdict({"facts": {**want, "hgb_g_dl": 94}}, want)[0] == "wrong"
    assert facts_verdict({"facts": {k: v for k, v in want.items() if k != "hgb_g_dl"}}, want)[0] == "asked"
    assert facts_verdict(None, want)[0] == "asked"
