"""Kanini Context dashboard: the demo in a browser.

Runs the same authorization agent and the same Kanini Context tools as `adk web`, and streams what happens to one
page: each model turn and its tokens, each tool call and its full result, the decision card, the staff approval, the
decision log. The knowledge graph and the ontology are read from the release; the comparison numbers from the latest
`propel/baseline/compare.py` report. Nothing on the page is staged: every animation follows an event from the run.

    python -m kanini_context.server.mcp_server --http 8765      # terminal 1: Kanini Context tools
    python -m propel.dashboard.server                      # terminal 2: http://127.0.0.1:8090

Options: --port N; --scripted runs the scripted stand-in model (tests/scripted_model.py) instead of GPT-5 mini,
for a rehearsal with no API key (the page says so).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from kanini_context import env  # noqa: E402  (loads .env)

import psycopg  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
PATIENTS = json.loads((ROOT / "propel/journey/patients.json").read_text())["patients"]
MANIFEST = {m["source_id"]: m for m in json.loads((ROOT / "kanini_context/sources/manifest.json").read_text())}
RESULTS = ROOT / "propel/baseline/results"
TTL = ROOT / "kanini_context/release/kanini-context.ttl"
LOG = Path(os.environ.get("PROPEL_DECISION_LOG", ROOT / "propel/journey/decisions.jsonl"))
SCRIPTED = False

app = FastAPI(title="Kanini Context dashboard")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def db() -> psycopg.Connection:
    return psycopg.connect(f"dbname={env.CLIENT_DB}", autocommit=True)


# --- What the page shows before any run ---------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    with db() as c:
        rel = c.execute("SELECT release_id, built_at, covers_through FROM kc_gov.release ORDER BY built_at DESC LIMIT 1").fetchone()
        rules = c.execute("SELECT count(*) FROM kc_core.rule WHERE upper_inf(recorded) AND status = 'active'").fetchone()[0]
    return {"release": rel[0] if rel else None, "covers_through": str(rel[2]) if rel else None, "rules": rules, "sources": len(MANIFEST),
            "model": "scripted stand-in (no API key)" if SCRIPTED else os.environ.get("PROPEL_MODEL", "openai/gpt-5-mini"),
            "scripted": SCRIPTED, "ai_key": bool(os.environ.get("OPENAI_API_KEY"))}


@app.get("/api/orders")
def orders():
    """The synthetic orders, with readable names from the pack: the plan, the state and each fact (name and unit)."""
    with db() as c:
        plans = {k: t.split(" (")[0] for k, t in c.execute(
            "SELECT e.natural_key, p.plan_type FROM kc_core.plan p JOIN kc_core.entity e ON e.entity_id = p.entity_id").fetchall()}
        payers = dict(c.execute("""SELECT e.natural_key, coalesce(py.short_name, py.legal_name) FROM kc_core.plan p
                                    JOIN kc_core.entity e ON e.entity_id = p.entity_id JOIN kc_core.payer py ON py.entity_id = p.payer_id""").fetchall())
        states = dict(c.execute("SELECT e.natural_key, j.name FROM kc_core.jurisdiction j JOIN kc_core.entity e ON e.entity_id = j.entity_id").fetchall())
        facts = {f: {"name": n, "unit": (u or "").replace("{1.73_m2}", "1.73 m²")} for f, n, u in c.execute(
            "SELECT DISTINCT ON (fact) fact, name, unit FROM kc_core.data_element WHERE fact IS NOT NULL ORDER BY fact, name").fetchall()}
    out = []
    for pid, p in PATIENTS.items():
        for o in p["orders"]:
            cov = p["coverage"]
            out.append({"patient": pid, "display_name": p["display_name"], "order_id": o["order_id"], "code": o["code"],
                        "description": o["description"], "date_of_service": o["date_of_service"],
                        "plan": cov["plan"], "plan_name": plans.get(cov["plan"], cov["plan"]), "payer": payers.get(cov["plan"], ""),
                        "state": cov["state"], "state_name": states.get(cov["state"], cov["state"]),
                        "facts": p["facts"], "fact_names": {k: facts.get(k, {"name": k, "unit": ""}) for k in p["facts"]}})
    return out


def _graph_name(c) -> str | None:
    row = c.execute("SELECT name FROM ag_catalog.ag_graph WHERE name LIKE 'ref_%' ORDER BY name DESC LIMIT 1").fetchone()
    return row[0] if row else None


@app.get("/api/graph")
def graph():
    """Every vertex and edge of the release's knowledge graph (Apache AGE), as built by release/build.py."""
    with db() as c:
        c.execute("LOAD 'age'")
        c.execute('SET search_path = ag_catalog, "$user", public')
        g = _graph_name(c)
        if not g:   # a release built without the graph (--no-graph): the page still loads, with an empty sky
            return {"graph": None, "nodes": [], "edges": []}
        nodes = [{**{("rule_kind" if k == "kind" else k): v for k, v in json.loads(str(p)).items() if k != "quote"},
                  "gid": str(i), "kind": json.loads(str(lbl))}
                 for i, lbl, p in c.execute(f"SELECT * FROM cypher('{g}', $$ MATCH (n) RETURN id(n), label(n), properties(n) $$)"
                                            " AS (i agtype, l agtype, p agtype)").fetchall()]
        edges = [{"s": str(a), "t": str(b), "rel": json.loads(str(lbl))}
                 for a, b, lbl in c.execute(f"SELECT * FROM cypher('{g}', $$ MATCH (a)-[e]->(b) RETURN id(a), id(b), label(e) $$)"
                                            " AS (a agtype, b agtype, l agtype)").fetchall()]
        c.execute('SET search_path = "$user", public')
        names = dict(c.execute("SELECT e.natural_key, d.name FROM kc_core.data_element d JOIN kc_core.entity e ON e.entity_id = d.entity_id").fetchall())
        names.update({k: t.split(" (")[0] for k, t in c.execute(
            "SELECT e.natural_key, p.plan_type FROM kc_core.plan p JOIN kc_core.entity e ON e.entity_id = p.entity_id").fetchall()})
        names.update(c.execute("SELECT e.natural_key, coalesce(p.short_name, p.legal_name) FROM kc_core.payer p JOIN kc_core.entity e ON e.entity_id = p.entity_id").fetchall())
        names.update({k: n.split(" (")[0] for k, n in c.execute("SELECT e.natural_key, p.name FROM kc_core.program p JOIN kc_core.entity e ON e.entity_id = p.entity_id").fetchall()})
        names.update(c.execute("SELECT e.natural_key, v.name FROM kc_core.value_set v JOIN kc_core.entity e ON e.entity_id = v.entity_id").fetchall())
        names.update(c.execute("SELECT e.natural_key, j.name FROM kc_core.jurisdiction j JOIN kc_core.entity e ON e.entity_id = j.entity_id").fetchall())
        names.update(c.execute("""SELECT e.natural_key, coalesce(py.short_name, '') || ' ' || en.interface FROM kc_core.endpoint en
                                   JOIN kc_core.entity e ON e.entity_id = en.entity_id JOIN kc_core.payer py ON py.entity_id = en.payer_id""").fetchall())
        akind = {}
        for key, cit, kind in c.execute("SELECT e.natural_key, a.citation, a.kind FROM kc_core.authority a JOIN kc_core.entity e ON e.entity_id = a.entity_id").fetchall():
            names[key], akind[key] = cit, kind
    for n in nodes:   # readable names from the pack: data elements by name, authorities by citation and kind
        if n["kind"] in ("DataElement", "Authority", "Plan", "Payer", "Program", "Jurisdiction", "ValueSet", "Endpoint") and n.get("id") in names:
            n["name"] = names[n["id"]]
        if n["kind"] == "Authority" and n.get("id") in akind:
            n["authority_kind"] = akind[n["id"]]
    return {"graph": g, "nodes": nodes, "edges": edges}


