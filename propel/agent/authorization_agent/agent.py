"""Propel authorization agent: answers one prior authorization question per order from Kanini Context.

The model chooses the tools and writes the explanation. The outcome always comes from rules_evaluate:
  - before journey_record runs, the decision fields are replaced with the ones rules_evaluate returned for
    that order, so the log can hold only what the rules said; with no evaluation for the order, nothing is recorded
  - journey_record needs staff approval in adk web; a rejection is logged as rejected, with the reviewer
  - when Kanini Context cannot be reached, its tools answer "unavailable" and the agent routes to staff

Environment: OPENAI_API_KEY, or the AZURE_OPENAI_* settings (propel/llm.py); KC_MCP_URL (default http://127.0.0.1:8765/mcp, the Kanini Context server);
PROPEL_MODEL (default openai/gpt-5-mini, or azure/<deployment> on Azure); PROPEL_REVIEWER (default demo-staff).
"""
from __future__ import annotations

import json
import os
import re
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
sys.path.insert(0, str(ROOT))   # adk web loads this file directly
from kanini_context import env  # noqa: F401,E402  (loads .env)
LOG = Path(os.environ.get("PROPEL_DECISION_LOG", ROOT / "propel" / "journey" / "decisions.jsonl"))
REVIEWER = os.environ.get("PROPEL_REVIEWER", "demo-staff")
KC_URL = os.environ.get("KC_MCP_URL", "http://127.0.0.1:8765/mcp")
from propel.llm import MODEL  # noqa: E402  (OpenAI or Azure OpenAI: propel/llm.py)
KC_TOOLS = ["context_applicable_rules", "rules_evaluate", "policy_search"]

INSTRUCTION = """You are the Propel authorization agent. For each order you answer one question: does this order need
prior authorization, under this plan, in this state, on this date? Kanini Context holds the reviewed payer and
Medicare rules. You never decide from your own knowledge of payer policy.

For every request, in this order (four turns):
1. Call journey_get for the patient or order named.
2. In one turn, call both context_applicable_rules (plan, state, date_of_service and code from the order's
   rules_evaluate_args) and rules_evaluate (the order's rules_evaluate_args exactly as journey_get returned them;
   facts is one object).
3. Call policy_search with rule_ref = primary_rule.rule_ref from the rules_evaluate result, to quote the deciding passage.
4. In one turn, reply with the decision card below, built only from the tool results, and, if safe_to_act is true or
   the outcome is other_gate or needs_facts, call journey_record for the order with outcome, primary_rule.rule_ref,
   primary_rule.citation, safe_to_act, pack_release and next_step copied from rules_evaluate.
   Staff approve or reject that call after reading the card.

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


# --- Model -------------------------------------------------------------------------------------------------------

def make_model():
    """The agent's model through LiteLLM. Tool routing needs little reasoning, so the reasoning effort defaults to
    low (PROPEL_REASONING_EFFORT; empty uses the provider's default)."""
    from google.adk.models.lite_llm import LiteLlm
    effort = os.environ.get("PROPEL_REASONING_EFFORT", "low").strip()
    return LiteLlm(model=MODEL, **({"reasoning_effort": effort} if effort else {}))


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



def _journey_params() -> StdioConnectionParams:
    return StdioConnectionParams(
        server_params=StdioServerParameters(command=sys.executable, args=["-m", "propel.journey.server"], cwd=str(ROOT),
                                            env={**os.environ, "PROPEL_DECISION_LOG": str(LOG), "PROPEL_REVIEWER": REVIEWER}),
        timeout=10)


def make_toolsets() -> list:
    """Fresh connections for one agent: journey reads, Kanini Context, and the approval-gated journey write."""
    return [
        McpToolset(connection_params=_journey_params(), tool_filter=["journey_get"]),
        KaniniContextToolset(connection_params=StreamableHTTPConnectionParams(url=os.environ.get("KC_MCP_URL", KC_URL), timeout=10),
                             tool_filter=KC_TOOLS),
        McpToolset(connection_params=_journey_params(), tool_filter=["journey_record"],
                   require_confirmation=True),   # staff approve or reject every decision in adk web
    ]


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
    code = code.strip().upper()
    return f"{plan.strip().lower()}|{state.strip().upper()}|{code if '|' in code else 'HCPCS|' + code}|{dos.strip()}"


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
                 "safe_to_act": ev["safe_to_act"], "pack_release": ev["pack_release"], "next_step": ev["next_step"],
                 "recorded_as_of": ev.get("recorded_as_of") or ""})
    return None


# --- What the model sees of each tool result ------------------------------------------------------------------------
# The MCP result reaches ADK as both text and structured content, so the model would read every result twice, in
# every later turn. after_tool hands the model one plain copy, and for the Kanini Context tools only the fields the
# decision card uses. The full rules_evaluate packet is kept for the decision log (propel:evals) before trimming;
# the tools themselves, the decision log and the direct tool calls are unchanged.

