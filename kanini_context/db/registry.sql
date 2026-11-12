-- Kanini Context prototype: the ontology registry and the structural reference data.
-- Run after schema.sql. Rules, codes, editions and bindings are not here: they come from the reviewed
-- files in kanini_context/rules/ through the loader, each with its source copy, page and quote.

CREATE FUNCTION kc_core.eid(t text, k text) RETURNS uuid LANGUAGE sql AS $$
  INSERT INTO kc_core.entity(entity_type, natural_key) VALUES (t, k)
  ON CONFLICT (entity_type, natural_key) DO UPDATE SET natural_key = EXCLUDED.natural_key
  RETURNING entity_id $$;
CREATE FUNCTION kc_core.prog(k text) RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT entity_id FROM kc_core.program WHERE kind = k $$;

-- Classes (spec section 3.2) ----------------------------------------------------------
INSERT INTO kc_meta.entity_type VALUES
 ('thing',NULL,'root','Anything the pack knows about',NULL),
 ('terminology_item','thing','terminology','A code, group of codes or clinical idea',NULL),
 ('code','terminology_item','terminology','A code from a standard code system, valid for dates','Coding'),
 ('value_set','terminology_item','terminology','A named group of codes','ValueSet'),
 ('concept','terminology_item','terminology','A plain clinical idea, used where codes are licensed or a source names a group',NULL),
 ('coverage_item','thing','coverage','Something that defines who pays',NULL),
 ('program','coverage_item','coverage','A coverage program such as Medicare Advantage',NULL),
 ('payer','coverage_item','coverage','An insurer or government payer','Organization'),
 ('plan','coverage_item','coverage','One plan offered by a payer under a program','InsurancePlan'),
 ('jurisdiction','coverage_item','coverage','A nation, state, Medicare contractor region or county',NULL),
 ('authority','thing','authority','A document or body with standing to set rules','DocumentReference'),
 ('data_element','thing','rule','A piece of documentation tied to a standard code','ObservationDefinition'),
 ('knowledge_artifact','thing','rule','An insurer questionnaire or logic library','Questionnaire'),
 ('endpoint','thing','access','A way to reach an insurer and whether it works','Endpoint'),
 ('rule','thing','rule','What an authority says about a subject, for a scope and dates (kc_core.rule)','PlanDefinition'),
 ('source','thing','provenance','Where a document is collected from (kc_core.source)',NULL),
 ('snapshot','thing','provenance','An exact captured copy of a document (kc_core.snapshot)','Binary');

-- Relations (spec section 3.3) ----------------------------------------------------------
INSERT INTO kc_meta.relation_type VALUES
 ('belongs_to','code','value_set','n:m','table','A code is a member of a value set for a period'),
 ('replaced_by','code','code','n:m','column','A retired code and its successor'),
 ('maps_to','concept','code','n:m','table','A concept and the codes that express it'),
 ('offered_under','plan','program','n:1','column','A plan delivers a program'),
 ('operated_by','plan','payer','n:1','column','The insurer behind the plan'),
 ('located_in','plan','jurisdiction','n:m','edge','Where a plan is offered'),
 ('within','jurisdiction','jurisdiction','n:m','edge','A state sits in a Medicare contractor region'),
 ('states','authority','rule','1:n','column','The authority that issued a rule (rule.authority_id)'),
 ('binds','authority','coverage_item','n:m','table','Which programs, plans and places an authority applies to'),
 ('supersedes','authority','authority','1:1','edge','A newer document replaces an older one'),
 ('preempts','authority','authority','n:m','edge','Federal law overrides state law for an aspect'),
 ('defers_to','authority','authority','n:m','edge','Insurer criteria follow Medicare policy where it exists'),
 ('about','rule','terminology_item','n:1','column','What a rule covers (rule.subject_id)'),
 ('requires','rule','data_element','n:m','table','Documentation a rule asks for (rule_requirement)'),
 ('documented_by','rule','knowledge_artifact','n:1','column','The insurer questionnaire for a rule'),
 ('exposes','payer','endpoint','1:n','column','How to reach an insurer (endpoint.payer_id)'),
 ('issued_in','authority','snapshot','1:n','table','An authority edition and its snapshot (authority_version)');

