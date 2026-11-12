-- Sample data for the worked examples and tests.
-- Real citations and quotes: 42 CFR 422.568(b)(3), 42 CFR 422.113(b)(2)(ii), LCD L39237 thresholds,
-- the UHC Medicare Advantage list entry for J1439 (August and October 2026 editions). Everything marked TEST is invented for the tests.

CREATE FUNCTION kc_core.eid(t text, k text) RETURNS uuid LANGUAGE sql AS $$
  INSERT INTO kc_core.entity(entity_type, natural_key) VALUES (t, k)
  ON CONFLICT (entity_type, natural_key) DO UPDATE SET natural_key = EXCLUDED.natural_key
  RETURNING entity_id $$;
CREATE FUNCTION kc_core.prog(k text) RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT entity_id FROM kc_core.program WHERE kind = k $$;

-- Ontology registry: classes ----------------------------------------------------
INSERT INTO kc_meta.entity_type VALUES
 ('thing',NULL,'root','Anything the pack knows about',NULL),
 ('terminology_item','thing','terminology','A code, group of codes or clinical idea',NULL),
 ('code','terminology_item','terminology','A code from a standard code system, valid for dates','Coding'),
 ('value_set','terminology_item','terminology','A named group of codes','ValueSet'),
 ('concept','terminology_item','terminology','A plain clinical idea, used where codes are licensed',NULL),
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

INSERT INTO kc_meta.rule_kind VALUES
 ('approval_required',NULL,'prior_auth','approval','additive',NULL,
  '{"type":"object","required":["required"],"properties":{"required":{"type":"boolean"},"applies_to":{"enum":["initial","continuation","both"]}},"additionalProperties":false}',1),
 ('exemption',NULL,'prior_auth','approval','override',NULL,
  '{"type":"object","required":["exempt","reason"],"properties":{"exempt":{"const":true},"reason":{"enum":["emergency","post_stabilization","bundled"]}},"additionalProperties":false}',1),
 ('documentation_requirement',NULL,'prior_auth','documentation','additive',NULL,
  '{"type":"object","required":["logic"],"properties":{"logic":{"enum":["all","any"]}},"additionalProperties":false}',1),
 ('obligation',NULL,'core','obligation','ceiling',NULL,'{"type":"object"}',1),
 ('decision_timeframe','obligation','prior_auth','timeframe','ceiling','{standard_hours,expedited_hours}',
  '{"type":"object","properties":{"standard_hours":{"type":"integer","minimum":1},"expedited_hours":{"type":"integer","minimum":1},"clock_starts":{"enum":["receipt","complete_request"]}},"additionalProperties":false,"minProperties":1}',1),
 ('appeal_window','obligation','denials','appeal','floor','{days}',
  '{"type":"object","required":["days"],"properties":{"days":{"type":"integer","minimum":1}},"additionalProperties":false}',1);

UPDATE kc_meta.rule_kind SET conflict_on = '{required}' WHERE rule_kind = 'approval_required';

INSERT INTO kc_meta.fact_type VALUES
 ('encounter_type','enum','["elective","urgent","emergency"]','Kind of encounter'),
 ('urgency','enum','["standard","expedited"]','Whether an expedited decision is requested'),
 ('request_type','enum','["initial","continuation"]','Initial therapy or continuation'),
 ('place_of_service','enum','["11","19","22","65","12"]','CMS place-of-service code (office, outpatient, ESRD facility, home)'),
 ('dialysis_status','enum','["none","in_center","home"]','Whether the patient is on dialysis, and where'),
 ('diagnosis_codes','code_list',NULL,'ICD-10-CM codes on the order, as a list'),
 ('esrd_coordination_month','integer',NULL,'Month of the ESRD coordination period, for employer plan plus Medicare');

