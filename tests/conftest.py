"""Shared fixtures: one TEST release built for the whole run, the MCP server in process and over HTTP,
and a fresh decision log per test. Needs PostgreSQL (PG* variables; default the local test server)."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# The tests build their own databases, so they never touch the demo release in kc_build / kc_client.
os.environ["KC_BUILD_DB"], os.environ["KC_CLIENT_DB"] = "kct_build", "kct_client"
from kanini_context import env  # noqa: F401,E402  (loads .env)
os.environ["KC_MCP_URL"] = "http://127.0.0.1:8799/mcp"   # the tests start their own server on this port
DOS = "2026-11-15"


def _pg_up() -> bool:
    try:
        psycopg.connect("dbname=postgres", connect_timeout=3).close()
        return True
    except psycopg.Error:
        return False


@pytest.fixture(scope="session")
def release():
    if not _pg_up():
        pytest.skip("PostgreSQL not reachable")
    r = subprocess.run([sys.executable, "-m", "kanini_context.release.build", "--test-review"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@pytest.fixture(scope="session")
def db(release):
    with psycopg.connect("dbname=kct_client", autocommit=True) as c:
        yield c


@pytest.fixture(scope="session")
def kc(release):
    """Call a Kanini Context tool in process: kc(tool, **args) -> structured result."""
    from fastmcp import Client
    from kanini_context.server.mcp_server import mcp

    def call(tool: str, **args):
        async def go():
            async with Client(mcp) as c:
                r = await c.call_tool(tool, args, raise_on_error=False)
                return r.structured_content
        return asyncio.run(go())
    return call


@pytest.fixture(scope="session")
def kc_http(release):
    """The Kanini Context server over HTTP, as the agent reaches it."""
    port = int(os.environ["KC_MCP_URL"].rsplit(":", 1)[1].split("/")[0])
    p = subprocess.Popen([sys.executable, "-m", "kanini_context.server.mcp_server", "--http", str(port)], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield p
    p.terminate()
    p.wait(5)


@pytest.fixture()
def decision_log(tmp_path, monkeypatch):
    log = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("PROPEL_DECISION_LOG", str(log))
    import propel.agent.authorization_agent.agent as agent
    monkeypatch.setattr(agent, "LOG", log)
    return log


def read_log(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def facts(**over):
    base = {"encounter_type": "elective", "dialysis_status": "none", "diagnosis_codes": ["D63.1", "N18.32"],
            "hgb_g_dl": 9.4, "egfr": 38, "creatinine_date": "2026-11-02"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def unscreened_programs(db, state: str, dos: str = DOS) -> list[str]:
    """The prior authorization programs bound to Original Medicare in a state on a date that have no ESA screen yet.
    Programs known from CMS but not yet saved (scope findings, section 6) stay here until their copies are recorded
    and approved; until then Original Medicare ESA answers in that state are "no rule found", naming them."""
    bound = db.execute("SELECT kc_api.bound_pa_programs(%s::jsonb, 'drug', %s::date)",
                       (json.dumps({"program": "medicare_ffs", "plan": "original-medicare", "state": state}), dos)).fetchone()[0]
    screened = {r[0] for r in db.execute("""SELECT authority_id::text FROM kc_core.rule
                                             WHERE rule_key LIKE 'kc:rule/esa-ckd-nd/no-pa/%' AND status = 'active'
                                               AND upper_inf(recorded)""").fetchall()}
    return sorted(b["citation"] for b in bound if b["authority_id"] not in screened)


@pytest.fixture(scope="session")
def screened(db):
    """screened("TN") is true when every program bound to Original Medicare in that state has an ESA screen."""
    return lambda state: not unscreened_programs(db, state)


@pytest.fixture(scope="session")
def asc_screened(screened) -> bool:
    """Whether Tennessee's programs are all screened (scenario 1 and the decision-log tests run on Tennessee)."""
    return screened("TN")


def assert_held_for_unscreened_program(out: dict, db=None, state: str = "TN"):
    assert out["outcome"] in ("unknown", "other_gate") and out["safe_to_act"] is False
    assert out["prior_authorization"]["reason"] == "pa_program_not_screened"
    if out["outcome"] == "unknown":
        assert out["next_step"] == "human_review" and "not screened against" in out["next_step_detail"]
    if db is not None:
        assert out["prior_authorization"]["notes"] == ["not screened against: " + "; ".join(unscreened_programs(db, state))]
