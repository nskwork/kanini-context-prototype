"""Run one scenario through the real agent, MCP servers and approval step, with the scripted model.

    python -m propel.agent.run_scripted maria-tn approve
    python -m propel.agent.run_scripted maria-tn reject

Prints the tool trace and the final text; returns the trace for tests.
"""
from __future__ import annotations

import asyncio
import sys

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from propel.agent.authorization_agent.agent import build_agent
from propel.agent.scripted_model import ScriptedModel


async def run(patient_id: str, decision: str = "approve", prompt: str | None = None, skip_checks: bool = False) -> dict:
    agent = build_agent(model=ScriptedModel(patient_id=patient_id, skip_checks=skip_checks))
    svc = InMemorySessionService()
    runner = Runner(app_name="propel", agent=agent, session_service=svc)
    session = await svc.create_session(app_name="propel", user_id="staff")
    trace, texts, msg = [], [], types.Content(role="user", parts=[types.Part(text=prompt or
        f"Check prior authorization for the open order for patient {patient_id}.")])
    for _ in range(3):   # first turn, then the approval answer
        confirm_id = None
        async for ev in runner.run_async(user_id="staff", session_id=session.id, new_message=msg):
            for p in (ev.content.parts if ev.content else []) or []:
                if p.function_call:
                    if p.function_call.name == "adk_request_confirmation":
                        confirm_id = p.function_call.id
                    else:
                        trace.append(p.function_call.name)
                if p.text and ev.author == agent.name:
                    texts.append(p.text)
        if not confirm_id:
            break
        msg = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id=confirm_id, name="adk_request_confirmation", response={"confirmed": decision == "approve"}))])
    for t in (agent.tools or []):
        close = getattr(t, "close", None)
        if close:
            await close()
    return {"trace": trace, "text": "\n".join(texts)}


if __name__ == "__main__":
    out = asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "maria-tn", sys.argv[2] if len(sys.argv) > 2 else "approve"))
    print(" -> ".join(out["trace"]))
    print(out["text"])