def _slim_applicable(p: dict) -> dict:
    if p.get("status") != "resolved":
        return p
    keep = ("rule_ref", "rule_kind", "citation", "effect", "basis")
    return {"status": p["status"], "item": p.get("item"), "as_of": p.get("as_of"),
            "rules": [{k: r[k] for k in keep if k in r} for r in p.get("rules") or []]}


def _slim_evaluate(p: dict) -> dict:
    if p.get("status") in ("invalid_input", "unavailable"):
        return p
    prim = dict(p.get("primary_rule") or {})
    src = next((s for s in p.get("sources") or [] if s.get("citation") == prim.get("citation")), None)
    if src and src.get("quote") and "quote" not in prim:
        prim.update({"page": (src.get("locator") or {}).get("page"), "quote": src["quote"]})
    cc = p.get("coverage_criteria") or {}
    out = {k: p.get(k) for k in ("status", "outcome", "safe_to_act", "not_safe_because", "reason", "reason_text",
                                 "reasons", "next_step", "next_step_detail", "missing_facts", "evidence",
                                 "decision_clock", "route", "freshness", "aging_sources", "review_exceptions",
                                 "pack_release")}
    out["primary_rule"] = prim
    out["not_checked"] = cc.get("not_checked") if isinstance(cc, dict) else None
    return {k: v for k, v in out.items() if v not in (None, [], {}, "")}


def _slim_policy(p: dict) -> dict:
    if "quote" not in p:
        return p
    return {k: v for k, v in {"rule_ref": p.get("rule_ref"), "citation": p.get("citation"), "edition": p.get("edition"),
                              "page": (p.get("locator") or {}).get("page"), "quote": p.get("quote")}.items() if v is not None}


