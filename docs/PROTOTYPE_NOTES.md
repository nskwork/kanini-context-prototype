# Kanini Context Prototype: notes

Prepared by NANDHU S K. Read with the prototype scope (12 Nov 2026), the scope findings (`SCOPE_FINDINGS.md`) and the schema specification v1.5.

## 1. What is built

| Scope build step | Prototype |
| --- | --- |
| 1. Store | PostgreSQL 16 with Apache AGE 1.6.0 (`docker-compose.yml`). Answers come from the relational schema of spec v1.5; the AGE graph `ref_YYYY_MM` is a view of the same release for browsing and for test 1. |
| 2. Sources | 10 public documents in `kanini_context/sources/manifest.json`: URL, edition, retrieval date, every check with its date, SHA-256 and size. The four in the scope, their two billing and coding articles, 42 CFR 422.568 and 422.572 as separate files, and the two CMS documents that list the Original Medicare prior authorization programs. |
| 3. Rules | One rule file per source in `kanini_context/rules/`, drafted with a model and checked by `curation/checks.py` against the page (or the column of the page) each quote cites. 16 rules, loaded as typed rows with their requirement items. |
| 4. Context Service | FastMCP server with three tools; Propel's journey tools are a second MCP server (section 3). |
| 5. Agent | Google ADK agent on GPT-5 mini through LiteLLM, two MCP toolsets, staff approval of `journey_record` in `adk web`. |
| 6. Tests | `tests/test_scope.py` (the 23 scope tests) and `tests/test_pack.py`. |

## 2. Answer contract

`rules_evaluate` returns one decision packet per order.

| Field | Meaning |
| --- | --- |
| `status` | resolved, needs_facts, no_rule_found, unavailable, conflict or invalid_input |
| `outcome` | pa_required, no_pa, exempt, other_gate (coverage criteria not met), needs_facts or unknown |
| `safe_to_act` | true only for pa_required, no_pa or exempt with nothing holding it back |
| `not_safe_because` | why not: coverage_criteria_not_met, needs_facts, source_aging, basis_interpreted, a coverage gap, ... |
| `reasons` | plain lines, e.g. "eGFR (CKD-EPI 2021) 52 is not < 45" |
| `next_step`, `next_step_detail` | a code (proceed, submit_prior_authorization, supply_facts, human_review, fix_input) and the same in words |
| `missing_facts` | registered facts still needed |
| `primary_rule` | the deciding rule as `rule_key@edition`, with its citation; this is the `ruleRef` in the decision log |
| `coverage_criteria` | each policy's groups and items, checked against the facts |
| `evidence`, `decision_clock`, `route` | what to send, the decision deadline (standard and expedited), how to submit |
| `rule_refs`, `sources` | every rule the answer rests on; page, quote, URL and file hash of each source |
| `review_exceptions`, `freshness`, `aging_sources`, `pack_release` | how the rules were approved, how current the sources are, which release answered |

A rule reference is `rule_key@edition`, the edition being the date the rule's edition takes effect, e.g. `kc:rule/esa-ckd-nd/L39237@2026-09-17`.

## 3. Choices made while building

1. **Tool names use underscores** (`context_applicable_rules`, `rules_evaluate`, `policy_search`, `journey_get`, `journey_record`). Model function names cannot contain dots.
2. **Two MCP servers.** Kanini Context holds rules and never receives a patient name or identifier: it gets plan, state, code, date and registered facts. Propel's journey server holds the patient and the decision log. A call to Kanini Context with any other field is refused (scope test 8).
3. **The outcome cannot be rewritten.** Before `journey_record` runs, its fields are replaced from the `rules_evaluate` result for that order; with no evaluation, nothing is recorded. A model that skips the checks cannot log a decision (scope test 22).
4. **Silence is never "no".** When the pack holds coverage criteria for a service but none apply to the plan, place or date, the answer is "no coverage policy loaded for CA" or "no edition loaded for 2026-09-01", never a pass (scope tests 4 and 14). "No PA" under Original Medicare holds only when every prior authorization program bound to the state was screened for the service; one unscreened program turns it into "no rule found" (scope findings, Finding 3).
5. **Coverage criteria are tested as written.** L39237 group 1 is "Hb < 10 or HCT < 30%", so either result can settle it; this is why scope tests 5 and 17 ask for "hemoglobin or hematocrit" when neither settles the group.
6. **Freshness is enforced.** Each answer checks the last confirmation of every source it rests on: past 14 days it is held for staff and names the source, past 45 days it is withheld (proposal appendix B.3).
7. **One approver needs a recorded exception.** Two approvers stay the rule (spec 7.1); a single named approver is accepted only with a `review_exception`, shown on every answer. A TEST build fills empty reviews with a test approver and says so.
8. **Codes keep open dates.** The billing articles list the HCPCS codes but do not say when the code set created them, and the HCPCS file is not loaded in the prototype, so code rows carry no invented start date. The article's revision date goes on the concept maps and rules.

## 4. What the prototype does not cover

- The HCPCS, ICD-10-CM and LOINC files are not loaded; codes come from the policies that list them.
- Change detection is manual: a recheck of each URL is recorded as a dated entry in the manifest.
- Approval of rule files is a field in each file, not a Git pull request flow.
- The client copy is a second database on the same server, not a signed package on another host.
- The UHC rule for ESA under a cancer diagnosis rests on an interpretation of "cancer diagnosis" as ICD-10-CM C00 to D09 (confidence 0.85), so it is never safe to act on alone.
