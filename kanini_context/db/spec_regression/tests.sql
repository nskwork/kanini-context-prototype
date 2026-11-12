-- Worked questions and safety tests. Run after schema.sql, seed.sql and resolver.sql.
SET kc.release = '2026.11.0';
\pset format unaligned
\pset tuples_only on
\set MA '{"program":"medicare_advantage","payer":"uhc","state":"TN","payer_position":1}'
\set ELECTIVE '{"encounter_type":"elective"}'

CREATE TEMP VIEW s AS SELECT 1;  -- placeholder so \echo lines group cleanly
CREATE FUNCTION pg_temp.brief(j jsonb) RETURNS text LANGUAGE sql AS $$
  SELECT concat_ws(' | ', 'status=' || (j->>'status'), 'reason=' || coalesce(j->>'reason','-'),
         'actionable=' || (j->>'actionable'), 'value=' || coalesce(j->'result'->>'value','-'),
         'missing=' || coalesce(j->>'missing_facts','-'), 'next_step=' || coalesce(j->>'next_step','-'),
         'reasons=' || coalesce(j->>'actionable_reasons','-')) $$;

\echo 'Q01 approval, J1439, UHC Medicare Advantage, TN, elective, 2026-11-20 (full answer)'
SELECT jsonb_pretty(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
\echo 'Q02 same question with no encounter type'
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439','{}','2026-11-20'));
\echo 'Q03 same question for an emergency'
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439','{"encounter_type":"emergency"}','2026-11-20'));
\echo 'Q04 decision deadline for J1439'
SELECT pg_temp.brief(kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20'));
\echo 'Q05 documentation for J0885 in TN (full answer)'
SELECT jsonb_pretty(kc_api.resolve_rules(:'MA','documentation_requirement','HCPCS|J0885','{}','2026-11-20'));
\echo 'Q06 documentation for J0885 in OH, where the Ohio policy is not loaded'
SELECT pg_temp.brief(kc_api.resolve_rules('{"program":"medicare_advantage","payer":"uhc","state":"OH"}','documentation_requirement','HCPCS|J0885','{}','2026-11-20'));
\echo 'Q07 approval for J1439 on 2026-07-15, before the list took effect'
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-07-15'));
\echo 'Q08 validate codes'
SELECT kc_api.validate_codes(ARRAY['HCPCS|J1439','HCPCS|X0001','HCPCS|ZZZZZ'],'2026-11-20');
\echo 'Q09 coverage stack for a patient with Medicaid managed care and Medicare Advantage'
SELECT kc_api.resolve_coverage_stack('[{"program":"medicaid_managed_care","payer":"mco-test","state":"TN"},{"program":"medicare_advantage","payer":"uhc","state":"TN"}]') -> 'coverages';
\echo 'Q10 Medicaid plan list (category rule) when Medicaid pays first'
SELECT pg_temp.brief(kc_api.resolve_rules('{"program":"medicaid_managed_care","payer":"mco-test","state":"TN","payer_position":1}','approval_required','HCPCS|J1439','{}','2026-11-20'));
\echo 'Q11 same list when Medicare pays first and Medicaid is second'
SELECT pg_temp.brief(kc_api.resolve_rules('{"program":"medicaid_managed_care","payer":"mco-test","state":"TN","payer_position":2}','approval_required','HCPCS|J1439','{}','2026-11-20'));
\echo 'Q12 Medicaid plan list on 2026-02-01, inside its uncertain start window'
SELECT pg_temp.brief(kc_api.resolve_rules('{"program":"medicaid_managed_care","payer":"mco-test","state":"TN","payer_position":1}','approval_required','HCPCS|J1439','{}','2026-02-01'));

\echo 'Q13 routes to UnitedHealthcare for a drug request on 2026-11-20'
SELECT kc_api.get_payer_capabilities('uhc','2026-11-20','drug');
\echo 'Q14 meaning of denial reason CARC 197, and an unknown code'
SELECT kc_api.map_reason_code('CARC','197','2026-11-20') ->> 'category', kc_api.map_reason_code('CARC','99999','2026-11-20') ->> 'result';
\echo 'Q15 a new edition replaces an old one: J1439 on 2026-09-15 cites the August edition, on 2026-11-20 the October edition'
SELECT '2026-09-15: ' || (kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-09-15') -> 'sources' -> 0 ->> 'version');
SELECT '2026-11-20: ' || (kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20') -> 'sources' -> 0 ->> 'version');
\echo 'Q16 replay: what the pack said on 2026-09-25, after the October edition was collected and before it was approved'
SELECT 'service 2026-09-20: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-09-20','2026-09-25 09:00+00'));
SELECT 'service 2026-10-15: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-10-15','2026-09-25 09:00+00'));
SELECT 'same question asked now: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-10-15'));

\echo 'T01 overlapping active version of the same rule (expect an error)'
\set ON_ERROR_STOP 0
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
SELECT rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, '{"required":false}', basis,
  'stated','2026-10-01','2026-10-01','active', snapshot_id, source_locator, source_quote, extracted_by, reviewed_by
FROM kc_core.rule WHERE rule_id = '00000000-0000-0000-0000-00000000c001';
\echo 'T02 active rule with one approver (expect an error)'
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
SELECT rule_kind, aspect, subject_id, authority_id, authority_version_id, effect, payload, basis,
  'stated','2027-01-01','2027-01-01','active', snapshot_id, source_locator, source_quote, extracted_by, '{curator.a}'
FROM kc_core.rule WHERE rule_id = '00000000-0000-0000-0000-00000000c002';
\echo 'T03 edge between the wrong kinds of entity (expect an error)'
INSERT INTO kc_core.edge (relation_type, from_id, to_id, valid) VALUES ('within', kc_core.eid('payer','uhc'), kc_core.eid('jurisdiction','TN'), '[2026-01-01,)');
\echo 'T04 interpreted rule without a mapping confidence (expect an error)'
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by)
SELECT rule_kind, aspect, subject_id, authority_id, authority_version_id, effect, payload, 'interpreted',
  'stated','2027-01-01','2027-01-01','draft', snapshot_id, source_locator, source_quote, extracted_by
