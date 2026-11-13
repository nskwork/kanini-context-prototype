"""Behaviour beyond the scope's 23 tests: closed lists, the UnitedHealthcare findings, freshness, the curation checks."""
from __future__ import annotations

import json

import pytest

from conftest import DOS, ROOT, assert_held_for_unscreened_program, facts


def ev(kc, plan="original-medicare", state="TN", code="J0885", **f):
    return kc("rules_evaluate", plan=plan, state=state, code=code, date_of_service=DOS, facts=facts(**f))


def test_no_pa_rests_on_every_bound_program(kc, db):
    from conftest import unscreened_programs
    for state, f in (("TN", {}), ("OH", {"diagnosis_codes": ["D63.1", "N18.31"], "egfr": 52}),
                     ("KY", {"diagnosis_codes": ["D63.1", "N18.31"], "egfr": 52})):
        out = ev(kc, state=state, **f)
        bound = db.execute("SELECT jsonb_array_length(kc_api.bound_pa_programs(%s::jsonb, 'drug', %s::date))",
                           (json.dumps({"program": "medicare_ffs", "plan": "original-medicare", "state": state}), DOS)).fetchone()[0]
        if unscreened_programs(db, state):
            assert out["prior_authorization"]["status"] == "no_rule_found", state
            assert_held_for_unscreened_program(out, db, state)
        else:   # "not required" stands only on a screen of every bound program
            assert out["prior_authorization"]["outcome"] == "no_pa" and out["prior_authorization"]["basis"] == ["stated_by_exhaustion"]
            assert len(out["prior_authorization"]["rule_refs"]) == bound, state


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
    # Asked at least 15 days after Article A56462 was last confirmed (and after the test release was loaded, since
    # the question reads the pack as recorded at that moment): past its 14-day limit, so the answer is held and names it.
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    m = json.loads((ROOT / "kanini_context/sources/manifest.json").read_text())
    last = max(datetime.fromisoformat(c["at"]) for x in m if x["source_id"] == "mcd-article-a56462" for c in x["checks"] if c.get("ok"))
    shown = last.astimezone(ZoneInfo("America/Chicago")).date()
    asked = max(last + timedelta(days=15), datetime.now(timezone.utc)).isoformat()
    out = db.execute("SELECT kc_api.evaluate_order('original-medicare','OH','HCPCS|J0885',%s::date,%s::jsonb,%s::timestamptz)",
                     (DOS, json.dumps(facts(egfr=52, diagnosis_codes=["D63.1", "N18.31"])), asked)).fetchone()[0]
    expected = f"Article A56462, last confirmed {shown}, limit 14 days"
    assert expected in out["aging_sources"] and out["safe_to_act"] is False
    assert expected in out["next_step_detail"]


def test_every_answer_shows_the_review_exception(kc):
    # Approved files carry the engineer's scope exception; files not yet approved load as TEST in the test build.
    from kanini_context.curation.approve import SCOPE_EXCEPTION
    from kanini_context.curation.load import TEST_EXCEPTION
    reviews = [json.loads(p.read_text()).get("review", {}) for p in (ROOT / "kanini_context/rules").glob("*.json")]
    expected = {SCOPE_EXCEPTION if any(r.get("reviewed_by") or []) else TEST_EXCEPTION for r in reviews}
    got = ev(kc)["review_exceptions"]
    assert got and set(got) <= expected


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
    # A copy of the rule files with every approval removed must be refused by a real (non-TEST) load.
    for p in (ROOT / "kanini_context/rules").glob("*.json"):
        d = json.loads(p.read_text())
        d.setdefault("review", {})["reviewed_by"] = []
        (tmp_path / p.name).write_text(json.dumps(d))
    _fresh("kct_approval")
    r = _load(sorted(tmp_path.glob("*.json")), "kct_approval")
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


