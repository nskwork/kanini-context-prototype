# Kanini Context Prototype

An authorization agent answers one prior authorization question per order from Kanini Context, through MCP tools. Every answer carries its rule and edition, the source page and quote, a safe-to-act flag and a next step, and staff approve each decision before it is logged.

The use case is the one in the prototype scope: an anemia drug (ESA) for a patient with chronic kidney disease who is not on dialysis, under Original Medicare in two contractor regions and under one UnitedHealthcare Medicare Advantage plan. The scope findings (`docs/SCOPE_FINDINGS.md`) explain the two changes to the use case: scenario 3 uses IV iron (J1439), and Original Medicare's "no PA" rests on the CMS program lists.

Kanini Context follows the schema specification v1.5. The prototype's database is the specification's schema with a short list of documented extensions (top of `kanini_context/db/schema.sql`), and the specification's 61 regression tests still give the same verdicts (`kanini_context/db/spec_regression/`).

Prepared by NANDHU S K.

## How it works

```
 source PDFs ──> rule files (drafted, checked against the page, approved) ──> loader ──> build DB ──> release ──> client copy (kc_client)
                                                                                                                       │
 Propel agent (ADK, GPT-5 mini) ──MCP──> Kanini Context server: context_applicable_rules, rules_evaluate, policy_search ┘
                                         (plus the seven tools of spec 7.2, for other agents)
        │
        └──MCP──> Propel journey (mock): journey_get, journey_record ──> decisions.jsonl (after staff approval)
```

The model chooses the tools and writes the explanation. The outcome always comes from `rules_evaluate`, which is SQL over reviewed rules, so the same question gets the same answer. Before `journey_record` runs, its fields are replaced with what `rules_evaluate` returned for that order, so the log can only hold what the rules said.

## Layout

| Path | What it holds |
| --- | --- |
| `kanini_context/sources/` | `manifest.json` (URL, edition, retrieval and check dates, SHA-256 of each copy), `place.py`, and `raw/` for the PDFs (not in git) |
| `kanini_context/rules/` | One rule file per source: each rule with its page, column, quote, payload and review |
| `kanini_context/curation/` | `checks.py` (quote on the cited page, numbers and codes in the quote, negation, schema, completeness), `approve.py`, `load.py`, `pdftext.py` |
| `kanini_context/db/` | `schema.sql`, `registry.sql`, `resolver.sql` (spec resolver), `api.sql` (agent-facing functions), `spec_regression/` |
| `kanini_context/release/` | `build.py` (build DB, rule digest, client copy with a read-only tool role, Apache AGE graph `ref_YYYY_MM`), `sync.py`, `owl.py` and the generated ontology `kanini-context.ttl` |
| `kanini_context/server/mcp_server.py` | The Kanini Context MCP server (FastMCP) |
| `propel/journey/` | Mock patients and orders, the journey MCP server, the decision log |
| `propel/agent/authorization_agent/` | The ADK agent for `adk web` |
| `propel/baseline/compare.py` | LLM calls and tokens with and without Kanini Context |
| `tests/` | The scope's 23 tests by number, pack tests, and a scripted stand-in model for offline agent runs |
| `docs/` | Scope findings, prototype notes, demo script |

## Run it on a laptop

Needs Docker and Python 3.12 or 3.13.

```bash
# 1. Database: PostgreSQL 16 with Apache AGE 1.6.0
docker compose up -d db

# 2. Python
python -m venv .venv && source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # then add OPENAI_API_KEY

# 3. Source copies: finds the PDFs in a folder by hash and puts them in kanini_context/sources/raw/
python -m kanini_context.sources.place ~/Downloads
#    Five CMS copies are not saved yet (program pages printed to PDF, and their code lists); save them under
#    the names `place` prints, then record them:
python -m kanini_context.sources.place --record ~/Downloads
python -m kanini_context.curation.checks                # fix any page numbers it reports for the new quotes

# 4. Approve the rule files (once, after checking each rule against its source), then commit them
python -m kanini_context.curation.approve --by "NANDHU S K"
git commit -am "Approve rule files"

# 5. Build the pack and the client copy (release 2026.11.0), then the tests (no API key needed)
python -m kanini_context.release.build
pytest

# 6. Demo: the Kanini Context server, then the ADK web UI
python -m kanini_context.server.mcp_server --http 8765 &
adk web propel/agent                                    # open http://127.0.0.1:8000, pick authorization_agent
```