FROM kc_core.rule WHERE rule_id = '00000000-0000-0000-0000-00000000c001';
\set ON_ERROR_STOP 1

\echo 'T05 open change flag on the insurer list'
INSERT INTO kc_gov.change_flag (source_id, snapshot_id, detected_at, applies_from, state)
VALUES ('uhc-ma-pa-list','00000000-0000-0000-0000-00000000a006', now(), '2026-11-01','open');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
DELETE FROM kc_gov.change_flag;

\echo 'T06 the insurer commits to 48 hours (faster than the federal 72)'
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
VALUES ('decision_timeframe','timeframe', kc_core.eid('value_set','vs:all-drugs'), kc_core.eid('authority','UHC-MA-PA'),
  '00000000-0000-0000-0000-00000000b006', kc_core.prog('medicare_advantage'), kc_core.eid('payer','uhc'),'additive',
  '{"standard_hours":48}','stated','stated','2026-10-01','2026-10-01','active','00000000-0000-0000-0000-00000000a006',
  '{"page":2}','TEST','manual','{a,b}');
SELECT pg_temp.brief(kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20'));
\echo 'T07 the insurer states 96 hours instead (slower than the federal 72)'
UPDATE kc_core.rule SET recorded = tstzrange(lower(recorded), now()) WHERE payload = '{"standard_hours":48}' AND upper_inf(recorded);
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
VALUES ('decision_timeframe','timeframe', kc_core.eid('value_set','vs:all-drugs'), kc_core.eid('authority','UHC-MA-PA'),
  '00000000-0000-0000-0000-00000000b006', kc_core.prog('medicare_advantage'), kc_core.eid('payer','uhc'),'additive',
  '{"standard_hours":96}','stated','stated','2026-10-01','2026-10-01','active','00000000-0000-0000-0000-00000000a006',
  '{"page":2}','TEST','manual','{a,b}');
SELECT pg_temp.brief(kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20')) || ' | notes=' ||
       (kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20') ->> 'notes');
UPDATE kc_core.rule SET recorded = tstzrange(lower(recorded), now()) WHERE payload = '{"standard_hours":96}' AND upper_inf(recorded);

\echo 'T08 same-layer disagreement on approval'
INSERT INTO kc_core.authority VALUES (kc_core.eid('authority','UHC-MA-DRUG-POLICY'),'payer_policy',kc_core.eid('payer','uhc'),'TEST UHC MA drug policy','T3','TEST UHC MA drug policy');
INSERT INTO kc_core.authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, snapshot_id)
VALUES ('00000000-0000-0000-0000-00000000b009', kc_core.eid('authority','UHC-MA-DRUG-POLICY'),'2026','stated','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a001');
INSERT INTO kc_core.authority_binding (authority_id, program_id, jurisdiction_id, item_scope, valid, reviewed_by)
VALUES (kc_core.eid('authority','UHC-MA-DRUG-POLICY'), kc_core.prog('medicare_advantage'), kc_core.eid('jurisdiction','US'),'all','[2026-01-01,)','{a,b}');
INSERT INTO kc_core.rule (rule_id, rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
VALUES ('00000000-0000-0000-0000-00000000c009','approval_required','approval', kc_core.eid('code','HCPCS|J1439'), kc_core.eid('authority','UHC-MA-DRUG-POLICY'),
  '00000000-0000-0000-0000-00000000b009', kc_core.prog('medicare_advantage'), kc_core.eid('payer','uhc'),'additive','{"required":false}','stated',
  'stated','2026-01-01','2026-01-01','active','00000000-0000-0000-0000-00000000a001','{"page":2}','TEST','manual','{a,b}');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
UPDATE kc_core.rule SET status = 'retired' WHERE rule_id = '00000000-0000-0000-0000-00000000c009';

\echo 'T09 the client copy has not synced for 3 days (insurer lists turn aging after 48 hours)'
DELETE FROM kc_client.sync_log; INSERT INTO kc_client.sync_log VALUES (now() - interval '3 days','2026.11.0');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
\echo 'T10 not synced for 8 days (insurer lists stop after 7 days; federal rules keep answering)'
DELETE FROM kc_client.sync_log; INSERT INTO kc_client.sync_log VALUES (now() - interval '8 days','2026.11.0');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
SELECT pg_temp.brief(kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20'));
INSERT INTO kc_client.sync_log VALUES (now() - interval '2 hours','2026.11.0');

\echo 'T11 correction over time: what the pack said before and after a correction'
-- The LCD rule's locator is corrected: the old row is closed in record time and a new row recorded.
SELECT now() AS before_fix \gset
SELECT pg_sleep(0.05);
UPDATE kc_core.rule SET recorded = tstzrange(lower(recorded), now()) WHERE rule_id = '00000000-0000-0000-0000-00000000c003';
INSERT INTO kc_core.rule (rule_id, rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
SELECT '00000000-0000-0000-0000-00000000c013', rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, effect, '{"logic":"all"}', basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, '{"section":"Coverage Criteria, item 2"}', source_quote, extracted_by, reviewed_by
FROM kc_core.rule WHERE rule_id = '00000000-0000-0000-0000-00000000c003';
INSERT INTO kc_core.rule_requirement (rule_id, group_no, group_logic, data_element_id, test, lookback, when_needed)
SELECT '00000000-0000-0000-0000-00000000c013', group_no, group_logic, data_element_id, test, lookback, when_needed
  FROM kc_core.rule_requirement WHERE rule_id = '00000000-0000-0000-0000-00000000c003';
SELECT 'as recorded before the fix: locator=' || (kc_api.resolve_rules(:'MA','documentation_requirement','HCPCS|J0885','{}','2026-11-20', :'before_fix') -> 'sources' -> 0 ->> 'locator');
SELECT 'as recorded now:            locator=' || (kc_api.resolve_rules(:'MA','documentation_requirement','HCPCS|J0885','{}','2026-11-20') -> 'sources' -> 0 ->> 'locator');

\echo 'T12 possible overlaps view (rules whose uncertain dates could overlap)'
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis, mapping_confidence,
  valid_from_basis, valid_from_lo, valid_from_hi, valid_to_lo, valid_to_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
SELECT rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis, mapping_confidence,
  'unknown','2025-01-01','2025-02-01','2025-12-15','2026-02-15','active', snapshot_id, source_locator, source_quote, extracted_by, reviewed_by
FROM kc_core.rule WHERE rule_id = '00000000-0000-0000-0000-00000000c004';
SELECT 'possible overlaps: ' || count(*) FROM kc_gov.possible_overlap;
\echo 'T13 deleting a rule is refused (expect an error)'
\set ON_ERROR_STOP 0
DELETE FROM kc_core.rule WHERE rule_id = '00000000-0000-0000-0000-00000000c002';
\set ON_ERROR_STOP 1
\echo 'T14 a documentation item for a rule that does not exist is refused (expect an error)'
\set ON_ERROR_STOP 0
INSERT INTO kc_core.rule_requirement (rule_id, group_no, group_logic, data_element_id, test, when_needed)
VALUES ('00000000-0000-0000-0000-0000000000ff', 1, 'all', kc_core.eid('data_element','LOINC|718-7'), '{"op":"present"}', 'initial');
\set ON_ERROR_STOP 1


\echo '================ Edge cases from real payer data ================'
\echo 'N01 decision deadlines for J1439: standard and expedited, payload fields kept'
SELECT kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20') -> 'result' ->> 'value';
\echo 'N02 insurer commits to 48 hours standard: 48 standard, 24 expedited, clock_starts kept'
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
VALUES ('decision_timeframe','timeframe', kc_core.eid('value_set','vs:all-drugs'), kc_core.eid('authority','UHC-MA-PA'),
  '00000000-0000-0000-0000-00000000b006', kc_core.prog('medicare_advantage'), kc_core.eid('payer','uhc'),'additive',
  '{"standard_hours":48}','stated','stated','2026-10-01','2026-10-01','active','00000000-0000-0000-0000-00000000a006','{"page":2}','TEST','manual','{a,b}');
SELECT kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20') -> 'result' ->> 'value';
UPDATE kc_core.rule SET recorded = tstzrange(lower(recorded), now()) WHERE payload = '{"standard_hours":48}' AND upper_inf(recorded);

\echo 'N03 appeal window floor on a Medicaid managed care plan: TEST minimums of 60 and 90 days, the plan states 65 -> 90 applies'
INSERT INTO kc_core.authority VALUES (kc_core.eid('authority','TEST-FED-APPEAL'),'regulation',kc_core.eid('payer','cms'),'TEST federal appeal rule','T0','TEST federal appeal window'),
                                     (kc_core.eid('authority','TEST-TN-APPEAL'),'state_rule',NULL,'TEST state appeal rule','T2','TEST state appeal window');
INSERT INTO kc_core.authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, snapshot_id) VALUES
 ('00000000-0000-0000-0000-00000000b011',kc_core.eid('authority','TEST-FED-APPEAL'),'TEST','stated','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a003'),
 ('00000000-0000-0000-0000-00000000b012',kc_core.eid('authority','TEST-TN-APPEAL'),'TEST','stated','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a003');
INSERT INTO kc_core.authority_binding (authority_id, program_id, jurisdiction_id, item_scope, valid, reviewed_by) VALUES
 (kc_core.eid('authority','TEST-FED-APPEAL'), kc_core.prog('medicaid_managed_care'), kc_core.eid('jurisdiction','US'),'all','[2026-01-01,)','{a,b}'),
 (kc_core.eid('authority','TEST-TN-APPEAL'), kc_core.prog('medicaid_managed_care'), kc_core.eid('jurisdiction','TN'),'all','[2026-01-01,)','{a,b}');
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('appeal_window','appeal', kc_core.eid('value_set','vs:all-items'), kc_core.eid('authority','TEST-FED-APPEAL'),'00000000-0000-0000-0000-00000000b011',
  kc_core.prog('medicaid_managed_care'),NULL,'floor','{"days":60}','stated','stated','2026-01-01','2026-01-01','active','00000000-0000-0000-0000-00000000a003','{}','TEST','manual','{a,b}'),
 ('appeal_window','appeal', kc_core.eid('value_set','vs:all-items'), kc_core.eid('authority','TEST-TN-APPEAL'),'00000000-0000-0000-0000-00000000b012',
  kc_core.prog('medicaid_managed_care'),NULL,'floor','{"days":90}','stated','stated','2026-01-01','2026-01-01','active','00000000-0000-0000-0000-00000000a003','{}','TEST','manual','{a,b}'),
 ('appeal_window','appeal', kc_core.eid('value_set','vs:all-items'), kc_core.eid('authority','MCO-TEST-PA'),'00000000-0000-0000-0000-00000000b004',
  kc_core.prog('medicaid_managed_care'),kc_core.eid('payer','mco-test'),'additive','{"days":65}','stated','stated','2026-03-01','2026-03-01','active','00000000-0000-0000-0000-00000000a005','{}','TEST','manual','{a,b}');
SELECT (kc_api.resolve_rules('{"program":"medicaid_managed_care","payer":"mco-test","state":"TN","payer_position":1}','appeal_window','HCPCS|J1439','{}','2026-11-20') -> 'result' ->> 'value') || ' | notes=' ||
       (kc_api.resolve_rules('{"program":"medicaid_managed_care","payer":"mco-test","state":"TN","payer_position":1}','appeal_window','HCPCS|J1439','{}','2026-11-20') ->> 'notes');

\echo 'N04 a plan-specific (T4) rule applies only to its own plan'
INSERT INTO kc_core.authority VALUES (kc_core.eid('authority','TEST-PLAN-DOC'),'plan_document',kc_core.eid('payer','uhc'),'TEST H1234-001 evidence of coverage','T4','TEST plan terms');
INSERT INTO kc_core.authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, snapshot_id) VALUES
 ('00000000-0000-0000-0000-00000000b013',kc_core.eid('authority','TEST-PLAN-DOC'),'2026','plan_year','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a001');
INSERT INTO kc_core.authority_binding (authority_id, plan_id, item_scope, valid, reviewed_by) VALUES
 (kc_core.eid('authority','TEST-PLAN-DOC'), kc_core.eid('plan','H1234-001'),'all','[2026-01-01,2027-01-01)','{a,b}');
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, plan_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, valid_to_lo, valid_to_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('approval_required','approval', kc_core.eid('code','HCPCS|J0885'), kc_core.eid('authority','TEST-PLAN-DOC'),'00000000-0000-0000-0000-00000000b013',
  kc_core.eid('plan','H1234-001'),'additive','{"required":true}','stated','plan_year','2026-01-01','2026-01-01','2027-01-01','2027-01-01','active',
  '00000000-0000-0000-0000-00000000a001','{}','TEST','manual','{a,b}');
SELECT 'plan H1234-001: ' || pg_temp.brief(kc_api.resolve_rules('{"program":"medicare_advantage","payer":"uhc","plan":"H1234-001","state":"TN"}','approval_required','HCPCS|J0885',:'ELECTIVE','2026-11-20'));
SELECT 'plan H1234-002: ' || pg_temp.brief(kc_api.resolve_rules('{"program":"medicare_advantage","payer":"uhc","plan":"H1234-002","state":"TN"}','approval_required','HCPCS|J0885',:'ELECTIVE','2026-11-20'));
UPDATE kc_core.rule SET status = 'retired' WHERE authority_id = kc_core.eid('authority','TEST-PLAN-DOC');

\echo 'N05 documentation for J0885 in TN keeps its groups: (Hb or Hct) and GFR and creatinine within a month'
SELECT jsonb_pretty(kc_api.resolve_rules(:'MA','documentation_requirement','HCPCS|J0885','{}','2026-11-20') -> 'result' -> 'requirements' -> 0);

\echo 'N06 three-valued conditions: a false condition beats a missing one'
SELECT kc_api.eval_conditions('[{"fact":"encounter_type","op":"eq","value":"emergency"},{"fact":"place_of_service","op":"eq","value":"22"}]','{"encounter_type":"elective"}') ->> 'state';
\echo 'N07 an emergency resolves to exempt even when a lower-layer rule needs a fact that was not given'
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, conditions, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('approval_required','approval', kc_core.eid('code','HCPCS|J0885'), kc_core.eid('authority','UHC-MA-PA'),'00000000-0000-0000-0000-00000000b006',
  kc_core.prog('medicare_advantage'),kc_core.eid('payer','uhc'),'additive','[{"fact":"diagnosis_codes","op":"overlaps","value":["C50.911","D63.0"]}]',
  '{"required":true}','stated','stated','2026-10-01','2026-10-01','active','00000000-0000-0000-0000-00000000a006','{}','TEST','manual','{a,b}');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J0885','{"encounter_type":"emergency"}','2026-11-20'));
\echo 'N08 the same drug, elective, with a cancer diagnosis vs a kidney diagnosis'
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J0885','{"encounter_type":"elective","diagnosis_codes":["D63.0","C50.911"]}','2026-11-20'));
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J0885','{"encounter_type":"elective","diagnosis_codes":["N18.4","D63.1"]}','2026-11-20'));
\echo 'N09 an unregistered fact (free text) is refused'
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439','{"patient_name":"x"}','2026-11-20'));

\echo 'N10 replaying a past answer: a change flag opened later does not alter what was said before'
SELECT now() AS t_before \gset
SELECT pg_sleep(0.05);
INSERT INTO kc_gov.change_flag (source_id, snapshot_id, detected_at, applies_from, state)
VALUES ('uhc-ma-pa-list','00000000-0000-0000-0000-00000000a006', now(), '2026-11-01','open');
SELECT 'as recorded before the flag: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20', :'t_before'));
SELECT 'as recorded now:             ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
UPDATE kc_gov.change_flag SET state = 'reviewed', resolved_at = now();

\echo 'N11 a client gold-card exemption (T5) from the client overlay'
INSERT INTO kc_client.client_authority VALUES (kc_core.eid('authority','CLIENT-GOLDCARD'),'plan_document',kc_core.eid('payer','uhc'),'TEST client gold card letter','T5','TEST gold card status for IV iron');
INSERT INTO kc_client.client_authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, snapshot_id)
VALUES ('00000000-0000-0000-0000-00000000b099',kc_core.eid('authority','CLIENT-GOLDCARD'),'2026','stated','2026-10-01','2026-10-01','00000000-0000-0000-0000-00000000a001');
INSERT INTO kc_client.client_binding (authority_id, program_id, jurisdiction_id, item_scope, valid, reviewed_by)
VALUES (kc_core.eid('authority','CLIENT-GOLDCARD'), kc_core.prog('medicare_advantage'), kc_core.eid('jurisdiction','US'),'all','[2026-10-01,)','{x,y}');
INSERT INTO kc_client.client_rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
VALUES ('exemption','approval', kc_core.eid('code','HCPCS|J1439'), kc_core.eid('authority','CLIENT-GOLDCARD'),'00000000-0000-0000-0000-00000000b099',
  kc_core.prog('medicare_advantage'), kc_core.eid('payer','uhc'),'override','{"exempt":true,"reason":"bundled"}','stated',
  'stated','2026-10-01','2026-10-01','active',NULL,'{}','TEST','manual','{x,y}');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
