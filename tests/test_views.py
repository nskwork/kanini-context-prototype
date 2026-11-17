"""The read-only views and kc_api.explain_order (views.sql). They are for people, not agents, and must never
change an answer: explain_order wraps evaluate_order, so its answer equals what rules_evaluate returns."""
from __future__ import annotations

import json

import pytest

from conftest import ROOT

PATIENTS = json.loads((ROOT / "propel" / "journey" / "patients.json").read_text())["patients"]


def order_args():
    """The exact rules_evaluate arguments the patient-record server hands the agent, one per order."""
    for pid, p in PATIENTS.items():
        for o in p["orders"]:
            yield o["order_id"], {
                "plan": p["coverage"]["plan"], "state": p["coverage"]["state"], "code": o["code"],
                "date_of_service": o["date_of_service"],
                "facts": {**p["facts"], "encounter_type": o["encounter_type"], "urgency": o["urgency"],
                          "request_type": o["request_type"]}}


ORDERS = list(order_args())
EDGE = [("unknown state", {"plan": "original-medicare", "state": "XX", "code": "J0885",
                           "date_of_service": "2026-11-15", "facts": {}}),
        ("unknown code", {"plan": "original-medicare", "state": "OH", "code": "J9999",
                          "date_of_service": "2026-11-15", "facts": {}}),
        ("unknown plan", {"plan": "acme-gold", "state": "OH", "code": "J0885",
                          "date_of_service": "2026-11-15", "facts": {}})]


def explain(db, a):
    return db.execute("SELECT kc_api.explain_order(%s, %s, %s, %s::date, %s::jsonb)",
                      (a["plan"], a["state"], a["code"], a["date_of_service"], json.dumps(a["facts"]))).fetchone()[0]


def same(x, y):
    return (x["outcome"], x["safe_to_act"], (x.get("primary_rule") or {}).get("rule_ref"), x.get("rule_refs")) == \
           (y["outcome"], y["safe_to_act"], (y.get("primary_rule") or {}).get("rule_ref"), y.get("rule_refs"))


@pytest.mark.parametrize("name,args", ORDERS + EDGE, ids=[o for o, _ in ORDERS] + [n for n, _ in EDGE])
def test_explain_order_answer_equals_rules_evaluate(db, kc, name, args):
    tool = kc("rules_evaluate", **args)
    trace = explain(db, args)
    assert same(trace["answer"], tool), name
    assert set(trace) == {"answer", "coverage", "policies", "programs"}
    assert isinstance(trace["coverage"], dict), name
    if name in {"unknown state", "unknown code", "unknown plan"}:
        assert trace["programs"] == [], name


def test_explain_order_ohio_trace(db):
    args = dict(ORDERS)["ord-003"]
    t = explain(db, args)
    cov = t["coverage"]
    assert (cov["state"], cov["state_name"], cov["contractor"]["code"], cov["contractor"]["contractor"]) == \
           ("OH", "Ohio", "J15", "CGS Administrators")
    progs = {p["program"]: p for p in t["programs"]}
    wiser = progs["CMS WISeR Model, performance year 1"]
    assert wiser["screened"] and wiser["used"] and wiser["page"] and wiser["finding"]
    # every screened program the answer relied on is marked used, and nothing unscreened is
    used = {p["rule_ref"] for p in t["programs"] if p["used"]}
    assert used == {r for r in t["answer"]["rule_refs"] if "/no-pa/" in r}
    assert all(p["screened"] for p in t["programs"] if p["used"])
    assert {r["citation"] for r in t["policies"]} >= {"LCD L34356", "Article A56462"}


def test_documents_view(db):
    rows = db.execute("SELECT source_id, document_group, document_type, fingerprint, freshness, rule_count, role FROM kc_views.documents").fetchall()
    n = db.execute("SELECT count(*) FROM kc_core.source").fetchone()[0]
    assert len(rows) == n
    by = {r[0]: r for r in rows}
    lcd = by["mcd-lcd-l39237"]
    assert lcd[1] == "Medicare coverage policies" and lcd[2] == "Medicare coverage policy (LCD)" and len(lcd[3]) == 12 and lcd[5] >= 1
    assert {r[1] for r in rows} <= {"Medicare coverage policies", "Payer documents", "Federal regulations", "CMS program lists"}
    assert all(r[4] in {"current", "aging", "stale", "never checked", "not saved"} for r in rows)
    assert all(r[6] for r in rows)


