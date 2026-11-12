"""Kanini Context MCP server: the agent's read-only window onto the client copy (kc_client).

Tools (names use underscores, as LLM function names must):
  context_applicable_rules  which reviewed rules apply to an order, and the facts and documents they need
  rules_evaluate            the decision packet for an order, given the patient's registered facts
  policy_search             the source text behind a rule reference, or rule references matching a phrase

Every answer comes from SQL over the reviewed pack, so the same question gives the same answer, with
rule references (rule_key@edition), page, quote and file hash. Each call writes one audit row: tool,
release, status, rule ids and duration; never the question's content.

    python -m kanini_context.server.mcp_server                 # stdio (how the agent starts it)
    python -m kanini_context.server.mcp_server --http 8765     # streamable HTTP, for inspection

Connection: KC_CLIENT_DSN (default dbname=kc_client) with the usual PG* variables.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import date
from typing import Any

import psycopg
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from kanini_context import env  # noqa: F401  (.env and PostgreSQL defaults)
DSN = os.environ.get("KC_CLIENT_DSN", "dbname=kc_client")



def _registry() -> tuple[str, str, str]:
    """Plans, services and facts the tools accept, read from the pack when the server starts, so the tool
    descriptions follow the loaded release instead of a hand-written list."""
    try:
        with psycopg.connect(DSN, connect_timeout=5) as c:
            plans = c.execute("""SELECT string_agg(e.natural_key || ' (' || coalesce(p.plan_type, e.natural_key) || ')', ', ' ORDER BY e.natural_key)
                                   FROM kc_core.plan p JOIN kc_core.entity e ON e.entity_id = p.entity_id""").fetchone()[0]
            services = c.execute("""SELECT string_agg(e.natural_key || ' (' || cc.label || ')', ', ' ORDER BY e.natural_key)
                                      FROM kc_core.concept cc JOIN kc_core.entity e ON e.entity_id = cc.entity_id""").fetchone()[0]
            facts = c.execute("""SELECT string_agg(fact || ' (' || CASE value_type
                                         WHEN 'enum' THEN (SELECT string_agg(x, '|') FROM jsonb_array_elements_text(allowed) x)
                                         WHEN 'code_list' THEN 'list of codes' WHEN 'date' THEN 'YYYY-MM-DD'
                                         ELSE value_type END || '): ' || description, '; ' ORDER BY fact)
                                    FROM kc_meta.fact_type""").fetchone()[0]
            return plans or "", services or "", facts or ""
    except psycopg.Error:
        return "(pack not reachable at start)", "(pack not reachable at start)", "(pack not reachable at start)"


PLANS, SERVICES, FACTS_HELP = _registry()

_lock = threading.Lock()
_conn: psycopg.Connection | None = None


def conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(DSN, autocommit=True)
    return _conn


def invalid(error: str) -> dict:
    return {"status": "invalid_input", "error": error, "safe_to_act": False, "next_step": "fix_input"}


def norm_code(code: str) -> str | None:
    """'SYSTEM|code' as given, or a bare HCPCS code (one letter and four digits) with its system added."""
    c = code.strip()
    if "|" in c:
        return c if re.fullmatch(r"[A-Z0-9]+\|[A-Za-z0-9.\-]+", c) else None
    c = c.upper()
    return "HCPCS|" + c if re.fullmatch(r"[A-Z]\d{4}", c) else None


def norm_item(service: str, code: str) -> tuple[str | None, str | None]:
    """An order item: a code, or a service key from the pack. Returns (item, error)."""
    if code.strip():
        c = norm_code(code)
        return (c, None) if c else (None, CODE_ERROR)
    sv = service.strip()
    if not sv:
        return None, "missing field: service (or code)"
    if norm_code(sv):
        return norm_code(sv), None
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", sv):
        return None, "service must be a service key from the pack, or a code"
    return sv, None


CODE_ERROR = "code must be SYSTEM|code, or a HCPCS code (one letter and four digits)"


def norm_date(d: str) -> str | None:
    try:
        return date.fromisoformat(d.strip()).isoformat()
    except (ValueError, AttributeError):
        return None


def call(tool: str, sql: str, args: tuple) -> dict:
    t0 = time.perf_counter()
    with _lock:
        c = conn()
        out = c.execute(sql, args).fetchone()[0]
        ms = (time.perf_counter() - t0) * 1000
        if out.get("status") == "invalid_input":   # refused input is not logged
            return out
        refs = out.get("rule_refs") or [r["rule_ref"] for r in out.get("rules", []) + out.get("results", []) if "rule_ref" in r] \
            or ([out["rule_ref"]] if isinstance(out.get("rule_ref"), str) else [])
        keys = [r.split("@", 1)[0] for r in refs if r]
        c.execute("""INSERT INTO kc_client.tool_audit (tool, release_id, status, rule_ids, duration_ms)
                     SELECT %s, coalesce(current_setting('kc.release', true), 'none'), %s,
                            coalesce((SELECT array_agg(DISTINCT rule_id) FROM kc_core.rule WHERE rule_key = ANY (%s)), '{}'), %s""",
                  (tool, out.get("status") or out.get("outcome") or "resolved", keys, round(ms, 2)))
    return out


mcp = FastMCP(
    "kanini-context",
    instructions=("Answers about prior authorization, coverage criteria, deadlines and submission routes come from "
                  "Kanini Context, a reviewed rule pack. Use these tools instead of reasoning about payer policy yourself. "
                  "Act only when safe_to_act is true; otherwise follow next_step. Quote rule_refs when explaining a decision."))


@mcp.tool(description="""Which reviewed rules apply to an order before any patient facts, each with its rule_ref (rule@edition), source,
    and the facts and documents it needs. Call this first, then rules_evaluate with the facts it lists.

    plan: one of """ + PLANS + """. state: two-letter US state of the patient. date_of_service: YYYY-MM-DD.
    service: a service key, one of """ + SERVICES + """; or code: a HCPCS code (one letter and four digits) or SYSTEM|code.
    """)
def context_applicable_rules(plan: str, state: str, date_of_service: str, service: str = "", code: str = "") -> dict:
    item, err = norm_item(service, code)
    if err:
        return invalid(err)
    d = norm_date(date_of_service)
    if not d:
        return invalid("date_of_service must be YYYY-MM-DD")
    return call("context_applicable_rules", "SELECT kc_api.applicable_rules(%s, %s, %s, %s::date)", (plan, state, item, d))


@mcp.tool(description="""The decision for one order. outcome: pa_required, no_pa, exempt, other_gate (coverage criteria not met),
    needs_facts or unknown; with reasons, safe_to_act, next_step and next_step_detail, missing_facts, the coverage
    criteria checked against the facts, evidence to send, decision_clock, route, primary_rule, rule_refs and sources.
    The outcome always comes from this tool; never state one it did not return.

    plan: one of """ + PLANS + """. state: two-letter US state. code: HCPCS code. date_of_service: YYYY-MM-DD.
    facts: """ + FACTS_HELP)
def rules_evaluate(plan: str, state: str, code: str, date_of_service: str, facts: dict[str, Any] | None = None) -> dict:
    c, d = norm_code(code), norm_date(date_of_service)
    if not c:
        return invalid(CODE_ERROR)
    if not d:
        return invalid("date_of_service must be YYYY-MM-DD")
    return call("rules_evaluate", "SELECT kc_api.evaluate_order(%s, %s, %s, %s::date, %s::jsonb)",
                (plan, state, c, d, json.dumps(facts or {})))


@mcp.tool
def policy_search(query: str = "", rule_ref: str = "") -> dict:
    """The source text behind a rule reference (rule_ref: rule_key@edition, as rules_evaluate returns in rule_refs), with page, quote,
    edition, URL and file hash; or, given query, up to 10 rule references whose source text matches every word."""
    if rule_ref.strip():
        return call("policy_search", "SELECT kc_api.policy_cite(%s)", (rule_ref.strip(),))
    if not query.strip():
        return invalid("give rule_ref or query")
    return call("policy_search", "SELECT kc_api.policy_search(%s)", (query.strip(),))


class InvalidInputAsAnswer(Middleware):
    """A malformed call (missing or unexpected argument, wrong type) gets the same answer shape as any other
    refusal, so the agent can read status and next_step instead of a framework error."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        try:
            return await call_next(context)
        except Exception as ex:   # validation errors raised before the tool runs
            errs = getattr(ex.__cause__ or ex, "errors", None)
            if callable(errs):
                words = {"missing_argument": "missing field", "missing": "missing field",
                         "unexpected_keyword_argument": "unexpected field"}
                msg = "; ".join(f"{words.get(e['type'], 'invalid field')}: {e['loc'][-1]}" for e in errs())
            else:
                msg = str(ex).splitlines()[0][:300]
            body = invalid(msg)   # refused input is not logged
            return ToolResult(content=json.dumps(body), structured_content=body)


mcp.add_middleware(InvalidInputAsAnswer())


if __name__ == "__main__":
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1])
        mcp.run(transport="http", host="127.0.0.1", port=port, show_banner=False)
    else:
        mcp.run(show_banner=False)