DELETE FROM kc_client.client_rule;

\echo 'N12 constraint checks (expect three errors)'
\set ON_ERROR_STOP 0
UPDATE kc_core.rule SET reviewed_by = '{curator.a,curator.a}' WHERE rule_id = '00000000-0000-0000-0000-00000000c001';
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, valid_to_lo, valid_to_hi, status, snapshot_id, source_locator, source_quote, extracted_by)
SELECT rule_kind, aspect, subject_id, authority_id, authority_version_id, effect, payload, basis,
  'stated','2026-10-01','2026-10-01','2026-10-01','2026-10-01','draft', snapshot_id, source_locator, source_quote, extracted_by
FROM kc_core.rule WHERE rule_id = '00000000-0000-0000-0000-00000000c001';
INSERT INTO kc_core.authority_binding (authority_id, item_scope, valid, reviewed_by) VALUES (kc_core.eid('authority','UHC-MA-PA'),'all','[2026-01-01,)','{solo}');
\set ON_ERROR_STOP 1
\echo 'N13 an edge to a subclass is accepted by the hierarchy-aware check'
INSERT INTO kc_meta.relation_type VALUES ('cites','authority','terminology_item','n:m','edge','TEST: an authority cites a terminology item');
INSERT INTO kc_core.edge (relation_type, from_id, to_id, valid) VALUES ('cites', kc_core.eid('authority','UHC-MA-PA'), kc_core.eid('code','HCPCS|J1439'), '[2026-08-01,)');
SELECT 'edge to a Code (subclass of terminology item) accepted: ' || count(*) FROM kc_core.edge WHERE relation_type = 'cites';
\echo 'N14 ESRD coordination period: employer plan pays first in month 12, Medicare first in month 34, and the month is asked for when missing'
SELECT kc_api.resolve_coverage_stack('[{"program":"medicare_advantage","payer":"uhc"},{"program":"employer_group","payer":"ghp"}]','{"esrd_coordination_month":12}') -> 'coverages' -> 0 ->> 'program';
SELECT kc_api.resolve_coverage_stack('[{"program":"medicare_advantage","payer":"uhc"},{"program":"employer_group","payer":"ghp"}]','{"esrd_coordination_month":34}') -> 'coverages' -> 0 ->> 'program';
SELECT kc_api.resolve_coverage_stack('[{"program":"medicare_advantage","payer":"uhc"},{"program":"employer_group","payer":"ghp"}]') ->> 'status';
\echo 'N15 integrated D-SNP is flagged'
SELECT kc_api.resolve_coverage_stack('[{"program":"medicare_advantage","payer":"uhc","dsnp_integration":"fide"},{"program":"medicaid_managed_care","payer":"uhc"}]') ->> 'note';
\echo 'N16 the whole graph as one edge list'
SELECT string_agg(relation_type || '=' || n, ', ' ORDER BY relation_type) FROM (SELECT relation_type, count(*) n FROM kc_core.graph_edges GROUP BY relation_type) x;