On the day of the demo, `python -m kanini_context.release.sync` records a sync, so insurer answers are not held as "sync aging" (48 hours). Prompts for the demo are in `docs/DEMO_SCRIPT.md`. Patients: `maria-tn`, `maria-tn-egfr52`, `maria-oh`, `maria-uhc`, `maria-uhc-esa`, `maria-nohgb` (synthetic, in `propel/journey/patients.json`).

## Approval

As the scope sets it, one engineer checks each rule against its source, and formal review comes later. `curation/approve.py` shows every rule with its page and quote and records the approval with a standing exception to the spec's two-approver rule; every answer shows that exception. The rule files are drafted and pass the automated checks; they are not approved yet. Until they are, the build refuses them, and `--test-review` (used by the tests) builds a release labelled `TEST-`. Details in `docs/PROTOTYPE_NOTES.md`, section 2.

## CMS programs found during the build

Three CMS prior authorization programs bind Original Medicare beyond the fiscal year 2024 statistics the first program list came from: the ASC demonstration (Tennessee and Ohio from early 2026), the DMEPOS probationary process (nationwide from 15 October 2026) and the IRF demonstration expansion (Tennessee from 2027) (scope findings, section 6). They are registered in the pack, but their copies are not saved yet, so until they are recorded and approved, Original Medicare ESA answers in Tennessee, Ohio and every other bound state are "no rule found", naming each unscreened program, and route to staff. That is the design working: "No PA" holds only when every bound program has been screened. `python -m kanini_context.sources.place` lists the five CMS copies to save (including the CMS index of initiatives, which is the check that the program set is complete). The tests work out from the pack which programs are screened, and expect the matching answer.

## Freshness

Each source has a last-confirmed date in the manifest. Past 14 days an answer that rests on it is held for staff ("source aging", naming the source); past 45 days it is withheld (proposal appendix B.3). Before the demo, recheck each source on its URL and add a dated entry to its `checks` list. Article A56462 was last confirmed on 2026-10-15, so Ohio answers are held until it is rechecked.

## Tests and the scope

`tests/test_scope.py` holds the scope's tests 1 to 23 under their numbers. Tests 2 to 8 and 11 to 19 call the MCP server; test 20 runs the loader; tests 9, 10 and 21 to 23 run the real agent, journey server, approval step and log with a scripted model in place of GPT-5 mini (`tests/scripted_model.py`), which proves the wiring offline. `tests/test_agent_live.py` runs tests 9 and 21 to 23 again with GPT-5 mini when `OPENAI_API_KEY` is set (skipped otherwise).

Field names follow the prototype's tool schemas, as the scope allows. Two answers differ from the scope's examples on purpose, both because L39237 accepts "Hb < 10 g/dL **or** HCT < 30%":

- Test 5, no hemoglobin: the answer asks for hemoglobin or hematocrit, since either one settles it.
- Test 17, Hb exactly 10.0: with a hematocrit of 30% or more the answer is "other gate"; with no hematocrit it is "not yet known", since a hematocrit below 30% would still meet the criterion.

`docs/PROTOTYPE_NOTES.md` sets out how the build follows spec v1.5 section by section, where it differs and why, and the answer contract.

## LLM calls saved

```bash
python -m propel.baseline.compare --offline      # no key: the workflow path, and how big a "read the policies" prompt is
python -m propel.baseline.compare --runs 5       # with the key: all three modes
```

Mode A gives GPT-5 mini the policy text and asks it to decide; mode B is the agent with Kanini Context; mode C calls `rules_evaluate` with no model. Offline, mode C answers each scenario the same way every run in about 30 ms, while mode A would send 21,000 to 57,000 input tokens of policy text to the model for every question.
