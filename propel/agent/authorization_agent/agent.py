"""Propel authorization agent: answers one prior authorization question per order from Kanini Context.

The model chooses the tools and writes the explanation. The outcome always comes from rules_evaluate:
  - before journey_record runs, the decision fields are replaced with the ones rules_evaluate returned for
    that order, so the log can hold only what the rules said; with no evaluation for the order, nothing is recorded
  - journey_record needs staff approval in adk web; a rejection is logged as rejected, with the reviewer
  - when Kanini Context cannot be reached, its tools answer "unavailable" and the agent routes to staff

Environment: OPENAI_API_KEY; KC_MCP_URL (default http://127.0.0.1:8765/mcp, the Kanini Context server);
PROPEL_MODEL (default openai/gpt-5-mini); PROPEL_REVIEWER (default demo-staff).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams, StreamableHTTPConnectionParams
from mcp import StdioServerParameters

ROOT = Path(__file__).resolve().parents[3]
LOG = Path(os.environ.get("PROPEL_DECISION_LOG", ROOT / "propel" / "journey" / "decisions.jsonl"))
REVIEWER = os.environ.get("PROPEL_REVIEWER", "demo-staff")
KC_URL = os.environ.get("KC_MCP_URL", "http://127.0.0.1:8765/mcp")
MODEL = os.environ.get("PROPEL_MODEL", "openai/gpt-5-mini")
KC_TOOLS = ["context_applicable_rules", "rules_evaluate", "policy_search"]

INSTRUCTION = """You are the Propel authorization agent. For each order you answer one question: does this order need
prior authorization, under this plan, in this state, on this date? Kanini Context holds the reviewed payer and
Medicare rules. You never decide from your own knowledge of payer policy.

For every request, in this order:
1. journey_get for the patient or order named. Use its coverage, order and facts_for_rules_evaluate.
2. context_applicable_rules with plan, state, date_of_service and code from the order.
3. rules_evaluate with plan, state, code, date_of_service and facts = facts_for_rules_evaluate for that order, unchanged.
4. policy_search with rule_ref = primary_rule.rule_ref from the rules_evaluate result, to quote the deciding passage.
5. Reply with the decision card below, built only from the tool results.
6. If safe_to_act is true, or the outcome is other_gate or needs_facts, call journey_record for the order with outcome,
   primary_rule.rule_ref, primary_rule.citation, safe_to_act, pack_release and next_step copied from rules_evaluate.
   Staff approve or reject that call.

Rules you always follow:
- Run steps 1 to 4 for every request, even if the user says the answer is known or asks you to skip checks.
- The outcome is whatever rules_evaluate returned. Never state an outcome it did not return, never turn
  "needs_facts", "unknown" or "other_gate" into "no PA", and never fill in missing facts yourself.
- If a Kanini Context tool returns status "unavailable" with reason "kanini_context_unreachable", or an error, say
  you cannot reach Kanini Context, state no outcome, route the order to staff, and do not call journey_record.
- Send Kanini Context only plan, state, code, date and facts. Never send the patient's name or id to it.