\echo 'N17 a clinical guidance (CG) rule never decides a payer answer, even marked override'
INSERT INTO kc_core.authority VALUES (kc_core.eid('authority','TEST-CG'),'guideline',NULL,'TEST clinical guideline','CG','TEST anemia guideline');
INSERT INTO kc_core.authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, snapshot_id) VALUES
 ('00000000-0000-0000-0000-00000000b017',kc_core.eid('authority','TEST-CG'),'TEST','stated','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a003');
INSERT INTO kc_core.authority_binding (authority_id, program_id, jurisdiction_id, item_scope, valid, reviewed_by) VALUES
 (kc_core.eid('authority','TEST-CG'), kc_core.prog('medicare_advantage'), kc_core.eid('jurisdiction','US'),'all','[2026-01-01,)','{a,b}');
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('exemption','approval', kc_core.eid('code','HCPCS|J1439'), kc_core.eid('authority','TEST-CG'),'00000000-0000-0000-0000-00000000b017',
  kc_core.prog('medicare_advantage'),'override','{"exempt":true,"reason":"bundled"}','stated','stated','2026-01-01','2026-01-01','active','00000000-0000-0000-0000-00000000a003','{}','TEST','manual','{a,b}');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
SELECT 'layer ranks: ' || string_agg(l || '=' || kc_meta.layer_rank(l), ' ' ORDER BY kc_meta.layer_rank(l)) FROM unnest(ARRAY['CG','T5','T0','T3']) l;