def test_tool_role_is_read_only(release):
    import psycopg
    import pytest as pt
    with psycopg.connect("dbname=kct_client", autocommit=True) as c:
        c.execute("SET ROLE kc_agent")
        assert c.execute("SELECT count(*) FROM kc_core.rule").fetchone()[0] > 0
        c.execute("INSERT INTO kc_client.tool_audit (tool, release_id, status, duration_ms) VALUES ('t','t','t',0)")
        for sql in ("UPDATE kc_core.rule SET payload = '{}'", "INSERT INTO kc_client.sync_log VALUES (now(), 'x')",
                    "DELETE FROM kc_client.tool_audit"):
            with pt.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)


def test_ontology_file(release):
    from rdflib import OWL, RDF, Graph
    g = Graph().parse(ROOT / "kanini_context/release/kanini-context.ttl", format="turtle")
    classes = {str(s).rsplit("#", 1)[1] for s in g.subjects(RDF.type, OWL.Class)}
    assert {"Rule", "CoverageCriteria", "PaProgram", "Lcd"} <= classes
    assert len(set(g.subjects(RDF.type, OWL.ObjectProperty))) == 17


# --- decision-card guard and code-list screen ------------------------------------------------------------------

def _card(text, evals, orders):
    from types import SimpleNamespace
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from propel.agent.authorization_agent.agent import after_model
    resp = LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))
    after_model(SimpleNamespace(state={"propel:evals": evals, "propel:orders": orders}), resp)
    return resp.content.parts[0].text


def test_card_guard_keeps_a_right_card_in_any_layout():
    ev = {"uhc-ma|TN|HCPCS|J1439|2026-11-15": {"outcome": "pa_required", "safe_to_act": True}}
    orders = {"ord-004": ["uhc-ma", "TN", "J1439", "2026-11-15"]}
    right = "Decision card · Maria (synthetic) · ord-004\n**Outcome:** PA required · safe to act\nWhy: listed"
    assert _card(right, ev, orders) == right                         # order id not first, markdown bold
    no_id = "Decision card\nOutcome: PA required · safe to act"
    assert _card(no_id, ev, orders) == no_id                         # one evaluation in the session
    wrong = "Decision card · ord-004\n- **Outcome:** No PA · safe to act"
    fixed = _card(wrong, ev, orders)
    assert "Outcome: PA required · safe to act" in fixed and "No PA" not in fixed


def test_card_guard_without_an_evaluation_says_not_known():
    out = _card("Decision card · ord-001\nOutcome: No PA · safe to act", {}, {})
    assert "Outcome: Not known (route to staff) · not safe to act" in out and "No PA" not in out


def test_code_list_screen_fails_when_a_screened_code_is_listed(monkeypatch, tmp_path):
    from kanini_context.curation import checks
    manifest = {k: dict(v) for k, v in checks.MANIFEST.items()}
    for sid in ("cms-asc-pa-demo", "cms-asc-pa-demo-codes"):
        manifest[sid]["sha256"] = "0" * 64                           # as if saved
        (tmp_path / manifest[sid]["file"]).write_bytes(b"")
    monkeypatch.setattr(checks, "RAW", tmp_path)
    monkeypatch.setattr(checks, "MANIFEST", manifest)
    real_pages, real_locate = checks.pages, checks.locate_text
    asc = manifest["cms-asc-pa-demo"]["file"]
    asc_text = ("for dates of service on or after January 19, 2026 California, Florida, Texas, Arizona, Ohio, Tennessee, "
                "Pennsylvania, Maryland, Georgia, and New York blepharoplasty, botulinum toxin injections, panniculectomy, "
                "rhinoplasty, and vein ablation")
    monkeypatch.setattr(checks, "pages", lambda f: ["J0585 J0586 J0885 64612"] if f == manifest["cms-asc-pa-demo-codes"]["file"]
                        else [asc_text] if f == asc else real_pages(f))
    monkeypatch.setattr(checks, "locate_text", lambda f, loc: asc_text if f == asc else real_locate(f, loc))
    rep = checks.check_file(ROOT / "kanini_context/rules/cms-pa-programs.json")
    assert any("J0885 (esa-ckd-nd) is on the program's code list" in e for e in rep.errors)