@app.get("/api/ontology")
def ontology():
    """Classes, subclass links and relations from the release's OWL file (release/owl.py)."""
    from rdflib import OWL, RDF, RDFS, Graph
    g = Graph()
    g.parse(TTL)
    name = lambda u: str(u).split("#")[-1]  # noqa: E731
    classes = sorted({name(c) for c in g.subjects(RDF.type, OWL.Class)})
    edges = [{"s": name(a), "t": name(b), "rel": "subClassOf"} for a, b in g.subject_objects(RDFS.subClassOf)
             if name(b) in classes and name(a) in classes]
    for p in g.subjects(RDF.type, OWL.ObjectProperty):
        for d in g.objects(p, RDFS.domain):
            for r in g.objects(p, RDFS.range):
                edges.append({"s": name(d), "t": name(r), "rel": name(p)})
    props = sum(1 for _ in g.subjects(RDF.type, OWL.DatatypeProperty))
    return {"nodes": [{"gid": c, "kind": "Class", "id": c} for c in classes], "edges": edges,
            "counts": {"classes": len(classes), "relations": len(set(g.subjects(RDF.type, OWL.ObjectProperty))), "attributes": props}}


@app.get("/api/compare")
def compare():
    """The latest comparison report written by propel/baseline/compare.py (none: the page shows only live numbers)."""
    files = sorted(RESULTS.glob("compare-*.json")) if RESULTS.exists() else []
    if not files:
        return {}
    rep = json.loads(files[-1].read_text())
    for e in rep.get("scenarios", {}).values():
        # lane A is RAG over the whole library; a report from before that change gave A the pack's pages, so its
        # A numbers are not shown against the RAG lane
        e.pop("A", None)
        if "A_rag" in e:
            e["A"] = e["A_rag"]
    return {"file": files[-1].name, **rep}


