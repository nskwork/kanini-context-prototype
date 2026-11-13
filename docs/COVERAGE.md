# Kanini Context Prototype: coverage of the scope and the specification

Prepared by NANDHU S K. Date: 12 November 2026.

This note maps every item of "Kanini Context Prototype: Scope" (12 Nov 2026) and of the Kanini Context schema specification v1.5 to the place in the repository that delivers it, and lists each point where the prototype differs on purpose, with the reason. Detail sits in `PROTOTYPE_NOTES.md` (spec conformance, section 3) and `SCOPE_FINDINGS.md` (what the sources say).

Status key: **Done** (built as written), **Done, adjusted** (built, with a documented difference), **Extra** (beyond the scope), **Not built** (named as out of scope or for later).

## 1. Scope, section 1: what the demo must show

| Scope item | Status | Where |
| --- | --- | --- |
| Kidney care; ESA for CKD not on dialysis | Done | Service concept `esa-ckd-nd`; `kanini_context/rules/mcd-lcd-*.json` |
| Original Medicare in two contractor regions | Done | L39237 (Palmetto GBA, J-J and J-M, Tennessee) and L34356 (CGS, J-15, Ohio) |
| One UnitedHealthcare Medicare Advantage plan | Done | Plan `uhc-ma`; `kanini_context/rules/uhc-*.json` |
| Four public documents turned into rule records | Done, extended | 16 saved documents (`kanini_context/sources/manifest.json`): the four, their two billing articles, the two eCFR sections as separate files, and the CMS program documents needed to state "No PA" (findings, sections 3 and 6) |
| One Google ADK agent on GPT-5 mini, mock patient data | Done | `propel/agent/authorization_agent/agent.py`; mock patients in `propel/journey/patients.json` (synthetic, no PHI) |
| ADK web UI for the demo | Done, plus dashboard | `adk web propel/agent` works; the browser dashboard (`propel/dashboard`) runs the same agent and is the main demo screen |
| Every answer carries its source, rule edition and a safe-to-act flag, and is logged | Done | `rules_evaluate` returns `primary_rule` (`rule_key@edition` with citation), `sources` (page, quote, URL, hash) and `safe_to_act`; approved and rejected decisions go to `propel/journey/decisions.jsonl` |

## 2. Scope, section 2: the scenarios

| # | Scope expects | Prototype answer | Status |
| --- | --- | --- | --- |
| 1 | No PA; criteria met; safe to act | No PA, L39237 criteria met, safe to act, after every CMS program bound to Tennessee is screened | Done |
| 2 | Other gate: Tennessee needs GFR below 45 | Other gate, "eGFR 52 is not < 45", not safe to act. The Tennessee article A58982 also lists CKD stage 3b to 5 only (N18.32 to N18.5), so a stage 3a diagnosis (N18.31) fails its diagnosis check too; the Ohio article A56462 lists N18.31 | Done |
| 2b | No PA: Ohio accepts GFR below 60 | No PA, L34356 criteria met. Safe to act only while every source is within its freshness limit: Article A56462 was last confirmed on 15 Oct 2026, past its 14-day limit, so it must be rechecked before the demo (section 12) | Done, recheck due |
| 3 | PA required, evidence list and Part B drug clock | Run with IV iron J1439 under the same plan: PA required, step-therapy evidence, 72 h standard and 24 h expedited, plan portal | Done, adjusted: UnitedHealthcare requires PA for an ESA only with a cancer diagnosis (findings, section 2) |
| 3b | (added) | ESA under UnitedHealthcare, CKD: no rule found, route to staff | Extra, from the findings |
| 4 | Not yet known; not safe; "obtain Hgb" | Not yet known, not safe, "Obtain Hematocrit or Hemoglobin" | Done, adjusted: L39237 accepts Hb < 10 or HCT < 30%, so either result settles it |

## 3. Scope, section 3: the five MCP tools

