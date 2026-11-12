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
        │
        └──MCP──> Propel journey (mock): journey_get, journey_record ──> decisions.jsonl (after staff approval)
```

The model chooses the tools and writes the explanation. The outcome always comes from `rules_evaluate`, which is SQL over reviewed rules, so the same question gets the same answer. Before `journey_record` runs, its fields are replaced with what `rules_evaluate` returned for that order, so the log can only hold what the rules said.

## Layout

| Path | What it holds |
| --- | --- |
| `kanini_context/sources/` | `manifest.json` (URL, edition, retrieval and check dates, SHA-256 of each copy), `place.py`, and `raw/` for the PDFs (not in git) |
| `kanini_context/rules/` | One rule file per source: each rule with its page, column, quote, payload and review |
| `kanini_context/curation/` | `checks.py` (quote on the cited page, numbers in the quote, negation, schema), `load.py`, `pdftext.py` |
| `kanini_context/db/` | `schema.sql`, `registry.sql`, `resolver.sql` (spec resolver), `api.sql` (agent-facing functions), `spec_regression/` |
| `kanini_context/release/build.py` | Build DB, rule digest, client copy, Apache AGE graph `ref_YYYY_MM` |
| `kanini_context/server/mcp_server.py` | The Kanini Context MCP server (FastMCP) |
| `propel/journey/` | Mock patients and orders, the journey MCP server, the decision log |
| `propel/agent/authorization_agent/` | The ADK agent for `adk web` |
| `propel/baseline/compare.py` | LLM calls and tokens with and without Kanini Context |
| `tests/` | The scope's 23 tests by number, plus pack tests |
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

# 3. Source copies: finds the 10 PDFs in a folder by hash and puts them in kanini_context/sources/raw/
python -m kanini_context.sources.place ~/Downloads

# 4. Build the pack and the client copy
python -m kanini_context.release.build --test-review    # see "Review status" below

# 5. Tests (23 scope tests plus pack tests; no API key needed)
pytest

# 6. Demo: the Kanini Context server, then the ADK web UI
python -m kanini_context.server.mcp_server --http 8765 &
adk web propel/agent                                    # open http://127.0.0.1:8000, pick authorization_agent
```

Prompts for the demo are in `docs/DEMO_SCRIPT.md`. Patients: `maria-tn`, `maria-tn-egfr52`, `maria-oh`, `maria-uhc`, `maria-uhc-esa`, `maria-nohgb` (synthetic, in `propel/journey/patients.json`).

## Review status

Two different approvers are the rule for every rule, binding and code map (spec section 7.1). One named approver is accepted only with a recorded `review_exception`, and every answer shows it.

The rule files are drafted and pass the automated checks, but `reviewed_by` is still empty. `--test-review` builds a pack labelled `TEST-...` with a test approver and the exception "TEST build: not reviewed", so everything can run before review. To release for the demo, each file's `review` block gets the approver names (and, with one approver, the exception), and the build runs without `--test-review`.

## Freshness

Each source has a last-confirmed date in the manifest. Past 14 days an answer that rests on it is held for staff ("source aging", naming the source); past 45 days it is withheld (proposal appendix B.3). Before the demo, recheck each source on its URL and add a dated entry to its `checks` list. Article A56462 was last confirmed on 2026-10-15, so Ohio answers are held until it is rechecked.

## Tests and the scope

`tests/test_scope.py` holds the scope's tests 1 to 23 under their numbers. Tests 2 to 8 and 11 to 19 call the MCP server; test 20 runs the loader; tests 9, 10 and 21 to 23 run the real agent, journey server, approval step and log with a scripted model in place of GPT-5 mini, which proves the wiring offline. The same prompts with GPT-5 mini are run in `adk web` (or with `propel/baseline/compare.py`) with the API key.

Field names follow the prototype's tool schemas, as the scope allows. Two answers differ from the scope's examples on purpose, both because L39237 accepts "Hb < 10 g/dL **or** HCT < 30%":

- Test 5, no hemoglobin: the answer asks for hemoglobin or hematocrit, since either one settles it.
- Test 17, Hb exactly 10.0: with a hematocrit of 30% or more the answer is "other gate"; with no hematocrit it is "not yet known", since a hematocrit below 30% would still meet the criterion.

`docs/PROTOTYPE_NOTES.md` lists the other choices: tool names with underscores, two MCP servers, the answer contract and the coverage-gap rules.

## LLM calls saved

```bash
python -m propel.baseline.compare --offline      # no key: the workflow path, and how big a "read the policies" prompt is
python -m propel.baseline.compare --runs 5       # with the key: all three modes
```

Mode A gives GPT-5 mini the policy text and asks it to decide; mode B is the agent with Kanini Context; mode C calls `rules_evaluate` with no model. Offline, mode C answers each scenario the same way every run in about 30 ms, while mode A would send 21,000 to 57,000 input tokens of policy text to the model for every question.