\echo 'N18 an insurer states a 96-hour ceiling under the federal 72: the federal limit applies, with a note'
UPDATE kc_core.rule SET recorded = tstzrange(lower(recorded), now()) WHERE rule_kind = 'decision_timeframe' AND authority_id = kc_core.eid('authority','UHC-MA-PA') AND upper_inf(recorded);
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('decision_timeframe','timeframe', kc_core.eid('value_set','vs:all-drugs'), kc_core.eid('authority','UHC-MA-PA'),'00000000-0000-0000-0000-00000000b006',
  kc_core.prog('medicare_advantage'),kc_core.eid('payer','uhc'),'ceiling','{"standard_hours":96}','stated','stated','2026-10-01','2026-10-01','active','00000000-0000-0000-0000-00000000a006','{}','TEST','manual','{a,b}');
SELECT (kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20') -> 'result' ->> 'value') || ' | notes=' || (kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20') ->> 'notes');

\echo 'N19 a client copy that has never synced is treated as out of date'
CREATE TEMP TABLE sync_backup AS SELECT * FROM kc_client.sync_log;
DELETE FROM kc_client.sync_log;
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
INSERT INTO kc_client.sync_log SELECT * FROM sync_backup;

\echo 'N20 a workflow other than prior authorization added as data only: a TEST denial rule kind, a TEST fact, one rule'
INSERT INTO kc_meta.fact_type VALUES ('carc_codes','code_list',NULL,'TEST: denial reason codes (CARC) on a remittance');
INSERT INTO kc_meta.rule_kind VALUES ('denial_next_action',NULL,'denials','denial_action','additive',NULL,
  '{"type":"object","required":["action"],"properties":{"action":{"type":"string"},"attach":{"type":"array","items":{"type":"string"}}},"additionalProperties":false}',1);
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, conditions, payload, basis,
  date_basis, valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('denial_next_action','denial_action', kc_core.eid('value_set','vs:all-items'), kc_core.eid('authority','UHC-MA-PA'),'00000000-0000-0000-0000-00000000b006',
  kc_core.prog('medicare_advantage'),kc_core.eid('payer','uhc'),'additive','[{"fact":"carc_codes","op":"overlaps","value":["197"]}]',
  '{"action":"TEST request retro authorization, then resubmit","attach":["authorization number"]}','stated',
  'remittance','stated','2026-10-01','2026-10-01','active','00000000-0000-0000-0000-00000000a006','{}','TEST','manual','{a,b}');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','denial_next_action','HCPCS|J1439','{"carc_codes":["197"]}','2026-11-20'));
