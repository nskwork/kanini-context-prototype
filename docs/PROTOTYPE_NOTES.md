# Kanini Context Prototype: notes

Prepared by NANDHU S K. Read with the prototype scope (12 Nov 2026), the scope findings (`SCOPE_FINDINGS.md`) and the schema specification v1.5.

## 1. What is built

| Scope build step | Prototype |
| --- | --- |
| 1. Store | PostgreSQL 16 with Apache AGE 1.6.0 (`docker-compose.yml`). Answers come from the relational schema of spec v1.5; the AGE graph `ref_YYYY_MM` is a view of the same release for browsing and for scope test 1. Its nodes are the spec's classes (Payer, Plan, Jurisdiction, Concept for the scope's Service, Code, Rule, Source, ...); each Rule node carries its citation, page and cited passage. |
| 2. Sources | 16 saved public documents, in `kanini_context/sources/manifest.json`: URL, edition, retrieval date, every check with its date, SHA-256 and size. The four in the scope, their two billing and coding articles, 42 CFR 422.568 and 422.572 as separate files, and the eight CMS documents that list the Original Medicare prior authorization programs and their code lists. |
| 3. Rules | One rule file per source in `kanini_context/rules/`, drafted with a model and checked by `curation/checks.py` against the page (or the column of the page) each quote cites. The engineer approves each file with `curation/approve.py`. 16 rules, loaded as typed rows with their requirement items. |
| 4. Context Service | FastMCP server: the scope's three tools (`context_applicable_rules`, `rules_evaluate`, `policy_search`) and the seven tools of spec 7.2, over the same SQL. Propel's journey tools are a second MCP server. |
| 5. Agent | Google ADK agent on GPT-5 mini through LiteLLM, with staff approval of `journey_record` in `adk web`. |
| 6. Tests | `tests/test_scope.py` (the 23 scope tests by number), `tests/test_pack.py`, and `tests/test_agent_live.py` (tests 9 and 21 to 23 with GPT-5 mini, when a key is set); the spec's 61 tests in `kanini_context/db/spec_regression/`. |

## 2. Approval: one engineer, as the scope sets it

The scope has the engineer check each drafted rule against its source, with formal review at a later stage. The spec's rule stays in the database: an approved rule, code map or binding needs two different approvers, or one named approver with a recorded exception (`review_exception`). `curation/approve.py` shows each rule with its page, quote, payload, conditions and tests, runs the automated checks first, and on approval records the engineer, the date and this exception:

> Prototype scope: one engineer checked each rule against its source; formal two-person review at a later stage

Every answer lists the exceptions of the rules it rests on (`review_exceptions`). The loader refuses a file with no approver; `--test-review` is only for the test suite and labels the release `TEST-`. Each review row records the Git commit of the approved files (spec 7.1: Git is where approval is kept). Moving to two approvers later needs no change: add the second name and drop the exception.

## 3. Conformance with spec v1.5

