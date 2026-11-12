# Kanini Context Prototype: findings from source ingestion

Date: 12 November 2026
Applies to: "Kanini Context Prototype: Scope" (12 November 2026)

The ten source documents were downloaded, hashed and read before any rule was drafted. The hashes are in `kanini_context/sources/manifest.json`. Most scope facts match the sources. Two scenarios do not, and one rule in the schema needs a small extension. This note records each finding, its effect and the change made.

## 1. Facts confirmed

| Scope item | Source | Location | Result |
| --- | --- | --- | --- |
| L39237 revision effective 17 Sep 2026 | LCD L39237 v13 | Header; Public Versions | Confirmed. Previous edition 13 Mar 2025 to 16 Sep 2026 |
| L39237, CKD not on dialysis: Hb < 10 g/dL or HCT < 30%; creatinine within the past month; GFR < 45 | LCD L39237 v13 | Coverage Criteria B, page 7 | Confirmed |
| L34356 revision effective 4 Oct 2026 | LCD L34356 v35 | Header; Revision History R22 | Confirmed. Previous edition 5 Mar 2026 to 3 Oct 2026 |
| L34356, CKD not on dialysis: Hgb < 10 g/dL or Hct < 30%; serum creatinine ≥ 3, creatinine clearance < 60 or GFR < 60 | LCD L34356 v35 | Coverage Criteria B, page 8 | Confirmed |
| L34356 labels the CKD criterion "Anemia of ESRD" | LCD L34356 v35 | Coverage Criteria B.1, page 8 | Confirmed. Recorded as written and flagged for the reviewer |
| Billing articles A58982 and A56462 | Both articles | Code tables | Confirmed |
| Part B drug clocks: 72 hours standard, 24 hours expedited | 42 CFR 422.568(b)(3); 42 CFR 422.572(a)(2) | Section text | Confirmed |

Two further points from the sources:

- **No creatinine time limit in L34356.** Only L39237 requires the most recent creatinine within the past month. Test 19 (stale creatinine) therefore applies to Tennessee only.
- **ESA codes for "ESA, CKD not on dialysis".** The non-ESRD codes are J0881 (darbepoetin alfa), J0885 (epoetin alfa), Q5106 (epoetin alfa-epbx, Retacrit) and J0888 (methoxy polyethylene glycol-epoetin beta, listed in A58982). J0882, J0887 and Q5105 are for ESRD on dialysis and are excluded from the concept.

## 2. Finding: UnitedHealthcare does not require prior authorization for an ESA in CKD

**Scope.** Scenario 3 runs the same ESA order under the UnitedHealthcare Medicare Advantage plan and expects "PA required".

**Source.** The UnitedHealthcare Medicare Advantage prior authorization requirements (effective 1 Oct 2026) list J0885 only under "Cancer supportive care", with prior authorization required "for a cancer diagnosis" (page 7). The same row names the codes that "also require prior authorization for non-oncology diagnosis"; J0885 is not among them. No other entry covers ESAs, and J0881 and Q5106 do not appear in the document. The UnitedHealthcare Part B Step Therapy policy (IAP.001.30, effective 1 Jul 2026) does not list ESAs.

**Effect.** Built as written, scenario 3 would state a requirement that no UnitedHealthcare document makes.

**Change.**

- **Scenario 3 uses IV iron.** The order becomes ferric carboxymaltose, J1439 Injectafer, for the same patient under the same plan. The UnitedHealthcare list requires prior authorization for J1439 under "Injectable medications", "Intravenous Iron Replacement", for all plans (page 17). Requests go through the Prior Authorization and Notification tool on the provider portal, under "Specialty Pharmacy" (page 19). The Part B Step Therapy policy lists Injectafer as non-preferred and gives the step criteria for iron deficiency anemia with CKD without ESRD (page 8). These criteria are a trial of at least two preferred IV irons, an intolerance to them, or continuation within 365 days. They become the evidence list. The 72-hour and 24-hour clocks apply.
- **Scenario 3b is added: the ESA under UnitedHealthcare.** The plan's ESA rule applies only to a cancer diagnosis, so for a CKD patient no rule applies. The expected answer is `no_rule_found`, not safe to act, next step "route to staff".

## 3. Finding: no source states "no prior authorization" for Original Medicare

**Scope.** Scenarios 1 and 2b expect "No PA, safe to act" under Original Medicare.

**Source.** No CMS document states that ESAs need no prior authorization. Original Medicare applies prior authorization only through named programs, each with a defined list of items:

- prior authorization for certain hospital outpatient department services;
- the repetitive, scheduled non-emergent ambulance transport model;
- the DMEPOS required prior authorization list;
- the review choice demonstrations for home health and inpatient rehabilitation facility services;
- the WISeR model, which runs in six states including Ohio (J15, CGS).