@app.get("/api/log")
def log():
    if not LOG.exists():
        return []
    return [json.loads(x) for x in LOG.read_text().splitlines() if x.strip()][-50:]


@app.get("/api/rule")
def rule(ref: str):
    """Page, quote, edition and source of one rule (rule_key, with or without @edition)."""
    with db() as c:
        if "@" not in ref:
            row = c.execute("SELECT rule_key || '@' || lower(valid)::text FROM kc_core.rule WHERE rule_key = %s AND upper_inf(recorded)"
                            " ORDER BY lower(valid) DESC LIMIT 1", (ref,)).fetchone()
            if not row:
                raise HTTPException(404, "no such rule")
            ref = row[0]
        return c.execute("SELECT kc_api.policy_cite(%s)", (ref,)).fetchone()[0]


@app.get("/api/source/{source_id}")
def source(source_id: str):
    m = MANIFEST.get(source_id)
    if not m:
        raise HTTPException(404, "no such source")
    return {k: m.get(k) for k in ("source_id", "title", "publisher", "edition", "url", "sha256", "retrieved_at", "checks")}


# --- Runs: the agent (B) or the workflow (C), streamed as events ----------------------------------------------------

class Run:
    def __init__(self, patient: str, mode: str):
        self.id = uuid.uuid4().hex[:12]
        self.patient, self.mode = patient, mode
        self.events: list[dict] = []
        self.cond = asyncio.Condition()
        self.confirm: asyncio.Future | None = None
        self.task: asyncio.Task | None = None
        self.stale = False   # replaced by a newer run: stops at its next model turn, without calling the model
        self.done = False
        self.t0 = time.perf_counter()

    def push(self, kind: str, **data):
        """Events are appended in the order they happen, synchronously, so the stream never reorders them."""
        self.events.append({"seq": len(self.events), "kind": kind, "ms": round((time.perf_counter() - self.t0) * 1000), **data})

    async def wake(self):
        async with self.cond:
            self.cond.notify_all()

    async def emit(self, kind: str, **data):
        self.push(kind, **data)
        await self.wake()


RUNS: dict[str, Run] = {}


def _order(pid: str) -> tuple[dict, dict]:
    p = PATIENTS[pid]
    return p, p["orders"][0]


async def run_workflow(run: Run):
    """Mode C: the order's rules_evaluate call made by plain code, with no model."""
    p, o = _order(run.patient)
    f = {**p["facts"], "encounter_type": o["encounter_type"], "urgency": o["urgency"], "request_type": o["request_type"]}
    args = {"plan": p["coverage"]["plan"], "state": p["coverage"]["state"], "code": o["code"], "date_of_service": o["date_of_service"]}
    await run.emit("tool_call", name="rules_evaluate", args={**args, "facts": f}, turn=0)
    t = time.perf_counter()
    with db() as c:
        r = c.execute("SELECT kc_api.evaluate_order(%s,%s,%s,%s::date,%s::jsonb)",
                      (args["plan"], args["state"], "HCPCS|" + o["code"], args["date_of_service"], json.dumps(f))).fetchone()[0]
    await run.emit("tool_result", name="rules_evaluate", payload=r, call_ms=round((time.perf_counter() - t) * 1000, 1))
    await run.emit("done", llm_calls=0, tokens_in=0, tokens_out=0)
    run.done = True


