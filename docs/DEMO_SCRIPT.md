# Demo script (15 minutes)

Prepared by NANDHU S K. Follows section 7 of the prototype scope, with scenario 3 as in the scope findings.

**Before the demo**

1. Recheck any source whose last check is older than 14 days (`python -m kanini_context.sources.place --checked SOURCE_ID "note"`), then `python -m kanini_context.release.build`. As of 12 Nov this is Article A56462 (`mcd-article-a56462`, last confirmed 15 Oct): open its CMS page, confirm the version is still the saved one, and record the check. Until then the Ohio answer is held for staff. The status bar must read "Release 2026.11.0 · 19 rules · 16 sources".
2. On the day: `docker compose up -d db`, `python -m kanini_context.release.sync`, `pytest` with no `KC_ALLOW_UNSCREENED` set (all must pass; the scope tests fail and name the gap if a program list is unscreened or a source is past its limit).
3. Start the Kanini Context server (`python -m kanini_context.server.mcp_server --http 8765`) and the dashboard (`python -m propel.dashboard.server`), and open http://127.0.0.1:8090. `adk web propel/agent` stays available as a fallback.
4. Clear the log: delete `propel/journey/decisions.jsonl`.

**Schema (any time).** The Schema tab shows the class diagram the pack is built on: where a rule comes from (Source, Snapshot, Authority), what it says (Rule and its kinds), what it is about (Terminology) and who it applies to (Coverage). After an answer, the classes it passed through glow; click Rule to show its fields.

**1. Source to rule (2 min).** Open LCD L39237 page 7 beside `kanini_context/rules/mcd-lcd-l39237.json`. The rule's quote is the passage word for word; its tests (Hb < 10 or HCT < 30, creatinine within a month, GFR < 45) carry the page they came from. Run `python -m kanini_context.curation.checks` to show every quote is checked against its page. Rules come from source documents, not AI search.

**2. Scenario 1 (3 min).** In the dashboard, pick `maria-tn` and press Ask the AI agent (in `adk web`: *"Check prior authorization for an ESA order for patient maria-tn on 15 Nov 2026."*). Show the graph: the nodes the answer rests on move to the centre, the screened programs pulse orange, and comets run from plan, code and state to LCD L39237 and its page. Show the trace (journey_get; context_applicable_rules and rules_evaluate together in one turn; policy_search) and the decision card, which arrives with the record request for staff to approve: No PA, safe to act, LCD L39237. Point out the line "Not checked here, confirm: iron stores …": the pack separates the criteria it tested (Hb, eGFR, creatinine date, diagnoses) from policy requirements it does not test, and names them rather than assuming them met. Point out that "No PA" rests on every CMS program list bound to Tennessee (the three national programs, the DMEPOS probationary process and the ASC demonstration), each screened.

**3. Scenarios 2 and 2b (3 min).** *"... for patient maria-tn-egfr52"*: Other gate, "eGFR 52 is not < 45", and the stage 3a diagnosis is not on the Tennessee article's list. *"... for patient maria-oh"*: No PA, "eGFR 52 is < 60" under L34356. Same order; the region's rule decides.

**4. Scenario 3 (2 min).** *"... for patient maria-uhc"* (Injectafer, J1439): PA required, evidence to send from the step therapy policy, 72 hours standard and 24 hours expedited, submit through the portal under Specialty Pharmacy. Then *"... for patient maria-uhc-esa"*: not known, route to staff, because the UnitedHealthcare list requires PA for ESAs only for a cancer diagnosis and states nothing for this one.

**5. Scenario 4 (2 min).** *"... for patient maria-nohgb"*: Not yet known, next step "Obtain Hematocrit or Hemoglobin". Silence is never "no PA".

**6. Approve and audit (2 min).** Approve the scenario 1 card; reject another. Open `propel/journey/decisions.jsonl`: the approved entry carries `kc:rule/esa-ckd-nd/L39237@2026-09-17`, the source, the approver and the time; the rejected one carries the reviewer and no outcome. Every decision can be replayed with the rule edition that applied.

**7. Repeated LLM calls removed (2 min).** The prototype's objective: show how the context layer reduces or eliminates repeated LLM calls for information already in the context. Pick an order and press Run A, B, C in turn. A is the same agent and model with no Kanini Context: for every order it searches and reads the policy documents again (the subtitle shows each search and page read live), then decides. B is the agent with Kanini Context: four short calls, no document text, the answer from the approved rules (reduce). C is Kanini Context with no AI: the same answer in milliseconds, no tokens (eliminate). Point at the three columns: AI calls and tokens fall from A to B to zero in C. After a run, each mark shows how often that way was right in testing (six orders, five runs each); one run on its own can land either way. Then open the Testing tab: the same orders over repeated runs, including earlier tests of an LLM application with search (RAG) or with every document, with how often each was right, how often its answer changed between runs, and why (the wrong region's policy, silence read as a rule). Most orders can take the C path; the agent is for the cases and questions that need one.

**Close.** Adding payers or services means loading more reviewed documents; the agent and tools stay the same.