-- Sources and snapshots -----------------------------------------------------------
INSERT INTO kc_core.source VALUES
 ('uhc-ma-pa-list','UnitedHealthcare Medicare Advantage prior authorization list','UnitedHealthcare','payer_list','pdf',
  'https://www.uhcprovider.com/','public','1 day','2 days','7 days', now() - interval '6 hours'),
 ('mco-test-list','TEST Medicaid managed care plan prior authorization list','TEST plan','payer_list','pdf',
  NULL,'public','1 day','14 days','45 days', now() - interval '6 hours'),
 ('mcd-lcd','Medicare Coverage Database, local coverage determinations','CMS','medicare_policy','rest_api',
  'https://www.cms.gov/medicare-coverage-database/','public','1 day','14 days','45 days', now() - interval '20 hours'),
 ('ecfr-42-422','eCFR Title 42 Part 422','Office of the Federal Register','federal','rest_api',
  'https://www.ecfr.gov/','public','1 day','14 days','45 days', now() - interval '20 hours'),
 ('hcpcs','HCPCS Level II quarterly file','CMS','code_set','file_download',
  'https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system','public','7 days','30 days','100 days', now() - interval '2 days');

-- Every check of each source (freshness is measured from the last successful check)
INSERT INTO kc_core.source_check
SELECT source_id, last_success_at, true FROM kc_core.source
UNION ALL SELECT source_id, last_success_at - interval '1 day', true FROM kc_core.source;

INSERT INTO kc_core.snapshot (snapshot_id, source_id, retrieved_at, content_sha256, blob_uri, media_type, stated_effective) VALUES
 ('00000000-0000-0000-0000-00000000a001','uhc-ma-pa-list','2026-08-02 09:00+00',repeat('a',64),'blob://snapshots/uhc/2026-08.pdf','application/pdf','Effective August 1, 2026'),
 ('00000000-0000-0000-0000-00000000a006','uhc-ma-pa-list','2026-09-18 09:00+00',repeat('f',64),'blob://snapshots/uhc/2026-10.pdf','application/pdf','Effective October 1, 2026'),
 ('00000000-0000-0000-0000-00000000a002','mcd-lcd','2026-10-05 09:00+00',repeat('b',64),'blob://snapshots/mcd/L39237.json','application/json',NULL),
 ('00000000-0000-0000-0000-00000000a003','ecfr-42-422','2026-10-05 09:00+00',repeat('c',64),'blob://snapshots/ecfr/part-422.xml','application/xml',NULL),
 ('00000000-0000-0000-0000-00000000a004','hcpcs','2026-10-01 09:00+00',repeat('d',64),'blob://snapshots/hcpcs/2026-Q4.zip','application/zip','October 2026'),
 ('00000000-0000-0000-0000-00000000a005','mco-test-list','2026-09-02 09:00+00',repeat('e',64),'blob://snapshots/mco-test/2026.pdf','application/pdf','2026');

-- Terminology -------------------------------------------------------------------------
INSERT INTO kc_core.code_system VALUES
 ('HCPCS','https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets','CMS','public','quarterly'),
 ('ICD10CM','http://hl7.org/fhir/sid/icd-10-cm','CDC NCHS','public','annual, October 1'),
 ('LOINC','http://loinc.org','Regenstrief Institute','free_with_attribution','twice a year');

INSERT INTO kc_core.code (entity_id, code_system_id, code, display, item_type, valid, first_release, replaced_by, snapshot_id) VALUES
 (kc_core.eid('code','HCPCS|J0885'),'HCPCS','J0885','Injection, epoetin alfa, (for non-ESRD use), 1000 units','drug','[2007-01-01,)','2007',NULL,'00000000-0000-0000-0000-00000000a004'),
 (kc_core.eid('code','HCPCS|J1439'),'HCPCS','J1439','Injection, ferric carboxymaltose, 1 mg','drug','[2015-01-01,)','2015',NULL,'00000000-0000-0000-0000-00000000a004'),
 (kc_core.eid('code','HCPCS|X0001'),'HCPCS','X0001','TEST retired code','drug','[2020-01-01,2026-01-01)','2020','{X0002}','00000000-0000-0000-0000-00000000a004'),
 (kc_core.eid('code','ICD10CM|N18.4'),'ICD10CM','N18.4','Chronic kidney disease, stage 4 (severe)','diagnosis','[2015-10-01,)','FY2016',NULL,NULL);