| Scope tool | Built as | Status |
| --- | --- | --- |
| `journey.get` | `journey_get` on the journey MCP server (`propel/journey`) | Done |
| `context.applicable_rules` | `context_applicable_rules` on the Kanini Context server | Done |
| `rules.evaluate` | `rules_evaluate`: outcome, reasons, missing facts, plus coverage criteria, evidence, clock, route, sources | Done, adjusted: it takes the order (plan, state, code, date of service, facts), not one rule id, because an answer combines several rules: the LCD, the billing article, the program screens and the federal clocks. The answer names the deciding rule as `rule_key@edition` |
| `policy.search` | `policy_search`: the cited passage with page and quote | Done |
| `journey.record` | `journey_record`, after staff approval; its fields are overwritten from the evaluation before it runs | Done |

Tool names use underscores because model function names cannot contain dots. Patient data stays on the journey server; Kanini Context never receives a name or identifier. The Kanini Context server also exposes the seven tools of spec 7.2.

## 4. Scope, section 4: build steps

| Step | Status | Where |
| --- | --- | --- |
| 1. Store: PostgreSQL with Apache AGE in Docker | Done | `docker-compose.yml`, `docker/Dockerfile` (PostgreSQL 16, AGE 1.6.0) |
| 2. Sources: URL, revision date and file hash | Done | `kanini_context/sources/manifest.json`, with each recheck and its date |
| 3. Rules: drafted with a model, checked by the engineer, saved and loaded as graph nodes | Done, adjusted | One rule file per source instead of one `rules.json`; quotes checked against their pages by `curation/checks.py`; approved by the engineer with `curation/approve.py`; 19 rules in release 2026.11.0. Graph `ref_2026_11` holds Payer, Plan, Jurisdiction, Concept (the scope's Service), Code, Rule and Source, with the cited passages |
| 4. Context Service: FastMCP, five tools, answer contract | Done | `kanini_context/server/mcp_server.py`; contract fields `status`, `safe_to_act`, source and edition, `next_step` |
| 5. Agent: ADK on GPT-5 mini through LiteLLM, tools before answering, decision card, staff approval | Done | Four model turns; the card's Outcome line is set from `rules_evaluate`; approval in `adk web` or the dashboard |
| 6. Tests scripted and run before the demo | Done | Section 6 below |

## 5. Scope, section 5: documents and ingestion checks

| Scope item | Status | Evidence |
| --- | --- | --- |
| LCD L39237, effective 17 Sep 2026 | Done | Coverage Criteria B, page 7 |
| LCD L34356, effective 4 Oct 2026 | Done | Coverage Criteria B, page 8 |
| UnitedHealthcare MA 2026 PA list and step therapy | Done | PA requirements (effective 1 Oct 2026), IAP.001.30 |
| 42 CFR 422.568 and 422.572 | Done | 422.568(b)(3) saved from eCFR (up to date as of 9 Nov 2026); 422.572(a)(2) saved as a printed CFR page with no edition date shown. The manifest records an open item to replace it with an eCFR export |
| One service concept with J0881, J0885 and biosimilars from the billing articles | Done | J0881, J0885, Q5106 and J0888 (J0888 is in A58982 only); ESRD codes excluded |
| Confirm billing articles A58982 and A56462 | Done | Both saved and loaded; diagnosis lists are requirement tests |
| Confirm the 72 h and 24 h Part B clocks from eCFR | Done | Confirmed; the clock in the answer cites both sections |
| Record the L34356 "Anemia of ESRD" wording as written and flag it | Done | The rule keeps the wording and carries a `wording_note`, loaded with the rule and shown to the engineer by `approve.py`; flagged in findings, section 1; tested in `test_pack.py` |

## 6. Scope, section 6: the 23 tests

All in `tests/test_scope.py`, named by number. Tests 9 and 21 to 23 also run with GPT-5 mini in `tests/test_agent_live.py` when a key is set. Pack-level checks the scope relies on (rule checks, approval, freshness, audit rows, documentation form, the ESA under UnitedHealthcare) are in `tests/test_pack.py`.

The scope tests are strict by default: on a release that is not full (a CMS program list not saved and screened, or a source past its freshness limit) they fail and name what is missing. Setting `KC_ALLOW_UNSCREENED=1` runs the held-for-staff branch instead, for a workspace without the saved CMS copies; such a run does not prove scenarios 1, 2b and 4, and is not the demo check.

| Test | Result | Difference from the scope example, if any |
| --- | --- | --- |
| 1 Rules loaded | Pass | 19 rules, not 3: the pack also holds the billing articles, the UnitedHealthcare rules and the CMS program screens. The three scope rules are checked by id, source and date |
| 2 Region, Tennessee | Pass | Source reads "LCD L39237, Coverage Criteria B" (the scope example says "2") |
| 3 Region, Ohio | Pass | |
| 4 Date before the edition | Pass | `next_step` is the code `human_review` ("route to staff" in words) |
| 5 Evaluate, four calls | Pass (5a to 5d) | Called with the order rather than a rule id (section 3). 5d asks for Hb or HCT, as L39237 is written |
| 6 Medicare Advantage plan | Pass | Run with J1439 (scenario 3 change) |
| 7 Unknown payer | Pass | |
| 8 Input guard | Pass | Nothing is written to the audit table |
| 9 Agent end to end | Pass | Same four tools; the rules lookup and the evaluation run in one model turn |
| 10 Decision log | Pass, plus 10b | 10b replays the logged decision with the same rule edition |
| 11 Missing field | Pass | |
| 12 Malformed date | Pass | |
| 13 Unknown service | Pass | |
| 14 State outside the loaded regions | Pass | Reason reads "no coverage policy loaded for CA" (scope: "no LCD loaded"), since the pack also holds non-LCD policies |
| 15 Date beyond the pack | Pass | |
| 16 Boundary, eGFR | Pass | |
| 17 Boundary, hemoglobin | Pass | With Hb 10.0 alone the answer asks for HCT, since L39237 accepts either; with HCT 31 it is Other gate |
| 18 Implausible value | Pass | |
| 19 Stale lab | Pass | Tennessee only: L34356 sets no creatinine time limit |
| 20 Overlapping rule version | Pass | |
| 21 Context Service unavailable | Pass | |
| 22 Instruction to skip checks | Pass, plus 22b | The trace check runs with GPT-5 mini (`test_agent_live.py`), on `maria-uhc` so the prompt's "no PA" contradicts the rules' "PA required". The scripted test covers a model that does obey the prompt: its record call is blocked and nothing is logged. 22b: a card that contradicts the rules is corrected from `rules_evaluate` |
| 23 Staff rejects the decision | Pass | |

## 7. Scope, section 7: demo script

`docs/DEMO_SCRIPT.md` follows the seven steps in order (source to rule, scenario 1, scenarios 2 and 2b, scenario 3, scenario 4, approve and audit, close) and adds one step: the same order answered three ways (the model reading the documents, the agent with Kanini Context, and a lookup with no model), which shows the model calls Kanini Context removes.

## 8. Scope, section 8: done when

All 23 tests pass. **Met.**

## 9. Specification v1.5

| Spec | Status | Note |
| --- | --- | --- |
| 3 Classes and relations | Done | 53 classes, 17 relations, 93 attributes (spec: 51, 17, 89) |
| 4.1 Layers and effects | Done | Resolver logic unchanged |
| 4.2 Rule kinds | Done, extended | Adds `coverage_criteria` through the registry |
| 4.3 Facts | Done, extended | Adds the lab facts the LCDs test, with plausible ranges |
| 4.4 Grouped requirements | Done | |
| 4.5 Diagnosis rules | Done, adjusted | Billing-article diagnosis lists are tests, not conditions, so a missing diagnosis is reported, not silently skipped |
| 4.6 When no approval is needed | Done, adjusted | Adds `stated_by_exhaustion` for screened closed lists (findings, section 3) |
| 5.1 to 5.3 Dates, fields, never stored | Done | Four date bounds and record time; no patient data stored |
| 5.4 When a new edition arrives | Partly | Change alerts are built: the UnitedHealthcare step therapy policy has an open alert for its 1 Jan 2027 edition, so the release covers dates of service through 31 Dec 2026 and later dates are "unavailable" (scope test 15). The resolver's "pending review" answer is as specified (spec tests Q15, Q16, T05). Automated watching and the merge of a new edition are not built |
| 5.5 Privacy and security | Done | Server runs as role `kc_agent`; audit rows hold no question or facts |
| 6.1 Ontology file | Done | Generated at every build (`release/owl.py`) |
| 6.2 Where each kind of data lives | Done, adjusted | Source copies are kept on disk (`kanini_context/sources/raw/`) with their SHA-256 in the manifest, not in write-once blob storage. AI drafts are rule files in Git, not `kc_stage` tables; the `kc_stage` schema exists and is empty |
| 6.3 Tables and keys | Done | As specified (`schema.sql`); additions listed at the top of the file |
| 6.4 How the database is divided | Done, adjusted | `kc_core`, `kc_gov`, `kc_api` and `kc_client` as specified; `kc_stage` empty (6.2); the client copy is a second database (6.6) |
| 6.5 What the database refuses | Done, adjusted | Approval by one engineer with a recorded `review_exception`, as the scope sets it |
| 6.6 Client copy and freshness | Done, adjusted | Second database `kc_client` instead of a signed read-only schema. Source freshness limits are 14 and 45 days per source (proposal appendix B.3), where the spec's sample data used 2 and 7 days for the insurer list |
| 7.1 Curation steps | Partly | Watch, draft, map, check, approve and release built; compare and two-pass extraction not built |
| 7.2 Tools and resolver | Done | Seven tools and ten resolver steps over MCP; REST form not built |
| 7.3 to 7.5 Answer, documentation form, runtime writes | Done | The documentation form also lists `coverage_criteria` items when no documentation rule applies (for J0885, the L39237 and A58982 items). Feedback counts not built |
| 7.6 Use by other agents and other use cases | Done | Spec tests N20 and N21 (denial management) and N22 (care gaps) pass as data only. In the prototype, the same pack is used by the agent (B) and by plain code with no model (C) |
| 8 Testing | Done | The 61 spec tests give the same verdicts (`kanini_context/db/spec_regression/`); the differences are added columns and the order of rows listed in a conflict, all listed in that folder's README. Of the spec's other checks: field schemas and the ontology file are checked at every load and build; the speed run on generated rules and the reverse load order run were not repeated |

## 10. Beyond the scope

- **Comparison with and without the context layer** (`propel/baseline/compare.py`): six orders, five runs each. The same agent without Kanini Context, reading the policy documents itself, matched the approved rules 16 of 30 times at about 22,700 tokens an order and changed its answer between runs on all six orders; with Kanini Context it gave the rules' answer 30 of 30 at about 12,000 tokens; Kanini Context with no AI answers with no tokens in about 30 ms and is the answer key. Earlier single-call baselines over the same library: search 11 of 30, every document 18 of 30. What broke, order by order, is in the README and the dashboard's Testing tab.
- **Dashboard** (`propel/dashboard`): live knowledge graph and schema view, decision card, approval step, and the three ways of answering run in turn on the same order.
- **CMS program screening**: every Original Medicare prior authorization program bound to the state is screened against its saved code list before "No PA" is given.

## 11. Not built, as the scope or spec leaves for later

Automated source watching, the change summary agent, two-pass extraction, formal two-person review, signed releases, the REST form of the tools, the HCPCS, ICD-10-CM and LOINC files, and an ingestion agent that drafts rules from new documents. The full list is in `PROTOTYPE_NOTES.md`, section 6.

## 12. Before the demo

- **Full release.** Every CMS program copy saved, approved and built: the dashboard reads "Release 2026.11.0 · 19 rules · 16 sources". With a copy missing, scenarios 1, 2b and 4 answer "Not known", and the strict scope tests fail and name it.
- **Freshness.** Article A56462 was last confirmed on 15 Oct 2026, past its 14-day limit, so Ohio answers are held for staff until it is rechecked against the CMS page. The other sources were confirmed on 9 to 12 Nov 2026 and stay within their limits through 23 Nov 2026.
- **Run** `pytest` with no `KC_ALLOW_UNSCREENED`: all must pass.
