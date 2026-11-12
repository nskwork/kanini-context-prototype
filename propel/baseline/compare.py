"""How many LLM calls and tokens Kanini Context saves, and whether answers repeat.

Each demo scenario is answered three ways, several times:
  A  LLM reads the policies   GPT-5 mini gets the policy text for the patient's coverage and decides (no Kanini Context)
  B  agent with Kanini        the Propel agent calls the Kanini Context tools; the outcome comes from rules_evaluate
  C  workflow with Kanini     Propel calls rules_evaluate directly; no LLM at all

Per mode and scenario it reports LLM calls, input and output tokens, time, the outcomes seen across runs, and
whether they match the reviewed rules (C is the reference: same reviewed pack, no model in the decision).

    python -m propel.baseline.compare --offline          # C only, plus the size of A's prompts (no API key needed)
    python -m propel.baseline.compare --runs 5           # all three (needs OPENAI_API_KEY and the Kanini server)

Mode A is given the documents the pack binds to the coverage, place, code and date, which favours it: a real
deployment would also have to find them. Sources with licensed text are cut to the pages the rules cite.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from kanini_context import env  # noqa: E402  (loads .env)
from kanini_context.curation.pdftext import pages  # noqa: E402

import psycopg  # noqa: E402

PATIENTS = json.loads((ROOT / "propel/journey/patients.json").read_text())["patients"]
MANIFEST = {m["source_id"]: m for m in json.loads((ROOT / "kanini_context/sources/manifest.json").read_text())}
OUT = ROOT / "propel/baseline/results"
MODEL = os.environ.get("PROPEL_MODEL", "openai/gpt-5-mini")
SCENARIOS = [(str(i), pid) for i, pid in enumerate(PATIENTS, 1)]   # every patient in the journey data
OUTCOMES = ["pa_required", "no_pa", "exempt", "other_gate", "needs_facts", "unknown"]

PROMPT = """You decide whether an order needs prior authorization, using only the policy documents below.
Coverage: {plan} ({state}). Order: HCPCS {code}, {desc}, date of service {dos}, {enc}, {urg} request.
Patient facts: {facts}

Answer with JSON only: {{"outcome": one of {outcomes}, "safe_to_act": true or false,
"reasons": [short strings], "citation": "document and section"}}.
Use other_gate when no approval is needed but the coverage criteria are not met; needs_facts when a fact needed
to decide is missing; unknown when the documents do not settle it.