def test_rules_view_matches_active_rules(db):
    n = db.execute("SELECT count(*) FROM kc_core.rule WHERE status = 'active' AND upper_inf(recorded)").fetchone()[0]
    rows = db.execute("SELECT rule_ref, rule_type, summary, citation, approved_by FROM kc_views.rules").fetchall()
    assert len(rows) == n
    assert all(r[0] and r[1] and r[2] and r[3] for r in rows)
    says = {r[0].split("@")[0]: r[2] for r in rows}
    assert says["kc:rule/esa-ckd-nd/L39237"].startswith("Covered when all of these hold: 1) ")
    assert "below 45 mL/min/1.73 m²" in says["kc:rule/esa-ckd-nd/L39237"]
    assert says["kc:rule/part-b-drug/422.568-b3"].startswith("Decision due within 72 hours")
    assert "emergency or urgent" in says["kc:rule/all-items/uhc-ma-emergency-urgent"]


def test_scope_programs_and_codes(db):
    oh = db.execute("SELECT place_name FROM kc_views.policy_scopes WHERE place_code = 'J15'").fetchall()
    assert oh and all("CGS Administrators" in p[0] and "OH" in p[0] for p in oh)
    progs = db.execute("SELECT program, is_checked, finding FROM kc_views.medicare_programs").fetchall()
    n_prog = db.execute("SELECT count(*) FROM kc_core.authority WHERE kind = 'pa_program'").fetchone()[0]
    assert len(progs) == n_prog and all(p[2] for p in progs)
    codes = {r[0] for r in db.execute("SELECT code FROM kc_views.codes WHERE code_group = 'ESA, CKD not on dialysis'")}
    assert {"J0885"} <= codes


def test_every_view_and_column_is_described(db):
    """Each view and each of its columns carries a description, so \\d+ in psql explains it."""
    missing = db.execute("""SELECT c.relname || '.' || a.attname FROM pg_attribute a
                              JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                             WHERE n.nspname = 'kc_views' AND c.relkind = 'v' AND a.attnum > 0
                               AND col_description(c.oid, a.attnum) IS NULL""").fetchall()
    assert not missing, missing
    views = db.execute("""SELECT c.relname, obj_description(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                           WHERE n.nspname = 'kc_views' AND c.relkind = 'v'""").fetchall()
    assert {v for v, _ in views} == {"documents", "rules", "policy_scopes", "medicare_programs", "codes", "releases"}
    assert all(d for _, d in views)


def test_effective_to_is_the_last_day(db):
    """effective_to is inclusive: WISeR year 1 ends on 31 Dec 2026, not 1 Jan 2027."""
    row = db.execute("SELECT effective_to FROM kc_views.policy_scopes WHERE citation LIKE 'CMS WISeR%' LIMIT 1").fetchone()
    assert str(row[0]) == "2026-12-31"


def test_views_change_no_table(db):
    """views.sql only adds read-only objects: no table, and every view is plain SELECT."""
    text = (ROOT / "kanini_context" / "db" / "views.sql").read_text().upper()
    assert "CREATE TABLE" not in text and "ALTER TABLE" not in text and "INSERT " not in text
    assert "UPDATE KC_" not in text and "DELETE " not in text


def test_explain_order_lists_only_checks_made_for_this_item(db):
    """A program list counts as checked only when it was checked for the ordered item: the iron infusion (J1439) was
    never checked against the CMS lists, so no program shows the ESA finding for it; past the pack's dates, none."""
    t = explain(db, {"plan": "original-medicare", "state": "OH", "code": "J1439", "date_of_service": "2026-11-15", "facts": {}})
    assert t["programs"] and not any(p["screened"] for p in t["programs"])
    late = explain(db, {"plan": "original-medicare", "state": "OH", "code": "J0885", "date_of_service": "2027-06-15", "facts": {}})
    assert late["answer"]["reason"] == "pack_not_current_for_date" and late["programs"] == []


def test_codes_include_code_lists_defined_by_a_rule(db):
    groups = {r[0] for r in db.execute("SELECT code_group FROM kc_views.codes WHERE code = 'J1439'")}
    assert {"All drug codes", "All items"} <= groups


