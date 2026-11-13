"""The same authorization agent without the context layer: the comparison lane A.

It is the Propel agent of propel/agent/authorization_agent with one difference: Kanini Context is gone. It has the
same model (GPT-5 mini through LiteLLM, same reasoning effort) and the same patient-record tool, and in place of
the Kanini Context tools it has the policy library the rules were drawn from:

  list_documents     the documents in the library, with their editions and page counts
  search_documents   the passages that best match a query, with document and page
  read_pages         the text of up to three pages of one document

So for every order it has to find and read the payer and Medicare policy again, and decide from it: the repeated
model calls, over information Kanini Context already holds, that the context layer removes. It does not record
decisions; it answers with the same decision card format, so its Outcome line can be checked against the rules.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool import McpToolset

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from kanini_context import env  # noqa: F401,E402  (loads .env)
from propel.agent.authorization_agent.agent import OUTCOME_LABEL, _journey_params, _payload, make_model  # noqa: E402
from propel.baseline import library  # noqa: E402

MAX_PAGES = 3          # pages per read_pages call
SEARCH_RESULTS = 6     # passages per search_documents call
MAX_CALLS = 14         # model calls per order, so a lost agent cannot run on

INSTRUCTION = """You are the Propel authorization agent. For each order you answer one question: does this order need
prior authorization, under this plan, in this state, on this date? There is no rules service. Decide only from the
payer and Medicare policy documents in the library, never from your own knowledge of payer policy.

For every request:
1. Call journey_get for the patient or order named.
2. Find the documents that govern this coverage, state, service and date: list_documents, search_documents.
3. Read the pages you need with read_pages (up to 3 pages a call) until you can decide, and cite what you read.
4. Reply with the decision card below. Do not ask the user anything.

Outcomes: No PA (no approval needed and coverage criteria met), PA required, Exempt, Other gate (coverage criteria
not met), Not yet known (a fact needed to decide is missing), Not known (route to staff: the documents do not settle it).

Decision card (plain text, short):
Decision card · <order_id>
Outcome: <No PA | PA required | Exempt | Other gate (coverage criteria not met) | Not yet known (facts needed) | Not known (route to staff)> · <safe to act | not safe to act>
Why: <one reason per line>
Next step: <what staff do next>
Source: <document, section and page>
"""


def list_documents() -> dict:
    """The policy documents in the library: id, title, edition and number of pages."""
    return {"documents": [{"source_id": d["source_id"], "title": d["title"], "edition": d["edition"], "pages": len(d["pages"])}
                          for d in library.documents()]}


def search_documents(query: str) -> dict:
    """The passages of the library that best match the query, each with its document id, title and page."""
    found, method = library.retrieve(query, k=SEARCH_RESULTS)
    return {"method": method, "passages": [{"source_id": p["source_id"], "title": p["title"], "page": p["page"], "text": p["text"]}
                                           for p in found]}


def read_pages(source_id: str, pages: list[int]) -> dict:
    """The text of up to three pages (1-based) of one document in the library."""
    doc = next((d for d in library.documents() if d["source_id"] == source_id), None)
    if not doc:
        return {"error": f"no document {source_id}; call list_documents"}
    want = [p for p in pages if isinstance(p, int) and 1 <= p <= len(doc["pages"])][:MAX_PAGES]
    if not want:
        return {"error": f"{source_id} has pages 1 to {len(doc['pages'])}"}
    return {"source_id": source_id, "title": doc["title"], "pages": [{"page": p, "text": doc["pages"][p - 1]} for p in want]}


_LINE = re.compile(r"^[ \t>*-]*(?:\d+[.)]\s*)?\**Outcome:\**[ \t]*(.*)$", re.M | re.I)   # also "2) Outcome:"


def card_outcome(text: str) -> str | None:
    """The outcome named on the card's Outcome line, as a rules outcome code (None when there is no card)."""
    found = list(_LINE.finditer(text or ""))
    if not found:
        return None
    m = found[-1]   # the final card: a model can write an early Outcome line and revise it after reading more
    t = re.sub(r"[*_`]", "", m.group(1)).lower()
    named = [k for k, lab in OUTCOME_LABEL.items() if lab.split(" (")[0].lower() in t]
    return sorted(named, key=lambda k: -len(OUTCOME_LABEL[k].split(" (")[0]))[0] if named else "unparseable"


def after_tool(tool, args: dict, tool_context, tool_response):
    """The patient record reaches the model once, as it does for B: an MCP result carries both text and structured
    content, and without this the model would read the record twice on every later turn."""
    if tool.name == "journey_get":
        return _payload(tool_response) or None
    return None


def build_agent(model=None) -> LlmAgent:
    return LlmAgent(
        name="authorization_agent_no_context",
        model=model or make_model(),
        description="The authorization agent without Kanini Context: it reads the policy documents itself.",
        instruction=INSTRUCTION,
        tools=[McpToolset(connection_params=_journey_params(), tool_filter=["journey_get"]),
               FunctionTool(list_documents), FunctionTool(search_documents), FunctionTool(read_pages)],
        after_tool_callback=after_tool,
    )


root_agent = build_agent()
