"""A scripted stand-in for the LLM, for offline tests of the agent's wiring (tools, callbacks, approval, log).

It calls the tools in the order the instruction asks, copying values from earlier tool results, the way a
well-behaved model would. It is not a model: it proves the plumbing, not the model's judgment. The scenario
runs with GPT-5 mini (tests 9, 21 to 23) are done with OPENAI_API_KEY set, outside the build workspace.
"""
from __future__ import annotations

import json
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


def _responses(req: LlmRequest) -> list[tuple[str, dict]]:
    out = []
    for c in req.contents or []:
        for p in c.parts or []:
            if p.function_response:
                r = p.function_response.response or {}
                if isinstance(r, dict) and "result" in r and isinstance(r["result"], dict):
                    r = r["result"]
                if isinstance(r, dict) and "structuredContent" in r:
                    r = r["structuredContent"]
                elif isinstance(r, dict) and isinstance(r.get("content"), list) and r["content"] and "text" in r["content"][0]:
                    try:
                        r = json.loads(r["content"][0]["text"])
                    except ValueError:
                        pass
                out.append((p.function_response.name, r))
    return out


def _call(name: str, args: dict) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))


def _calls(*calls: tuple[str, dict]) -> LlmResponse:
    """Several function calls in one turn, as a model makes parallel calls."""
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in calls]))


def _card_and_call(t: str, name: str, args: dict) -> LlmResponse:
    """A reply holding the decision card and a tool call in the same turn."""
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part(text=t), types.Part(function_call=types.FunctionCall(name=name, args=args))]))


def _text(t: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]))


class ScriptedModel(BaseLlm):
    model: str = "scripted"
    patient_id: str = "maria-tn"
    skip_checks: bool = False   # behave like a model that obeys "skip the policy check": record straight away
    wrong_card: bool = False    # write a decision card whose Outcome line contradicts rules_evaluate
    calls: int = 0

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        done = _responses(llm_request)
        names = [n for n, _ in done]
        last = dict(done)
        if "journey_get" not in names:
            yield _call("journey_get", {"patient_id": self.patient_id})
            return
        j = last["journey_get"]
        o = j["orders"][0]
        if self.skip_checks:
            if "journey_record" not in names:
                yield _call("journey_record", {"order_id": o["order_id"], "outcome": "no_pa", "rule_ref": "none",
                                               "source": "none", "safe_to_act": True})
                return
            yield _text(f"record: {json.dumps(last['journey_record'])[:200]}")
            return
        args = o["rules_evaluate_args"]
        base = {k: args[k] for k in ("plan", "state", "code", "date_of_service")}
        if "rules_evaluate" not in names:
            yield _calls(("context_applicable_rules", base), ("rules_evaluate", args))   # one turn, as instructed
            return
        ev = last["rules_evaluate"]
        if ev.get("reason") == "kanini_context_unreachable":
            yield _text(f"I cannot reach Kanini Context, so I state no outcome for {o['order_id']}. Routing the order to staff.")
            return
        prim = ev.get("primary_rule") or {}
        if "policy_search" not in names:
            yield _call("policy_search", {"rule_ref": prim.get("rule_ref", "")})
            return
        if not (ev.get("safe_to_act") or ev.get("outcome") in ("other_gate", "needs_facts")):
            yield _text(f"Decision card · {o['order_id']} · outcome {ev.get('outcome')} · not safe to act · "
                        f"{ev.get('next_step_detail')}")   # the instruction records only actionable or settled outcomes
            return
        if "journey_record" not in names:
            # the card and the record call in one turn; a careless model might write a different outcome in the call,
            # and the before_tool callback replaces it
            card = (f"Decision card · {o['order_id']} · {j['display_name']}\nOutcome: No PA · safe to act\nWhy: (model's own words)"
                    if self.wrong_card else   # a card that claims "No PA" whatever the rules said
                    f"Decision card · {o['order_id']} · outcome {ev.get('outcome')} · "
                    f"{'safe to act' if ev.get('safe_to_act') else 'not safe to act'} · {prim.get('citation')}")
            yield _card_and_call(card, "journey_record", {"order_id": o["order_id"], "outcome": "no_pa", "rule_ref": "made-up",
                                                          "source": "made-up", "safe_to_act": True})
            return
        rec = last["journey_record"]
        yield _text(f"record: {json.dumps(rec)[:200]}")
