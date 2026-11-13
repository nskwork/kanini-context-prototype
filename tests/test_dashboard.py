"""The dashboard's API: the release graph and ontology it draws, and a workflow run streamed as events."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")


@pytest.fixture(scope="module")
def client(release):
    from fastapi.testclient import TestClient
    from propel.dashboard import server
    with TestClient(server.app) as c:   # one event loop for the module, as under uvicorn, so background runs finish
        yield c


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


def test_documents_run_marks_the_ai_answer_against_the_rules(client, monkeypatch):
    # Mode A with the model call replaced: the page gets the prompt size, the AI's answer, and whether it matches the rules.
    import types
    import litellm

    async def fake(**kw):
        assert "DOCUMENTS" in kw["messages"][0]["content"]   # the policy pages went into the prompt
        msg = types.SimpleNamespace(content='{"outcome": "pa_required", "reasons": ["listed"], "citation": "UHC list"}')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)],
                                     usage=types.SimpleNamespace(prompt_tokens=31000, completion_tokens=400))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("PROPEL_RAG", "bm25")   # rank passages without the embeddings call
    monkeypatch.setattr(litellm, "acompletion", fake)
    rid = client.post("/api/run", json={"patient": "maria-uhc-esa", "mode": "documents"}).json()["run"]
    events = []
    with client.stream("GET", f"/api/run/{rid}/events") as s:
        for line in s.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    kinds = [e["kind"] for e in events]
    assert kinds[:2] == ["start", "doc_start"] and kinds[-1] == "done", events
    start = next(e for e in events if e["kind"] == "doc_start")
    assert start["prompt_tokens"] > 1000 and start["whole_tokens"] > start["prompt_tokens"]
    assert start["method"] == "BM25 keyword ranking" and len(start["documents"]) == 16   # RAG over the whole library
    ref = next(e for e in events if e["kind"] == "reference")
    assert ref["outcome"] == "unknown" and ref["matches"] is False   # the AI said PA required; the rules say route to staff


def test_schema_view_has_classes_fields_kinds_and_relations(client):
    s = client.get("/api/ontology").json()["schema"]
    ent = {e["name"]: e for e in s["entities"]}
    assert {"Rule", "Authority", "Source", "Plan", "Code"} <= set(ent)
    assert len(ent["Rule"]["fields"]) >= 20 and "Lcd" in ent["Authority"]["kinds"]
    assert {"rel": "states", "s": "Authority", "t": "Rule"} in s["relations"]


def test_lane_a_is_the_same_agent_without_kanini_context(client, monkeypatch):
    # Lane A live, with the scripted stand-in model: patient record, document search, page read, a card, and the
    # card's outcome marked against the rules' answer for the same order.
    from propel.dashboard import server
    monkeypatch.setattr(server, "SCRIPTED", True)
    monkeypatch.setenv("PROPEL_RAG", "bm25")
    rid = client.post("/api/run", json={"patient": "maria-oh", "mode": "nocontext"}).json()["run"]
    events = []
    with client.stream("GET", f"/api/run/{rid}/events") as s:
        for line in s.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    tools = [e["name"] for e in events if e["kind"] == "tool_call"]
    assert tools == ["journey_get", "search_documents", "read_pages"], events
    assert not {"context_applicable_rules", "rules_evaluate", "policy_search"} & set(tools)   # no Kanini Context
    assert next(e for e in events if e["kind"] == "answer")["answer"] == "unknown"
    ref = next(e for e in events if e["kind"] == "reference")
    assert ref["matches"] is (ref["outcome"] == "unknown") and events[-1]["kind"] == "done"
