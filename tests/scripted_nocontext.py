"""A scripted stand-in for lane A's model (the agent without Kanini Context), for offline tests and rehearsal.

It looks up the patient, searches the library, reads the top page and writes a card. It proves the plumbing of
lane A (tools, events, grading), not a model's judgment: its card always says "Not known".
"""
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from scripted_model import _call, _responses, _text


class ScriptedNoContextModel(BaseLlm):
    model: str = "scripted"
    patient_id: str = "maria-tn"

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        done = dict(_responses(llm_request))
        if "journey_get" not in done:
            yield _call("journey_get", {"patient_id": self.patient_id})
        elif "search_documents" not in done:
            o = done["journey_get"]["orders"][0]
            yield _call("search_documents", {"query": f"prior authorization {o['code']} {o.get('description', '')}"})
        elif "read_pages" not in done:
            p = done["search_documents"]["passages"][0]
            yield _call("read_pages", {"source_id": p["source_id"], "pages": [p["page"]]})
        else:
            o = done["journey_get"]["orders"][0]
            yield _text(f"Decision card · {o['order_id']}\nOutcome: Not known (route to staff) · not safe to act\n"
                        "Why: scripted stand-in\nNext step: route to staff\nSource: none")