-- Rule kinds (spec section 4.2), plus coverage_criteria for this prototype -----------------
INSERT INTO kc_meta.rule_kind VALUES
 ('approval_required',NULL,'prior_auth','approval','additive',NULL,
  '{"type":"object","required":["required"],"properties":{"required":{"type":"boolean"},"applies_to":{"enum":["initial","continuation","both"]},"submit_via":{"type":"string"}},"additionalProperties":false}',1),
 ('exemption',NULL,'prior_auth','approval','override',NULL,
  '{"type":"object","required":["exempt","reason"],"properties":{"exempt":{"const":true},"reason":{"enum":["emergency","post_stabilization","bundled","emergency_or_urgent"]}},"additionalProperties":false}',1),
 ('documentation_requirement',NULL,'prior_auth','documentation','additive',NULL,
  '{"type":"object","required":["logic"],"properties":{"logic":{"enum":["all","any"]},"applies_when":{"type":"string"}},"additionalProperties":false}',1),
 ('obligation',NULL,'core','obligation','ceiling',NULL,'{"type":"object"}',1),
 ('decision_timeframe','obligation','prior_auth','timeframe','ceiling','{standard_hours,expedited_hours}',
  '{"type":"object","properties":{"standard_hours":{"type":"integer","minimum":1},"expedited_hours":{"type":"integer","minimum":1},"clock_starts":{"enum":["receipt","complete_request"]}},"additionalProperties":false,"minProperties":1}',1),
 ('appeal_window','obligation','denials','appeal','floor','{days}',
  '{"type":"object","required":["days"],"properties":{"days":{"type":"integer","minimum":1}},"additionalProperties":false}',1),
 -- A coverage policy's criteria for an indication, checked against the patient's registered facts.
 -- Its requirement items hold the tests; the answer is met, not met, or the facts still needed.
 ('coverage_criteria',NULL,'prior_auth','coverage','additive',NULL,
  '{"type":"object","required":["logic","indication"],"properties":{"logic":{"enum":["all","any"]},"indication":{"type":"string"},"not_modelled":{"type":"array","items":{"type":"string"}},"wording_note":{"type":"string"}},"additionalProperties":false}',1);

UPDATE kc_meta.rule_kind SET conflict_on = '{required}' WHERE rule_kind = 'approval_required';

-- Facts an agent may pass (spec section 4.3), plus the lab facts the coverage criteria test.
-- Number facts carry a plausible range: a value outside it is refused as invalid input.
INSERT INTO kc_meta.fact_type VALUES
 ('encounter_type','enum','["elective","urgent","emergency"]','Kind of encounter'),
 ('urgency','enum','["standard","expedited"]','Whether an expedited decision is requested'),
 ('request_type','enum','["initial","continuation"]','Initial therapy or continuation'),
 ('place_of_service','enum','["11","19","22","65","12"]','CMS place-of-service code (office, outpatient, ESRD facility, home)'),
 ('dialysis_status','enum','["none","in_center","home"]','Whether the patient is on dialysis, and where'),
 ('diagnosis_codes','code_list',NULL,'ICD-10-CM codes on the order, as a list'),
 ('esrd_coordination_month','integer',NULL,'Month of the ESRD coordination period, for employer plan plus Medicare'),
 ('hgb_g_dl','number','{"min":1,"max":25}','Most recent hemoglobin, g/dL'),
 ('hct_pct','number','{"min":5,"max":75}','Most recent hematocrit, percent'),
 ('egfr','number','{"min":0,"max":200}','Most recent eGFR, mL/min/1.73 m2'),
 ('crcl_ml_min','number','{"min":0,"max":250}','Most recent creatinine clearance, mL/min'),
 ('serum_creatinine_mg_dl','number','{"min":0.1,"max":30}','Most recent serum creatinine, mg/dL'),
 ('creatinine_date','date',NULL,'Date of the most recent serum creatinine result');

