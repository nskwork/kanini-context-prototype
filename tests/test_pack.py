"""Behaviour beyond the scope's 23 tests: closed lists, the UnitedHealthcare findings, freshness, the curation checks."""
from __future__ import annotations

import json

import pytest

from conftest import DOS, ROOT, facts


def ev(kc, plan="original-medicare", state="TN", code="J0885", **f):
    return kc("rules_evaluate", plan=plan, state=state, code=code, date_of_service=DOS, facts=facts(**f))


def test_no_pa_rests_on_every_bound_program(kc):
    tn, oh = ev(kc), ev(kc, state="OH", diagnosis_codes=["D63.1", "N18.31"], egfr=52)
    assert tn["prior_authorization"]["basis"] == ["stated_by_exhaustion"]
    assert len(tn["prior_authorization"]["rule_refs"]) == 3          # HOPD, RSNAT, DMEPOS (national)
    assert len(oh["prior_authorization"]["rule_refs"]) == 5          # plus home health RCD and WISeR in Ohio


def test_unscreened_program_turns_no_into_unknown(db):
    db.execute("BEGIN")
    try:
        db.execute("""INSERT INTO kc_core.authority VALUES (kc_core.eid('authority','TEST-NEW'),'pa_program',
                      kc_core.eid('payer','cms'),'TEST new program','T1','TEST',true)""")
        db.execute("""INSERT INTO kc_core.authority_binding (authority_id, program_id, jurisdiction_id, item_scope, valid,
                      reviewed_by, review_exception) VALUES (kc_core.eid('authority','TEST-NEW'), kc_core.prog('medicare_ffs'),
                      kc_core.eid('jurisdiction','TN'),'all','[2026-01-01,)','{t}','test')""")
        out = db.execute("SELECT kc_api.evaluate_order('original-medicare','TN','HCPCS|J0885',%s::date,%s::jsonb)",
                         (DOS, json.dumps(facts()))).fetchone()[0]
        assert out["outcome"] == "unknown" and out["prior_authorization"]["reason"] == "pa_program_not_screened"
    finally:
        db.execute("ROLLBACK")


def test_uhc_esa_outside_cancer_is_not_answered(kc):
    out = ev(kc, plan="uhc-ma")
    assert out["outcome"] == "unknown" and out["status"] == "no_rule_found" and out["safe_to_act"] is False


def test_uhc_esa_for_cancer_is_an_interpretation(kc):
    out = ev(kc, plan="uhc-ma", diagnosis_codes=["D63.0", "C50.911"])
    assert out["prior_authorization"]["outcome"] == "pa_required" and out["safe_to_act"] is False
    assert "basis_interpreted" in out["not_safe_because"]


def test_uhc_emergency_is_exempt(kc):
    out = ev(kc, plan="uhc-ma", code="J1439", encounter_type="emergency")
    assert out["outcome"] == "exempt" and out["safe_to_act"] is True


def test_expedited_clock(kc):
    out = ev(kc, plan="uhc-ma", code="J1439", urgency="expedited")
    assert out["decision_clock"]["applies"] == "expedited" and out["decision_clock"]["hours"] == 24


def test_freshness_names_source(db):
    # As if asked on 2026-11-18: A56462 (last confirmed 2026-10-15) is past 14 days, the others are not.
    out = db.execute("SELECT kc_api.evaluate_order('original-medicare','OH','HCPCS|J0885',%s::date,%s::jsonb,'2026-11-18 12:00-06')",
                     (DOS, json.dumps(facts(egfr=52, diagnosis_codes=["D63.1", "N18.31"])))).fetchone()[0]
    assert out["aging_sources"] == ["Article A56462, last confirmed 2026-10-15, limit 14 days"] and out["safe_to_act"] is False
    assert out["next_step_detail"].endswith("(Article A56462, last confirmed 2026-10-15, limit 14 days)")


def test_every_answer_shows_the_review_exception(kc):
    assert ev(kc)["review_exceptions"] == ["TEST build: not reviewed; for local tests only, never released"]


def test_policy_cite_returns_page_quote_and_hash(kc):
    out = kc("policy_search", rule_ref="kc:rule/esa-ckd-nd/L39237@2026-09-17")
    assert out["locator"]["page"] == 7 and "GFR) less than 45" in out["quote"]
    assert out["source"]["sha256"].startswith("134d2e595b")


def test_same_question_same_answer(kc):
    a, b = ev(kc), ev(kc)
    for x in (a, b):
        x.pop("recorded_as_of")
    assert a == b


# --- curation checks -------------------------------------------------------------------------------------------

@pytest.fixture()
def l39237(tmp_path):
    def write(mutate):
        d = json.loads((ROOT / "kanini_context/rules/mcd-lcd-l39237.json").read_text())
        mutate(d)
        p = tmp_path / "rule.json"
        p.write_text(json.dumps(d))
        from kanini_context.curation.checks import check_file
        return check_file(p).errors
    return write


