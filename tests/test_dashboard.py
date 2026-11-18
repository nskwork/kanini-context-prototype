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


def test_pages_read_the_views(client, db):
    """The Sources, Knowledge and Ask pages read the release's read-only views; each answer carries its SQL."""
    s = client.get("/api/sources").json()
    n_src = db.execute("SELECT count(*) FROM kc_core.source").fetchone()[0]
    assert len(s["documents"]) == n_src and s["sql"]["documents"].startswith("SELECT")
    assert {r["code"] for r in s["regions"]} >= {"J15", "JJ"}
    assert s["releases"] and s["releases"][0]["covers_through"]
    lcd = next(x for x in s["documents"] if x["source_id"] == "mcd-lcd-l39237")
    assert [r["rule_type"] for r in lcd["rules"]] == ["Coverage criteria"]
    k = client.get("/api/knowledge").json()
    assert {"rules", "policy_scopes", "medicare_programs", "codes", "sql"} <= set(k) and k["rules"]


def test_explain_matches_the_workflow_answer(client, db):
    """The Ask page's "Why is this right?" trace gives the same answer lane C streams for the order."""
    e = client.get("/api/explain", params={"patient": "maria-oh"}).json()
    rid = client.post("/api/run", json={"patient": "maria-oh", "mode": "workflow"}).json()["run"]
    with client.stream("GET", f"/api/run/{rid}/events") as st:
        ans = next(json.loads(line[5:])["payload"] for line in st.iter_lines()
                   if line.startswith("data:") and '"tool_result"' in line)
    assert (e["answer"]["outcome"], e["answer"]["safe_to_act"], e["answer"].get("primary_rule")) == \
           (ans["outcome"], ans["safe_to_act"], ans.get("primary_rule"))
    assert e["coverage"]["contractor"]["code"] == "J15" and e["sql"]["trace"].startswith("SELECT kc_api.explain_order(")
    assert client.get("/api/explain", params={"patient": "nobody"}).status_code == 404


def test_ask_about_the_rules_answers_from_the_stored_text(client, db):
    """"Ask about this rule": the rules whose summary or source quote has every word of the question, limited to one
    case's rules when asked from that case; nothing is written by a model."""
    refs = db.execute("""SELECT kc_api.evaluate_order('original-medicare','OH','HCPCS|J0885','2026-11-15',
                         '{"dialysis_status":"none","diagnosis_codes":["D63.1","N18.31"],"hgb_g_dl":9.4,"egfr":52,
                         "creatinine_date":"2026-11-02"}') -> 'rule_refs'""").fetchone()[0]
    r = client.get("/api/ask", params={"q": "What hemoglobin level is required?", "refs": ",".join(refs)}).json()
    assert r["scope"] == len(refs) and r["words"][:1] == ["hemoglobin"]
    assert r["results"] and all(x["rule_ref"] in refs for x in r["results"])
    assert any("L34356" in x["citation"] for x in r["results"])
    one = client.get("/api/ask", params={"q": "dialysis", "refs": refs[0]}).json()
    assert one["scope"] == 1
    assert client.get("/api/ask", params={"q": "what is the"}).status_code == 400


def test_raw_chart_run_streams_the_facts_b_built(client, kc_http, monkeypatch):
    """Lane B in raw chart mode, with the scripted stand-in: the record server sends the chart, B builds the facts."""
    from propel.dashboard import server
    monkeypatch.setattr(server, "SCRIPTED", True)
    rid = client.post("/api/run", json={"patient": "maria-oh", "mode": "agent", "raw": True}).json()["run"]
    with client.stream("GET", f"/api/run/{rid}/events") as st:
        evs = [json.loads(line[5:]) for line in st.iter_lines() if line.startswith("data:")]
    assert evs[0]["raw"] is True
    call = next(e for e in evs if e["kind"] == "tool_call" and e["name"] == "rules_evaluate")
    assert call["args"]["facts"]["egfr"] == 52
    got = next(e for e in evs if e["kind"] == "tool_result" and e["name"] == "journey_get")
    assert "chart" in json.dumps(got) and "rules_evaluate_args" not in json.dumps(got)


def test_run_refuses_input_of_the_wrong_type(client):
    for body in ({"patient": ["maria-tn"]}, {"patient": "maria-tn", "mode": ["workflow"]}, {"patient": "nobody"}):
        assert client.post("/api/run", json=body).status_code == 400


def test_explain_shows_the_stored_rows_behind_each_step(client):
    """Each of the five steps carries the rows the views hold for it and the query that read them; the deciding
    rule's stored row is the answer's own rule."""
    e = client.get("/api/explain", params={"patient": "maria-oh"}).json()
    assert len(e["rows"]) == 5 and all(t["sql"].startswith("SELECT") and "error" not in t for s in e["rows"] for t in s)
    [region] = e["rows"][0]
    assert region["rows"] == [["OH", "Ohio", "J15", region["rows"][0][3]]]
    answer, rule = e["rows"][4]
    assert answer["rows"][0][answer["cols"].index("outcome")] == e["answer"]["outcome"]
    cited = rule["rows"][0][rule["cols"].index("citation")]
    assert cited == e["answer"]["primary_rule"]["citation"] == "LCD L34356"
    assert {"quote", "approved_by", "source_last_checked"} <= set(rule["cols"])
    progs = [r[0] for r in e["rows"][3][0]["rows"]]
    assert progs == sorted(g["program"] for g in e["programs"])   # the same programs explain_order checked
    uhc = client.get("/api/explain", params={"patient": "maria-uhc"}).json()
    assert uhc["rows"][0][0]["title"] == "Policies stored for this plan" and uhc["rows"][3][0]["rows"] == []