-- Code systems -------------------------------------------------------------------------
INSERT INTO kc_core.code_system VALUES
 ('HCPCS','https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets','CMS','public','quarterly'),
 ('ICD10CM','http://hl7.org/fhir/sid/icd-10-cm','CDC/NCHS','public','annual (October 1)'),
 ('LOINC','http://loinc.org','Regenstrief Institute','free_with_attribution','twice yearly'),
 ('KCDE','https://kanini.com/fhir/CodeSystem/kc-data-element','Kanini','public','with each release');

-- Programs and payers --------------------------------------------------------------------
INSERT INTO kc_core.program VALUES
 (kc_core.eid('program','medicare_ffs'),'medicare_ffs','Original Medicare (fee-for-service)'),
 (kc_core.eid('program','medicare_advantage'),'medicare_advantage','Medicare Advantage');
INSERT INTO kc_core.payer (entity_id, legal_name, short_name) VALUES
 (kc_core.eid('payer','cms'),'Centers for Medicare & Medicaid Services','CMS'),
 (kc_core.eid('payer','uhc'),'UnitedHealthcare','UHC');

-- Places: the nation, states, and the Medicare A/B contractor regions named in the two LCDs.
INSERT INTO kc_core.jurisdiction VALUES
 (kc_core.eid('jurisdiction','US'),'nation','US','United States',NULL),
 (kc_core.eid('jurisdiction','JJ'),'mac_ab','JJ','Medicare A/B Jurisdiction J','Palmetto GBA'),
 (kc_core.eid('jurisdiction','JM'),'mac_ab','JM','Medicare A/B Jurisdiction M','Palmetto GBA'),
 (kc_core.eid('jurisdiction','J15'),'mac_ab','J15','Medicare A/B Jurisdiction 15','CGS Administrators');
INSERT INTO kc_core.jurisdiction
SELECT kc_core.eid('jurisdiction', s), 'state', s, n, NULL FROM (VALUES
 ('AL','Alabama'),('GA','Georgia'),('TN','Tennessee'),('NC','North Carolina'),('SC','South Carolina'),
 ('VA','Virginia'),('WV','West Virginia'),('KY','Kentucky'),('OH','Ohio'),('IL','Illinois'),('TX','Texas'),
 ('FL','Florida'),('OK','Oklahoma'),('PA','Pennsylvania'),('NJ','New Jersey'),('WA','Washington'),
 ('AZ','Arizona'),('CA','California')) t(s, n);
-- Which state sits in which A/B region: from the Contractor Information tables of L39237 and L34356.
INSERT INTO kc_core.edge (relation_type, from_id, to_id, valid)
SELECT 'within', kc_core.eid('jurisdiction', s), kc_core.eid('jurisdiction', r), '[2020-01-01,)' FROM (VALUES
 ('AL','JJ'),('GA','JJ'),('TN','JJ'),('NC','JM'),('SC','JM'),('VA','JM'),('WV','JM'),('KY','J15'),('OH','J15')) t(s, r);

-- Plans. 'original-medicare' is Original Medicare itself; 'uhc-ma' stands for the UnitedHealthcare
-- Medicare Advantage plans named under "Plans included" in UHC's prior authorization requirements.
INSERT INTO kc_core.plan (entity_id, payer_id, program_id, external_id, plan_type, valid) VALUES
 (kc_core.eid('plan','original-medicare'), kc_core.eid('payer','cms'), kc_core.prog('medicare_ffs'),
  NULL, 'Original Medicare', '[1966-07-01,)'),
 (kc_core.eid('plan','uhc-ma'), kc_core.eid('payer','uhc'), kc_core.prog('medicare_advantage'),
  NULL, 'UHC Medicare Advantage (HMO, HMO-POS, PPO, Regional PPO; AARP Medicare Advantage)', '[2026-01-01,)');