def test_summaries_read_as_the_source_says(db):
    says = dict(db.execute("SELECT rule_key, summary FROM kc_views.rules").fetchall())
    l39 = says["kc:rule/esa-ckd-nd/L39237"]
    assert "Hematocrit below 30%" in l39 and "30 %" not in l39
    assert "at initiation" in l39                                    # Hgb, Hct and eGFR apply when therapy starts
    assert "1 month at initiation" not in l39                        # the creatinine date applies on every request
    assert says["kc:rule/esa-ckd-nd/L34356"].endswith("These apply at initiation of therapy.")
    assert "; or " in says["kc:rule/J1439/uhc-step-therapy"]


def test_plans_and_programs_show_by_name(db):
    rows = db.execute("SELECT applies_in FROM kc_views.rules WHERE rule_key LIKE '%uhc%'").fetchall()
    assert rows and all("uhc-ma" not in r[0] and "UHC Medicare Advantage" in r[0] for r in rows)
    plan = db.execute("SELECT plan, plan_code FROM kc_views.policy_scopes WHERE plan_code = 'uhc-ma' LIMIT 1").fetchone()
    assert plan == ("UHC Medicare Advantage", "uhc-ma")
    wiser = db.execute("SELECT applies_in FROM kc_views.medicare_programs WHERE program LIKE 'CMS WISeR%'").fetchone()[0]
    assert "from 2026-01-01 until 2026-12-31" in wiser


def test_documents_show_a_pending_change(db):
    row = db.execute("SELECT is_change_pending, change_applies_from FROM kc_views.documents WHERE source_id = 'uhc-ma-partb-step-therapy'").fetchone()
    assert row[0] is True and str(row[1]) == "2027-01-01"


def test_releases_view(db):
    rows = db.execute("SELECT release_id, covers_through FROM kc_views.releases").fetchall()
    assert rows and all(r[0] for r in rows)


def test_ohio_trace_matches_the_stored_rows(db):
    """The Ohio trace, checked against the tables directly rather than through the resolver: the programs are every
    CMS program bound on the date to Ohio, its contractor region or the nation; the policies are the ones bound to
    Ohio's region; the hemoglobin limit is the stored one."""
    t = explain(db, dict(ORDERS)["ord-003"])
    stored = {r[0] for r in db.execute("""
        SELECT DISTINCT a.citation FROM kc_core.authority a JOIN kc_core.authority_binding b ON b.authority_id = a.entity_id
          JOIN kc_core.jurisdiction j ON j.entity_id = b.jurisdiction_id
         WHERE a.kind = 'pa_program' AND upper_inf(b.recorded) AND b.valid @> date '2026-11-15'
           AND b.item_scope IN ('all', 'drugs')
           AND (j.code IN ('US', 'OH') OR j.entity_id IN (
                 SELECT e.to_id FROM kc_core.edge e JOIN kc_core.jurisdiction s ON s.entity_id = e.from_id
                  WHERE s.code = 'OH' AND e.relation_type = 'within' AND upper_inf(e.recorded)))""")}
    assert {p["program"] for p in t["programs"]} == stored
    policies = {r[0] for r in db.execute("""
        SELECT DISTINCT a.citation FROM kc_core.authority a JOIN kc_core.authority_binding b ON b.authority_id = a.entity_id
          JOIN kc_core.jurisdiction j ON j.entity_id = b.jurisdiction_id
         WHERE a.kind IN ('lcd', 'billing_article') AND upper_inf(b.recorded) AND j.code = 'J15'""")}
    assert {r["citation"] for r in t["policies"] if r["basis"] != "stated_by_exhaustion"} >= policies == {"LCD L34356", "Article A56462"}
    limit = db.execute("""SELECT q.test ->> 'value' FROM kc_core.rule_requirement q JOIN kc_core.rule r USING (rule_id)
                           JOIN kc_core.data_element d ON d.entity_id = q.data_element_id
                          WHERE r.rule_key = 'kc:rule/esa-ckd-nd/L34356' AND upper_inf(r.recorded) AND d.name = 'Hemoglobin'""").fetchone()[0]
    items = [i for p in t["answer"]["coverage_criteria"]["policies"] if p["citation"] == "LCD L34356"
             for g in p["groups"] for i in g["items"] if i.get("element") == "Hemoglobin"]
    assert items and items[0]["status"] == "met" and f"< {limit}" in items[0]["detail"]