INSERT INTO kc_core.value_set VALUES
 (kc_core.eid('value_set','vs:all-drugs'),'https://kanini.example/fhir/ValueSet/all-drugs','1','All drug codes','intensional','{"item_type":"drug"}',NULL),
 (kc_core.eid('value_set','vs:all-items'),'https://kanini.example/fhir/ValueSet/all-items','1','All items and services','intensional','{"any":true}',NULL);

INSERT INTO kc_core.concept VALUES (kc_core.eid('concept','iv-iron'),'Intravenous iron','Iron replacement given by vein');
INSERT INTO kc_core.concept_map (concept_id, code_system_id, code, map_kind, basis, confidence, ships_in, valid, reviewed_by)
 VALUES (kc_core.eid('concept','iv-iron'),'HCPCS','J1439','narrower','interpreted',0.90,'shared_pack','[2015-01-01,)','{curator.a,curator.b}');

-- Coverage ----------------------------------------------------------------------------
INSERT INTO kc_core.program VALUES
 (kc_core.eid('program','medicare_advantage'),'medicare_advantage','Medicare Advantage'),
 (kc_core.eid('program','medicaid_managed_care'),'medicaid_managed_care','Medicaid managed care');
INSERT INTO kc_core.payer (entity_id, legal_name, short_name) VALUES
 (kc_core.eid('payer','uhc'),'UnitedHealthcare Insurance Company','UnitedHealthcare'),
 (kc_core.eid('payer','cms'),'Centers for Medicare & Medicaid Services','CMS'),
 (kc_core.eid('payer','mco-test'),'TEST Medicaid managed care plan','TEST MCO');
INSERT INTO kc_core.jurisdiction VALUES
 (kc_core.eid('jurisdiction','US'),'nation','US','United States',NULL),
 (kc_core.eid('jurisdiction','TN'),'state','TN','Tennessee',NULL),
 (kc_core.eid('jurisdiction','OH'),'state','OH','Ohio',NULL),
 (kc_core.eid('jurisdiction','JJ'),'mac_ab','JJ','Medicare Part A/B Jurisdiction J','Palmetto GBA'),
 (kc_core.eid('jurisdiction','DMEC'),'mac_dme','DMEC','DME MAC Jurisdiction C','CGS');
INSERT INTO kc_core.edge (relation_type, from_id, to_id, valid) VALUES
 ('within', kc_core.eid('jurisdiction','TN'), kc_core.eid('jurisdiction','JJ'), '[2020-01-01,)'),
 ('within', kc_core.eid('jurisdiction','TN'), kc_core.eid('jurisdiction','DMEC'), '[2020-01-01,)');
INSERT INTO kc_core.plan VALUES
 (kc_core.eid('plan','H1234-001'),kc_core.eid('payer','uhc'),kc_core.prog('medicare_advantage'),'H1234-001','HMO',NULL,'[2026-01-01,2027-01-01)'),
 (kc_core.eid('plan','H1234-002'),kc_core.eid('payer','uhc'),kc_core.prog('medicare_advantage'),'H1234-002','PPO',NULL,'[2026-01-01,2027-01-01)');

INSERT INTO kc_core.endpoint VALUES
 (kc_core.eid('endpoint','uhc-pa-api'),kc_core.eid('payer','uhc'),'payer_pa_api',1,NULL,NULL,true,false,NULL,'[2027-01-01,)'),
 (kc_core.eid('endpoint','uhc-278'),kc_core.eid('payer','uhc'),'x12_278',2,NULL,'005010X217',false,true,now(),'[2020-01-01,)'),
 (kc_core.eid('endpoint','uhc-portal'),kc_core.eid('payer','uhc'),'portal',3,'https://www.uhcprovider.com/',NULL,false,true,now(),'[2020-01-01,)'),
 (kc_core.eid('endpoint','mco-portal'),kc_core.eid('payer','mco-test'),'portal',1,NULL,NULL,false,true,now(),'[2020-01-01,)');

