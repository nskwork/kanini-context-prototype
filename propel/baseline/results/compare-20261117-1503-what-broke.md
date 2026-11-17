Test run started 17 Nov 2026, 15:03 Central time (`propel/baseline/results/compare-20261117-1503.json`). Each order was answered 5 times by each baseline; GPT-5 mini, reasoning effort low.

**`maria-tn`**: the rules say **No PA**, resting on LCD L39237, page 7.
- A · agent without Kanini Context: right 5 of 5.
- Single LLM call with search (RAG): right 5 of 5.

**`maria-tn-egfr52`**: the rules say **Other gate**, resting on LCD L39237, page 7.
- A · agent without Kanini Context: right 4 of 5. 1 run answered No PA: the order does not meet the coverage criteria.
  - In its own words (No PA): “Medicare Part B coverage article A58982 lists J0885 (epoetin alfa, non-ESRD use) as a covered code for CKD patients not on dialysis and specifies clinical documentation required (serum creatinine/GFR, diagnosis of symptomatic anemia of CKD such as D63.1 plus…” Source it cited: Billing and Coding Article A58982, “Erythropoiesis Stimulating Agents (for L39237)”, pages 9-10 and 17.
- Single LLM call with search (RAG): right 5 of 5.

**`maria-oh`**: the rules say **No PA**, resting on LCD L34356, page 8.
- A · agent without Kanini Context: right 0 of 5. 5 runs answered Other gate: the order meets the coverage criteria that govern it; Other gate is the answer under Tennessee's eGFR limit (below 45), not Ohio's (below 60) (4 of 5 runs used it); the stage 3a (N18.31) exclusion is in Tennessee's article A58982, while Ohio's article A56462 lists N18.31 (3 of 5 runs used it). 5 of them cited Tennessee's policy.
  - In its own words (Other gate): “CMS billing/coding guidance for ESA (A58982 / L39237) requires CKD not on dialysis patients to have supporting GFR/creatinine consistent with coverage (guidance cites a GFR < 45 mL/min/1.73 m2 as a reasonable threshold), and Maria’s eGFR is 52 (does not meet…” Source it cited: Billing and Coding Article A58982 (for LCD L39237), pages 10–11 — required documentation and GFR threshold language; dosing and Hb pre-initiation requirement (page…
- Single LLM call with search (RAG): right 0 of 5. 5 runs answered Other gate: the order meets the coverage criteria that govern it; Other gate is the answer under Tennessee's eGFR limit (below 45), not Ohio's (below 60) (5 of 5 runs used it); the stage 3a (N18.31) exclusion is in Tennessee's article A58982, while Ohio's article A56462 lists N18.31 (4 of 5 runs used it).
  - In its own words (Other gate): “Original Medicare (Part B) does not require a prior authorization for J0885 per the provided CMS/ LCD documents, but coverage criteria are not met; Patient has CKD stage code N18.31 (CKD stage 3a) which is not among the CKD stage diagnoses listed as…”
  The deciding page was **not** among the 16 passages retrieved; 12 came from Tennessee's policy and 2 from Ohio's.

**`maria-uhc`**: the rules say **PA required**, resting on UHC MA prior authorization requirements, page 17.
- A · agent without Kanini Context: right 4 of 5. 1 run answered Not yet known: the plan's list requires PA for this code whatever the facts.
  - In its own words (Not yet known, what it said was missing): “Obtain documentation of prior IV iron therapy (dates, agents, duration and response) or documentation of contraindication/intolerance to at least two preferred IV iron products — or evidence of Injectafer use within past 365 days — then re-evaluate…” Source it cited: UnitedHealthcare Medicare Advantage Drug Policy IAP.001.30, "Medicare Part B Step Therapy Programs", pages 8 and 18 (Intravenous Iron Replacement Therapy: preferred vs…
- Single LLM call with search (RAG): right 3 of 5. 2 runs answered Not yet known: the plan's list requires PA for this code whatever the facts.
  - In its own words (Not yet known, what it said was missing): “Member has iron deficiency anemia with CKD (D50.9, D63.1, N18.32) and Hgb 9.4, eGFR 38, but documentation is missing regarding prior trials of preferred IV irons, documented contraindications/intolerance to preferred agents, or prior Injectafer use within 365…”
  The deciding page was among the 16 passages retrieved.

**`maria-uhc-esa`**: the rules say **Not known**: the documents do not settle it, so the order goes to staff.
- A · agent without Kanini Context: right 0 of 5. 5 runs answered PA required: the plan's list names this drug only under cancer supportive care, with PA "for a cancer diagnosis" (page 7), and this patient has none, so the documents do not settle this order.
  - In its own words (PA required): “UnitedHealthcare Medicare Advantage prior authorization list explicitly lists HCPCS J0885 (Epoetin alfa non‑ESRD) as requiring prior authorization for all plans (instructions to submit via the UHC Provider Portal or call 888-397-8129).” Source it cited: UnitedHealthcare Medicare Advantage/Peoples Health Plans prior authorization requirements (uhc-ma-pa-list), page 7; CMS Billing & Coding Article A56462 (ESA), page 3.
- Single LLM call with search (RAG): right 0 of 5. 5 runs answered PA required: the plan's list names this drug only under cancer supportive care, with PA "for a cancer diagnosis" (page 7), and this patient has none, so the documents do not settle this order.
  - In its own words (PA required): “UnitedHealthcare Medicare Advantage requires prior authorization for J0885 (Epoetin alfa); Patient meets LCD coverage criteria for CKD not on dialysis: diagnosis D63.1 and N18.32 present; Clinical values support ESA use per LCD: Hgb 9.4 g/dL and eGFR 38…”

**`maria-nohgb`**: the rules say **Not yet known**, resting on LCD L39237, page 7.
- A · agent without Kanini Context: right 4 of 5. 1 run answered No PA: the order lacks a hematocrit or hemoglobin result, so it cannot be decided yet.
  - In its own words (No PA): “Original Medicare LCD L39237 lists epoetin alfa (J0885) as a covered ESA indication for anemia related to CKD stages IIIb–V (non-dialysis) (coverage criteria) — patient has diagnosis N18.32 (CKD stage 3b) and anemia (D63.1) with eGFR 38, which matches the…” Source it cited: LCD L39237 “Erythropoiesis Stimulating Agents” (Palmetto GBA), Coverage Indications, Limitations, and/or Medical Necessity (pages 5–6); Billing/Coding Article A58982…
- Single LLM call with search (RAG): right 1 of 5. 3 runs answered Other gate: the order lacks a hematocrit or hemoglobin result, so it cannot be decided yet. 1 run answered No PA: the order lacks a hematocrit or hemoglobin result, so it cannot be decided yet.
  - In its own words (Other gate): “Original Medicare Part B/ LCD does not impose a prior authorization requirement for ESA J0885 (coverage criteria are documented in the LCD/article rather than a PA program); Claim has appropriate diagnosis codes for CKD non-dialysis (D63.1 and N18.32) and…”
  - In its own words (No PA): “Original Medicare Part B does not require prior authorization for ESAs per the CMS LCD/article guidance; HCPCS J0885 is a non-ESRD ESA and the claim for CKD non-dialysis requires D63.1 plus a CKD stage code; the order includes D63.1 and N18.32 which align…”
  The deciding page was **not** among the 16 passages retrieved; 2 came from Ohio's policy and 12 from Tennessee's.