Decision card (plain text, short):
Decision card · <order_id> · <display_name>
Outcome: <No PA | PA required | Exempt | Other gate (coverage criteria not met) | Not yet known (facts needed) | Not known (route to staff)> · <safe to act | not safe to act>
Why: <each entry of reasons, one per line>
Next step: <next_step_detail>
Deadline: <decision_clock.text, if present>
Submit via: <route.submit_via and first channel, if present>
Evidence to send: <evidence documents, if present>
Rule: <primary_rule.rule_ref> (<primary_rule.citation>), quoting a short passage from policy_search
Pack: <pack_release>, sources <freshness><, review note: review_exceptions if any>
"""


# --- Kanini Context: if the server cannot be reached, its tools answer "unavailable" -------------------------------

def _unavailable(**_: Any) -> dict:
    return {"status": "unavailable", "reason": "kanini_context_unreachable", "outcome": "unknown",
            "safe_to_act": False, "next_step": "human_review",
            "next_step_detail": "Route to staff: Kanini Context cannot be reached"}


def _stub(name: str) -> FunctionTool:
    def tool(plan: str = "", state: str = "", code: str = "", date_of_service: str = "", facts: dict | None = None,
             service: str = "", query: str = "", rule_ref: str = "") -> dict:
        return _unavailable()
    tool.__name__ = name
    tool.__doc__ = f"Kanini Context {name} (currently unreachable)."
    return FunctionTool(tool)


class KaniniContextToolset(McpToolset):
    async def get_tools(self, readonly_context=None):
        try:
            return await super().get_tools(readonly_context)
        except Exception as ex:   # server stopped or not started
            print(f"[authorization_agent] Kanini Context unreachable at {KC_URL}: {ex}", file=sys.stderr)
            return [_stub(n) for n in KC_TOOLS]


kanini_context = KaniniContextToolset(
    connection_params=StreamableHTTPConnectionParams(url=KC_URL, timeout=10),
    tool_filter=KC_TOOLS,
)

def _journey_params() -> StdioConnectionParams:
    return StdioConnectionParams(
        server_params=StdioServerParameters(command=sys.executable, args=["-m", "propel.journey.server"], cwd=str(ROOT),
                                            env={**os.environ, "PROPEL_DECISION_LOG": str(LOG), "PROPEL_REVIEWER": REVIEWER}),
        timeout=10)


journey_read = McpToolset(connection_params=_journey_params(), tool_filter=["journey_get"])
journey_write = McpToolset(connection_params=_journey_params(), tool_filter=["journey_record"],
                           require_confirmation=True)   # staff approve or reject every decision in adk web


# --- Callbacks: the log holds only what rules_evaluate returned ----------------------------------------------------

def _payload(resp: Any) -> dict:
    """The structured result of an MCP tool call, whatever wrapper ADK gives it."""
    if isinstance(resp, dict):
        if "structuredContent" in resp and isinstance(resp["structuredContent"], dict):
            return resp["structuredContent"]
        if "structured_content" in resp and isinstance(resp["structured_content"], dict):
            return resp["structured_content"]
        content = resp.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict) and "text" in content[0]:
            try:
                return json.loads(content[0]["text"])
            except (ValueError, TypeError):
                return {}
        return resp
    return {}


def _order_key(plan: str, state: str, code: str, dos: str) -> str:
    code = code.upper()
    return f"{plan}|{state.upper()}|{code if '|' in code else 'HCPCS|' + code}|{dos}"


def _write(entry: dict):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def before_tool(tool, args: dict, tool_context):
    if tool.name != "journey_record":
        return None
    order = tool_context.state.get("propel:orders", {}).get(args.get("order_id", ""))
    evals = tool_context.state.get("propel:evals", {})
    ev = evals.get(_order_key(*order)) if order else None
    if not ev:
        return {"status": "blocked", "error": "no rules_evaluate result for this order in this session; nothing recorded"}
    if ev.get("reason") == "kanini_context_unreachable":
        return {"status": "blocked", "error": "Kanini Context was unreachable; nothing recorded"}
    # authoritative fields from the evaluation, whatever the model wrote
    args.update({"outcome": ev["outcome"], "rule_ref": ev["rule_ref"], "source": ev["source"],
                 "safe_to_act": ev["safe_to_act"], "pack_release": ev["pack_release"], "next_step": ev["next_step"]})
    return None


def after_tool(tool, args: dict, tool_context, tool_response):
    name = tool.name
    if name == "journey_get":
        p = _payload(tool_response)
        if p.get("status") == "found":
            orders = dict(tool_context.state.get("propel:orders", {}))
            for o in p["orders"]:
                orders[o["order_id"]] = [p["coverage"]["plan"], p["coverage"]["state"], o["code"], o["date_of_service"]]
            tool_context.state["propel:orders"] = orders
    elif name == "rules_evaluate":
        p = _payload(tool_response)
        key = _order_key(args.get("plan", ""), args.get("state", ""), args.get("code", ""), args.get("date_of_service", ""))
        evals = dict(tool_context.state.get("propel:evals", {}))
        prim = p.get("primary_rule") or {}
        evals[key] = {"outcome": p.get("outcome", "unknown"), "rule_ref": prim.get("rule_ref"), "source": prim.get("citation"),
                      "safe_to_act": bool(p.get("safe_to_act")), "pack_release": p.get("pack_release"),
                      "next_step": p.get("next_step"), "reason": p.get("reason")}
        tool_context.state["propel:evals"] = evals
    elif name == "journey_record" and isinstance(tool_response, dict) and tool_response.get("error") == "This tool call is rejected.":
        _write({"order": args.get("order_id"), "decision": "rejected", "outcome_proposed": args.get("outcome"),
                "ruleRef": args.get("rule_ref"), "reviewer": REVIEWER,
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return None


def on_tool_error(tool, args: dict, tool_context, error: Exception):
    if tool.name in KC_TOOLS:
        return _unavailable()
    return {"status": "error", "error": str(error)[:300]}


def build_agent(model=None) -> LlmAgent:
    return LlmAgent(
        name="authorization_agent",
        model=model or LiteLlm(model=MODEL),
        description="Answers whether an order needs prior authorization, from Kanini Context, with a staff-approved decision log.",
        instruction=INSTRUCTION,
        tools=[journey_read, kanini_context, journey_write],
        before_tool_callback=before_tool,
        after_tool_callback=after_tool,
        on_tool_error_callback=on_tool_error,
    )


root_agent = build_agent()