-- Authorities -----------------------------------------------------------------------------
INSERT INTO kc_core.authority VALUES
 (kc_core.eid('authority','42CFR422.113'),'regulation',kc_core.eid('payer','cms'),'42 CFR 422.113(b)(2)(ii)','T0','Special rules for emergency and urgently needed services'),
 (kc_core.eid('authority','42CFR422.568'),'regulation',kc_core.eid('payer','cms'),'42 CFR 422.568(b)(3)','T0','Standard timeframes and notice requirements for organization determinations'),
 (kc_core.eid('authority','42CFR422.572'),'regulation',kc_core.eid('payer','cms'),'42 CFR 422.572(a)(2)','T0','Timeframes for expedited organization determinations'),
 (kc_core.eid('authority','LCD-L39237'),'lcd',kc_core.eid('payer','cms'),'L39237','T1','Erythropoiesis Stimulating Agents (Palmetto GBA)'),
 (kc_core.eid('authority','UHC-MA-PA'),'payer_list',kc_core.eid('payer','uhc'),'UHC MA prior authorization list','T3','UnitedHealthcare Medicare Advantage prior authorization requirements'),
 (kc_core.eid('authority','MCO-TEST-PA'),'payer_list',kc_core.eid('payer','mco-test'),'TEST MCO prior authorization list','T3','TEST Medicaid managed care plan prior authorization list');

INSERT INTO kc_core.authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, snapshot_id) VALUES
 ('00000000-0000-0000-0000-00000000b000',kc_core.eid('authority','42CFR422.113'),'eCFR 2026-10-01','stated','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a003'),
 ('00000000-0000-0000-0000-00000000b001',kc_core.eid('authority','42CFR422.568'),'eCFR 2026-10-01','stated','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a003'),
 ('00000000-0000-0000-0000-00000000b005',kc_core.eid('authority','42CFR422.572'),'eCFR 2026-10-01','stated','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a003'),
 ('00000000-0000-0000-0000-00000000b002',kc_core.eid('authority','LCD-L39237'),'current revision','stated','2025-01-01','2025-01-01','00000000-0000-0000-0000-00000000a002'),
 
 ('00000000-0000-0000-0000-00000000b004',kc_core.eid('authority','MCO-TEST-PA'),'2026 edition','unknown','2026-01-01','2026-03-01','00000000-0000-0000-0000-00000000a005');

-- A new edition replaces an old one. The August edition was loaded first with no end date. When the October edition
-- was approved on 2026-10-02, the August row was closed in record time and re-recorded ending 2026-10-01, and the
-- October edition was recorded from that moment. Nothing is deleted, so earlier answers can be replayed.
INSERT INTO kc_core.authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi,
  valid_to_lo, valid_to_hi, snapshot_id, recorded) VALUES
 ('00000000-0000-0000-0000-00000000b003',kc_core.eid('authority','UHC-MA-PA'),'August 2026 edition','stated','2026-08-01','2026-08-01',NULL,NULL,
  '00000000-0000-0000-0000-00000000a001','[2026-08-03 12:00+00,2026-10-02 12:00+00)'),
 ('00000000-0000-0000-0000-00000000b007',kc_core.eid('authority','UHC-MA-PA'),'August 2026 edition','stated','2026-08-01','2026-08-01','2026-10-01','2026-10-01',
  '00000000-0000-0000-0000-00000000a001','[2026-10-02 12:00+00,)'),
 ('00000000-0000-0000-0000-00000000b006',kc_core.eid('authority','UHC-MA-PA'),'October 2026 edition','stated','2026-10-01','2026-10-01',NULL,NULL,
  '00000000-0000-0000-0000-00000000a006','[2026-10-02 12:00+00,)');

