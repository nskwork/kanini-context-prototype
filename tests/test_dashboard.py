"""The dashboard's API: the release graph and ontology it draws, and a workflow run streamed as events."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")


@pytest.fixture(scope="module")
def client(release):
    from fastapi.testclient import TestClient
    from propel.dashboard import server
    return TestClient(server.app)


def test_graph_is_the_release_graph(client, db):
    g = client.get("/api/graph").json()
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"Rule", "Authority", "Source", "Jurisdiction", "Plan"} <= kinds
    rule = next(n for n in g["nodes"] if n["kind"] == "Rule" and n["id"] == "kc:rule/esa-ckd-nd/L39237")
    assert rule["rule_kind"] == "coverage_criteria" and rule["citation"] == "LCD L39237"
    # an authority links to the states its bindings name (L39237 to Jurisdiction J, which holds Tennessee)
    ids = {n["gid"]: n for n in g["nodes"]}
    binds = {(ids[e["s"]].get("id"), ids[e["t"]].get("id")) for e in g["edges"] if e["rel"] == "BINDS"}
    assert ("LCD-L39237", "JJ") in binds


def test_ontology_counts(client):
    o = client.get("/api/ontology").json()
    assert o["counts"]["classes"] == 53 and o["counts"]["relations"] == 17


def test_workflow_run_streams_the_answer(client):
    rid = client.post("/api/run", json={"patient": "maria-uhc", "mode": "workflow"}).json()["run"]
    events = []
    with client.stream("GET", f"/api/run/{rid}/events") as s:
        for line in s.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    res = next(e for e in events if e["kind"] == "tool_result")
    assert res["payload"]["outcome"] == "pa_required" and events[-1]["llm_calls"] == 0