-- Data elements: what a criterion or a form item is about, its code, and the fact that carries its value.
INSERT INTO kc_core.data_element (entity_id, name, code_system_id, code, value_type, unit, us_core_profile, fact) VALUES
 (kc_core.eid('data_element','LOINC|718-7'),'Hemoglobin','LOINC','718-7','quantity','g/dL','us-core-observation-lab','hgb_g_dl'),
 (kc_core.eid('data_element','LOINC|4544-3'),'Hematocrit','LOINC','4544-3','quantity','%','us-core-observation-lab','hct_pct'),
 (kc_core.eid('data_element','LOINC|98979-8'),'eGFR (CKD-EPI 2021)','LOINC','98979-8','quantity','mL/min/{1.73_m2}','us-core-observation-lab','egfr'),
 (kc_core.eid('data_element','LOINC|2164-2'),'Creatinine clearance','LOINC','2164-2','quantity','mL/min','us-core-observation-lab','crcl_ml_min'),
 (kc_core.eid('data_element','LOINC|2160-0'),'Serum creatinine','LOINC','2160-0','quantity','mg/dL','us-core-observation-lab','serum_creatinine_mg_dl'),
 (kc_core.eid('data_element','LOINC|2160-0|date'),'Serum creatinine (date of most recent result)','LOINC','2160-0','date',NULL,'us-core-observation-lab','creatinine_date'),
 (kc_core.eid('data_element','ICD10CM|D63.1'),'Diagnosis: anemia in chronic kidney disease','ICD10CM','D63.1','coded',NULL,'us-core-condition-problems-health-concerns','diagnosis_codes'),
 (kc_core.eid('data_element','ICD10CM|ckd-not-on-dialysis'),'Diagnosis: chronic kidney disease stage (listed codes)','ICD10CM',NULL,'coded',NULL,'us-core-condition-problems-health-concerns','diagnosis_codes'),
 (kc_core.eid('data_element','KCDE|iv-iron-preferred-trials'),'Trial of at least 3 weeks of at least two preferred IV iron therapies, each with minimal clinical response','KCDE','iv-iron-preferred-trials','attachment',NULL,'us-core-documentreference',NULL),
 (kc_core.eid('data_element','KCDE|iv-iron-preferred-intolerance'),'Contraindication, intolerance or adverse event to at least two preferred IV iron therapies','KCDE','iv-iron-preferred-intolerance','attachment',NULL,'us-core-documentreference',NULL),
 (kc_core.eid('data_element','KCDE|iv-iron-continuation-365d'),'Continuation of prior Injectafer or Monoferric therapy within the past 365 days','KCDE','iv-iron-continuation-365d','attachment',NULL,'us-core-documentreference',NULL);

-- Value sets defined by a filter: every HCPCS drug code (the federal Part B drug deadlines), and every item.
INSERT INTO kc_core.value_set VALUES
 (kc_core.eid('value_set','vs:all-drugs'),'https://kanini.com/fhir/ValueSet/all-drugs','1','All drug codes','intensional','{"item_type":"drug"}',NULL),
 (kc_core.eid('value_set','vs:all-items'),'https://kanini.com/fhir/ValueSet/all-items','1','All items','intensional','{"any":true}',NULL);

-- How the client copy becomes cautious when it stops syncing (spec section 6.6)
INSERT INTO kc_client.sync_limit VALUES
 ('payer_list','48 hours','7 days'), ('payer_policy','48 hours','7 days'),
 ('federal','14 days','30 days'), ('medicare_policy','14 days','30 days'),
 ('state','14 days','30 days'), ('code_set','14 days','30 days'), ('interface','48 hours','7 days');