INSERT INTO kc_core.authority_binding (authority_id, program_id, jurisdiction_id, item_scope, applies_when_secondary, valid, reviewed_by) VALUES
 (kc_core.eid('authority','42CFR422.113'),kc_core.prog('medicare_advantage'),kc_core.eid('jurisdiction','US'),'all',true,'[2026-01-01,)','{curator.a,curator.b}'),
 (kc_core.eid('authority','42CFR422.568'),kc_core.prog('medicare_advantage'),kc_core.eid('jurisdiction','US'),'drugs',true,'[2026-01-01,)','{curator.a,curator.b}'),
 (kc_core.eid('authority','42CFR422.572'),kc_core.prog('medicare_advantage'),kc_core.eid('jurisdiction','US'),'drugs',true,'[2026-01-01,)','{curator.a,curator.b}'),
 (kc_core.eid('authority','LCD-L39237'),kc_core.prog('medicare_advantage'),kc_core.eid('jurisdiction','JJ'),'all',true,'[2025-01-01,)','{curator.a,curator.b}'),
 (kc_core.eid('authority','UHC-MA-PA'),kc_core.prog('medicare_advantage'),kc_core.eid('jurisdiction','US'),'all',false,'[2026-08-01,)','{curator.a,curator.b}'),
 (kc_core.eid('authority','MCO-TEST-PA'),kc_core.prog('medicaid_managed_care'),kc_core.eid('jurisdiction','TN'),'all',false,'[2026-01-01,)','{curator.a,curator.b}');

INSERT INTO kc_core.edge (relation_type, from_id, to_id, valid) VALUES
 ('defers_to', kc_core.eid('authority','UHC-MA-PA'), kc_core.eid('authority','LCD-L39237'), '[2026-08-01,)');

-- Data elements -------------------------------------------------------------------------------
INSERT INTO kc_core.data_element VALUES
 (kc_core.eid('data_element','LOINC|718-7'),'Hemoglobin','LOINC','718-7','quantity','g/dL','us-core-observation-lab'),
 (kc_core.eid('data_element','LOINC|98979-8'),'eGFR (CKD-EPI 2021)','LOINC','98979-8','quantity','mL/min/{1.73_m2}','us-core-observation-lab'),
 (kc_core.eid('data_element','LOINC|4544-3'),'Hematocrit','LOINC','4544-3','quantity','%','us-core-observation-lab'),
 (kc_core.eid('data_element','LOINC|2160-0'),'Creatinine, serum','LOINC','2160-0','quantity','mg/dL','us-core-observation-lab');