| Spec | Prototype |
| --- | --- |
| 3. Classes and relations | As specified (`registry.sql`). The combined links view also lists `documented_by`, `issued_in` and `replaced_by`. |
| 4.1 Layers and effects | As specified, unchanged resolver logic. |
| 4.2 Rule kinds | The five kinds, plus `coverage_criteria`, added as one registry row (the spec's way to add a kind). The exemption reason list adds `emergency_or_urgent`, the UnitedHealthcare wording. |
| 4.3 Facts | The seven facts, plus the lab facts the LCD criteria test (`hgb_g_dl`, `hct_pct`, `egfr`, `crcl_ml_min`, `serum_creatinine_mg_dl`, `creatinine_date`), with plausible ranges. Condition operators add `prefix_overlaps`, for the UnitedHealthcare "cancer diagnosis" rule. |
| 4.4 Grouped requirements | As specified. The L39237 groups are kept: Hb or HCT; eGFR; creatinine within a month. |
| 4.5 Diagnosis rules | Changed. The billing article diagnosis lists are requirement tests (`includes`, `includes_any`), not conditions. As a condition, a diagnosis outside the list would make the article silently not apply; as a test, the answer says "none of the listed diagnoses is on the order". The insurer's cancer-only rule stays a condition, as in spec test N08. |
| 4.6 When no approval is needed | Adjusted as sent in the scope findings, section 3: basis `stated_by_exhaustion`, given only when every prior authorization program bound to the coverage, state and date is a closed list and was screened; otherwise "no rule found". A program known from its URL but not yet saved is registered unscreened (findings, section 6: the ASC demonstration). |
| 5.1 and 5.2 Dates and fields | As specified: four date bounds, record time, snapshot, locator, quote, basis, confidence, approvers, release, license. Rules add `rule_key` (readable id) and `review_exception`. |
| 5.3 Never stored | As specified. The tools accept only registered facts and coverage fields; a patient name is refused and nothing is logged. |
| 5.5 Privacy and security | The tool server works as the role `kc_agent`: it can read the pack, run the tools and add audit rows, and nothing else. The audit row holds no question or facts. |
| 6.1 and 6.2 Ontology file | `release/owl.py` writes `kanini-context.ttl` from the registry and the tables at each build: 53 classes, 17 relations, 93 attributes (the spec's 51, 17 and 89, plus `coverage_criteria`, `pa_program` and four columns). |
| 6.5 What the database refuses | As specified. Overlapping uncertain editions are listed for a reviewer, not refused (T12); confirmed overlaps are refused (scope test 20). |
| 6.6 Client copy and freshness | Release named `YYYY.MM.0`, a sync log, and both clocks. Differences: the client copy is a second database (`kc_client`), not a signed read-only schema per release; insurer list source limits are 14 and 45 days from proposal appendix B.3, where the spec's sample data used 2 and 7. The release also records the last date of service it answers for (`covers_through`), the day before the first change the pack knows is coming. |
| 7.1 Curation steps | Watch: manual rechecks recorded in the manifest, with the CMS index of initiatives as the check that the program set is complete. Draft and Map: AI-assisted drafts in rule files (the holding area is the rule files in Git, in place of `kc_stage`). Check: quote at page or column, numbers and codes in the quote, negation, schema, registered facts, completeness against a reviewed `not_drafted` list, and each closed-list screen against the program's saved code list (no code of the screened concept may be on it; a program whose list is not saved stays unscreened). Compare: not built. Approve: one engineer, section 2. Release: `release/build.py`. Two independent extraction passes for L39237: not run. |
| 7.2 Tools and resolver | The seven tools and the ten resolver steps as specified, over MCP (the REST form is not built); every tool that reads rules takes the optional "as recorded" moment. The scope's three tools are built on them (section 4). |
| 7.3 Answer | `resolve_rules` returns the spec's answer. `rules_evaluate` returns the decision packet in section 4. |
| 7.4 Documentation form | As specified; for J0885 the form lists the L39237 and A58982 items, since those criteria are a `coverage_criteria` rule. |
| 7.5 Runtime writes | Tool audit rows with no patient data. Feedback counts: not built. |
| 8. Testing | The 61 spec tests give the same verdicts (`spec_regression/README.md` lists the additive differences). |

The full list of schema and resolver changes is at the top of `schema.sql` and `resolver.sql`.

## 4. Answer contract of `rules_evaluate`

| Field | Meaning |
| --- | --- |
| `status` | resolved, needs_facts, no_rule_found, unavailable, conflict or invalid_input (spec 7.2) |
| `outcome` | pa_required, no_pa, exempt, other_gate (coverage criteria not met), needs_facts or unknown |
| `safe_to_act` | the spec's `actionable`: true only for pa_required, no_pa or exempt with nothing holding it back |
| `not_safe_because` | coverage_criteria_not_met, needs_facts, source_aging, sync_aging, basis_interpreted, a coverage gap, ... |
| `reasons` | plain lines from the rules, e.g. "eGFR (CKD-EPI 2021) 52 is not < 45" |
| `next_step`, `next_step_detail` | a code (proceed, submit_prior_authorization, supply_facts, human_review, fix_input) and the same in words |
| `missing_facts` | registered facts still needed |
| `primary_rule` | the deciding rule as `rule_key@edition` with its citation: the approval rule when approval is required or exempt, otherwise the coverage policy that drove the outcome (a coverage policy before a billing article). This is the `ruleRef` in the decision log |
| `coverage_criteria` | each policy's groups and items, checked against the facts, and `not_checked`: what the policies require that the pack does not test (such as iron stores under A58982), also named in `next_step_detail` whenever the answer says to proceed or submit |
| `evidence`, `decision_clock`, `route` | what to send, the decision deadline (standard and expedited), how to submit |
| `rule_refs`, `sources` | every rule the answer rests on; page, quote, URL and file hash of each source |
| `review_exceptions`, `freshness`, `aging_sources`, `pack_release`, `recorded_as_of` | how the rules were approved, how current the sources are, which release answered and when; with `as_recorded` the same answer is returned later |

A rule reference is `rule_key@edition`, the edition being the date the rule's edition takes effect, e.g. `kc:rule/esa-ckd-nd/L39237@2026-09-17`.

## 5. Other choices

1. **Tool names use underscores.** Model function names cannot contain dots.
2. **Two MCP servers.** Kanini Context holds rules and never receives a patient name or identifier; Propel's journey server holds the patient and the decision log.
3. **The outcome cannot be rewritten.** Before `journey_record` runs, its fields are replaced from the `rules_evaluate` result for that order; with no evaluation, nothing is recorded (scope test 22). The decision card's Outcome line in the reply is also set from that result, so the reply cannot contradict the rules either (test 22b).
4. **Silence is never "no".** When the pack holds coverage criteria for a service but none apply to the plan, place or date, the answer is "no coverage policy loaded for CA" or "no edition loaded for 2026-09-01", never a pass (scope tests 4 and 14).
5. **Criteria are tested as written.** L39237 group 1 is "Hb < 10 or HCT < 30%", so either result settles it (scope tests 5 and 17).
6. **Answers come from the pack, not from code.** Wording, the deciding rule, freshness limits, the last covered date, the plans and facts in the tool descriptions, and the comparison's documents all come from the loaded data. A new drug was added by editing rule files only, and answered with no code change.
7. **The agent reads short answers.** It works in four model turns (the patient record; the applicable rules and the evaluation together; the deciding passage; the card with the record request). The model sees one plain copy of each tool result, with only the fields the card uses; the full `rules_evaluate` packet goes to the decision log. Reasoning effort is low (`PROPEL_REASONING_EFFORT`), the same in the comparison's mode A.
8. **Codes keep open dates.** The billing articles list the HCPCS codes but not when the code set created them, and the HCPCS file is not loaded, so code rows carry no invented start date.

## 6. Not covered by the prototype

- The HCPCS, ICD-10-CM and LOINC files; codes come from the policies that list them.
- The drafting step itself: the rule files were drafted with a model from the saved copies outside the repository; the repository holds the steps after it (quote and completeness checks, approval, loading, release). Column bands for the two-column UHC pages were set by hand.
- Automated source watching, the change summary agent, two-pass extraction, signed releases and the REST form of the tools.
- Freshness limits are set per source class in `curation/load.py` (proposal appendix B.3), not yet stored in the registry.
- J0888 is listed in A58982 (Tennessee) but not in A56462 (Ohio); the shared service concept still applies L34356 to a J0888 order in Ohio.
- The Docker image (`docker/Dockerfile`) builds Apache AGE from the commit tested here; the image itself was not built in the build workspace, which has no Docker daemon.
- Medicare Advantage deference to Medicare criteria (spec 4.2) as an override effect: no insurer coverage criteria are loaded, so nothing competes with the LCDs yet.
- J1437 (Monoferric), listed with J1439 on the UnitedHealthcare list; recorded in that file's `not_drafted` list for the next edition.
- The UnitedHealthcare ESA-for-cancer rule rests on an interpretation of "cancer diagnosis" as ICD-10-CM C00 to D09 (confidence 0.85), so it is never safe to act on alone.
