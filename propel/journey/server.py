"""Propel journey service (mock): the patient's coverage, order and facts, and the decision log.

This side holds who the patient is. Kanini Context never receives a name or an identifier: the agent passes it
only plan, state, code, date and registered facts.

  journey_get     coverage, order and facts for a patient (or for one order)
  journey_record  writes the staff-approved decision to decisions.jsonl, with ruleRef@edition

Two modes:
  structured (default)  each order carries ready-made rules_evaluate arguments, built from the patient's registered
                        facts; the model passes them unchanged
  raw chart (--raw)     the chart instead: labs with dates and units, diagnoses, clinical notes and the order text,
                        with no ready-made arguments and no facts; the model builds the facts itself (charts.json)

The negative test cases (charts.json "negatives") are served in both modes; in structured mode with the facts a
careful reader would take from their chart.

    python -m propel.journey.server          # stdio, as the agent starts it
    python -m propel.journey.server --raw    # raw chart mode
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
PATIENTS = json.loads((HERE / "patients.json").read_text(encoding="utf-8"))["patients"]
CHARTS = json.loads((HERE / "charts.json").read_text(encoding="utf-8"))
NEGATIVES = {pid: {**n, "facts": n["expected_facts"]} for pid, n in CHARTS["negatives"].items()}
ALL = {**PATIENTS, **NEGATIVES}
RAW = "--raw" in sys.argv or os.environ.get("PROPEL_JOURNEY_RAW") == "1"


def chart_of(pid: str) -> dict:
    """The synthetic chart for a patient: labs with dates and units, diagnoses, notes."""
    return CHARTS["charts"].get(pid) or CHARTS["negatives"].get(pid, {}).get("chart") or {}
LOG = Path(os.environ.get("PROPEL_DECISION_LOG", HERE / "decisions.jsonl"))
REVIEWER = os.environ.get("PROPEL_REVIEWER", "demo-staff")

mcp = FastMCP("propel-journey", instructions="Patient journey data for the authorization agent, and the decision log.")


def find_order(order_id: str):
    for pid, p in ALL.items():
        for o in p["orders"]:
            if o["order_id"] == order_id:
                return pid, p, o
    return None, None, None


def journey_get(patient_id: str = "", order_id: str = "") -> dict:
    """Coverage (plan, state), the open order (code, date of service, encounter type, urgency) and the patient's facts
    (labs, diagnoses, dialysis status) for a patient id such as maria-tn, or for one order id such as ord-001."""
    if order_id:
        pid, p, o = find_order(order_id)
        if not p:
            return {"status": "not_found", "order_id": order_id}
        orders = [o]
    else:
        pid, p = patient_id, ALL.get(patient_id)
        if not p:
            return {"status": "not_found", "patient_id": patient_id, "known_patients": sorted(PATIENTS)}
        orders = p["orders"]
    if RAW:   # the chart as it is: no ready-made arguments, no facts
        return {"status": "found", "patient_id": pid, "display_name": p["display_name"], "coverage": p["coverage"],
                "orders": [dict(o) for o in orders], "chart": chart_of(pid)}
    # each order carries the exact arguments for rules_evaluate, so the model passes them unchanged
    orders = [{**o, "rules_evaluate_args": {
                   "plan": p["coverage"]["plan"], "state": p["coverage"]["state"], "code": o.get("code", ""),
                   "date_of_service": o["date_of_service"],
                   "facts": {**p["facts"], "encounter_type": o["encounter_type"], "urgency": o["urgency"],
                             "request_type": o["request_type"]}}} for o in orders]
    return {"status": "found", "patient_id": pid, "display_name": p["display_name"], "coverage": p["coverage"],
            "facts": p["facts"], "orders": orders}


def journey_record(order_id: str, outcome: str, rule_ref: str, source: str, safe_to_act: bool,
                   pack_release: str = "", next_step: str = "", recorded_as_of: str = "", note: str = "") -> dict:
    """Record the decision for an order after staff approval. The decision fields must be copied from the
    rules_evaluate result for this order (outcome, primary_rule.rule_ref, primary_rule.citation, safe_to_act,
    pack_release, next_step, recorded_as_of). Staff approve or reject the call before it runs. With pack_release and
    recorded_as_of the decision can be replayed later (rules_evaluate with as_recorded)."""
    _, p, o = find_order(order_id)
    if not o:
        return {"status": "not_found", "order_id": order_id}
    entry = {"order": order_id, "decision": "approved", "outcome": outcome, "ruleRef": rule_ref, "source": source,
             "safe_to_act": safe_to_act, "next_step": next_step, "pack_release": pack_release, "recorded_as_of": recorded_as_of,
             "approved_by": REVIEWER, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if note:
        entry["note"] = note[:500]
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return {"status": "recorded", "entry": entry}


_GET_RAW_DOC = """The patient's chart for a patient id such as maria-tn, or for one order id such as ord-001: coverage
(plan, state), the open order (code, date of service, encounter type, urgency, request type, description) and the
chart as recorded (labs with value, unit and date; diagnoses with status and date noted; clinical notes). The chart is
not pre-processed: read it to build the facts."""
if RAW:
    journey_get.__doc__ = _GET_RAW_DOC
mcp.tool(journey_get)
mcp.tool(journey_record)


if __name__ == "__main__":
    mcp.run(show_banner=False)