async def run_documents(run: Run):
    """Mode A, live: a plain LLM application with no Kanini Context. The order's best-matching passages from the whole
    policy library (RAG, propel/baseline/library.py) and the patient facts go to the model in one prompt, as in
    propel/baseline/compare.py, and its answer is compared with the rules' answer for the same order."""
    import litellm
    from propel.baseline import compare as cmp
    p, o = _order(run.patient)
    try:
        if not os.environ.get("OPENAI_API_KEY"):
            await run.emit("unavailable", message="needs OPENAI_API_KEY in .env: the AI is not called")
            await run.emit("done", llm_calls=0, tokens_in=0, tokens_out=0)
            return
        prompt, sent = await asyncio.to_thread(cmp.prompt_for, run.patient, "rag")
        n = litellm.token_counter(model=cmp.MODEL.split("/")[-1], text=prompt)
        await run.emit("doc_start", prompt_tokens=n, whole_tokens=await asyncio.to_thread(library_tokens),
                       method=sent["method"], documents=[f"{x['title']}, p. {x['page']}" for x in sent["passages"]])
        effort = os.environ.get("PROPEL_REASONING_EFFORT", "low").strip()
        resp = await litellm.acompletion(model=cmp.MODEL, messages=[{"role": "user", "content": prompt}],
                                         response_format={"type": "json_object"}, **({"reasoning_effort": effort} if effort else {}))
        try:
            ans = json.loads(resp.choices[0].message.content)
        except (ValueError, TypeError):
            ans = {"outcome": "unparseable"}
        u = resp.usage
        await run.emit("model", turn=1, tokens_in=u.prompt_tokens, tokens_out=u.completion_tokens, answer=ans.get("outcome"),
                       reasons=ans.get("reasons") or [], citation=ans.get("citation"))
        f = {**p["facts"], "encounter_type": o["encounter_type"], "urgency": o["urgency"], "request_type": o["request_type"]}
        with db() as c:   # the rules' answer for the same order, to mark the AI's answer right or wrong
            ref = c.execute("SELECT kc_api.evaluate_order(%s,%s,%s,%s::date,%s::jsonb)", (p["coverage"]["plan"], p["coverage"]["state"],
                            "HCPCS|" + o["code"], o["date_of_service"], json.dumps(f))).fetchone()[0]
        await run.emit("reference", outcome=ref["outcome"], matches=ans.get("outcome") == ref["outcome"])
        await run.emit("done", llm_calls=1, tokens_in=u.prompt_tokens, tokens_out=u.completion_tokens)
    except Exception as ex:
        await run.emit("error", message=str(ex)[:400])
    finally:
        run.done = True


# --- Mode B: one agent for the whole server -------------------------------------------------------------------------
# The agent and its MCP connections (the journey server and Kanini Context) are made once and kept open, so a run
# starts at its first model call. Each run has its own ADK session; the callbacks find the run by its session id.

AGENT: dict = {"agent": None, "own": None}
SESSIONS: dict[str, Run] = {}


def _run_of(ctx) -> Run | None:
    return SESSIONS.get(ctx.session.id)


def _lap(run: Run, kind: str, label: str):
    c, now = run.clock, time.perf_counter()
    if c["setup"] is None and kind == "ai_start":   # until the first model call: session and connections
        c["setup"] = now - c["t"]
    if kind in ("ai_end", "tool_end"):
        d = now - c["t"]
        c["ai" if kind == "ai_end" else "tools"] += d
        c["steps"].append((label, round(d, 2)))
    c["t"] = now


def _timing(run: Run) -> dict:
    c = run.clock
    total = time.perf_counter() - c["t0"]
    rep = {"setup_s": round(c["setup"] or 0, 2), "ai_s": round(c["ai"], 2), "tools_s": round(c["tools"], 2),
           "total_s": round(total, 2), "steps": c["steps"]}
    rep["between_s"] = round(total - rep["setup_s"] - rep["ai_s"] - rep["tools_s"], 2)
    print(f"[dashboard] B timing for {run.patient}: total {rep['total_s']} s = start-up {rep['setup_s']} + AI {rep['ai_s']} "
          f"+ tools {rep['tools_s']} + between steps {rep['between_s']}; " + ", ".join(f"{k} {v} s" for k, v in c["steps"]), flush=True)
    return rep


def _fire(run: Run, kind: str, **data):   # called from the agent's synchronous callbacks
    run.push(kind, **data)
    run.loop.create_task(run.wake())