-- Rules -----------------------------------------------------------------------------------------
-- The insurer list requires approval for J1439 (T3 additive), in both editions. History of the rule:
--   c000: from the August edition, no end date, as recorded 2026-08-03 until the October edition was approved
--   c00b: the same rule re-recorded with its end date, 2026-10-01
--   c001: from the October edition, from 2026-10-01
INSERT INTO kc_core.rule (rule_id, rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, jurisdiction_id, effect, conditions, payload, basis, mapping_confidence, valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by, release_id, valid_to_lo, valid_to_hi, recorded)
VALUES
 -- Emergency services are exempt from prior approval under Medicare Advantage (T0 override)
 ('00000000-0000-0000-0000-00000000c000','exemption','approval',kc_core.eid('value_set','vs:all-items'),kc_core.eid('authority','42CFR422.113'),
  '00000000-0000-0000-0000-00000000b000',kc_core.prog('medicare_advantage'),NULL,NULL,'override',
  '[{"fact":"encounter_type","op":"in","value":["emergency"]}]','{"exempt":true,"reason":"emergency"}','stated',NULL,
  'stated','2026-01-01','2026-01-01','active','00000000-0000-0000-0000-00000000a003','{"section":"422.113(b)(2)(ii)"}',
  'Regardless of whether there is prior authorization for the services.','manual','{curator.a,curator.b}','2026.11.0',
  NULL,NULL,DEFAULT),
 -- Federal 72-hour limit for Part B drug requests (T0 ceiling)
 ('00000000-0000-0000-0000-00000000c002','decision_timeframe','timeframe',kc_core.eid('value_set','vs:all-drugs'),kc_core.eid('authority','42CFR422.568'),
  '00000000-0000-0000-0000-00000000b001',kc_core.prog('medicare_advantage'),NULL,NULL,'ceiling','[]',
  '{"standard_hours":72,"clock_starts":"receipt"}','stated',NULL,'stated','2026-01-01','2026-01-01','active',
  '00000000-0000-0000-0000-00000000a003','{"section":"422.568(b)(3)"}','but no later than 72 hours after receipt of the request',
  'manual','{curator.a,curator.b}','2026.11.0',
  NULL,NULL,DEFAULT),
 -- Medicare policy documentation criteria for J0885 (T1 override: MA must follow it where it exists)
 ('00000000-0000-0000-0000-00000000c003','documentation_requirement','documentation',kc_core.eid('code','HCPCS|J0885'),kc_core.eid('authority','LCD-L39237'),
  '00000000-0000-0000-0000-00000000b002',kc_core.prog('medicare_advantage'),NULL,NULL,'override','[]','{"logic":"all"}','stated',NULL,
  'stated','2025-01-01','2025-01-01','active','00000000-0000-0000-0000-00000000a002','{"section":"Coverage Criteria, CKD not on dialysis"}',
  'Hb below 10 g/dL or HCT below 30% ... most recent creatinine within the month ... GFR less than 45 (abbreviated)','llm_assisted','{curator.a,curator.b}','2026.11.0',
  NULL,NULL,DEFAULT),
 -- Federal 24-hour limit for expedited Part B drug requests (T0 ceiling)
 ('00000000-0000-0000-0000-00000000c005','decision_timeframe','timeframe',kc_core.eid('value_set','vs:all-drugs'),kc_core.eid('authority','42CFR422.572'),
  '00000000-0000-0000-0000-00000000b005',kc_core.prog('medicare_advantage'),NULL,NULL,'ceiling','[]',
  '{"expedited_hours":24}','stated',NULL,'stated','2026-01-01','2026-01-01','active',
  '00000000-0000-0000-0000-00000000a003','{"section":"422.572(a)(2)"}','but no later than 24 hours after receiving the request',
  'manual','{curator.a,curator.b}','2026.11.0',
  NULL,NULL,DEFAULT),
 -- TEST: Medicaid plan list says "all IV iron products" need approval; Kanini links the category to J1439 (interpreted)
 ('00000000-0000-0000-0000-00000000c004','approval_required','approval',kc_core.eid('concept','iv-iron'),kc_core.eid('authority','MCO-TEST-PA'),
  '00000000-0000-0000-0000-00000000b004',kc_core.prog('medicaid_managed_care'),kc_core.eid('payer','mco-test'),NULL,'additive','[]',
  '{"required":true,"applies_to":"both"}','interpreted',0.90,'unknown','2026-01-01','2026-03-01','active',
  '00000000-0000-0000-0000-00000000a005','{"page":4}','All IV iron products','llm_assisted','{curator.a,curator.b}','2026.11.0',
  NULL,NULL,DEFAULT),
 -- The insurer list requires approval for J1439 (T3 additive), in both editions (history explained above)
 ('00000000-0000-0000-0000-0000000c0000','approval_required','approval',kc_core.eid('code','HCPCS|J1439'),kc_core.eid('authority','UHC-MA-PA'),
  '00000000-0000-0000-0000-00000000b003',kc_core.prog('medicare_advantage'),kc_core.eid('payer','uhc'),NULL,'additive',
  '[]','{"required":true,"applies_to":"both"}','stated',NULL,'stated','2026-08-01','2026-08-01',
  'active','00000000-0000-0000-0000-00000000a001','{"section":"Injectable medications","entry":"Intravenous Iron Replacement"}',
  'J1439 – Injectafer','llm_assisted','{curator.a,curator.b}','2026.08.0',NULL,NULL,'[2026-08-03 12:00+00,2026-10-02 12:00+00)'),
 ('00000000-0000-0000-0000-0000000c000b','approval_required','approval',kc_core.eid('code','HCPCS|J1439'),kc_core.eid('authority','UHC-MA-PA'),
  '00000000-0000-0000-0000-00000000b007',kc_core.prog('medicare_advantage'),kc_core.eid('payer','uhc'),NULL,'additive',
  '[]','{"required":true,"applies_to":"both"}','stated',NULL,'stated','2026-08-01','2026-08-01',
  'active','00000000-0000-0000-0000-00000000a001','{"section":"Injectable medications","entry":"Intravenous Iron Replacement"}',
  'J1439 – Injectafer','llm_assisted','{curator.a,curator.b}','2026.10.0','2026-10-01','2026-10-01','[2026-10-02 12:00+00,)'),
 ('00000000-0000-0000-0000-00000000c001','approval_required','approval',kc_core.eid('code','HCPCS|J1439'),kc_core.eid('authority','UHC-MA-PA'),
  '00000000-0000-0000-0000-00000000b006',kc_core.prog('medicare_advantage'),kc_core.eid('payer','uhc'),NULL,'additive',
  '[]','{"required":true,"applies_to":"both"}','stated',NULL,'stated','2026-10-01','2026-10-01',
  'active','00000000-0000-0000-0000-00000000a006','{"section":"Injectable medications","entry":"Intravenous Iron Replacement"}',
  'J1439 – Injectafer','llm_assisted','{curator.a,curator.b}','2026.10.0',NULL,NULL,'[2026-10-02 12:00+00,)');