def test_model_sees_a_trimmed_single_copy_of_rules_evaluate(db):
    # The agent hands the model one plain copy with the fields the card uses; the decision log keeps the full packet.
    from propel.agent.authorization_agent.agent import _slim_evaluate
    full = db.execute("SELECT kc_api.evaluate_order('uhc-ma','TN','HCPCS|J1439',%s::date,%s::jsonb)",
                      (DOS, json.dumps(facts(diagnosis_codes=["D50.9"])))).fetchone()[0]
    slim = _slim_evaluate(full)
    for k in ("outcome", "safe_to_act", "next_step_detail", "pack_release"):
        assert slim[k] == full[k]
    assert slim["primary_rule"]["rule_ref"] == full["primary_rule"]["rule_ref"] and slim["primary_rule"].get("quote")
    assert "sources" not in slim and "rule_refs" not in slim
    assert len(json.dumps(slim)) < len(json.dumps(full)) / 2


def test_audit_row_holds_no_question_or_facts(kc, db):
    # Spec 5.5: a valid call writes one audit row, and the audit table has no column that could hold the question or facts.
    before = db.execute("SELECT count(*) FROM kc_client.tool_audit").fetchone()[0]
    ev(kc, hgb_g_dl=9.4)
    assert db.execute("SELECT count(*) FROM kc_client.tool_audit").fetchone()[0] == before + 1
    cols = {r[0] for r in db.execute("""SELECT column_name FROM information_schema.columns
                                        WHERE table_schema = 'kc_client' AND table_name = 'tool_audit'""").fetchall()}
    assert cols == {"audit_id", "called_at", "tool", "release_id", "status", "rule_ids", "duration_ms"}


def test_l34356_wording_is_recorded_as_written_and_flagged(db):
    # Scope, section 5: L34356 labels its CKD hemoglobin criterion "Anemia of ESRD"; the rule keeps it, with a note.
    [(note,)] = db.execute("""SELECT payload->>'wording_note' FROM kc_core.rule
                              WHERE rule_key = 'kc:rule/esa-ckd-nd/L34356' AND status = 'active' AND upper_inf(recorded)""").fetchall()
    assert "Anemia of ESRD" in note and "flagged for the reviewer" in note


def test_card_check_compares_meaning_not_wording():
    from propel.agent.authorization_agent.agent import outcome_line_agrees
    ev = {"outcome": "no_pa", "safe_to_act": True}
    assert outcome_line_agrees("**Outcome:** No PA – Safe to act (LCD L39237)", ev)
    assert not outcome_line_agrees("Outcome: No PA · not safe to act", ev)
    assert not outcome_line_agrees("Outcome: PA required · safe to act", ev)
    held = {"outcome": "unknown", "safe_to_act": False}
    assert outcome_line_agrees("Outcome: Not known (route to staff) · not safe to act", held)
    assert not outcome_line_agrees("Outcome: Not yet known · not safe to act", held)
    assert outcome_line_agrees("Outcome: Other gate · not safe to act", {"outcome": "other_gate", "safe_to_act": False})


def test_record_request_without_a_card_gets_one_from_the_rules():
    # GPT-5 mini often sends journey_record with no text; staff must still see the card before they approve.
    from types import SimpleNamespace
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from propel.agent.authorization_agent.agent import _order_key, after_model
    ev = {"outcome": "no_pa", "safe_to_act": True,
          "card": {"reasons": ["Hgb 9.4 < 10", "eGFR 38 < 45"], "next_step_detail": "Proceed with the order",
                   "primary_rule": {"rule_ref": "kc:rule/esa-ckd-nd/L39237@2026-09-17", "citation": "LCD L39237", "page": 7}}}
    order = ["original-medicare", "TN", "J0885", "2026-11-20"]
    ctx = SimpleNamespace(state={"propel:orders": {"ord-001": order}, "propel:evals": {_order_key(*order): ev}})
    resp = LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name="journey_record", args={"order_id": "ord-001"}))]))
    after_model(ctx, resp)
    text, call = resp.content.parts[0].text, resp.content.parts[1].function_call
    assert "Outcome: No PA · safe to act" in text and "Why: eGFR 38 < 45" in text and "LCD L39237, p. 7" in text
    assert call.name == "journey_record"