def _install(agent):
    """Wrap the agent's own callbacks (journey_record overwrite, card guard, trimming) with the page's event stream."""
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from propel.agent.authorization_agent import agent as A
    own_before, own_after, own_model, own_before_model = (agent.before_tool_callback, agent.after_tool_callback,
                                                          agent.after_model_callback, agent.before_model_callback)

    def before_tool(tool, args, tool_context):
        run = _run_of(tool_context)
        if run:
            _lap(run, "tool_start", tool.name)
            _fire(run, "tool_call", name=tool.name, args=dict(args), turn=run.usage["turn"])
        return own_before(tool, args, tool_context)

    def after_tool(tool, args, tool_context, tool_response):
        run = _run_of(tool_context)
        if run:
            _lap(run, "tool_end", tool.name)
            _fire(run, "tool_result", name=tool.name, payload=A._payload(tool_response))   # the full result, before trimming
        return own_after(tool, args, tool_context, tool_response)

    def before_model(callback_context, llm_request):
        run = _run_of(callback_context)
        if run and run.stale:   # replaced by a newer run: no model call, the run ends here
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Stopped: a newer run replaced this one.")]))
        if run:
            _lap(run, "ai_start", "")
        return own_before_model(callback_context, llm_request) if own_before_model else None

    def after_model(callback_context, llm_response):
        out = own_model(callback_context, llm_response)   # the card guard first, so the page shows the corrected card
        run = _run_of(callback_context)
        if not run or run.stale:
            return out
        um = llm_response.usage_metadata
        u = run.usage
        u["turn"] += 1
        _lap(run, "ai_end", f"AI step {u['turn']}")
        tin, tout = (um.prompt_token_count or 0, um.candidates_token_count or 0) if um else (0, 0)
        u["in"] += tin
        u["out"] += tout
        parts = (llm_response.content.parts if llm_response.content else None) or []
        _fire(run, "model", turn=u["turn"], tokens_in=tin, tokens_out=tout,
              calls=[p.function_call.name for p in parts if p.function_call],
              text="\n".join(p.text for p in parts if getattr(p, "text", None)))
        return out

    agent.before_tool_callback, agent.after_tool_callback = before_tool, after_tool
    agent.after_model_callback, agent.before_model_callback = after_model, before_model
    return agent


async def shared_agent():
    """The server's one agent (real model), with its MCP connections opened ahead of the first run."""
    if AGENT["agent"] is None:
        from propel.agent.authorization_agent import agent as A
        agent = _install(A.build_agent(model=A.make_model()))
        for ts in agent.tools or []:
            try:
                await ts.get_tools()   # opens the connection now, not in the first run
            except Exception as ex:
                print(f"[dashboard] tool connection not ready yet ({ex}); it opens on the first run", flush=True)
        AGENT["agent"] = agent
    return AGENT["agent"]


_LIB_TOKENS: list[int] = []


def library_tokens() -> int:
    """Tokens of the whole policy library, read whole (counted once)."""
    if not _LIB_TOKENS:
        import litellm
        from propel.baseline import compare as cmp, library
        _LIB_TOKENS.append(litellm.token_counter(model=cmp.MODEL.split("/")[-1], text=library.whole_text()))
    return _LIB_TOKENS[0]


@app.on_event("startup")
async def _warm():
    if not SCRIPTED and os.environ.get("OPENAI_API_KEY"):
        await shared_agent()
        from propel.baseline import library   # embed the library once, so lane A's first run does not wait for it
        await asyncio.to_thread(library.retrieve, "prior authorization")
        await asyncio.to_thread(library_tokens)


@app.on_event("shutdown")
async def _close():
    for ts in (AGENT["agent"].tools if AGENT["agent"] else []) or []:
        try:
            await ts.close()
        except Exception:
            pass