The first five appear in the CMS program statistics for fiscal year 2024 (published 16 Sep 2025). WISeR appears in its 2026 provider fact sheet. None of the lists includes ESAs or physician-administered Part B drugs.

**Effect.** Under the schema, `no_rule_found` never means "not required", and a rule Kanini infers has basis `interpreted` and is never actionable. Without a change, scenarios 1 and 2b would return "not safe to act", which contradicts the scope.

**Change: closed-list sources.** A source whose document defines the full set of items it covers is marked as a closed list (`closed_list = true` on the authority, with the list's scope: program, jurisdictions, item categories). The resolver may return `approval_required: not_required` with basis `stated_by_exhaustion` only when all of the following hold:

1. every prior authorization program bound to the coverage, state and date is loaded and marked as a closed list;
2. the ordered code is on none of those lists;
3. none of those sources is stale.

The answer cites each list it checked. If any program for the state is not loaded, or the state itself is not loaded, the answer stays `no_rule_found`. Tests 7, 13 and 14 continue to route to staff.

**Not every list qualifies.** The UnitedHealthcare prior authorization requirements are not marked as a closed list. Their general information section states that health care professionals contracted with a delegated medical group "must follow the delegate's protocols" (page 1), so prior authorization rules exist outside the document. For this reason scenario 3b returns `no_rule_found` and not "No PA".

**Known limit.** The CMS program statistics document is dated September 2025. It is the most recent CMS summary of the program set. The answer shows its age through the normal freshness fields, and a newer CMS list replaces it when one is published.

## 4. Updated scenario table

| # | Scenario | Expected answer | Source |
| --- | --- | --- | --- |
| 1 | Original Medicare, Tennessee, ESA, Hb 9.4 g/dL, eGFR 38 | No PA; LCD criteria met; safe to act | Closed lists checked; L39237 B |
| 2 | Same, eGFR 52 | No PA; LCD criteria not met (GFR not below 45); document or hold | L39237 B |
| 2b | Same as 2, in Ohio | No PA; LCD criteria met (GFR below 60) | Closed lists checked, including WISeR; L34356 B |
| 3 | UnitedHealthcare MA, IV iron (J1439 Injectafer) | PA required; evidence list from the step criteria; 72 h standard, 24 h expedited; submit through the plan portal | UHC PA requirements p.17; IAP.001.30 p.8; 42 CFR 422.568(b)(3), 422.572(a)(2) |
| 3b | UnitedHealthcare MA, ESA | No rule found for CKD; not safe to act; route to staff | UHC PA requirements p.7 (cancer only) |
| 4 | Hemoglobin missing | Not yet known; not safe to act; obtain Hgb | L39237 B |

## 5. Changes to the schema

- `kc_core.authority` gains `closed_list boolean` and a list scope.
- The resolver gains the closed-list check described in section 3, with basis `stated_by_exhaustion`.
- The LCD criteria are held as rules of a coverage criteria kind, added through the registry as data.

The other tables, constraints and statuses are unchanged.

## 6. Addendum: Original Medicare programs found during the build (12 November 2026)

**Source.** The fiscal year 2024 program statistics are not the whole program set for November 2026. Three further CMS initiatives bind the scenario states or nearby ones:

| Program | Places and dates | Items |
| --- | --- | --- |
| Prior Authorization Demonstration for Certain Ambulatory Surgical Center Services (five years) | California, Florida, Tennessee, Pennsylvania, Maryland, Georgia and New York from 19 January 2026; Texas, Arizona and Ohio from 16 February 2026 | Blepharoplasty, botulinum toxin injections, panniculectomy, rhinoplasty, vein ablation |
| Probationary Prior Authorization Process for Newly Enrolled Suppliers of Certain DMEPOS Items | Nationwide from 15 October 2026 | Codes on the CMS code list, for newly enrolled suppliers and suppliers after a change in ownership, for one year |
| Review Choice Demonstration for IRF Services, expansion | Texas from 2 March 2026, California from 1 May 2026, all of Jurisdiction J (Alabama, Georgia, Tennessee) from 1 January 2027 | IRF services |

**Effect.** The first two bind Tennessee and Ohio now; the third binds Tennessee from 2027. Under the closed-list rule in section 3, "No PA" holds only once every bound program is loaded and screened. ESAs are very unlikely to be on any of these lists, but each screen has to be made against the saved list, like the others. The completeness of the program set itself should rest on the CMS index of prior authorization and pre-claim review initiatives, saved as a source, rather than on one report.

**Change.** The three programs are registered in the pack with their places and dates. Until their pages and code lists are saved and the screens are approved, Original Medicare ESA answers in the bound states return "no rule found", naming each unscreened program, and route to staff. Once saved and approved, they return to "No PA" with no code change. The saved copies, and the CMS index page, go in through `python -m kanini_context.sources.place --record`.