SELECT kc_api.resolve_rules(:'MA','denial_next_action','HCPCS|J1439','{"carc_codes":["197"]}','2026-11-20') -> 'result' -> 'value';
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','denial_next_action','HCPCS|J1439','{"carc_codes":["16"]}','2026-11-20'));
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','denial_next_action','HCPCS|J1439','{}','2026-11-20'));

\echo 'N21 additive conflict for a non-PA rule kind: two insurer-layer denial rules give different actions -> conflict'
UPDATE kc_meta.rule_kind SET conflict_on = '{action}' WHERE rule_kind = 'denial_next_action';
INSERT INTO kc_core.authority VALUES (kc_core.eid('authority','TEST-UHC-DENIAL'),'payer_policy',kc_core.eid('payer','uhc'),'TEST UHC denial policy','T3','TEST denial handling policy');
INSERT INTO kc_core.authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, snapshot_id) VALUES
 ('00000000-0000-0000-0000-00000000b021',kc_core.eid('authority','TEST-UHC-DENIAL'),'TEST','stated','2026-01-01','2026-01-01','00000000-0000-0000-0000-00000000a001');
INSERT INTO kc_core.authority_binding (authority_id, program_id, jurisdiction_id, item_scope, valid, reviewed_by) VALUES
 (kc_core.eid('authority','TEST-UHC-DENIAL'), kc_core.prog('medicare_advantage'), kc_core.eid('jurisdiction','US'),'all','[2026-01-01,)','{a,b}');
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, conditions, payload, basis,
  date_basis, valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('denial_next_action','denial_action', kc_core.eid('value_set','vs:all-items'), kc_core.eid('authority','TEST-UHC-DENIAL'),'00000000-0000-0000-0000-00000000b021',
  kc_core.prog('medicare_advantage'),kc_core.eid('payer','uhc'),'additive','[{"fact":"carc_codes","op":"overlaps","value":["197"]}]',
  '{"action":"TEST file a formal appeal"}','stated','remittance','stated','2026-01-01','2026-01-01','active','00000000-0000-0000-0000-00000000a001','{}','TEST','manual','{a,b}');
SELECT pg_temp.brief(kc_api.resolve_rules(:'MA','denial_next_action','HCPCS|J1439','{"carc_codes":["197"]}','2026-11-20'));

\echo 'N22 a clinical guidance workflow: CG rules answer guidance questions, with number (lte) and date (lt) conditions and a requirement list'
INSERT INTO kc_meta.fact_type VALUES ('egfr_value','number',NULL,'TEST: latest eGFR, mL/min/1.73m2'),
                                     ('last_hemoglobin_date','date',NULL,'TEST: date of the latest hemoglobin result');
INSERT INTO kc_meta.rule_kind VALUES ('care_gap_check',NULL,'clinical_guidance','care_gap','additive',NULL,
  '{"type":"object","required":["recommendation"],"properties":{"recommendation":{"type":"string"}},"additionalProperties":false}',1);
INSERT INTO kc_core.rule (rule_id, rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, effect, conditions, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('00000000-0000-0000-0000-00000000c022','care_gap_check','care_gap', kc_core.eid('value_set','vs:all-items'), kc_core.eid('authority','TEST-CG'),'00000000-0000-0000-0000-00000000b017',
  kc_core.prog('medicare_advantage'),'additive',
  '[{"fact":"egfr_value","op":"lte","value":45},{"fact":"last_hemoglobin_date","op":"lt","value":"2026-10-21"}]',
  '{"recommendation":"TEST hemoglobin is overdue; order one"}','stated','stated','2026-01-01','2026-01-01','active','00000000-0000-0000-0000-00000000a003','{}','TEST','manual','{a,b}');
INSERT INTO kc_core.rule_requirement (rule_id, group_no, group_logic, data_element_id, test, lookback, when_needed) VALUES
 ('00000000-0000-0000-0000-00000000c022',1,'all',kc_core.eid('data_element','LOINC|718-7'),'{"op":"present"}','1 month','always');
SELECT 'eGFR 45, last Hb 2026-09-01: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','care_gap_check','HCPCS|J0885','{"egfr_value":45,"last_hemoglobin_date":"2026-09-01"}','2026-11-20'));
SELECT 'requirements returned: ' || jsonb_array_length(kc_api.resolve_rules(:'MA','care_gap_check','HCPCS|J0885','{"egfr_value":45,"last_hemoglobin_date":"2026-09-01"}','2026-11-20') -> 'result' -> 'requirements');
SELECT 'eGFR 45.5: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','care_gap_check','HCPCS|J0885','{"egfr_value":45.5,"last_hemoglobin_date":"2026-09-01"}','2026-11-20'));
SELECT 'Hb on 2026-11-01: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','care_gap_check','HCPCS|J0885','{"egfr_value":30,"last_hemoglobin_date":"2026-11-01"}','2026-11-20'));
SELECT 'bad date: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','care_gap_check','HCPCS|J0885','{"egfr_value":30,"last_hemoglobin_date":"Nov 1"}','2026-11-20'));
SELECT 'approval for J1439 still from the insurer: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));

