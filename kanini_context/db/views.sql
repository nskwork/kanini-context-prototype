-- Kanini Context: read-only views of what the pack holds, for people rather than agents.
--
-- Loaded after api.sql. Additive only: nothing here changes a table, a function the tools call, or an answer.
-- The views show the release in plain columns (documents, rules in plain words, where each policy applies, the
-- Medicare prior authorization programs checked, codes), and kc_api.explain_order traces one answer step by step
-- by calling the same kc_api.evaluate_order the tools call, so the trace can never disagree with the answer.

-------------------------------------------------------------------------------
-- Plain-words helpers
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.kind_label(p_kind text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_kind
    WHEN 'lcd' THEN 'Medicare coverage policy (LCD)'
    WHEN 'ncd' THEN 'National coverage determination'
    WHEN 'billing_article' THEN 'Medicare billing article'
    WHEN 'payer_list' THEN 'Payer prior authorization list'
    WHEN 'payer_policy' THEN 'Payer drug policy'
    WHEN 'regulation' THEN 'Federal regulation'
    WHEN 'statute' THEN 'Statute'
    WHEN 'pa_program' THEN 'CMS prior authorization program'
    WHEN 'state_rule' THEN 'State rule'
    WHEN 'state_manual' THEN 'State manual'
    WHEN 'plan_document' THEN 'Plan document'
    WHEN 'guideline' THEN 'Guideline'
    ELSE p_kind END $$;

CREATE FUNCTION kc_api.rule_kind_label(p_kind text, p_basis text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_kind = 'approval_required' AND p_basis = 'stated_by_exhaustion' THEN 'Program list checked'
    WHEN p_kind = 'approval_required' THEN 'Prior authorization'
    WHEN p_kind = 'coverage_criteria' THEN 'Coverage criteria'
    WHEN p_kind = 'documentation_requirement' THEN 'Evidence needed'
    WHEN p_kind = 'exemption' THEN 'Exemption'
    WHEN p_kind = 'decision_timeframe' THEN 'Decision deadline'
    WHEN p_kind = 'appeal_window' THEN 'Appeal window'
    WHEN p_kind = 'obligation' THEN 'Obligation'
    ELSE p_kind END $$;

CREATE FUNCTION kc_api.unit_text(p_unit text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_unit WHEN 'mL/min/{1.73_m2}' THEN 'mL/min/1.73 m²' ELSE coalesce(p_unit, '') END $$;

CREATE FUNCTION kc_api.list_text(p_values jsonb, p_join text DEFAULT ', ') RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE jsonb_typeof(p_values)
    WHEN 'array' THEN (SELECT string_agg(replace(v, '_', ' '), p_join ORDER BY o)
                         FROM jsonb_array_elements_text(p_values) WITH ORDINALITY AS t(v, o))
    ELSE replace(p_values #>> '{}', '_', ' ') END $$;

-- one test on one data element, e.g. "Hemoglobin below 10 g/dL"
CREATE FUNCTION kc_api.test_text(p_name text, p_unit text, p_test jsonb, p_lookback interval) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_test ->> 'op'
    WHEN 'lt'  THEN p_name || ' below '    || (p_test ->> 'value') || ' ' || kc_api.unit_text(coalesce(p_test ->> 'unit', p_unit))
    WHEN 'lte' THEN p_name || ' at most '  || (p_test ->> 'value') || ' ' || kc_api.unit_text(coalesce(p_test ->> 'unit', p_unit))
    WHEN 'gt'  THEN p_name || ' above '    || (p_test ->> 'value') || ' ' || kc_api.unit_text(coalesce(p_test ->> 'unit', p_unit))
    WHEN 'gte' THEN p_name || ' '          || (p_test ->> 'value') || ' ' || kc_api.unit_text(coalesce(p_test ->> 'unit', p_unit)) || ' or more'
    WHEN 'present' THEN p_name || CASE WHEN p_lookback IS NULL THEN ' on file'
                                       ELSE ' from the past ' || replace(replace(p_lookback::text, ' mons', ' months'), ' mon', ' month') END
    WHEN 'includes' THEN p_name || ' (' || (p_test ->> 'value') || ')'
    WHEN 'includes_any' THEN p_name || ' (one of ' || kc_api.list_text(p_test -> 'value') || ')'
    ELSE p_name || ' ' || p_test::text END $$;

-- one condition on when a rule applies, e.g. "patient not on dialysis"
CREATE FUNCTION kc_api.condition_text(p_cond jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_cond ->> 'fact' = 'dialysis_status' AND p_cond -> 'value' = '["none"]' THEN 'the patient is not on dialysis'
    WHEN p_cond ->> 'fact' = 'encounter_type' THEN 'the encounter is ' || kc_api.list_text(p_cond -> 'value', ' or ')
    WHEN p_cond ->> 'fact' = 'diagnosis_codes' AND p_cond ->> 'op' = 'prefix_overlaps'
      THEN 'a diagnosis code starts with ' || kc_api.list_text(p_cond -> 'value', ' or ')
    WHEN p_cond ->> 'op' = 'in' THEN replace(p_cond ->> 'fact', '_', ' ') || ' is ' || kc_api.list_text(p_cond -> 'value', ' or ')
    ELSE replace(p_cond ->> 'fact', '_', ' ') || ' ' || (p_cond ->> 'op') || ' ' || kc_api.list_text(p_cond -> 'value') END $$;

CREATE FUNCTION kc_api.conditions_text(p_conds jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT string_agg(kc_api.condition_text(c), ' and ' ORDER BY o)
    FROM jsonb_array_elements(coalesce(p_conds, '[]')) WITH ORDINALITY AS t(c, o) $$;

-- what a rule says, in plain words
CREATE FUNCTION kc_api.rule_summary(p_rule_id uuid) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE r kc_core.rule; v_groups text; v_items text; v_when text;
BEGIN
  SELECT * INTO r FROM kc_core.rule WHERE rule_id = p_rule_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  v_when := kc_api.conditions_text(r.conditions);
  SELECT CASE WHEN count(*) > 1 AND r.rule_kind = 'coverage_criteria'
              THEN 'all of these hold: ' || string_agg(group_no || ') ' || g, '; ' ORDER BY group_no)
              ELSE string_agg(g, '; ' ORDER BY group_no) END INTO v_groups FROM (
    SELECT q.group_no,
           string_agg(kc_api.test_text(d.name, d.unit, q.test, q.lookback),
                      CASE q.group_logic WHEN 'any' THEN ' or ' ELSE ' and ' END ORDER BY d.name) AS g
      FROM kc_core.rule_requirement q JOIN kc_core.data_element d ON d.entity_id = q.data_element_id
     WHERE q.rule_id = p_rule_id GROUP BY q.group_no) x;
  RETURN CASE r.rule_kind
    WHEN 'coverage_criteria' THEN
      'Covered when ' || coalesce(v_groups, 'its criteria are met')
        || CASE WHEN r.payload ->> 'logic' = 'any' THEN ' (any one group is enough)' ELSE '' END
        || CASE WHEN v_when IS NOT NULL THEN '. Applies when ' || v_when ELSE '' END || '.'
    WHEN 'approval_required' THEN
      CASE WHEN r.basis = 'stated_by_exhaustion'
             THEN 'Not on this program''s prior authorization list. ' || coalesce(r.source_locator ->> 'screen_finding', '')
           WHEN (r.payload ->> 'required')::boolean
             THEN 'Prior authorization required' || coalesce(' when ' || v_when, '')
                  || coalesce('. Submit through ' || (r.payload ->> 'submit_via'), '') || '.'
           ELSE 'No prior authorization required' || coalesce(' when ' || v_when, '') || '.' END
    WHEN 'exemption' THEN 'Exempt from prior authorization when ' || coalesce(v_when, 'its conditions are met') || '.'
    WHEN 'documentation_requirement' THEN
      'Evidence needed (' || CASE WHEN r.payload ->> 'logic' = 'any' THEN 'any one of' ELSE 'all of' END || '): '
        || coalesce(v_groups, '') || coalesce('. ' || (r.payload ->> 'applies_when'), '')
        || CASE WHEN v_when IS NOT NULL THEN '. Applies when ' || v_when ELSE '' END || '.'
    WHEN 'decision_timeframe' THEN
      'Decision due within ' || concat_ws(' or ',
         (r.payload ->> 'standard_hours') || ' hours for a standard request',
         (r.payload ->> 'expedited_hours') || ' hours for an expedited request')
        || coalesce(', from ' || (r.payload ->> 'clock_starts'), '') || '.'
    ELSE r.payload::text END;
END $$;

-- what a rule is about: the concept's label, or the code
CREATE FUNCTION kc_api.subject_label(p_entity uuid) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT coalesce(c.label, vs.name, replace(e.natural_key, 'HCPCS|', 'HCPCS '))
    FROM kc_core.entity e
    LEFT JOIN kc_core.concept c ON c.entity_id = e.entity_id
    LEFT JOIN kc_core.value_set vs ON vs.entity_id = e.entity_id
   WHERE e.entity_id = p_entity $$;

-- where an authority applies: "Original Medicare (fee-for-service): Ohio, Kentucky (J15)" and so on
CREATE FUNCTION kc_api.binding_place(p_jurisdiction uuid) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT CASE j.kind
    WHEN 'nation' THEN 'nationwide'
    WHEN 'state'  THEN j.name
    WHEN 'mac_ab' THEN j.name || ' (' || coalesce(j.contractor, '') || ': '
                        || coalesce((SELECT string_agg(s.code, ', ' ORDER BY s.code) FROM kc_core.edge e
                                       JOIN kc_core.jurisdiction s ON s.entity_id = e.from_id
                                      WHERE e.to_id = j.entity_id AND e.relation_type = 'within'
                                        AND upper_inf(e.recorded)), '') || ')'
    ELSE j.name END
    FROM kc_core.jurisdiction j WHERE j.entity_id = p_jurisdiction $$;

-------------------------------------------------------------------------------
-- The documents
-------------------------------------------------------------------------------
CREATE VIEW kc_api.v_sources AS
WITH snap AS (
  SELECT DISTINCT ON (source_id) source_id, snapshot_id, retrieved_at, content_sha256, stated_effective, blob_uri
    FROM kc_core.snapshot ORDER BY source_id, retrieved_at DESC),
checked AS (
  SELECT source_id, max(checked_at) FILTER (WHERE ok) AS last_ok FROM kc_core.source_check GROUP BY source_id),
kinds AS (
  SELECT av.snapshot_id, string_agg(DISTINCT kc_api.kind_label(a.kind), ', ') AS kind
    FROM kc_core.authority_version av JOIN kc_core.authority a ON a.entity_id = av.authority_id
   WHERE upper_inf(av.recorded) GROUP BY av.snapshot_id),
rules AS (
  SELECT snapshot_id, count(*) AS n FROM kc_core.rule
   WHERE status = 'active' AND upper_inf(recorded) GROUP BY snapshot_id)
SELECT s.source_id,
       s.name                      AS title,
       s.publisher,
       coalesce(k.kind, CASE
         WHEN s.source_id LIKE '%-codes' THEN 'CMS program code list'
         WHEN s.source_id = 'cms-pa-initiatives-index' THEN 'CMS index of prior authorization programs'
         WHEN s.source_id LIKE 'cms-%' THEN 'CMS prior authorization program'
         ELSE initcap(replace(s.source_class, '_', ' ')) END) AS kind,
       sn.stated_effective         AS edition,
       s.url,
       sn.retrieved_at             AS saved_at,
       left(sn.content_sha256, 12) AS fingerprint,
       sn.content_sha256           AS sha256,
       c.last_ok                   AS last_checked,
       CASE WHEN sn.snapshot_id IS NULL THEN 'not saved'
            WHEN c.last_ok IS NULL THEN 'never checked'
            WHEN now() - c.last_ok > s.stale_after THEN 'stale'
            WHEN now() - c.last_ok > s.aging_after THEN 'aging'
            ELSE 'current' END     AS freshness,
       s.aging_after,
       s.stale_after,
       coalesce(r.n, 0)            AS rules,
       CASE
         WHEN sn.snapshot_id IS NULL THEN 'Not saved in this copy of the pack'
         WHEN coalesce(r.n, 0) > 0 THEN coalesce(r.n, 0) || ' rule' || CASE WHEN r.n = 1 THEN '' ELSE 's' END
         WHEN s.source_id = 'cms-asc-pa-demo-codes' THEN 'Code list for the CMS ASC demonstration screen'
         WHEN s.source_id = 'cms-dmepos-ppa-codes' THEN 'Code list for the CMS DMEPOS probationary screen'
         WHEN s.source_id = 'cms-pa-initiatives-index' THEN 'Confirms the set of CMS prior authorization programs'
         ELSE 'Supporting document' END AS used_for
  FROM kc_core.source s
  LEFT JOIN snap sn ON sn.source_id = s.source_id
  LEFT JOIN checked c ON c.source_id = s.source_id
  LEFT JOIN kinds k ON k.snapshot_id = sn.snapshot_id
  LEFT JOIN rules r ON r.snapshot_id = sn.snapshot_id;

-------------------------------------------------------------------------------
-- The rules, in plain words
-------------------------------------------------------------------------------
CREATE VIEW kc_api.v_rules AS
SELECT r.rule_key,
       kc_api.rule_ref(r.rule_key, r.valid::text) AS rule_ref,
       kc_api.rule_kind_label(r.rule_kind, r.basis)      AS kind,
       kc_api.subject_label(r.subject_id)                AS about,
       kc_api.rule_summary(r.rule_id)                    AS says,
       a.citation                                        AS source,
       kc_api.kind_label(a.kind)                         AS source_kind,
       (r.source_locator ->> 'page')::int                AS page,
       r.source_quote                                    AS quote,
       sn.stated_effective                               AS edition,
       lower(r.valid)                                    AS in_force_from,
       upper(r.valid)                                    AS in_force_until,
       coalesce(
         (SELECT string_agg(DISTINCT pr.name, ', ') FROM kc_core.program pr WHERE pr.entity_id = r.program_id),
         (SELECT string_agg(DISTINCT pr.name, ', ') FROM kc_core.authority_binding b
            JOIN kc_core.program pr ON pr.entity_id = b.program_id
           WHERE b.authority_id = r.authority_id AND upper_inf(b.recorded)))   AS programs,
       coalesce(
         (SELECT kc_api.binding_place(r.jurisdiction_id) WHERE r.jurisdiction_id IS NOT NULL),
         (SELECT string_agg(DISTINCT kc_api.binding_place(b.jurisdiction_id), '; ') FROM kc_core.authority_binding b
           WHERE b.authority_id = r.authority_id AND upper_inf(b.recorded) AND b.jurisdiction_id IS NOT NULL),
         (SELECT string_agg(DISTINCT e.natural_key, ', ') FROM kc_core.authority_binding b
            JOIN kc_core.entity e ON e.entity_id = b.plan_id
           WHERE b.authority_id = r.authority_id AND upper_inf(b.recorded)))   AS applies_in,
       r.basis,
       array_to_string(r.reviewed_by, ', ')              AS approved_by,
       r.reviewed_at                                     AS approved_at,
       r.review_exception                                AS approval_note,
       r.release_id                                      AS first_release
  FROM kc_core.rule r
  JOIN kc_core.authority a ON a.entity_id = r.authority_id
  JOIN kc_core.snapshot sn ON sn.snapshot_id = r.snapshot_id
 WHERE r.status = 'active' AND upper_inf(r.recorded);

-------------------------------------------------------------------------------
-- Where each policy applies
-------------------------------------------------------------------------------
CREATE VIEW kc_api.v_bindings AS
SELECT a.citation                            AS policy,
       kc_api.kind_label(a.kind)             AS kind,
       pr.name                               AS program,
       pe.natural_key                        AS plan,
       j.code                                AS place_code,
       kc_api.binding_place(b.jurisdiction_id) AS place,
       j.kind                                AS place_kind,
       CASE b.item_scope WHEN 'all' THEN 'all items' WHEN 'drugs' THEN 'drugs'
                         ELSE 'items and services' END AS items,
       lower(b.valid)                        AS from_date,
       upper(b.valid)                        AS until_date
  FROM kc_core.authority_binding b
  JOIN kc_core.authority a ON a.entity_id = b.authority_id
  LEFT JOIN kc_core.program pr ON pr.entity_id = b.program_id
  LEFT JOIN kc_core.entity pe ON pe.entity_id = b.plan_id
  LEFT JOIN kc_core.jurisdiction j ON j.entity_id = b.jurisdiction_id
 WHERE upper_inf(b.recorded);

-------------------------------------------------------------------------------
-- The Medicare prior authorization programs, and what each screen found
-------------------------------------------------------------------------------
CREATE VIEW kc_api.v_program_screens AS
SELECT a.citation                                   AS program,
       a.title,
       a.closed_list,
       (SELECT string_agg(DISTINCT CASE j.kind WHEN 'nation' THEN 'nationwide' ELSE j.code END
                          || CASE WHEN upper(b.valid) IS NOT NULL THEN ' (to ' || (upper(b.valid) - 1) || ')'
                                  ELSE ' (from ' || lower(b.valid) || ')' END, ', ')
          FROM kc_core.authority_binding b JOIN kc_core.jurisdiction j ON j.entity_id = b.jurisdiction_id
         WHERE b.authority_id = a.entity_id AND upper_inf(b.recorded))  AS binds,
       s.rule_key IS NOT NULL                       AS screened,
       kc_api.subject_label(s.subject_id)           AS drug,
       CASE WHEN s.rule_key IS NULL THEN 'Not screened: the program''s list is not saved in this copy of the pack'
            ELSE s.source_locator ->> 'screen_finding' END AS finding,
       (s.source_locator ->> 'page')::int           AS page,
       s.source_quote                               AS quote,
       s.rule_key
  FROM kc_core.authority a
  LEFT JOIN kc_core.rule s ON s.authority_id = a.entity_id AND s.basis = 'stated_by_exhaustion'
                          AND s.status = 'active' AND upper_inf(s.recorded)
 WHERE a.kind = 'pa_program';

-------------------------------------------------------------------------------
-- Codes and code lists the rules use
-------------------------------------------------------------------------------
CREATE VIEW kc_api.v_codes AS
SELECT c.label                       AS concept,
       m.code_system_id              AS system,
       m.code,
       (SELECT k.display FROM kc_core.code k WHERE k.code_system_id = m.code_system_id AND k.code = m.code
          AND upper_inf(k.recorded) ORDER BY lower(k.valid) DESC LIMIT 1) AS display,
       m.map_kind                    AS relation,
       lower(m.valid)                AS from_date,
       upper(m.valid)                AS until_date
  FROM kc_core.concept_map m JOIN kc_core.concept c ON c.entity_id = m.concept_id
 WHERE upper_inf(m.recorded)
UNION ALL
SELECT vs.name, vm.code_system_id, vm.code,
       (SELECT k.display FROM kc_core.code k WHERE k.code_system_id = vm.code_system_id AND k.code = vm.code
          AND upper_inf(k.recorded) ORDER BY lower(k.valid) DESC LIMIT 1),
       'member', lower(vm.valid), upper(vm.valid)
  FROM kc_core.value_set_member vm JOIN kc_core.value_set vs ON vs.entity_id = vm.value_set_id
 WHERE upper_inf(vm.recorded);

-------------------------------------------------------------------------------
-- One answer, step by step
-------------------------------------------------------------------------------
-- Returns the answer exactly as kc_api.evaluate_order gives it (key "answer"), plus:
--   coverage  the plan, program and state, with the state's name and its Medicare A/B contractor
--   policies  the reviewed rules in force on the date (kc_api.applicable_rules)
--   programs  every Medicare prior authorization program bound to this coverage on the date, with its list
--             finding, page and quote, and whether the answer used it
CREATE FUNCTION kc_api.explain_order(p_plan text, p_state text, p_code text, p_as_of date, p_facts jsonb DEFAULT '{}',
                                     p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE v_ans jsonb; v_cov jsonb; v_place jsonb; v_pol jsonb; v_prog jsonb := '[]'; v_type text;
BEGIN
  v_ans := kc_api.evaluate_order(p_plan, p_state, p_code, p_as_of, p_facts, p_recorded_as_of);
  v_cov := coalesce(v_ans -> 'coverage', kc_api.coverage_for(p_plan, p_state));
  SELECT jsonb_build_object('state_name', s.name,
           'contractor', (SELECT jsonb_build_object('code', m.code, 'name', m.name, 'contractor', m.contractor)
                            FROM kc_core.edge e JOIN kc_core.jurisdiction m ON m.entity_id = e.to_id
                           WHERE e.from_id = s.entity_id AND e.relation_type = 'within' AND m.kind = 'mac_ab'
                             AND e.valid @> p_as_of AND e.recorded @> p_recorded_as_of LIMIT 1))
    INTO v_place
    FROM kc_core.jurisdiction s WHERE s.kind = 'state' AND s.code = upper(p_state);
  v_pol := kc_api.applicable_rules(p_plan, p_state, p_code, p_as_of, p_recorded_as_of);
  IF v_cov IS NOT NULL AND v_ans ->> 'code' IS NOT NULL THEN
    SELECT k.item_type INTO v_type FROM kc_core.code k
     WHERE k.code_system_id || '|' || k.code = v_ans ->> 'code' AND upper_inf(k.recorded) LIMIT 1;
    SELECT coalesce(jsonb_agg(jsonb_build_object(
             'program', a.citation, 'title', a.title, 'closed_list', a.closed_list,
             'screened', s.rule_id IS NOT NULL,
             'finding', s.source_locator ->> 'screen_finding',
             'page', (s.source_locator ->> 'page')::int,
             'quote', s.source_quote,
             'rule_ref', CASE WHEN s.rule_id IS NOT NULL THEN kc_api.rule_ref(s.rule_key, s.valid::text) END,
             'used', CASE WHEN s.rule_id IS NOT NULL THEN
                       (v_ans -> 'rule_refs') ? kc_api.rule_ref(s.rule_key, s.valid::text) ELSE false END)
             ORDER BY a.citation), '[]')
      INTO v_prog
      FROM jsonb_array_elements(kc_api.bound_pa_programs(v_cov, coalesce(v_type, 'drug'), p_as_of, p_recorded_as_of)) bp
      JOIN kc_core.authority a ON a.entity_id = (bp ->> 'authority_id')::uuid
      LEFT JOIN kc_core.rule s ON s.authority_id = a.entity_id AND s.basis = 'stated_by_exhaustion'
                              AND s.status = 'active' AND s.recorded @> p_recorded_as_of AND s.valid @> p_as_of;
  END IF;
  RETURN jsonb_build_object(
    'answer', v_ans,
    'coverage', coalesce(v_cov, '{}') || coalesce(v_place, '{}'),
    'policies', coalesce(v_pol -> 'rules', '[]'),
    'programs', v_prog);
END $$;
