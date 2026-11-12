"""Propel journey service (mock): the patient's coverage, order and facts, and the decision log.

This side holds who the patient is. Kanini Context never receives a name or an identifier: the agent passes it
only plan, state, code, date and registered facts.

  journey_get     coverage, order and facts for a patient (or for one order)
  journey_record  writes the staff-approved decision to decisions.jsonl, with ruleRef@edition

    python -m propel.journey.server          # stdio, as the agent starts it
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
PATIENTS = json.loads((HERE / "patients.json").read_text())["patients"]
LOG = Path(os.environ.get("PROPEL_DECISION_LOG", HERE / "decisions.jsonl"))
REVIEWER = os.environ.get("PROPEL_REVIEWER", "demo-staff")

mcp = FastMCP("propel-journey", instructions="Patient journey data for the authorization agent, and the decision log.")


def find_order(order_id: str):
    for pid, p in PATIENTS.items():
        for o in p["orders"]:
            if o["order_id"] == order_id:
                return pid, p, o
    return None, None, None


@mcp.tool
def journey_get(patient_id: str = "", order_id: str = "") -> dict:
    """Coverage (plan, state), the open order (code, date of service, encounter type, urgency) and the patient's facts
    (labs, diagnoses, dialysis status) for a patient id such as maria-tn, or for one order id such as ord-001."""
    if order_id:
        pid, p, o = find_order(order_id)
        if not p:
            return {"status": "not_found", "order_id": order_id}
        orders = [o]
    else:
        pid, p = patient_id, PATIENTS.get(patient_id)
        if not p:
            return {"status": "not_found", "patient_id": patient_id, "known_patients": sorted(PATIENTS)}
        orders = p["orders"]
    # each order carries the exact arguments for rules_evaluate, so the model passes them unchanged
    orders = [{**o, "rules_evaluate_args": {
                   "plan": p["coverage"]["plan"], "state": p["coverage"]["state"], "code": o["code"],
                   "date_of_service": o["date_of_service"],
                   "facts": {**p["facts"], "encounter_type": o["encounter_type"], "urgency": o["urgency"],
                             "request_type": o["request_type"]}}} for o in orders]
    return {"status": "found", "patient_id": pid, "display_name": p["display_name"], "coverage": p["coverage"],
            "facts": p["facts"], "orders": orders}


@mcp.tool
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
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return {"status": "recorded", "entry": entry}


if __name__ == "__main__":
    mcp.run(show_banner=False)