\echo 'N23 dialysis needs no approval under Mississippi Medicaid (real rule, Title 23 Part 216, Rule 1.2(B)); a drug with no rule there is not called "not required"'
INSERT INTO kc_core.source VALUES ('ms-medicaid-216','Mississippi Administrative Code Title 23 Part 216, Dialysis Services','Mississippi Division of Medicaid','state','pdf',
  'https://medicaid.ms.gov/','public','7 days','30 days','100 days', now() - interval '1 day');
INSERT INTO kc_core.source_check VALUES ('ms-medicaid-216', now() - interval '1 day', true);
INSERT INTO kc_core.snapshot (snapshot_id, source_id, retrieved_at, content_sha256, blob_uri, media_type, stated_effective) VALUES
 ('00000000-0000-0000-0000-00000000a007','ms-medicaid-216','2026-11-02 09:00+00',repeat('7',64),'blob://snapshots/ms/title23-part216.pdf','application/pdf','Revised 08/01/2026');
INSERT INTO kc_core.code_system VALUES ('CPT','http://www.ama-assn.org/go/cpt','American Medical Association','client_license','annual');
INSERT INTO kc_core.code (entity_id, code_system_id, code, display, item_type, valid, first_release) VALUES
 (kc_core.eid('code','CPT|90935'),'CPT','90935',NULL,'service','[2026-01-01,)','as loaded for the test');
INSERT INTO kc_core.value_set VALUES (kc_core.eid('value_set','vs:dialysis-services'),'https://kanini.example/fhir/ValueSet/dialysis-services','1','Dialysis services','extensional',NULL,NULL);
INSERT INTO kc_core.value_set_member (value_set_id, code_system_id, code, valid) VALUES (kc_core.eid('value_set','vs:dialysis-services'),'CPT','90935','[2026-01-01,)');
INSERT INTO kc_core.program VALUES (kc_core.eid('program','medicaid_ffs'),'medicaid_ffs','Medicaid fee-for-service');
INSERT INTO kc_core.jurisdiction VALUES (kc_core.eid('jurisdiction','MS'),'state','MS','Mississippi',NULL);
INSERT INTO kc_core.authority VALUES (kc_core.eid('authority','MS-23-216'),'state_manual',NULL,'Miss. Admin. Code Title 23, Part 216, Rule 1.2(B)','T2','Mississippi Medicaid: Dialysis Services');
INSERT INTO kc_core.authority_version (authority_version_id, authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, snapshot_id) VALUES
 ('00000000-0000-0000-0000-00000000b023',kc_core.eid('authority','MS-23-216'),'Revised 08/01/2026','document_date','2026-08-01','2026-08-01','00000000-0000-0000-0000-00000000a007');
INSERT INTO kc_core.authority_binding (authority_id, program_id, jurisdiction_id, item_scope, valid, reviewed_by) VALUES
 (kc_core.eid('authority','MS-23-216'), kc_core.prog('medicaid_ffs'), kc_core.eid('jurisdiction','MS'),'all','[2026-08-01,)','{a,b}');
INSERT INTO kc_core.rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, jurisdiction_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('approval_required','approval', kc_core.eid('value_set','vs:dialysis-services'), kc_core.eid('authority','MS-23-216'),'00000000-0000-0000-0000-00000000b023',
  kc_core.prog('medicaid_ffs'), kc_core.eid('jurisdiction','MS'),'additive','{"required":false}','stated',
  'document_date','2026-08-01','2026-08-01','active','00000000-0000-0000-0000-00000000a007','{"rule":"1.2(B)"}',
  'Prior authorization is not required for dialysis services.','manual','{a,b}');
SELECT 'dialysis (CPT 90935), Mississippi Medicaid: ' || pg_temp.brief(kc_api.resolve_rules('{"program":"medicaid_ffs","state":"MS","payer_position":1}','approval_required','CPT|90935',:'ELECTIVE','2026-11-20'));
SELECT 'cited: ' || (kc_api.resolve_rules('{"program":"medicaid_ffs","state":"MS","payer_position":1}','approval_required','CPT|90935',:'ELECTIVE','2026-11-20') -> 'sources' -> 0 ->> 'quote');
SELECT 'J1439, Mississippi Medicaid:                 ' || pg_temp.brief(kc_api.resolve_rules('{"program":"medicaid_ffs","state":"MS","payer_position":1}','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));

\echo 'N24 dialysis: a TEST insurer exemption for iron given at the dialysis facility as part of the dialysis bundle'
INSERT INTO kc_core.rule (rule_id, rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, conditions, payload, basis, mapping_confidence,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by) VALUES
 ('00000000-0000-0000-0000-00000000c023','exemption','approval', kc_core.eid('concept','iv-iron'), kc_core.eid('authority','UHC-MA-PA'),'00000000-0000-0000-0000-00000000b006',
  kc_core.prog('medicare_advantage'),kc_core.eid('payer','uhc'),'override',
  '[{"fact":"dialysis_status","op":"in","value":["in_center","home"]},{"fact":"place_of_service","op":"in","value":["65","12"]}]',
  '{"exempt":true,"reason":"bundled"}','stated',NULL,'stated','2026-10-01','2026-10-01','active','00000000-0000-0000-0000-00000000a006','{}','TEST','manual','{a,b}');
SELECT 'on in-center dialysis, given at the facility: ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439','{"encounter_type":"elective","dialysis_status":"in_center","place_of_service":"65"}','2026-11-20'));
SELECT 'not on dialysis, given in the office:         ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439','{"encounter_type":"elective","dialysis_status":"none","place_of_service":"11"}','2026-11-20'));
SELECT 'dialysis status not given:                    ' || pg_temp.brief(kc_api.resolve_rules(:'MA','approval_required','HCPCS|J1439',:'ELECTIVE','2026-11-20'));
UPDATE kc_core.rule SET recorded = tstzrange(lower(recorded), now()) WHERE rule_id = '00000000-0000-0000-0000-00000000c023';