async def run_agent(run: Run):
    """Mode B: the authorization agent; the page sees each step as it happens."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    run.loop = asyncio.get_running_loop()
    run.usage = {"turn": 0, "in": 0, "out": 0}
    run.clock = {"t": time.perf_counter(), "t0": time.perf_counter(), "ai": 0.0, "tools": 0.0, "setup": None, "steps": []}
    if SCRIPTED:   # a fresh agent per run: the scripted stand-in follows one patient
        sys.path.insert(0, str(ROOT / "tests"))
        from scripted_model import ScriptedModel
        from propel.agent.authorization_agent import agent as A
        agent = _install(A.build_agent(model=ScriptedModel(patient_id=run.patient)))
    else:
        agent = await shared_agent()
    svc = InMemorySessionService()
    runner = Runner(app_name="dashboard", agent=agent, session_service=svc)
    s = await svc.create_session(app_name="dashboard", user_id="staff")
    SESSIONS[s.id] = run
    msg = types.Content(role="user", parts=[types.Part(text=f"Check prior authorization for the open order for patient {run.patient}.")])
    try:
        for _ in range(3):
            confirm_id = None
            async for ev in runner.run_async(user_id="staff", session_id=s.id, new_message=msg):
                for part in (ev.content.parts if ev.content else []) or []:
                    if part.function_call and part.function_call.name == "adk_request_confirmation":
                        confirm_id = part.function_call.id
            if not confirm_id or run.stale:   # a replaced run never waits for approval
                break
            run.confirm = run.loop.create_future()
            await run.emit("timing", **_timing(run))
            await run.emit("confirm_request", llm_calls=run.usage["turn"], tokens_in=run.usage["in"], tokens_out=run.usage["out"])
            try:
                approved = await asyncio.wait_for(run.confirm, timeout=600)
            except (asyncio.TimeoutError, asyncio.CancelledError):   # left waiting, or a new run started: nothing is recorded
                await run.emit("abandoned")
                break
            await run.emit("confirm", approved=approved)
            msg = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id=confirm_id, name="adk_request_confirmation", response={"confirmed": approved}))])
        if not any(e["kind"] == "timing" for e in run.events):
            await run.emit("timing", **_timing(run))
        if run.stale:
            await run.emit("abandoned")
        await asyncio.sleep(0)
        await run.emit("done", llm_calls=run.usage["turn"], tokens_in=run.usage["in"], tokens_out=run.usage["out"])
    except Exception as ex:   # shown on the page; the run stops
        await run.emit("error", message=str(ex)[:400])
    finally:
        SESSIONS.pop(s.id, None)
        if SCRIPTED:   # the shared agent keeps its connections; a per-run agent closes its own
            for t in agent.tools or []:
                close = getattr(t, "close", None)
                if close:
                    try:
                        await close()
                    except Exception:
                        pass
        run.done = True


@app.post("/api/run")
async def start(body: dict):
    pid, mode = body.get("patient"), body.get("mode", "agent")
    if pid not in PATIENTS or mode not in ("agent", "workflow", "documents"):
        raise HTTPException(400, "patient must be a known patient id and mode agent, workflow or documents")
    for old in [x for x in RUNS.values() if x.mode == mode]:   # a new run of a mode replaces the last one of that mode
        if old.confirm and not old.confirm.done():
            old.confirm.cancel()
        elif old.task and not old.task.done():   # still mid-run: it stops at its next model turn and closes its sessions
            old.stale = True
    for rid in [r for r, x in RUNS.items() if x.done][:-20]:   # keep the last runs only
        RUNS.pop(rid, None)
    run = Run(pid, mode)
    RUNS[run.id] = run
    await run.emit("start", patient=pid, mode=mode, order=PATIENTS[pid]["orders"][0]["order_id"])
    run.task = asyncio.create_task({"agent": run_agent, "workflow": run_workflow, "documents": run_documents}[mode](run))
    return {"run": run.id}


@app.post("/api/run/{rid}/confirm")
async def confirm(rid: str, body: dict):
    run = RUNS.get(rid)
    if not run or not run.confirm or run.confirm.done():
        raise HTTPException(409, "nothing waiting for approval")
    run.confirm.set_result(bool(body.get("approved")))
    return {"ok": True}


@app.get("/api/run/{rid}/events")
async def events(rid: str, request: Request):
    run = RUNS.get(rid)
    if not run:
        raise HTTPException(404, "no such run")
    last = request.headers.get("last-event-id")   # a reconnecting browser resumes after the last event it saw

    async def stream():
        i = int(last) + 1 if last and last.isdigit() else 0
        while True:
            async with run.cond:
                while i >= len(run.events):
                    if run.done:   # a browser resuming after the last event: nothing more will come
                        yield "retry: 86400000\n\n"
                        return
                    try:
                        await asyncio.wait_for(run.cond.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        break
                batch = run.events[i:]
            if not batch:
                yield ": keep-alive\n\n"
                continue
            for ev in batch:
                i += 1
                yield f"id: {ev['seq']}\ndata: {json.dumps(ev, default=str)}\n\n"
                if ev["kind"] in ("done", "error"):
                    yield "retry: 86400000\n\n"   # the run is over: the browser should not reconnect
                    return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def main(argv: list[str]) -> int:
    global SCRIPTED
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--scripted", action="store_true", help="scripted stand-in model, for a rehearsal with no API key")
    args = ap.parse_args(argv)
    SCRIPTED = args.scripted
    import uvicorn
    print(f"Kanini Context dashboard: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