def after_tool(tool, args: dict, tool_context, tool_response):
    name = tool.name
    if name == "journey_get":
        p = _payload(tool_response)
        if p.get("status") == "found":
            orders = dict(tool_context.state.get("propel:orders", {}))
            for o in p["orders"]:
                orders[o["order_id"]] = [p["coverage"]["plan"], p["coverage"]["state"], o["code"], o["date_of_service"]]
            tool_context.state["propel:orders"] = orders
        return p or None
    elif name == "context_applicable_rules":
        p = _payload(tool_response)
        return _slim_applicable(p) if p else None
    elif name == "policy_search":
        p = _payload(tool_response)
        return _slim_policy(p) if p else None
    elif name == "rules_evaluate":
        p = _payload(tool_response)
        key = _order_key(args.get("plan", ""), args.get("state", ""), args.get("code", ""), args.get("date_of_service", ""))
        evals = dict(tool_context.state.get("propel:evals", {}))
        prim = p.get("primary_rule") or {}
        evals[key] = {"outcome": p.get("outcome", "unknown"), "rule_ref": prim.get("rule_ref"), "source": prim.get("citation"),
                      "safe_to_act": bool(p.get("safe_to_act")), "pack_release": p.get("pack_release"),
                      "next_step": p.get("next_step"), "reason": p.get("reason"), "recorded_as_of": p.get("recorded_as_of"),
                      "card": _slim_evaluate(p) if p else {}}
        tool_context.state["propel:evals"] = evals
        return _slim_evaluate(p) if p else None
    elif name == "journey_record" and isinstance(tool_response, dict) and tool_response.get("error") == "This tool call is rejected.":
        _write({"order": args.get("order_id"), "decision": "rejected", "outcome_proposed": args.get("outcome"),
                "ruleRef": args.get("rule_ref"), "reviewer": REVIEWER,
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return None


def on_tool_error(tool, args: dict, tool_context, error: Exception):
    if tool.name in KC_TOOLS:
        return _unavailable()
    return {"status": "error", "error": str(error)[:300]}


# --- The decision card says what rules_evaluate returned, whatever the model wrote ---------------------------------

OUTCOME_LABEL = {"no_pa": "No PA", "pa_required": "PA required", "exempt": "Exempt",
                 "other_gate": "Other gate (coverage criteria not met)", "needs_facts": "Not yet known (facts needed)",
                 "unknown": "Not known (route to staff)"}
_ORDER_ID = re.compile(r"\b(ord-[\w-]+)\b", re.I)
_OUTCOME_LINE = re.compile(r"^([ \t>*-]*)\**Outcome:\**[ \t]*.*$", re.M | re.I)   # also **Outcome:** in markdown


def card_outcome_line(ev: dict | None) -> str:
    if not ev:
        return "Outcome: Not known (route to staff) · not safe to act (no rules_evaluate result for this order)"
    return f"Outcome: {OUTCOME_LABEL.get(ev['outcome'], ev['outcome'])} · {'safe to act' if ev['safe_to_act'] else 'not safe to act'}"


def outcome_line_agrees(line: str, ev: dict | None) -> bool:
    """Whether a model-written Outcome line states the same outcome and the same safe-to-act flag as rules_evaluate.
    Wording, dashes, capitals and anything added after the flag do not matter; the meaning does."""
    if not ev or not line:
        return False
    t = re.sub(r"[*_`]", "", line).lower().split("outcome:", 1)[-1]
    named = [k for k, lab in OUTCOME_LABEL.items() if lab.split(" (")[0].lower() in t]
    # "Not yet known" also contains no other label, and "No PA" is not inside "PA required": keep the longest match
    named = sorted(named, key=lambda k: -len(OUTCOME_LABEL[k].split(" (")[0]))[:1]
    says_not_safe = "not safe" in t or "unsafe" in t
    says_safe = "safe to act" in t and not says_not_safe
    return named == [ev["outcome"]] and (says_safe if ev["safe_to_act"] else says_not_safe)


def card_from_rules(order_id: str, ev: dict) -> str:
    """The decision card written from the rules_evaluate result alone, for a record request the model sent without one."""
    c = ev.get("card") or {}
    prim = c.get("primary_rule") or {}
    lines = [f"Decision card · {order_id}", card_outcome_line(ev)]
    lines += ["Why: " + r for r in (c.get("reasons") or [])[:6]]
    if c.get("next_step_detail") or c.get("next_step"):
        lines.append(f"Next step: {c.get('next_step_detail') or c.get('next_step')}")
    if (c.get("decision_clock") or {}).get("text"):
        lines.append(f"Deadline: {c['decision_clock']['text']}")
    route = c.get("route") or {}
    if route.get("submit_via"):
        ch = (route.get("channels") or [{}])[0]
        lines.append(f"Submit via: {route['submit_via']}" + (f" ({ch['interface']}: {ch['address']})" if ch.get("address") else ""))
    for e in c.get("evidence") or []:
        if isinstance(e, dict) and e.get("documents"):
            lines.append(f"Evidence to send ({'any one of' if e.get('logic') == 'any' else 'all of'}): " + "; ".join(e["documents"]))
    if prim.get("rule_ref"):
        q = f', "{prim["quote"][:160]}"' if prim.get("quote") else ""
        lines.append(f"Rule: {prim['rule_ref']} ({prim.get('citation', '')}{', p. ' + str(prim['page']) if prim.get('page') else ''}){q}")
    if c.get("pack_release"):
        lines.append(f"Pack: {c['pack_release']}")
    lines.append("(Card written from rules_evaluate: the record request came without one.)")
    return "\n".join(lines)


def after_model(callback_context, llm_response):
    """If the reply holds a decision card, its Outcome line is set from the rules_evaluate result for that order,
    so neither the log nor the reply can state an outcome the rules did not return. If the reply asks to record a
    decision without a card, the card is written from the rules first, so staff always see it before they approve."""
    parts = (llm_response.content.parts if llm_response and llm_response.content else None) or []
    state = callback_context.state
    calls = [p.function_call for p in parts if getattr(p, "function_call", None) and p.function_call.name == "journey_record"]
    if calls and not any("outcome:" in (getattr(p, "text", None) or "").lower() for p in parts):
        cards = []
        for fc in calls:
            oid = (fc.args or {}).get("order_id", "")
            order = state.get("propel:orders", {}).get(oid)
            ev = state.get("propel:evals", {}).get(_order_key(*order)) if order else None
            if ev:
                cards.append(card_from_rules(oid, ev))
        if cards:
            from google.genai import types
            llm_response.content.parts = [types.Part(text="\n\n".join(cards))] + list(parts)
            return None
    for part in parts:
        text = getattr(part, "text", None)
        if not text or "Outcome:" not in text:
            continue
        # the order the card is about: its id anywhere in the card, or the session's only evaluation
        m = _ORDER_ID.search(text)
        evals = state.get("propel:evals", {})
        order = state.get("propel:orders", {}).get(m.group(1).lower()) if m else None
        ev = evals.get(_order_key(*order)) if order else (next(iter(evals.values())) if len(evals) == 1 else None)
        want = card_outcome_line(ev)
        line = _OUTCOME_LINE.search(text)
        got = re.sub(r"\*+", "", line.group(0)).strip().lstrip("-•> ").strip() if line else ""
        if line and got != want:
            # the line is always set to the rules' wording; the note is added only when the model's line meant
            # something else (another outcome, or the wrong safe-to-act flag), not for a different dash or capital
            fixed = text[:line.start()] + line.group(1) + want + text[line.end():]
            part.text = fixed if outcome_line_agrees(got, ev) else fixed + "\n(Outcome line set from rules_evaluate.)"
    return None


def build_agent(model=None) -> LlmAgent:
    return LlmAgent(
        name="authorization_agent",
        model=model or make_model(),
        description="Answers whether an order needs prior authorization, from Kanini Context, with a staff-approved decision log.",
        instruction=INSTRUCTION,
        tools=make_toolsets(),
        before_tool_callback=before_tool,
        after_tool_callback=after_tool,
        on_tool_error_callback=on_tool_error,
        after_model_callback=after_model,
    )


root_agent = build_agent()