\echo 'N25 an override cannot set aside a higher-layer rule from another body: a TEST client (T5) override on the federal deadline -> conflict'
INSERT INTO kc_client.client_rule (rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, effect, payload, basis,
  valid_from_basis, valid_from_lo, valid_from_hi, status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by)
VALUES ('decision_timeframe','timeframe', kc_core.eid('value_set','vs:all-drugs'), kc_core.eid('authority','CLIENT-GOLDCARD'),'00000000-0000-0000-0000-00000000b099',
  kc_core.prog('medicare_advantage'), kc_core.eid('payer','uhc'),'override','{"standard_hours":240}','stated',
  'stated','2026-10-01','2026-10-01','active',NULL,'{}','TEST','manual','{x,y}');
SELECT pg_temp.brief(kc_api.get_obligations(:'MA','HCPCS|J1439','2026-11-20'));
DELETE FROM kc_client.client_rule;

\echo 'N26 documentation form for J0885, initial request: built from L39237, each item with its LOINC code and US Core pre-fill profile'
SELECT f ->> 'status', f ->> 'form_source', f ->> 'next_step', f ->> 'return_route', jsonb_array_length(f -> 'items')
  FROM (SELECT kc_api.get_documentation_form(:'MA','HCPCS|J0885','2026-11-20','{"request_type":"initial"}') f) x;
SELECT string_agg((i ->> 'group') || ':' || (i ->> 'group_logic') || ':' || (i ->> 'code') || ':' || (i -> 'prefill' ->> 'profile'), ', ')
  FROM jsonb_array_elements(kc_api.get_documentation_form(:'MA','HCPCS|J0885','2026-11-20','{"request_type":"initial"}') -> 'items') i;
\echo 'N27 continuation request: only the items needed for continuation (creatinine within a month)'
SELECT string_agg(i ->> 'code', ', ') FROM jsonb_array_elements(kc_api.get_documentation_form(:'MA','HCPCS|J0885','2026-11-20','{"request_type":"continuation"}') -> 'items') i;
\echo 'N28 the insurer asks for more: hemoglobin (known) and a TEST code the pack has no rule for -> known item marked, unknown sent to a person'
SELECT f ->> 'status', f ->> 'actionable', f ->> 'next_step', f -> 'requested_not_in_pack',
       (SELECT string_agg(i ->> 'code', ',') FROM jsonb_array_elements(f -> 'items') i WHERE (i ->> 'requested_by_insurer')::boolean)
  FROM (SELECT kc_api.get_documentation_form(:'MA','HCPCS|J0885','2026-11-20','{"request_type":"initial"}', ARRAY['718-7','TEST-0001']) f) x;
\echo 'N29 the insurer publishes its own questionnaire (TEST): the form points to it, and the rule-built items stay as pre-fill hints'
INSERT INTO kc_core.knowledge_artifact VALUES (kc_core.eid('knowledge_artifact','uhc-iv-iron-q'),'questionnaire','FHIR SDC Questionnaire',
  'https://payer.example/fhir/Questionnaire/TEST-iv-iron','1.0',kc_core.eid('payer','uhc'),'00000000-0000-0000-0000-00000000a006');
UPDATE kc_core.rule SET knowledge_artifact_id = kc_core.eid('knowledge_artifact','uhc-iv-iron-q') WHERE rule_id = '00000000-0000-0000-0000-00000000c001';
SELECT f ->> 'form_source', f -> 'insurer_questionnaire' -> 0 ->> 'canonical_url', f ->> 'return_route'
  FROM (SELECT kc_api.get_documentation_form(:'MA','HCPCS|J1439','2026-11-20',:'ELECTIVE') f) x;
UPDATE kc_core.rule SET knowledge_artifact_id = NULL WHERE rule_id = '00000000-0000-0000-0000-00000000c001';
\echo 'N30 a code with no documentation rule and no questionnaire: no form, sent to a person'
SELECT f ->> 'status', f ->> 'form_source', f ->> 'next_step' FROM (SELECT kc_api.get_documentation_form(:'MA','HCPCS|J1439','2026-11-20',:'ELECTIVE') f) x;

\echo 'N31 when the insurer API goes live, the form for a drug still avoids it (the API excludes drugs) and uses the next live route'
UPDATE kc_core.endpoint SET live = true, valid = '[2026-01-01,)' WHERE interface = 'payer_pa_api' AND payer_id = kc_core.eid('payer','uhc');
SELECT kc_api.get_documentation_form(:'MA','HCPCS|J0885','2026-11-20','{"request_type":"initial"}') ->> 'return_route';
UPDATE kc_core.endpoint SET live = false, valid = '[2027-01-01,)' WHERE interface = 'payer_pa_api' AND payer_id = kc_core.eid('payer','uhc');
