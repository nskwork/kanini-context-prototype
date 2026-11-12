# Demo script (15 minutes)

Prepared by NANDHU S K. Follows section 7 of the prototype scope, with scenario 3 as in the scope findings.

**Before the demo**

1. Recheck each source on its URL and add a dated `checks` entry in `kanini_context/sources/manifest.json` (Article A56462 especially; until it is rechecked, Ohio answers are held as "source aging").
2. Record the approvals in each rule file's `review` block, then `python -m kanini_context.release.build` (without `--test-review`).
3. `pytest` (all must pass), then start the Kanini Context server and `adk web propel/agent`.
4. Clear the log: delete `propel/journey/decisions.jsonl`.

**1. Source to rule (2 min).** Open LCD L39237 page 7 beside `kanini_context/rules/mcd-lcd-l39237.json`. The rule's quote is the passage word for word; its tests (Hb < 10 or HCT < 30, creatinine within a month, GFR < 45) carry the page they came from. Run `python -m kanini_context.curation.checks` to show every quote is checked against its page. Rules come from source documents, not AI search.

**2. Scenario 1 (3 min).** In `adk web`: *"Check prior authorization for an ESA order for patient maria-tn on 15 Nov 2026."* Show the trace (journey_get, context_applicable_rules, rules_evaluate, policy_search) and the decision card: No PA, safe to act, LCD L39237. Point out that "No PA" rests on the three national CMS program lists, each screened.

**3. Scenarios 2 and 2b (3 min).** *"... for patient maria-tn-egfr52"*: Other gate, "eGFR 52 is not < 45", and the stage 3a diagnosis is not on the Tennessee article's list. *"... for patient maria-oh"*: No PA, "eGFR 52 is < 60" under L34356. Same order; the region's rule decides.

**4. Scenario 3 (2 min).** *"... for patient maria-uhc"* (Injectafer, J1439): PA required, evidence to send from the step therapy policy, 72 hours standard and 24 hours expedited, submit through the portal under Specialty Pharmacy. Then *"... for patient maria-uhc-esa"*: not known, route to staff, because the UnitedHealthcare list requires PA for ESAs only for a cancer diagnosis and states nothing for this one.

**5. Scenario 4 (2 min).** *"... for patient maria-nohgb"*: Not yet known, next step "Obtain Hematocrit or Hemoglobin". Silence is never "no PA".

**6. Approve and audit (2 min).** Approve the scenario 1 card; reject another. Open `propel/journey/decisions.jsonl`: the approved entry carries `kc:rule/esa-ckd-nd/L39237@2026-09-17`, the source, the approver and the time; the rejected one carries the reviewer and no outcome. Every decision can be replayed with the rule edition that applied.

**7. LLM calls saved (1 min).** Show the latest `propel/baseline/results/compare-*.md`: the same scenarios when the model reads the policies (tens of thousands of input tokens per question, answers that can vary) against the agent with Kanini Context and the workflow call with no model.

**Close.** Adding payers or services means loading more reviewed documents; the agent and tools stay the same.