def test_checks_pass_on_every_rule_file():
    from kanini_context.curation.checks import check_file
    for p in sorted((ROOT / "kanini_context/rules").glob("*.json")):
        assert check_file(p).errors == [], p.name


def test_checks_catch_a_changed_threshold(l39237):
    def m(d):
        d["rules"][0]["requirements"][3]["test"]["value"] = 50
    assert any("number 50 is not in the quote" in e for e in l39237(m))


def test_checks_catch_a_quote_on_the_wrong_page(l39237):
    assert any("found on page [7]" in e for e in l39237(lambda d: d["rules"][0]["locator"].update(page=3)))


def test_checks_catch_an_unregistered_fact(l39237):
    assert any("unregistered fact" in e for e in l39237(lambda d: d["rules"][0]["conditions"].append({"fact": "x", "op": "in", "value": [1]})))


def test_checks_catch_a_negation_read_as_required():
    from kanini_context.curation import checks
    rep = checks.Report("t")
    rule = {"rule_key": "t", "rule_kind": "approval_required", "payload": {"required": True}, "basis": "stated",
            "locator": {"page": 1}, "quote": "Prior authorization is not required for emergency or urgent care."}
    src = {"file": "UHC-MA-PA-requirements-2026-10-01.pdf"}
    checks.check_rule(rep, src, rule, set())
    assert any("negation" in e for e in rep.errors)


# --- approval, sync, replay -------------------------------------------------------------------------------------

def _fresh(db_name):
    import psycopg
    from kanini_context.release.build import SQL_FILES, run_sql
    with psycopg.connect("dbname=postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {db_name}")
    run_sql(db_name, SQL_FILES)


def _load(files, db_name, *flags):
    import os
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-m", "kanini_context.curation.load", *flags, *map(str, files)], cwd=ROOT,
                          capture_output=True, text=True, env={**os.environ, "KC_DSN": f"dbname={db_name}"})


def test_unapproved_rules_are_refused_outside_test_mode(release, tmp_path):
    _fresh("kct_approval")
    r = _load(sorted((ROOT / "kanini_context/rules").glob("*.json")), "kct_approval")
    assert r.returncode == 1 and "not approved" in r.stdout


def test_engineer_approval_loads_as_a_real_release(release, tmp_path):
    import subprocess
    import sys
    import psycopg
    from kanini_context.curation.approve import SCOPE_EXCEPTION
    for p in (ROOT / "kanini_context/rules").glob("*.json"):
        (tmp_path / p.name).write_text(p.read_text())
    files = sorted(tmp_path.glob("*.json"))
    a = subprocess.run([sys.executable, "-m", "kanini_context.curation.approve", "--by", "Test Engineer", "--yes", *map(str, files)],
                       cwd=ROOT, capture_output=True, text=True)
    assert a.returncode == 0 and f"{len(files)} file(s) approved" in a.stdout
    _fresh("kct_approval")
    r = _load(files, "kct_approval")
    assert r.returncode == 0 and "TEST" not in r.stdout
    with psycopg.connect("dbname=kct_approval") as c:
        rows = c.execute("SELECT DISTINCT reviewed_by, review_exception FROM kc_core.rule").fetchall()
        assert rows == [(["Test Engineer"], SCOPE_EXCEPTION)]
        assert c.execute("SELECT count(*) FROM kc_gov.review WHERE git_commit IS NULL").fetchone()[0] == 0


def test_sync_records_a_check(release, db):
    import subprocess
    import sys
    before = db.execute("SELECT count(*) FROM kc_client.sync_log").fetchone()[0]
    r = subprocess.run([sys.executable, "-m", "kanini_context.release.sync"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "is current" in r.stdout
    assert db.execute("SELECT count(*) FROM kc_client.sync_log").fetchone()[0] == before + 1


def test_release_name_and_last_covered_date(db):
    rel, covers = db.execute("SELECT release_id, covers_through FROM kc_gov.release").fetchone()
    assert rel.startswith("TEST-") and rel.endswith(".0")
    assert str(covers) == "2026-12-31"   # the day before the first change the pack knows is coming


def test_documentation_form_lists_the_lcd_items(kc):
    cov = {"program": "medicare_ffs", "payer": "cms", "plan": "original-medicare", "state": "TN"}
    out = kc("get_documentation_form", coverage=cov, code="J0885", date=DOS, facts={"dialysis_status": "none"})
    names = {i["element"] for i in out["items"]}
    assert out["status"] == "resolved" and {"Hemoglobin", "Hematocrit", "eGFR (CKD-EPI 2021)"} <= names


def test_coverage_object_takes_no_patient_fields(kc):
    out = kc("resolve_rules", coverage={"program": "medicare_advantage", "plan": "uhc-ma", "state": "TN", "patient": "x"},
             rule_kind="approval_required", code="J1439", date=DOS)
    assert out["error"] == "unexpected field: coverage.patient"