def test_lane_a_reads_the_library_without_the_pack(monkeypatch):
    # The plain-LLM baseline: RAG over every saved document, or all of them whole; nothing taken from the pack.
    monkeypatch.setenv("PROPEL_RAG", "bm25")
    from propel.baseline import compare, library
    prompt, sent = compare.prompt_for("maria-uhc-esa", "rag")
    assert sent["method"] == "BM25 keyword ranking" and len(sent["passages"]) == library.TOP_K
    assert "kc:rule" not in prompt and "rule_ref" not in prompt
    whole, sent_whole = compare.prompt_for("maria-uhc-esa", "whole")
    assert len(sent_whole["documents"]) == len(library.documents()) and len(whole) > 5 * len(prompt)


def test_no_context_agent_tools_and_card(monkeypatch):
    # Lane A's agent: the same model and patient tool as B, document tools instead of Kanini Context.
    monkeypatch.setenv("PROPEL_RAG", "bm25")
    from propel.agent.no_context_agent import agent as N
    names = {getattr(t, "name", None) or getattr(getattr(t, "func", None), "__name__", "") for t in N.build_agent(model="x").tools}
    assert {"list_documents", "search_documents", "read_pages"} <= names
    assert not any("rules" in n or "context" in n for n in names if n)
    assert len(N.read_pages("mcd-lcd-l39237", [1, 2, 3, 4, 5])["pages"]) == N.MAX_PAGES
    assert N.search_documents("ESA hemoglobin Tennessee")["passages"]
    assert N.card_outcome("Decision card · ord-1\n**Outcome:** No PA – safe to act") == "no_pa"
    assert N.card_outcome("Outcome: Not yet known (facts needed) · not safe to act") == "needs_facts"
    assert N.card_outcome("no card here") is None


def test_what_broke_names_the_wrong_answers_and_the_missing_page():
    # propel/baseline/failures.py: from a report, each wrong answer set against the rules, and whether search found
    # the page the rule rests on.
    from propel.baseline import failures
    rep = {"at": "2026-11-13T14:42:57+00:00", "scenarios": {"1": {
        "patient": "maria-oh", "reference_outcome": "no_pa",
        "A_rag": {"runs": 5, "matches_rules": 0, "outcomes": {"other_gate": 5}, "tokens_in": 8000},
        "A_rag_sent": {"passages": [{"title": "Billing and Coding Article A58982, Erythropoiesis Stimulating Agents (for L39237)", "page": 4}]}}}}
    x = failures.explain(rep)["orders"][0]
    assert x["rules"] == "no_pa" and x["search"]["broke"][0]["answer"] == "other_gate"
    assert "Tennessee's eGFR limit" in x["search"]["broke"][0]["why"]
    assert x["search"]["deciding_page_found"] is False and x["search"]["other_region"] == 1
    md = failures.to_markdown({"at": rep["at"], "orders": [x]})
    assert "13 Nov 2026, 08:42 Central time" in md and "**not** among" in md


def test_lane_a_reads_the_patient_record_once_and_is_graded_on_its_last_card():
    # Lane A gets the same one-copy patient record as B, and a revised card counts, not the first draft.
    from types import SimpleNamespace
    from propel.agent.no_context_agent import agent as N
    assert N.build_agent(model="x").after_tool_callback is N.after_tool
    record = {"content": [{"type": "text", "text": "{}"}], "structuredContent": {"status": "found", "orders": []}}
    assert N.after_tool(SimpleNamespace(name="journey_get"), {}, None, record) == {"status": "found", "orders": []}
    assert N.card_outcome("Outcome: Other gate · not safe to act\n…read page 8…\nOutcome: No PA · safe to act") == "no_pa"


def test_what_broke_reads_every_source_line():
    from propel.baseline import failures
    card = "Decision card\nOutcome: No PA\n4) Why (from the documents):\n- meets criteria\n- Source: LCD L39237 p. 7\n- Source: LCD L34356 p. 8"
    assert failures.sources(card) == ["LCD L39237 p. 7", "LCD L34356 p. 8"]
    q = failures._quote({"outcome": "no_pa", "card": card})
    assert q["why"] == "meets criteria" and "L34356" in q["source"]
