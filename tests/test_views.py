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


def test_sources_view(db):
    rows = db.execute("SELECT source_id, kind, fingerprint, freshness, rules, used_for FROM kc_api.v_sources").fetchall()
    assert rows
    by = {r[0]: r for r in rows}
    lcd = by["mcd-lcd-l39237"]
    assert lcd[1] == "Medicare coverage policy (LCD)" and len(lcd[2]) == 12 and lcd[4] >= 1
    assert all(r[3] in {"current", "aging", "stale", "never checked", "not saved"} for r in rows)
    assert all(r[5] for r in rows)


def test_rules_view_matches_active_rules(db):
    n = db.execute("SELECT count(*) FROM kc_core.rule WHERE status = 'active' AND upper_inf(recorded)").fetchone()[0]
    rows = db.execute("SELECT rule_ref, kind, says, source, approved_by FROM kc_api.v_rules").fetchall()
    assert len(rows) == n
    assert all(r[0] and r[1] and r[2] and r[3] for r in rows)
    says = {r[0].split("@")[0]: r[2] for r in rows}
    assert says["kc:rule/esa-ckd-nd/L39237"].startswith("Covered when all of these hold: 1) ")
    assert "below 45 mL/min/1.73 m²" in says["kc:rule/esa-ckd-nd/L39237"]
    assert says["kc:rule/part-b-drug/422.568-b3"].startswith("Decision due within 72 hours")
    assert "emergency or urgent" in says["kc:rule/all-items/uhc-ma-emergency-urgent"]


def test_bindings_and_screens_and_codes(db):
    oh = db.execute("SELECT place FROM kc_api.v_bindings WHERE place_code = 'J15'").fetchall()
    assert oh and all("CGS Administrators" in p[0] and "OH" in p[0] for p in oh)
    screens = db.execute("SELECT program, screened, finding FROM kc_api.v_program_screens").fetchall()
    n_prog = db.execute("SELECT count(*) FROM kc_core.authority WHERE kind = 'pa_program'").fetchone()[0]
    assert len(screens) == n_prog and all(s[2] for s in screens)
    codes = {r[0] for r in db.execute("SELECT code FROM kc_api.v_codes WHERE concept = 'ESA, CKD not on dialysis'")}
    assert {"J0885"} <= codes


def test_views_change_no_table(db):
    """views.sql only adds read-only objects in kc_api: no table, and every view is plain SELECT."""
    text = (ROOT / "kanini_context" / "db" / "views.sql").read_text().upper()
    assert "CREATE TABLE" not in text and "ALTER TABLE" not in text and "INSERT " not in text
    assert "UPDATE KC_" not in text and "DELETE " not in text