-- The change flag raised when the October edition was collected, and closed when it was approved.
INSERT INTO kc_gov.change_flag (source_id, snapshot_id, detected_at, resolved_at, applies_from, state) VALUES
 ('uhc-ma-pa-list','00000000-0000-0000-0000-00000000a006','2026-09-18 09:05+00','2026-10-02 12:00+00','2026-10-01','reviewed');

-- L39237, CKD not on dialysis, initial therapy:
--   group 1 (any): Hb < 10 g/dL or Hct < 30%;  group 2: GFR < 45;  group 3: a creatinine result within the past month
INSERT INTO kc_core.rule_requirement (rule_id, group_no, group_logic, data_element_id, test, lookback, when_needed) VALUES
 ('00000000-0000-0000-0000-00000000c003',1,'any',kc_core.eid('data_element','LOINC|718-7'),'{"op":"<","value":10,"unit":"g/dL"}',NULL,'initial'),
 ('00000000-0000-0000-0000-00000000c003',1,'any',kc_core.eid('data_element','LOINC|4544-3'),'{"op":"<","value":30,"unit":"%"}',NULL,'initial'),
 ('00000000-0000-0000-0000-00000000c003',2,'all',kc_core.eid('data_element','LOINC|98979-8'),'{"op":"<","value":45,"unit":"mL/min/{1.73_m2}"}',NULL,'initial'),
 ('00000000-0000-0000-0000-00000000c003',3,'all',kc_core.eid('data_element','LOINC|2160-0'),'{"op":"present"}','1 month','both');

-- Client copy state ------------------------------------------------------------------------------
-- Earlier successful source checks, so a replay of late September sees the sources as checked.
INSERT INTO kc_core.source_check SELECT source_id, '2026-09-25 06:00+00', true FROM kc_core.source;
INSERT INTO kc_client.sync_log VALUES ('2026-09-15 06:00+00','2026.09.1'), ('2026-09-25 06:00+00','2026.09.2'),
 (now() - interval '26 hours','2026.11.0'), (now() - interval '2 hours','2026.11.0');
INSERT INTO kc_client.sync_limit VALUES
 ('payer_list','48 hours','7 days'), ('payer_policy','48 hours','7 days'),
 ('federal','14 days','30 days'), ('medicare_policy','14 days','30 days'),
 ('state','14 days','30 days'), ('code_set','14 days','30 days'), ('interface','48 hours','7 days');

-- Denial reason codes (wording shortened; CARC 197 and 15 relate to missing or invalid authorization)
INSERT INTO kc_core.reason_code (code_set, code, description, category, valid) VALUES
 ('CARC','197','Precertification/authorization/notification/pre-treatment absent.','authorization','[2008-01-01,)'),
 ('CARC','15','The authorization number is missing, invalid, or does not apply to the billed services or provider.','authorization','[2008-01-01,)');

-- The sample rows above were loaded into the pack in July 2026 (record time), except the rows whose history is
-- spelled out explicitly (the two UnitedHealthcare editions). This lets the tests replay past answers.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['kc_core.code','kc_core.value_set_member','kc_core.concept_map','kc_core.authority_version',
                           'kc_core.authority_binding','kc_core.edge','kc_core.rule','kc_core.reason_code'] LOOP
    EXECUTE format('UPDATE %s SET recorded = tstzrange(''2026-07-01 00:00+00'', NULL)
                     WHERE upper_inf(recorded) AND lower(recorded) > ''2026-10-03''', t);
  END LOOP;
END $$;
