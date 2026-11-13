<div align="center">

# Kanini Context

**Read the rules once. Answer every order from them.**

A context layer for prior authorization: reviewed payer and Medicare rules, served to AI agents and workflows through MCP tools, with the source page behind every answer.

![tests](https://img.shields.io/badge/tests-75%20of%2075%20passing-a3e635?style=flat-square)
![scope tests](https://img.shields.io/badge/scope%20tests-23%20of%2023-a3e635?style=flat-square)
![spec](https://img.shields.io/badge/schema%20spec-v1.5-22d3ee?style=flat-square)
![release](https://img.shields.io/badge/release-2026.11.0-22d3ee?style=flat-square)
![stack](https://img.shields.io/badge/PostgreSQL%2016%20%C2%B7%20Apache%20AGE%20%C2%B7%20FastMCP%20%C2%B7%20Google%20ADK-334155?style=flat-square)

Prepared by NANDHU S K

</div>

---

## Latest results · 13 Nov 2026

The prototype's objective: show the value of the context layer, and how it reduces or eliminates repeated LLM calls for information that is already in the context. So the comparison is the same agent with and without it. Six synthetic orders, five runs each, GPT-5 mini at reasoning effort low. Reports in `propel/baseline/results/`: `compare-20261113-1056` (A; first run `compare-20261113-1017`), `compare-20261112-2256` (B), `compare-20261113-0850` and `compare-20261113-1031` (one-call baselines).

> **How to read this table.** A is the Propel agent with no Kanini Context: same model, same patient-record tool, and document tools (list, search, read pages) in place of the context layer, so it finds and reads the policy itself for every order. B is the same agent with Kanini Context. C is Kanini Context with no AI. C is the answer key: the engineer-approved rules applied by plain code, so it cannot score anything but full marks; its column shows cost and repeatability. Whether the key is right rests on the rules: each quotes its source page and was checked by the engineer, and the six expected answers match the scope's scenarios (scenario 3 changed as `docs/SCOPE_FINDINGS.md` explains). B takes its answer from the rules by design, so its check is that the agent asked the rules the right question; when it asks to record a decision without writing a card, its card is filled in from the rules' answer, while A is graded on the Outcome line of the card it writes itself. A's document tools are a plain library: the documents' own titles, a search over every document with no filter by state, and page reads; working out which contractor's policy governs an order is part of what the context layer does.

| | **A** · same agent, no context | **B** · agent with Kanini Context (reduce) | **C** · Kanini Context, no AI (eliminate) |
| :-- | :--: | :--: | :--: |
| **Against the answer key** | 18 of 30 match (first run 16 of 30; 34 of 60 over both) | 30 of 30 used the rules' answer | *is the key* |
| **Model calls per order** | 5.2 to 6.0 | 4 to 5 | **0** |
| **Input tokens per order** | 18,387 to 27,025 (average 22,700) | 10,512 to 13,671 | **0** |
| **Document text read per order** | 1.2 to 2.0 searches, 2.0 to 3.2 pages | none | none |
| **Time per order** | 12.8 to 14.7 s | 7.1 to 16 s | **0.03 s** |
| **Same answer every run** | no: changed between runs on 4 of 6 orders (6 of 6 in the first run) | yes | yes |

**In plain words**

- **Without the context layer, the agent works it out again for every order, and gets it wrong two times in five.** It searched and read the policy on every order, used about twice B's tokens, matched the rules 18 times in 30 (16 in its first run), and gave more than one answer to the same question on 4 of the six orders.
- **It reads the wrong region's policy.** On the Ohio order it answered "Other gate" in 3 of 5 runs (4 of 5 in the first run), each time relying on Tennessee's billing article A58982: its eGFR limit of 45 (Ohio's is 60) or its list of CKD codes, which leaves out the stage 3a code N18.31 that Ohio's article lists. Kanini Context binds each rule to its contractor region, so the question never reaches the wrong policy.
- **It reads silence as a rule.** On the anemia drug under UnitedHealthcare it said "PA required" in 5 of 5 runs (4 of 5 in the first), writing that the plan's list "explicitly lists Erythropoiesis Stimulating Agents (J0885) as requiring prior authorization". The list names J0885 only under cancer supportive care, for a cancer diagnosis (page 7), and this patient has none; the documents do not settle the order, so it goes to staff.
- **It invents requirements.** On the Tennessee order that needs no PA, it answered "Not yet known" in 2 of 5 runs (3 of 5 in the first), asking for iron studies the approved coverage rule does not test.
- **B reduces.** With Kanini Context the same agent makes 4 to 5 short calls, reads no document text and can only state what the rules returned, with source, edition and safe-to-act flag. When it sends the record request without a card, the card is written from the rules, so staff always see it before approving.
- **C eliminates.** Once the rules are read, checked and approved, deciding an order needs no model at all: 30 milliseconds, the same answer every time.
- **Not counted in any lane:** the one-off model use when the rules were drafted from the documents.

### Other baselines: one call over the library

Two plain LLM applications, each answering in one call from the same 16-document library, tested twice (`compare-20261113-0850` at 08:42 and `compare-20261113-1031` at 10:23 Central time, 13 Nov 2026). The second run kept every answer with the AI's own reasons, quoted below.

| | **Search (RAG)**: the 16 best-matching passages | **Every document**: the whole library |
| :-- | :--: | :--: |
| **Against the answer key, first run** | 11 of 30 | 18 of 30 |
| **Against the answer key, second run** | 16 of 30 | 20 of 30 |
| **Both runs** | 27 of 60 | 38 of 60 |
| **Input tokens per order** | 7,605 to 9,343 | 147,514 to 147,531 |
| **Answer changed between runs of the same order** | on 3 of 6 orders, then 2 of 6 | on 4 of 6 orders, then 3 of 6 |

The same test, run twice an hour and forty minutes apart, moved by five and by two answers: the score of a method without the context layer depends on the run. Given exactly the pages Kanini Context picked for each order, a single call still matched only 21 of 30 at low reasoning effort and 23 of 30 at medium (`compare-20261112-2210`, `compare-20261112-2238`).

### Scenario by scenario

| Order | Answer key (rules say) | A · no context (run 2) | B | C | Search (RAG), run 1 · 2 | Every document, run 1 · 2 |
| :-- | :-- | :--: | :--: | :--: | :--: | :--: |
| `maria-tn` · Original Medicare, TN, eGFR 38 | **No PA** | ⚠️ 3/5 | ✅ 5/5 | key | ⚠️ 3/5 · ✅ 5/5 | ✅ 5/5 · ⚠️ 4/5 |
| `maria-tn-egfr52` · same, eGFR 52 | **Other gate** (TN needs eGFR < 45) | ✅ 5/5 | ✅ 5/5 | key | ✅ 5/5 · ✅ 5/5 | ✅ 5/5 · ✅ 5/5 |
| `maria-oh` · Ohio, eGFR 52 | **No PA** (OH allows < 60) | ❌ 2/5 | ✅ 5/5 | key | ❌ 0/5 · ❌ 0/5 | ⚠️ 2/5 · ⚠️ 3/5 |
| `maria-uhc` · UHC Medicare Advantage, IV iron J1439 | **PA required**, 72 h / 24 h clock | ⚠️ 4/5 | ✅ 5/5 | key | ❌ 1/5 · ⚠️ 4/5 | ⚠️ 3/5 · ✅ 5/5 |
| `maria-uhc-esa` · UHC, anemia drug, no cancer diagnosis | **Not known**, route to staff | ❌ 0/5 | ✅ 5/5 | key | ❌ 0/5 · ❌ 0/5 | ❌ 0/5 · ❌ 0/5 |
| `maria-nohgb` · TN, hemoglobin missing | **Not yet known**, get the hemoglobin | ⚠️ 4/5 | ✅ 5/5 | key | ❌ 2/5 · ❌ 2/5 | ⚠️ 3/5 · ⚠️ 3/5 |

### What broke in testing

Order by order: how each way without Kanini Context answered in its latest test, what each wrong answer got wrong against the approved rules, and the AI's own words and the source it cited. Generated from the test reports by `python -m propel.baseline.failures <report.json> [more reports]`; the dashboard's Testing tab shows the same text.

Each order was answered 5 times by each way; GPT-5 mini, reasoning effort low. Same agent, no context: test run started 13 Nov 2026, 10:48 Central time (`compare-20261113-1056.json`). Search (RAG): test run started 13 Nov 2026, 10:23 Central time (`compare-20261113-1031.json`). Every document: test run started 13 Nov 2026, 10:23 Central time (`compare-20261113-1031.json`).

**`maria-tn`**: the rules say **No PA**, resting on LCD L39237, page 7.
- Same agent, no context: right 3 of 5. 2 runs answered Not yet known: every fact the rule tests is on the order.
  - In its own words (Not yet known, what it said was missing): “Staff should obtain and add to the chart the required pre-initiation iron studies (TSAT and ferritin) and documentation that other causes of anemia were evaluated/treated; confirm required claim modifiers (EC and JA/JB as appropriate) will be billed. Re-check…” Source it cited: Billing and Coding Article A58982 (for LCD L39237), pages 10–11 — required documentation elements for CKD not on dialysis (serum creatinine/GFR, diagnosis codes,…
- Search (RAG): right 5 of 5.
- Every document: right 4 of 5. 1 run answered Not yet known: every fact the rule tests is on the order.
  - In its own words (Not yet known, what it said was missing): “Missing required iron‑store data (transferrin saturation and ferritin) and documentation of iron repletion if low — required prior to ESA initiation/continuation”

**`maria-tn-egfr52`**: the rules say **Other gate**, resting on LCD L39237, page 7.
- Same agent, no context: right 5 of 5.
- Search (RAG): right 5 of 5.
- Every document: right 5 of 5.

**`maria-oh`**: the rules say **No PA**, resting on LCD L34356, page 8.
- Same agent, no context: right 2 of 5. 3 runs answered Other gate: the order meets the coverage criteria that govern it; Other gate is the answer under Tennessee's eGFR limit (below 45), not Ohio's (below 60) (3 of 3 runs used it). 3 of them cited Tennessee's policy.
  - In its own words (Other gate): “Patient has CKD not on dialysis and diagnosis D63.1 (documented) but current eGFR 52 mL/min/1.73 m2 which is above the LCD’s reasonable GFR threshold (<45 mL/min/1.73 m2) to support ESA initiation for CKD-not-on-dialysis; hemoglobin 9.4 g/dL alone does not…” Source it cited: Billing & Coding Article A58982 (supports LCD L39237) — “For CKD NOT on Dialysis… a GFR < 45 mL/min/1.73m2 is a reasonable threshold to support ESA administration” and…
- Search (RAG): right 0 of 5. 5 runs answered Other gate: the order meets the coverage criteria that govern it; Other gate is the answer under Tennessee's eGFR limit (below 45), not Ohio's (below 60) (4 of 5 runs used it); the stage 3a (N18.31) exclusion is in Tennessee's article A58982, while Ohio's article A56462 lists N18.31 (5 of 5 runs used it).
  - In its own words (Other gate): “Patient has D63.1 (anemia in CKD) but CKD diagnosis is N18.31 (CKD stage 3a) which is not among the covered CKD stage codes required for non-dialysis ESA (N18.32, N18.4, N18.5, etc.); Guidance indicates a GFR <45 mL/min/1.73 m2 is a reasonable threshold to…”
  The deciding page was **not** among the 16 passages retrieved; 12 came from Tennessee's policy and 2 from Ohio's.
- Every document: right 3 of 5. 1 run answered PA required: no approval is needed for this order. 1 run answered Other gate: the order meets the coverage criteria that govern it; the stage 3a (N18.31) exclusion is in Tennessee's article A58982, while Ohio's article A56462 lists N18.31 (1 of 1 runs used it).
  - In its own words (PA required): “Patient has CKD not on dialysis with Hgb 9.4 (<10 g/dL) — meets the hemoglobin criterion for non‑ESRD ESA therapy under the CGS (Ohio) LCD; Patient’s eGFR 52 mL/min/1.73m2 meets the CGS LCD cutoff for CKD non‑dialysis coverage (GFR <60 mL/min/1.73m2) and…”
  - In its own words (Other gate): “HCPCS J0885 is for non-ESRD (CKD non-dialysis) ESA use under the CGS (Ohio) LCD (L34356).; Coverage for CKD non-dialysis requires anemia (Hgb <10) AND the required CKD stage documentation per CGS: L34356 requires GFR/Cr thresholds but the related…”

**`maria-uhc`**: the rules say **PA required**, resting on UHC MA prior authorization requirements, page 17.
- Same agent, no context: right 4 of 5. 1 run answered Not yet known: the plan's list requires PA for this code whatever the facts.
  - In its own words (Not yet known, what it said was missing): “Staff should submit a prior authorization request (or document medical necessity) that includes either: clinical documentation showing trials (dates, agents, doses, and response) of at least two preferred IV iron therapies (e.g., iron sucrose J1756, sodium…” Source it cited: UnitedHealthcare Medicare Advantage Drug Policy IAP.001.30, "Intravenous Iron Replacement Therapy" (pages 8, 17–18); UnitedHealthcare Medicare Advantage/Peoples Health…
- Search (RAG): right 4 of 5. 1 run answered Not yet known: the plan's list requires PA for this code whatever the facts.
  - In its own words (Not yet known, what it said was missing): “No documentation provided of prior trials, contraindications, or intolerance to preferred IV iron therapies for this member”
  The deciding page was among the 16 passages retrieved.
- Every document: right 5 of 5.

**`maria-uhc-esa`**: the rules say **Not known**: the documents do not settle it, so the order goes to staff.
- Same agent, no context: right 0 of 5. 5 runs answered PA required: the plan's list names this drug only under cancer supportive care, with PA "for a cancer diagnosis" (page 7), and this patient has none, so the documents do not settle this order.
  - In its own words (PA required): “UnitedHealthcare Medicare Advantage prior authorization list (Effective Oct 1, 2026) explicitly lists Erythropoiesis Stimulating Agents (J0885) as requiring prior authorization.” Source it cited: UnitedHealthcare Medicare Advantage/Peoples Health Plans prior authorization requirements, Effective October 1, 2026 — Erythropoiesis Stimulating Agents (J0885) (page…
- Search (RAG): right 0 of 5. 5 runs answered PA required: the plan's list names this drug only under cancer supportive care, with PA "for a cancer diagnosis" (page 7), and this patient has none, so the documents do not settle this order.
  - In its own words (PA required): “HCPCS J0885 is listed in UnitedHealthcare Medicare Advantage prior authorization requirements for erythropoiesis stimulating agents; Member has CKD non-dialysis diagnosis (D63.1 and N18.32) which aligns with indications in the ESA billing guidance, but UHC…”
- Every document: right 0 of 5. 5 runs answered PA required: the plan's list names this drug only under cancer supportive care, with PA "for a cancer diagnosis" (page 7), and this patient has none, so the documents do not settle this order.
  - In its own words (PA required): “UnitedHealthcare Medicare Advantage lists erythropoiesis stimulating agents (HCPCS J0885) as a drug requiring prior authorization (see UnitedHealthcare prior authorization requirements).; Medicare/local coverage criteria (LCD L39237 / Billing & Coding A58982)…”

**`maria-nohgb`**: the rules say **Not yet known**, resting on LCD L39237, page 7.
- Same agent, no context: right 4 of 5. 1 run answered Other gate: the order lacks a hematocrit or hemoglobin result, so it cannot be decided yet.
  - In its own words (Other gate): “Patient and order meet some CMS coverage prerequisites (J0885 for CKD non‑dialysis; diagnosis D63.1 and N18.32; eGFR 38 <45) — but required pre‑treatment iron studies and related documentation are not on file.” Source it cited: LCD L39237 (Palmetto, jurisdiction J covers Tennessee) — page 1; Billing & Coding Article A58982, “Erythropoiesis Stimulating Agents” — pages 10, 15, 17.
- Search (RAG): right 2 of 5. 3 runs answered No PA: the order lacks a hematocrit or hemoglobin result, so it cannot be decided yet.
  - In its own words (No PA): “Original Medicare Part B coverage policies (LCD L34356 / A58982) do not state a prior authorization requirement for non‑ESRD ESA (J0885).; Order is for non‑dialysis CKD anemia (HCPCS J0885) and the claim includes D63.1 and N18.32, which are the dual diagnoses…”
  The deciding page was **not** among the 16 passages retrieved; 2 came from Ohio's policy and 12 from Tennessee's.
- Every document: right 3 of 5. 2 runs answered No PA: the order lacks a hematocrit or hemoglobin result, so it cannot be decided yet.
  - In its own words (No PA): “This is Original Medicare Part B (not a Medicare Advantage plan) and none of the CMS prior-authorization/pre-claim review initiatives or demonstrations in the provided documents require prior authorization for ESA J0885.; Coverage and billing requirements for…”

### Tests

| Suite | Result |
| :-- | :-- |
| Prototype scope, tests 1 to 23 (`tests/test_scope.py`) | ✅ all pass, plus the variants 5a to 5d, 10b and 22b |
| Scope tests 9 and 21 to 23 with the real model (`tests/test_agent_live.py`) | ✅ pass with GPT-5 mini |
| Pack tests: checks, approval, freshness, decision log, card guard (`tests/test_pack.py`) | ✅ pass |
| Dashboard: release graph and schema, live runs of A, B and C (`tests/test_dashboard.py`) | ✅ pass |
| Schema specification v1.5, 61 regression tests (`kanini_context/db/spec_regression/`) | ✅ same verdicts as the specification |

### Scope tests, one by one

The 23 tests of the prototype scope (12 Nov 2026, section 6), in `tests/test_scope.py` under the same numbers. They are strict: on a release with an unscreened CMS program or a source past its freshness limit, they fail and name it. Last full run on the engineer's laptop: all tests passed, 13 Nov 2026 (the count is in the badge above).

| # | Scope test | The scope expects | Result | Difference from the scope's example |
| --: | :-- | :-- | :--: | :-- |
| 1 | Rules loaded | the scope's rules listed with source and date | ✅ pass | 19 rules: the three in the scope (checked by id, source and date) plus the billing articles, UnitedHealthcare rules and CMS program screens |
| 2 | Region, Tennessee | L39237 resolved for Original Medicare in TN | ✅ pass | Citation reads "LCD L39237, Coverage Criteria B" |
| 3 | Region, Ohio | L34356 resolved for Original Medicare in OH | ✅ pass | none |
| 4 | Date | unavailable: no edition loaded for a date before the LCD | ✅ pass | Next step is the code `human_review` (route to staff) |
| 5 | Evaluate (four calls) | No PA, Other gate, No PA (Ohio), Not yet known | ✅ pass | Called with the order (plan, state, code, date, facts) rather than a rule id; 5d asks for Hb or HCT, as L39237 accepts either |
| 6 | Medicare Advantage plan | PA required, evidence from the UHC policy, 72 h / 24 h clock | ✅ pass | Run with IV iron J1439: UnitedHealthcare lists the ESA only with a cancer diagnosis (`docs/SCOPE_FINDINGS.md`) |
| 7 | Unknown payer | no rule found, not safe, route to staff | ✅ pass | none |
| 8 | Input guard | invalid input: unexpected field | ✅ pass | Nothing is written to the audit table |
| 9 | Agent end to end | trace journey.get → applicable rules → evaluate → policy search, then the card | ✅ pass | Same four tools; the rules lookup and the evaluation run in one model turn. Also run with GPT-5 mini |
| 10 | Decision log | approved entry with order, outcome, rule and reviewer | ✅ pass | Plus 10b: the logged decision replays with the same rule edition |
| 11 | Missing field | invalid input: missing field: plan | ✅ pass | none |
| 12 | Malformed date | invalid input: date_of_service must be a date | ✅ pass | none |
| 13 | Unknown service | no rule found, route to staff | ✅ pass | none |
| 14 | State outside the loaded regions | no rule found for CA | ✅ pass | Reason reads "no coverage policy loaded for CA", since the pack also holds non-LCD policies |
| 15 | Date beyond the pack | unavailable: pack not current for 2027 | ✅ pass | none |
| 16 | Boundary, eGFR | eGFR 45 is not < 45: Other gate | ✅ pass | none |
| 17 | Boundary, hemoglobin | Hb 10.0 is not < 10: Other gate | ✅ pass | With Hb 10.0 alone the answer asks for HCT (L39237 accepts either); with HCT 31 it is Other gate |
| 18 | Implausible value | invalid input: egfr out of range | ✅ pass | none |
| 19 | Stale lab | creatinine older than the LCD allows: Other gate | ✅ pass | Tennessee only: L34356 sets no time limit |
| 20 | Overlapping rule version | load rejected: overlapping active versions; graph unchanged | ✅ pass | none |
| 21 | Context Service unavailable | agent says it cannot reach Kanini Context and routes to staff | ✅ pass | Also run with GPT-5 mini |
| 22 | Instruction to skip checks | trace still shows the rules tools; answer from the tools | ✅ pass | With GPT-5 mini on `maria-uhc`, where the prompt's "no PA" contradicts the rules. Plus 22b: a card that contradicts the rules is corrected |
| 23 | Staff rejects the decision | rejected entry with the reviewer; nothing approved | ✅ pass | Also run with GPT-5 mini |

Every difference is explained in `docs/COVERAGE.md`, section 6.

---

## How it works

```mermaid
flowchart LR
  subgraph Curation["Curation · once per source edition"]
    P[Source PDFs<br/>16 saved copies] --> D[Rule files<br/>drafted with AI]
    D --> K{{checks.py<br/>quote on the cited page,<br/>numbers, negation, codes}}
    K --> AP[approve.py<br/>engineer approves]
    AP --> B[(Build DB<br/>spec v1.5 schema)]
    B --> R[(Release 2026.11.0<br/>client copy + AGE graph + OWL)]
  end
  subgraph Runtime["Runtime · every order"]
    O[Order] --> AG[Propel agent<br/>Google ADK · GPT-5 mini]
    O --> WF[Workflow<br/>plain code, no model]
    AG -- MCP --> T[Kanini Context tools]
    WF --> T
    T --> R
    AG --> CARD[Decision card] --> ST{Staff approve?} --> LOG[(Decision log<br/>rule + edition + source)]
  end
```

- **The outcome always comes from `rules_evaluate`:** SQL over reviewed rules, so the same question gets the same answer.
- **The model chooses the tools and writes the explanation, but it cannot change the outcome.** Before `journey_record` runs, its fields are replaced with what `rules_evaluate` returned, and the card's Outcome line is set from the same result.
- **Silence is never "no".** No rule found, a missing fact, an aging source or an unscreened program all route to staff, naming the reason.

---

## Dashboard

`python -m propel.dashboard.server` opens the demo in a browser (http://127.0.0.1:8090). It runs the same agent and tools as `adk web` and shows each step as it happens:

| | |
| :-- | :-- |
| **Knowledge graph** | Every node and edge of the release, zoomable. Nodes settle in shells by type; labels appear as you zoom. When an order is answered, the nodes it rests on light up and move to the centre, the screened PA programs pulse orange, and comets run along the real path from plan, code and state to the rule and its source page. Click any node for its page, quote, hash or links. |
| **Schema** | The ontology as a class diagram, read from `kanini-context.ttl` at every release: one box per class with its fields and its kinds (53 classes, 93 fields), the Terminology and Coverage groups as frames, and the 17 relations as named arrows. Click a class to see all its fields and zoom to its neighbours; after an answer, the classes it passed through glow. |
| **Decision card** | Outcome, reasons, next step, deadline, route, evidence, the rule and its quote, with Approve and Reject. The approval goes to the decision log. |
| **Without and with Kanini Context** | Three lanes run live on the chosen order, in turn or one at a time. **A** is the same agent and model with no Kanini Context: it searches and reads the policy documents itself for every order (each search and page shows as it happens) and its answer is marked against the rules. **B** is the agent with Kanini Context (reduce: short calls, no document text). **C** is Kanini Context with no AI (eliminate). Nothing shows before a run; after it, each mark comes with how often that way was right in testing. |
| **Testing** | The same six orders over repeated runs, from `propel/baseline/results/`: the agent without the context layer, and earlier baselines (an LLM application with search, or with every document), with how often each was right, how often its answer changed between runs, tokens and calls, and the reasons it went wrong. |

`--scripted` runs a scripted stand-in model, for a rehearsal with no API key; the page says so.

---

## The schema: specification v1.5 and what the prototype built

The database is the specification's schema. The release also writes it out as an OWL ontology (`kanini_context/release/kanini-context.ttl`) and an Apache AGE graph, all from the same registry.

```mermaid
classDiagram
  direction LR
  class CoverageItem
  CoverageItem <|-- Payer
  CoverageItem <|-- Plan
  CoverageItem <|-- Program
  CoverageItem <|-- Jurisdiction
  class Authority
  Authority <|-- Lcd
  Authority <|-- BillingArticle
  Authority <|-- PayerList
  Authority <|-- PayerPolicy
  Authority <|-- Regulation
  Authority <|-- PaProgram
  class Rule
  Rule <|-- ApprovalRequired
  Rule <|-- Exemption
  Rule <|-- DocumentationRequirement
  Rule <|-- Obligation
  Rule <|-- CoverageCriteria
  Obligation <|-- DecisionTimeframe
  Obligation <|-- AppealWindow
  class TerminologyItem
  TerminologyItem <|-- Concept
  TerminologyItem <|-- Code
  TerminologyItem <|-- ValueSet
  Plan --> Payer : operatedBy
  Plan --> Program : offeredUnder
  Plan --> Jurisdiction : locatedIn
  Authority --> CoverageItem : binds
  Authority --> Rule : states
  Authority --> Snapshot : issuedIn
  Snapshot --> Source
  Rule --> TerminologyItem : about
  Rule --> DataElement : requires
  Rule --> KnowledgeArtifact : documentedBy
  Concept --> Code : mapsTo
  Code --> ValueSet : belongsTo
  Payer --> Endpoint : exposes
  Authority --> Authority : supersedes / preempts / defersTo
```

<sub>Core of the model. The full ontology has 53 classes, including every program, authority and endpoint kind.</sub>

| | Specification v1.5 | Built in the prototype |
| :-- | :--: | :--: |
| Classes | 51 | **53**: adds `CoverageCriteria` (rule kind) and `PaProgram` (authority kind) |
| Relations | 17 | **17**: as specified |
| Attributes | 89 | **93**: adds `rule_key`, `review_exception`, `closed_list`, `covers_through` |
| Rule kinds | 5 | **6**: adds `coverage_criteria`, so medical-necessity criteria are tested, not just PA |
| Layers and effects (4.1) | ✓ | ✓ unchanged resolver logic |
| Dates: four bounds and record time (5.1) | ✓ | ✓ every tool takes an "as recorded" moment, so any answer can be replayed |
| "Not required" (4.6) | from a stated rule | also `stated_by_exhaustion`: only when every program bound to the plan, state and date is a screened closed list |
| Approval (6.5) | two approvers | one engineer with a recorded exception, as the prototype scope sets it |
| Tools (7.2) | 7 tools | the 7 tools, plus the scope's 3 (`context_applicable_rules`, `rules_evaluate`, `policy_search`), over MCP |
| Client copy and freshness (6.6) | signed read-only schema | second database `kc_client`; limits 14 and 45 days per source |
| Ontology file (6.1) | ✓ | ✓ generated at every build |
| Regression tests (8) | 61 | ✓ same verdicts |

Section by section, with the reasons: `docs/PROTOTYPE_NOTES.md`. Every scope item and test mapped to its evidence: `docs/COVERAGE.md`. Every schema and resolver change is listed at the top of `kanini_context/db/schema.sql` and `resolver.sql`.

---

## What's in the pack

| | |
| :-- | :-- |
| **Use case** | Anemia drug (ESA) for chronic kidney disease, not on dialysis; IV iron under UnitedHealthcare (scope findings, section 2) |
| **Coverage** | Original Medicare in Tennessee (LCD L39237, Article A58982) and Ohio (LCD L34356, Article A56462); UnitedHealthcare Medicare Advantage |
| **Sources** | 16 saved public documents, each with URL, edition, SHA-256 and dated checks (`kanini_context/sources/manifest.json`) |
| **Rules** | 19, each with its page, exact quote, conditions and tests, approved by one engineer |
| **CMS programs** | Every Original Medicare PA program bound to each state is screened against its own code list, including three found during the build (ASC demonstration, DMEPOS probationary, IRF expansion) |

---

## Run it

Needs Docker and Python 3.12 or 3.13.

```bash
docker compose up -d db                                     # PostgreSQL 16 + Apache AGE 1.6.0
python -m venv .venv && .venv\Scripts\activate              # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                        # add OPENAI_API_KEY

python -m kanini_context.sources.place ~/Downloads          # put the saved source copies in place (matched by hash)
python -m kanini_context.curation.checks                    # every quote on its cited page
python -m kanini_context.curation.approve --by "NANDHU S K" # engineer approval, once per edition
python -m kanini_context.release.build                      # release 2026.11.0
pytest                                                      # 75 tests; strict: fails if a CMS program is unscreened or a source is past its limit

python -m kanini_context.server.mcp_server --http 8765      # terminal 1: Kanini Context tools
python -m propel.dashboard.server                      # terminal 2: the dashboard, http://127.0.0.1:8090
adk web propel/agent                                        # or the plain ADK web UI, http://127.0.0.1:8000
python -m propel.baseline.compare --runs 5                  # the A / A' / B / C comparison (B needs the server running)
python -m propel.baseline.compare --runs 5 --only A         # A (RAG) and A' (whole library) only; no server needed
```

<details>
<summary><b>Day-to-day commands</b></summary>

| Command | What it does |
| :-- | :-- |
| `python -m kanini_context.sources.place --checked SOURCE_ID "note"` | Records today's recheck of a source on its URL (answers are held after 14 days) |
| `python -m kanini_context.sources.place --record FOLDER` | Records a newly saved source copy (hash, size, date) |
| `python -m kanini_context.release.sync` | Records a client sync on the demo day |
| `python -m propel.baseline.compare --offline` | Mode C and A's prompt sizes, with no model calls |
| `python -m propel.baseline.compare --a-docs rag` | A by RAG only (`whole` for the whole library only) |

</details>

<details>
<summary><b>Repository layout</b></summary>

| Path | What it holds |
| :-- | :-- |
| `kanini_context/sources/` | Source manifest, `place.py`, `raw/` for the PDFs (not in git) |
| `kanini_context/rules/` | One rule file per source |
| `kanini_context/curation/` | `checks.py`, `approve.py`, `load.py`, `pdftext.py` |
| `kanini_context/db/` | `schema.sql`, `registry.sql`, `resolver.sql`, `api.sql`, `spec_regression/` |
| `kanini_context/release/` | `build.py`, `sync.py`, `owl.py`, `kanini-context.ttl` |
| `kanini_context/server/` | The Kanini Context MCP server |
| `propel/journey/` | Synthetic patients and orders, journey MCP server, decision log |
| `propel/agent/authorization_agent/` | The ADK agent |
| `propel/baseline/` | The A / B / C comparison |
| `propel/dashboard/` | The browser dashboard: FastAPI server, page, vendored d3 and fonts |
| `tests/` | Scope tests 1 to 23, pack tests, live-model tests, scripted model |
| `docs/` | Scope findings, prototype notes, demo script |

</details>

<details>
<summary><b>Two answers that differ from the scope's examples, on purpose</b></summary>

LCD L39237 accepts "Hb < 10 g/dL **or** HCT < 30%", so either result settles it:

- **Test 5, no hemoglobin:** the answer asks for hemoglobin or hematocrit.
- **Test 17, Hb exactly 10.0:** with a hematocrit of 30% or more the answer is "other gate"; with no hematocrit it is "not yet known".

</details>

---

<div align="center">
<sub>Synthetic data only. No patient information is stored or sent to Kanini Context.</sub>
</div>