DOCUMENTS
{docs}"""


def order(pid: str) -> tuple[dict, dict, dict]:
    p = PATIENTS[pid]
    o = p["orders"][0]
    f = {**p["facts"], "encounter_type": o["encounter_type"], "urgency": o["urgency"], "request_type": o["request_type"]}
    return p, o, f


def docs_for(plan: str, state: str, code: str, dos: str) -> dict[str, list[int] | None]:
    """The documents mode A reads: every source behind a rule the pack binds to this coverage, place, code and
    date (the same documents Kanini Context answers from). A source with licensed text (license_note in the
    manifest) is cut to the pages its rules cite; the others are given whole."""
    with psycopg.connect(f"dbname={env.CLIENT_DB}") as c:
        refs = [r["rule_ref"].split("@")[0] for r in
                (c.execute("SELECT kc_api.applicable_rules(%s,%s,%s,%s::date)", (plan, state, "HCPCS|" + code, dos)).fetchone()[0]
                 .get("rules") or [])]
        rows = c.execute("""SELECT sn.source_id, r.source_locator FROM kc_core.rule r JOIN kc_core.snapshot sn ON sn.snapshot_id = r.snapshot_id
                             WHERE r.rule_key = ANY (%s) AND upper_inf(r.recorded)""", (refs,)).fetchall()
    docs: dict[str, list[int] | None] = {}
    for sid, loc in rows:
        if "license_note" not in MANIFEST[sid]:
            docs[sid] = None
            continue
        cited = [loc["page"]] + [loc[k]["page"] for k in ("continued", "row_statement", "code_cell") if k in loc]
        docs[sid] = sorted(set((docs.get(sid) or []) + cited))
    return docs


def docs_text(docs: dict[str, list[int] | None]) -> str:
    out = []
    for sid, pg in sorted(docs.items()):
        txt = pages(MANIFEST[sid]["file"])
        sel = [txt[i - 1] for i in pg] if pg else txt
        out.append(f"=== {MANIFEST[sid]['title']} ({MANIFEST[sid]['edition']})\n" + "\n".join(sel))
    return "\n\n".join(out)


def prompt_for(pid: str) -> str:
    p, o, f = order(pid)
    docs = docs_for(p["coverage"]["plan"], p["coverage"]["state"], o["code"], o["date_of_service"])
    return PROMPT.format(plan=p["coverage"]["plan"], state=p["coverage"]["state"], code=o["code"], desc=o["description"],
                         dos=o["date_of_service"], enc=o["encounter_type"], urg=o["urgency"], facts=json.dumps(p["facts"]),
                         outcomes=OUTCOMES, docs=docs_text(docs))


# --- C: workflow with Kanini Context, no LLM ----------------------------------------------------------------------

def run_c(pid: str) -> dict:
    p, o, f = order(pid)
    with psycopg.connect(f"dbname={env.CLIENT_DB}") as c:
        t = time.perf_counter()
        r = c.execute("SELECT kc_api.evaluate_order(%s,%s,%s,%s::date,%s::jsonb)",
                      (p["coverage"]["plan"], p["coverage"]["state"], "HCPCS|" + o["code"], o["date_of_service"], json.dumps(f))).fetchone()[0]
        ms = (time.perf_counter() - t) * 1000
    return {"outcome": r["outcome"], "safe_to_act": r["safe_to_act"], "llm_calls": 0, "tokens_in": 0, "tokens_out": 0,
            "ms": ms, "rule": (r.get("primary_rule") or {}).get("rule_ref")}


# --- A: the LLM reads the policies --------------------------------------------------------------------------------

def run_a(pid: str) -> dict:
    import litellm
    t = time.perf_counter()
    resp = litellm.completion(model=MODEL, messages=[{"role": "user", "content": prompt_for(pid)}],
                              response_format={"type": "json_object"})
    ms = (time.perf_counter() - t) * 1000
    try:
        ans = json.loads(resp.choices[0].message.content)
    except (ValueError, TypeError):
        ans = {"outcome": "unparseable"}
    u = resp.usage
    return {"outcome": ans.get("outcome"), "safe_to_act": ans.get("safe_to_act"), "llm_calls": 1,
            "tokens_in": u.prompt_tokens, "tokens_out": u.completion_tokens, "ms": ms, "rule": ans.get("citation")}


# --- B: the agent with Kanini Context ----------------------------------------------------------------------------

async def run_b_async(pid: str) -> dict:
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from propel.agent.authorization_agent.agent import build_agent

    usage = {"calls": 0, "in": 0, "out": 0}

    def count(callback_context, llm_response):
        um = llm_response.usage_metadata
        if um:
            usage["calls"] += 1
            usage["in"] += um.prompt_token_count or 0
            usage["out"] += um.candidates_token_count or 0
        return None

    agent = build_agent(model=LiteLlm(model=MODEL))
    guard = agent.after_model_callback   # keep the agent's own decision-card guard, and count usage after it

    def guarded(callback_context, llm_response):
        guard(callback_context, llm_response)
        return count(callback_context, llm_response)
    agent.after_model_callback = guarded
    svc = InMemorySessionService()
    runner = Runner(app_name="compare", agent=agent, session_service=svc)
    s = await svc.create_session(app_name="compare", user_id="staff")
    msg = types.Content(role="user", parts=[types.Part(text=f"Check prior authorization for the open order for patient {pid}.")])
    outcome, t = None, time.perf_counter()
    for _ in range(3):
        confirm = None
        async for ev in runner.run_async(user_id="staff", session_id=s.id, new_message=msg):
            for part in (ev.content.parts if ev.content else []) or []:
                if part.function_call and part.function_call.name == "adk_request_confirmation":
                    confirm = part.function_call.id
                if part.function_response and part.function_response.name == "rules_evaluate":
                    r = part.function_response.response or {}
                    r = r.get("structuredContent") or (json.loads(r["content"][0]["text"]) if r.get("content") else r)
                    outcome = r.get("outcome")
        if not confirm:
            break
        msg = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id=confirm, name="adk_request_confirmation", response={"confirmed": False}))])   # never write to the demo log
    for ts in agent.tools:
        await ts.close()
    return {"outcome": outcome, "safe_to_act": None, "llm_calls": usage["calls"], "tokens_in": usage["in"],
            "tokens_out": usage["out"], "ms": (time.perf_counter() - t) * 1000, "rule": None}


def run_b(pid: str) -> dict:
    os.environ["PROPEL_DECISION_LOG"] = str(OUT / "compare_decisions.jsonl")
    return asyncio.run(run_b_async(pid))


# --- report ------------------------------------------------------------------------------------------------------

def summarise(runs: list[dict], ref: str) -> dict:
    outs = Counter(r["outcome"] for r in runs)
    return {"runs": len(runs), "outcomes": dict(outs), "matches_rules": sum(1 for r in runs if r["outcome"] == ref),
            "llm_calls": statistics.mean(r["llm_calls"] for r in runs),
            "tokens_in": statistics.mean(r["tokens_in"] for r in runs), "tokens_out": statistics.mean(r["tokens_out"] for r in runs),
            "seconds": statistics.mean(r["ms"] for r in runs) / 1000}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--offline", action="store_true", help="mode C and A's prompt sizes only; no LLM calls")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    import litellm

    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": MODEL, "scenarios": {}}
    for sc, pid in SCENARIOS:
        c_runs = [run_c(pid) for _ in range(args.runs)]
        ref = c_runs[0]["outcome"]
        entry = {"patient": pid, "reference_outcome": ref, "rule": c_runs[0]["rule"], "C": summarise(c_runs, ref),
                 "A_prompt_tokens": litellm.token_counter(model=MODEL.split("/")[-1], text=prompt_for(pid))}
        if not args.offline:
            entry["A"] = summarise([run_a(pid) for _ in range(args.runs)], ref)
            entry["B"] = summarise([run_b(pid) for _ in range(args.runs)], ref)
        report["scenarios"][sc] = entry
        print(f"scenario {sc:3} {pid:16} rules: {ref:12} A prompt {entry['A_prompt_tokens']:>7,} tokens"
              + ("" if args.offline else f" | A {entry['A']['matches_rules']}/{args.runs} match, {dict(entry['A']['outcomes'])}"
                                         f" | B {entry['B']['matches_rules']}/{args.runs} match, {entry['B']['llm_calls']:.1f} calls"))

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (OUT / f"compare-{stamp}.json").write_text(json.dumps(report, indent=2))
    lines = [f"# Kanini Context: LLM calls saved ({report['at']}, {MODEL}, {args.runs} runs per scenario)", "",
             "| Scenario | Rules say | A: LLM reads policies | B: agent + Kanini | C: workflow + Kanini |",
             "| --- | --- | --- | --- | --- |"]
    for sc, e in report["scenarios"].items():
        def cell(m):
            if m not in e:
                return f"{e['A_prompt_tokens']:,} input tokens per call (not run)" if m == "A" else "not run"
            s = e[m]
            return (f"{s['matches_rules']}/{s['runs']} match; {s['llm_calls']:.1f} LLM calls; "
                    f"{s['tokens_in']:,.0f} in / {s['tokens_out']:,.0f} out tokens; {s['seconds']:.2f} s")
        lines.append(f"| {sc} ({e['patient']}) | {e['reference_outcome']} | {cell('A')} | {cell('B')} | {cell('C')} |")
    (OUT / f"compare-{stamp}.md").write_text("\n".join(lines) + "\n")
    print(f"\nreport: propel/baseline/results/compare-{stamp}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
