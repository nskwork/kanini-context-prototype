-- Read path behind the agent tools (proposal Appendix B.1). Deterministic: the same release,
-- the same question and the same "as recorded" moment always return the same answer.
-- Nothing here reads the wall clock directly: every freshness check is measured against
-- p_recorded_as_of (default: now), so a past answer can be reproduced exactly for an appeal.
-- The MCP server (Python) writes one kc_client.tool_audit row per call; these functions only read.
--
-- Prototype 1.0 changes from SQL v0.9:
--   check_facts       number facts are range-checked ({"min","max"} in fact_type.allowed); dates must be real dates
--   eval_conditions   adds the 'prefix_overlaps' operator (a code list against code prefixes, e.g. ICD-10-CM C)
--   candidate_rules   step 4 of the resolver as its own function, shared by resolve_rules and applicable_rules
--   resolve_rules     closed-list completeness check (a "not required" from closed lists needs every bound
--                     prior authorization program to have been screened); rule_key and edition on each winner;
--                     each requirement item carries its element id and the fact that holds its value
--   get_documentation_form   items are joined on the element id rather than the code

-------------------------------------------------------------------------------
-- Facts: only registered facts are accepted (no free text, no patient identifiers)
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.check_facts(p_facts jsonb) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT string_agg(k, ', ' ORDER BY k) FROM jsonb_object_keys(coalesce(p_facts,'{}')) k
   WHERE NOT EXISTS (SELECT 1 FROM kc_meta.fact_type f WHERE f.fact = k)
      OR EXISTS (SELECT 1 FROM kc_meta.fact_type f WHERE f.fact = k AND (
             (f.value_type = 'enum'      AND NOT (f.allowed @> jsonb_build_array(p_facts -> k)))
          OR (f.value_type IN ('integer','number') AND jsonb_typeof(p_facts -> k) <> 'number')
          OR (f.value_type IN ('integer','number') AND jsonb_typeof(p_facts -> k) = 'number' AND f.allowed IS NOT NULL
              AND (CASE WHEN jsonb_typeof(p_facts -> k) = 'number'
                        THEN (f.allowed ? 'min' AND (p_facts ->> k)::numeric < (f.allowed ->> 'min')::numeric)
                          OR (f.allowed ? 'max' AND (p_facts ->> k)::numeric > (f.allowed ->> 'max')::numeric) END))
          OR (f.value_type = 'boolean'   AND jsonb_typeof(p_facts -> k) <> 'boolean')
          OR (f.value_type = 'date'      AND (jsonb_typeof(p_facts -> k) <> 'string'
                                              OR (p_facts ->> k) !~ '^\d{4}-\d{2}-\d{2}$'
                                              OR NOT pg_input_is_valid(p_facts ->> k, 'date')))
          OR (f.value_type = 'code_list' AND jsonb_typeof(p_facts -> k) <> 'array')))
$$;

-------------------------------------------------------------------------------
-- Conditions, in three-valued logic: false wins over unknown (false AND unknown = false)
-------------------------------------------------------------------------------
-- Compares a fact with a rule value: numbers as numbers, ISO dates (YYYY-MM-DD) as dates. NULL when not comparable,
-- which makes the condition false rather than guessing.
CREATE FUNCTION kc_api.cmp_fact(a jsonb, b jsonb) RETURNS int LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN jsonb_typeof(a) = 'number' AND jsonb_typeof(b) = 'number'
      THEN sign((a #>> '{}')::numeric - (b #>> '{}')::numeric)::int
    WHEN jsonb_typeof(a) = 'string' AND jsonb_typeof(b) = 'string'
         AND (a #>> '{}') ~ '^\d{4}-\d{2}-\d{2}$' AND (b #>> '{}') ~ '^\d{4}-\d{2}-\d{2}$'
      THEN sign((a #>> '{}')::date - (b #>> '{}')::date)::int
  END $$;

CREATE FUNCTION kc_api.eval_conditions(p_conds jsonb, p_facts jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE c jsonb; v jsonb; r boolean; anyfalse boolean := false; missing text[] := '{}';
BEGIN
  FOR c IN SELECT * FROM jsonb_array_elements(coalesce(p_conds,'[]')) LOOP
    v := p_facts -> (c ->> 'fact');
    IF v IS NULL THEN missing := array_append(missing, c ->> 'fact'); CONTINUE; END IF;
    r := CASE c ->> 'op'
      WHEN 'in'       THEN (c -> 'value') @> jsonb_build_array(v)
      WHEN 'not_in'   THEN NOT (c -> 'value') @> jsonb_build_array(v)
      WHEN 'eq'       THEN v = c -> 'value'
      WHEN 'neq'      THEN v <> c -> 'value'
      WHEN 'overlaps' THEN jsonb_typeof(v) = 'array'               -- e.g. any diagnosis in a list
                           AND EXISTS (SELECT 1 FROM jsonb_array_elements(v) a WHERE (c -> 'value') @> jsonb_build_array(a))
      WHEN 'prefix_overlaps' THEN jsonb_typeof(v) = 'array'        -- e.g. any diagnosis starting with C (neoplasms)
                           AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(v) a, jsonb_array_elements_text(c -> 'value') p
                                        WHERE a LIKE p || '%')
      WHEN 'lt'  THEN kc_api.cmp_fact(v, c -> 'value') <  0
      WHEN 'lte' THEN kc_api.cmp_fact(v, c -> 'value') <= 0
      WHEN 'gt'  THEN kc_api.cmp_fact(v, c -> 'value') >  0
      WHEN 'gte' THEN kc_api.cmp_fact(v, c -> 'value') >= 0
      ELSE false END;
    IF r IS NOT TRUE THEN anyfalse := true; END IF;
  END LOOP;
  RETURN jsonb_build_object('state', CASE WHEN anyfalse THEN 'false'
                                          WHEN cardinality(missing) > 0 THEN 'unknown' ELSE 'true' END,
                            'missing', CASE WHEN anyfalse THEN '[]'::jsonb ELSE to_jsonb(missing) END);
END $$;

-------------------------------------------------------------------------------
-- validate_codes
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.validate_codes(p_codes text[], p_as_of date,
                                     p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT jsonb_agg(jsonb_build_object(
           'code', q.code,
           'result', CASE WHEN c.code IS NULL THEN 'invalid'
                          WHEN c.valid @> p_as_of THEN 'valid' ELSE 'expired' END,
           'valid', c.valid::text,
           'replaced_by', to_jsonb(c.replaced_by)) ORDER BY q.ord)
  FROM unnest(p_codes) WITH ORDINALITY AS q(code, ord)
  LEFT JOIN LATERAL (
     SELECT * FROM kc_core.code k
      WHERE k.code_system_id = split_part(q.code, '|', 1) AND k.code = split_part(q.code, '|', 2)
        AND k.recorded @> p_recorded_as_of
      ORDER BY (k.valid @> p_as_of) DESC, upper(k.valid) DESC NULLS FIRST LIMIT 1) c ON true
$$;

-------------------------------------------------------------------------------
-- resolve_coverage_stack: which coverage pays first.
-- Covers Medicare + Medicaid (Medicaid pays last), the 30-month ESRD coordination period with an
-- employer group plan, and integrated D-SNPs (one combined determination). Other cases say so.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.resolve_coverage_stack(p_coverages jsonb, p_facts jsonb DEFAULT '{}') RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE has_ghp boolean; has_mcare boolean; month int; out jsonb; integrated boolean;
BEGIN
  has_ghp   := EXISTS (SELECT 1 FROM jsonb_array_elements(p_coverages) c WHERE c ->> 'program' = 'employer_group');
  has_mcare := EXISTS (SELECT 1 FROM jsonb_array_elements(p_coverages) c WHERE c ->> 'program' LIKE 'medicare%');
  integrated := EXISTS (SELECT 1 FROM jsonb_array_elements(p_coverages) c WHERE c ->> 'dsnp_integration' IN ('fide','hide'));
  IF has_ghp AND has_mcare THEN
    month := (p_facts ->> 'esrd_coordination_month')::int;
    IF month IS NULL THEN
      RETURN jsonb_build_object('status','needs_facts','missing_facts', jsonb_build_array('esrd_coordination_month'),
        'reason','employer plan and Medicare: order depends on the ESRD 30-month coordination period');
    END IF;
  END IF;
  SELECT jsonb_agg(x.cov || jsonb_build_object('payer_position', x.pos, 'order_basis', x.basis) ORDER BY x.pos) INTO out
  FROM (
    SELECT cov,
           row_number() OVER (ORDER BY
             CASE WHEN cov ->> 'program' = 'employer_group' AND month IS NOT NULL AND month <= 30 THEN 0
                  WHEN cov ->> 'program' LIKE 'medicare%' THEN 1
                  WHEN cov ->> 'program' IN ('employer_group','exchange') THEN 2 ELSE 3 END, ord) AS pos,
           CASE WHEN cov ->> 'program' = 'employer_group' AND month <= 30 THEN 'esrd_coordination_period'
                WHEN cov ->> 'program' LIKE 'medicaid%' AND has_mcare THEN 'medicaid_pays_last'
                WHEN has_ghp AND has_mcare AND month > 30 THEN 'after_esrd_coordination_period'
                ELSE 'single_or_primary' END AS basis
      FROM jsonb_array_elements(p_coverages) WITH ORDINALITY AS t(cov, ord)) x;
  RETURN jsonb_build_object('status','resolved','coverages', out, 'integrated_dsnp', integrated,
     'note', CASE WHEN integrated THEN 'integrated D-SNP: Medicare and Medicaid approval and appeals are decided together' END);
END $$;

-------------------------------------------------------------------------------
-- candidate_rules: resolver steps 2 to 4 (spec section 7.2). Every approved rule that could answer this
-- question: about the code or a group it belongs to, possibly in force on the date (or on any date, when
-- p_any_date is true), within the coverage's scope, and from an authority bound to that coverage.
-- Returns one JSON object per rule (without its condition result), ordered by layer, or NULL.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.candidate_rules(
  p_coverage jsonb, p_rule_kind text, p_code text, p_as_of date,
  p_recorded_as_of timestamptz DEFAULT now(), p_any_date boolean DEFAULT false
) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
  c record; k record;
  v_prog uuid; v_payer uuid; v_plan uuid; v_pos int; v_subjects uuid[]; v_jur uuid[]; v_lapse interval;
  v_cands jsonb;
BEGIN
  SELECT * INTO k FROM kc_meta.rule_kind WHERE rule_kind = p_rule_kind;
  SELECT * INTO c FROM kc_core.code
   WHERE code_system_id = split_part(p_code, '|', 1) AND code = split_part(p_code, '|', 2)
     AND recorded @> p_recorded_as_of
   ORDER BY (valid @> p_as_of) DESC LIMIT 1;
  IF NOT FOUND OR k.rule_kind IS NULL THEN RETURN NULL; END IF;

  v_prog  := (SELECT entity_id FROM kc_core.program WHERE kind = p_coverage ->> 'program');
  v_payer := (SELECT entity_id FROM kc_core.entity WHERE entity_type = 'payer' AND natural_key = p_coverage ->> 'payer');
  v_plan  := (SELECT entity_id FROM kc_core.entity WHERE entity_type = 'plan'  AND natural_key = p_coverage ->> 'plan');
  v_pos   := coalesce((p_coverage ->> 'payer_position')::int, 1);

  -- Everything the code belongs to on that date: the code, its value sets, its concepts.
  SELECT array_agg(DISTINCT s) INTO v_subjects FROM (
    SELECT c.entity_id AS s
    UNION SELECT m.value_set_id FROM kc_core.value_set_member m
      WHERE m.code_system_id = c.code_system_id AND m.code = c.code AND (p_any_date OR m.valid @> p_as_of) AND m.recorded @> p_recorded_as_of
    UNION SELECT vs.entity_id FROM kc_core.value_set vs
      WHERE vs.definition = 'intensional' AND (vs.filter ->> 'item_type' = c.item_type OR vs.filter ? 'any')
    UNION SELECT cm.concept_id FROM kc_core.concept_map cm
      WHERE cm.code_system_id = c.code_system_id AND cm.code = c.code AND (p_any_date OR cm.valid @> p_as_of) AND cm.recorded @> p_recorded_as_of
  ) x;

  -- Places that apply: the nation, the state, and every region it sits within (any depth).
  WITH RECURSIVE up(j) AS (
      SELECT entity_id FROM kc_core.jurisdiction WHERE code IN ('US', p_coverage ->> 'state')
      UNION SELECT e.to_id FROM kc_core.edge e JOIN up ON e.from_id = up.j
       WHERE e.relation_type = 'within' AND e.valid @> p_as_of AND e.recorded @> p_recorded_as_of)
  SELECT array_agg(j) INTO v_jur FROM up;

  -- How long the client copy had gone without a sync, as of the moment asked about.
  SELECT p_recorded_as_of - max(synced_at) INTO v_lapse FROM kc_client.sync_log WHERE synced_at <= p_recorded_as_of;
  -- A copy that has never synced is treated as out of date, not as current.
  v_lapse := coalesce(v_lapse, interval '9999 days');

  SELECT jsonb_agg(jsonb_build_object(
           'rule_id', r.rule_id, 'rule_key', r.rule_key, 'rule_kind', r.rule_kind, 'effect', r.effect, 'payload', r.payload,
           'conditions', r.conditions,
           'basis', r.basis, 'mapping_confidence', r.mapping_confidence, 'not_stated', to_jsonb(r.not_stated),
           'layer', a.layer, 'lrank', kc_meta.layer_rank(a.layer), 'issuer', a.issuer_id, 'citation', a.citation, 'authority', a.title,
           'authority_id', a.entity_id, 'authority_kind', a.kind, 'closed_list', a.closed_list, 'version', av.version_label,
           'confirmed_valid', r.valid::text, 'uncertain', NOT (r.valid @> p_as_of),
           'snapshot_id', r.snapshot_id, 'locator', r.source_locator, 'quote', r.source_quote,
           'stale', coalesce(p_recorded_as_of - fr.last_ok > src.stale_after, false),
           'aging', coalesce(p_recorded_as_of - fr.last_ok > src.aging_after, false),
           'sync_aging', coalesce(v_lapse > sl.aging_after, false),
           'out_of_sync', v_lapse IS NOT NULL AND v_lapse > sl.stop_after,
           'pending_change', EXISTS (SELECT 1 FROM kc_gov.change_flag f WHERE f.source_id = src.source_id
                                      AND f.detected_at <= p_recorded_as_of
                                      AND (f.resolved_at IS NULL OR f.resolved_at > p_recorded_as_of)
                                      AND (f.applies_from IS NULL OR f.applies_from <= p_as_of)),
           'requirements', (SELECT coalesce(jsonb_agg(g ORDER BY g ->> 'group'), '[]') FROM (
                               SELECT jsonb_build_object('group', rq.group_no, 'logic', min(rq.group_logic),
                                        'items', jsonb_agg(jsonb_build_object('element', de.name,
                                           'element_id', de.entity_id, 'fact', de.fact, 'unit', de.unit,
                                           'system', de.code_system_id, 'loinc', de.code,
                                           'test', rq.test, 'lookback', rq.lookback::text, 'when', rq.when_needed)
                                           ORDER BY de.code, rq.test::text, de.name)) AS g
                                 FROM kc_core.rule_requirement rq JOIN kc_core.data_element de ON de.entity_id = rq.data_element_id
                                WHERE rq.rule_id = r.rule_id GROUP BY rq.group_no) gg))
         ORDER BY kc_meta.layer_rank(a.layer), r.rule_id)
    INTO v_cands
    FROM kc_api.all_rule r
    JOIN kc_api.all_authority a          ON a.entity_id = r.authority_id
    JOIN kc_api.all_authority_version av ON av.authority_version_id = r.authority_version_id
    LEFT JOIN kc_core.snapshot sn       ON sn.snapshot_id = r.snapshot_id
    LEFT JOIN kc_core.source src        ON src.source_id = sn.source_id
    LEFT JOIN LATERAL (SELECT max(checked_at) AS last_ok FROM kc_core.source_check sc
                        WHERE sc.source_id = src.source_id AND sc.ok AND sc.checked_at <= p_recorded_as_of) fr ON true
    LEFT JOIN kc_client.sync_limit sl   ON sl.source_class = src.source_class
   WHERE r.status = 'active' AND r.recorded @> p_recorded_as_of
     AND (a.layer <> 'CG' OR k.use_case = 'clinical_guidance')   -- guidance never decides a payer rule, but answers guidance questions
     AND r.aspect = k.aspect
     AND r.subject_id = ANY (v_subjects)
     AND (p_any_date OR r.valid_possible @> p_as_of)
     AND (r.program_id IS NULL OR r.program_id = v_prog)
     AND (r.payer_id IS NULL OR r.payer_id = v_payer)
     AND (r.plan_id IS NULL OR r.plan_id = v_plan)
     AND (r.jurisdiction_id IS NULL OR r.jurisdiction_id = ANY (v_jur))
     AND EXISTS (SELECT 1 FROM kc_api.all_binding b
                  WHERE b.authority_id = r.authority_id AND b.recorded @> p_recorded_as_of AND (p_any_date OR b.valid @> p_as_of)
                    AND (b.program_id IS NULL OR b.program_id = v_prog)
                    AND (b.plan_id IS NULL OR b.plan_id = v_plan)
                    AND (b.jurisdiction_id IS NULL OR b.jurisdiction_id = ANY (v_jur))
                    AND (v_pos = 1 OR b.applies_when_secondary)
                    AND (b.item_scope = 'all' OR b.item_scope =
                         CASE WHEN c.item_type = 'drug' THEN 'drugs' ELSE 'items_and_services' END));
  RETURN v_cands;
END $$;

-- The prior authorization programs bound to a coverage on a date (authority kind 'pa_program').
-- A "not required" built from closed lists holds only when every one of these was screened.
CREATE FUNCTION kc_api.bound_pa_programs(p_coverage jsonb, p_item_type text, p_as_of date,
                                         p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  WITH RECURSIVE up(j) AS (
      SELECT entity_id FROM kc_core.jurisdiction WHERE code IN ('US', p_coverage ->> 'state')
      UNION SELECT e.to_id FROM kc_core.edge e JOIN up ON e.from_id = up.j
       WHERE e.relation_type = 'within' AND e.valid @> p_as_of AND e.recorded @> p_recorded_as_of)
  SELECT coalesce(jsonb_agg(DISTINCT jsonb_build_object('authority_id', a.entity_id, 'citation', a.citation,
                                                        'closed_list', a.closed_list)), '[]')
    FROM kc_api.all_authority a JOIN kc_api.all_binding b ON b.authority_id = a.entity_id
   WHERE a.kind = 'pa_program' AND b.recorded @> p_recorded_as_of AND b.valid @> p_as_of
     AND (b.program_id IS NULL OR b.program_id = (SELECT entity_id FROM kc_core.program WHERE kind = p_coverage ->> 'program'))
     AND (b.jurisdiction_id IS NULL OR b.jurisdiction_id IN (SELECT j FROM up))
     AND (b.item_scope = 'all' OR b.item_scope = CASE WHEN p_item_type = 'drug' THEN 'drugs' ELSE 'items_and_services' END)
$$;

-------------------------------------------------------------------------------
-- resolve_rules: what the rules say about one code, for one coverage, on one date
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.resolve_rules(
  p_coverage jsonb,                 -- {"program":"medicare_advantage","payer":"uhc","plan":"H1234-001","state":"TN","payer_position":1}
  p_rule_kind text,                 -- 'approval_required', 'documentation_requirement', 'decision_timeframe', ...
  p_code text,                      -- 'HCPCS|J1439'
  p_facts jsonb,                    -- registered facts only, e.g. {"encounter_type":"elective","urgency":"standard"}
  p_as_of date,                     -- the date the question is about (date of service, by default)
  p_recorded_as_of timestamptz DEFAULT now()  -- what the pack knew at this moment
) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
  c record; k record; m text; v_bad text;
  v_payer uuid; v_pos int;
  v_cands jsonb; v_live jsonb; v_win jsonb; v_missing jsonb; v_lay text;
  v_status text := 'resolved'; v_reason text; v_result jsonb; v_notes text[] := '{}';
  v_reasons text[] := '{}'; v_actionable boolean; v_next text;
  v_cap numeric; v_floor numeric; v_val numeric; v_payload jsonb; v_kept jsonb; v_unscreened jsonb;
BEGIN
  SELECT * INTO k FROM kc_meta.rule_kind WHERE rule_kind = p_rule_kind;
  IF NOT FOUND THEN RETURN jsonb_build_object('status','invalid_input','reason','unknown_rule_kind','actionable',false); END IF;
  v_bad := kc_api.check_facts(p_facts);
  IF v_bad IS NOT NULL THEN
    RETURN jsonb_build_object('status','invalid_input','reason','unregistered_or_invalid_fact: ' || v_bad,'actionable',false);
  END IF;

  -- 1. The code must exist and be valid on the date.
  SELECT * INTO c FROM kc_core.code
   WHERE code_system_id = split_part(p_code, '|', 1) AND code = split_part(p_code, '|', 2)
     AND recorded @> p_recorded_as_of
   ORDER BY (valid @> p_as_of) DESC LIMIT 1;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('status','invalid_input','reason','unknown_code','actionable',false);
  ELSIF NOT c.valid @> p_as_of THEN
    RETURN jsonb_build_object('status','unavailable','reason','code_not_valid_on_date','actionable',false,
                              'replaced_by', to_jsonb(c.replaced_by),'next_step','validate_codes');
  END IF;

  v_payer := (SELECT entity_id FROM kc_core.entity WHERE entity_type = 'payer' AND natural_key = p_coverage ->> 'payer');
  v_pos   := coalesce((p_coverage ->> 'payer_position')::int, 1);

  -- 2 to 4. Candidate rules (shared release plus the client's own T5 layer), with each rule's conditions tested.
  SELECT jsonb_agg(x || jsonb_build_object('cond', kc_api.eval_conditions(x -> 'conditions', coalesce(p_facts,'{}')))
                   ORDER BY (x ->> 'lrank')::int, x ->> 'rule_id')
    INTO v_cands
    FROM jsonb_array_elements(kc_api.candidate_rules(p_coverage, p_rule_kind, p_code, p_as_of, p_recorded_as_of)) x;

  IF v_cands IS NULL THEN
    v_status := 'no_rule_found'; v_reason := 'no_reviewed_rule_in_scope';
  ELSE
    -- 5. Conditions. An override that already holds at layer L makes missing facts below L irrelevant.
    SELECT x ->> 'layer' INTO v_lay FROM jsonb_array_elements(v_cands) x
     WHERE x ->> 'effect' = 'override' AND x -> 'cond' ->> 'state' = 'true'
     ORDER BY (x ->> 'lrank')::int LIMIT 1;
    SELECT jsonb_agg(DISTINCT mm) INTO v_missing
      FROM jsonb_array_elements(v_cands) x, jsonb_array_elements_text(x -> 'cond' -> 'missing') mm
     WHERE x -> 'cond' ->> 'state' = 'unknown' AND (v_lay IS NULL OR (x ->> 'lrank')::int <= kc_meta.layer_rank(v_lay));
    IF v_missing IS NOT NULL THEN
      RETURN jsonb_build_object('status','needs_facts','reason','rule_depends_on_missing_fact',
               'missing_facts', v_missing,'actionable',false,'next_step','supply_facts',
               'pack_release', current_setting('kc.release', true));
    END IF;
    SELECT jsonb_agg(x ORDER BY (x ->> 'lrank')::int, x ->> 'rule_id') INTO v_live
      FROM jsonb_array_elements(v_cands) x WHERE x -> 'cond' ->> 'state' = 'true';
    IF v_live IS NULL THEN v_status := 'no_rule_found'; v_reason := 'no_reviewed_rule_in_scope'; END IF;
  END IF;

  -- 6. Combine by effect.
  IF v_status = 'resolved' THEN
    IF v_lay IS NOT NULL THEN
      -- override: the highest layer that holds wins; disagreement inside that layer is a conflict
      SELECT jsonb_agg(x ORDER BY x ->> 'rule_id') INTO v_win FROM jsonb_array_elements(v_live) x
       WHERE x ->> 'effect' = 'override' AND x ->> 'layer' = v_lay;
      IF (SELECT count(DISTINCT x -> 'payload') FROM jsonb_array_elements(v_win) x) > 1 THEN
        v_status := 'conflict'; v_reason := 'same_layer_disagreement';
      END IF;
      -- What an override may set aside: rules at its own layer and below, and the insurer or plan rules (T3, T4)
      -- of the body that issued it (an insurer's own gold card sets aside that insurer's list). Any other rule
      -- from a higher layer still stands, and the answer goes to a person rather than letting the lower layer win.
      SELECT jsonb_agg(x ORDER BY (x ->> 'lrank')::int, x ->> 'rule_id') INTO v_kept FROM jsonb_array_elements(v_live) x
       WHERE (x ->> 'lrank')::int < kc_meta.layer_rank(v_lay)
         AND NOT (x ->> 'layer' IN ('T3','T4') AND EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) w
                                                            WHERE w ->> 'issuer' = x ->> 'issuer'));
      IF v_kept IS NOT NULL THEN
        v_status := 'conflict'; v_reason := 'override_below_higher_layer_rule';
        v_win := v_win || v_kept;
      ELSIF jsonb_array_length(v_live) > jsonb_array_length(v_win) THEN
        v_notes := array_append(v_notes, 'rules set aside by an override at layer ' || v_lay);
      END IF;
    ELSIF k.measures IS NOT NULL THEN
      -- ceiling / floor: per measure, the tightest ceiling (min) and the tightest floor (max) bound the
      -- value; the most specific layer's stated value is used inside those bounds. Other payload keys
      -- are merged from general to specific, so fields like clock_starts are kept.
      v_win := v_live;
      SELECT jsonb_object_agg(e.key, e.value) INTO v_payload FROM (
        SELECT DISTINCT ON (kv.key) kv.key, kv.value FROM jsonb_array_elements(v_live) x, jsonb_each(x -> 'payload') kv
         ORDER BY kv.key, (x ->> 'lrank')::int DESC) e;
      FOREACH m IN ARRAY k.measures LOOP
        SELECT min((x -> 'payload' ->> m)::numeric) INTO v_cap   FROM jsonb_array_elements(v_live) x
         WHERE x ->> 'effect' = 'ceiling' AND x -> 'payload' ? m;
        SELECT max((x -> 'payload' ->> m)::numeric) INTO v_floor FROM jsonb_array_elements(v_live) x
         WHERE x ->> 'effect' = 'floor' AND x -> 'payload' ? m;
        SELECT (x -> 'payload' ->> m)::numeric INTO v_val FROM jsonb_array_elements(v_live) x
         WHERE x -> 'payload' ? m   -- the most specific stated value, whatever its effect; bounds are applied next
         ORDER BY (x ->> 'lrank')::int DESC, x ->> 'rule_id' LIMIT 1;
        IF v_cap IS NOT NULL AND v_floor IS NOT NULL AND v_floor > v_cap THEN
          v_status := 'conflict'; v_reason := 'floor_above_ceiling_for_' || m;
        END IF;
        v_val := coalesce(v_val, v_cap, v_floor);
        IF v_cap IS NOT NULL AND v_val > v_cap THEN
          v_notes := array_append(v_notes, m || ': a lower layer states ' || v_val || ', above the limit of ' || v_cap || '; the limit applies');
          v_val := v_cap;
        END IF;
        IF v_floor IS NOT NULL AND v_val < v_floor THEN
          v_notes := array_append(v_notes, m || ': a lower layer states ' || v_val || ', below the minimum of ' || v_floor || '; the minimum applies');
          v_val := v_floor;
        END IF;
        IF v_val IS NOT NULL THEN v_payload := v_payload || jsonb_build_object(m, v_val); END IF;
      END LOOP;
    ELSE
      -- additive: every applicable rule counts. Two rules in one layer that disagree on a field the rule kind
      -- lists in conflict_on (for example 'required', or a denial rule's 'action') are a conflict.
      v_win := v_live;
      IF k.conflict_on IS NOT NULL AND jsonb_array_length(v_live) > 1
         AND EXISTS (SELECT 1 FROM jsonb_array_elements(v_live) a, jsonb_array_elements(v_live) b, unnest(k.conflict_on) f
                  WHERE a ->> 'layer' = b ->> 'layer' AND a -> 'payload' ? f AND b -> 'payload' ? f
                    AND a -> 'payload' -> f <> b -> 'payload' -> f) THEN
        v_status := 'conflict'; v_reason := 'same_layer_disagreement';
      END IF;
    END IF;

    v_result := jsonb_build_object(
      'aspect', k.aspect,
      'value', CASE
        WHEN k.aspect = 'approval' THEN
          CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x -> 'payload' ->> 'exempt')::boolean)
               THEN to_jsonb('exempt'::text)
               WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x -> 'payload' ->> 'required')::boolean)
               THEN to_jsonb('required'::text) ELSE to_jsonb('not_required'::text) END
        WHEN k.measures IS NOT NULL THEN v_payload
        WHEN k.aspect = 'documentation' THEN to_jsonb('all_rules_apply'::text)
        WHEN k.aspect = 'coverage' THEN to_jsonb('criteria_apply'::text)
        -- any other rule kind (other workflows): every applicable rule's payload, so new kinds need no resolver change
        ELSE (SELECT jsonb_agg(jsonb_build_object('rule_id', x -> 'rule_id', 'layer', x -> 'layer', 'payload', x -> 'payload')
                               ORDER BY (x ->> 'lrank')::int, x ->> 'rule_id') FROM jsonb_array_elements(v_win) x) END,
      -- documentation: one entry per rule, each with its own logic and groups, so nothing is flattened
      'requirements', CASE WHEN k.aspect = 'documentation'
                             OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE jsonb_array_length(coalesce(x -> 'requirements', '[]')) > 0) THEN
          (SELECT coalesce(jsonb_agg(jsonb_build_object('rule_id', x -> 'rule_id', 'citation', x -> 'citation',
                    'logic', coalesce(x -> 'payload' -> 'logic', '"all"'), 'groups', x -> 'requirements')
                    ORDER BY (x ->> 'lrank')::int), '[]') FROM jsonb_array_elements(v_win) x)
        ELSE '[]'::jsonb END);

    -- 6b. A "not required" that rests on closed lists holds only if every prior authorization program bound to
    --     this coverage was screened for this item. One unscreened program makes it "no rule found", never a "no".
    IF v_status = 'resolved' AND k.aspect = 'approval' AND v_result ->> 'value' = 'not_required'
       AND EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE x ->> 'basis' = 'closed_list') THEN
      SELECT jsonb_agg(p -> 'citation' ORDER BY p ->> 'citation') INTO v_unscreened
        FROM jsonb_array_elements(kc_api.bound_pa_programs(p_coverage, c.item_type, p_as_of, p_recorded_as_of)) p
       WHERE NOT (p ->> 'closed_list')::boolean
          OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x
                          WHERE x ->> 'authority_id' = p ->> 'authority_id' AND x ->> 'basis' = 'closed_list');
      IF v_unscreened IS NOT NULL THEN
        v_status := 'no_rule_found'; v_reason := 'pa_program_not_screened';
        v_notes := array_append(v_notes, 'not screened against: ' || (SELECT string_agg(t, '; ') FROM jsonb_array_elements_text(v_unscreened) t));
      END IF;
    END IF;

    -- 7. Honest status: uncertain dates, unreviewed changes, stale sources and a lapsed copy block action.
    IF v_status = 'resolved' THEN
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'uncertain')::boolean) THEN
        v_status := 'unavailable'; v_reason := 'uncertain_date';
      ELSIF EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'pending_change')::boolean) THEN
        v_status := 'unavailable'; v_reason := 'pending_review';
      ELSIF EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'stale')::boolean) THEN
        v_status := 'unavailable'; v_reason := 'stale_source';
      ELSIF EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'out_of_sync')::boolean) THEN
        v_status := 'unavailable'; v_reason := 'out_of_sync';
      END IF;
    END IF;
  END IF;

  -- 8. The safe-to-act flag and the reasons when it is false. A closed-list "not required" counts as stated.
  IF v_status = 'resolved' THEN
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE x ->> 'basis' = 'interpreted') THEN
      v_reasons := array_append(v_reasons, 'basis_interpreted'::text); END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'aging')::boolean) THEN
      v_reasons := array_append(v_reasons, 'source_aging'::text); END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'sync_aging')::boolean) THEN
      v_reasons := array_append(v_reasons, 'sync_aging'::text); END IF;
  ELSE
    v_reasons := array_append(v_reasons, v_status);
  END IF;
  v_actionable := v_status = 'resolved' AND cardinality(v_reasons) = 0;

  -- 9. Next step when the agent cannot act alone. For prior authorization: the first working route to this
  -- insurer. Other workflows route to a person until they register their own next steps.
  IF NOT v_actionable THEN
    IF v_status = 'conflict' OR k.use_case NOT IN ('prior_auth','core') THEN v_next := 'human_review';
    ELSE
      SELECT CASE e.interface WHEN 'payer_pa_api' THEN 'payer_pa_api' WHEN 'x12_278' THEN 'clearinghouse_278'
                              WHEN 'clearinghouse' THEN 'clearinghouse_278' WHEN 'portal' THEN 'payer_portal'
                              WHEN 'fax' THEN 'fax' ELSE 'human_review' END
        INTO v_next
        FROM kc_core.endpoint e
       WHERE e.payer_id = v_payer AND e.live AND e.valid @> p_as_of
         AND NOT (c.item_type = 'drug' AND e.excludes_drugs)   -- go-live dates come from the endpoint's valid range
       ORDER BY e.preference LIMIT 1;
      v_next := coalesce(v_next, 'human_review');
    END IF;
  END IF;

  RETURN jsonb_build_object(
    'code', p_code, 'rule_kind', p_rule_kind, 'as_of', p_as_of, 'payer_position', v_pos,
    'status', v_status, 'reason', v_reason,
    'actionable', v_actionable, 'actionable_reasons', to_jsonb(v_reasons),
    'result', v_result, 'notes', to_jsonb(v_notes),
    'winning_rules', (SELECT jsonb_agg(jsonb_build_object('rule_id', x -> 'rule_id', 'rule_key', x -> 'rule_key',
                        'version', x -> 'version', 'layer', x -> 'layer',
                        'rule_kind', x -> 'rule_kind', 'effect', x -> 'effect', 'payload', x -> 'payload',
                        'basis', x -> 'basis', 'mapping_confidence', x -> 'mapping_confidence',
                        'confirmed_valid', x -> 'confirmed_valid', 'not_stated', x -> 'not_stated'))
                      FROM jsonb_array_elements(v_win) x),
    'sources', (SELECT jsonb_agg(jsonb_build_object('citation', x -> 'citation', 'authority', x -> 'authority',
                  'version', x -> 'version', 'layer', x -> 'layer', 'snapshot_id', x -> 'snapshot_id',
                  'locator', x -> 'locator', 'quote', x -> 'quote'))
                FROM jsonb_array_elements(v_win) x),
    -- freshness: stale (source past its stale limit), out_of_sync (client copy past its stop limit),
    -- aging (past a first limit, source or sync), or current
    'freshness', CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'stale')::boolean) THEN 'stale'
                      WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'out_of_sync')::boolean) THEN 'out_of_sync'
                      WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_win) x WHERE (x ->> 'aging')::boolean OR (x ->> 'sync_aging')::boolean)
                      THEN 'aging' ELSE 'current' END,
    'candidates_considered', coalesce(jsonb_array_length(v_cands), 0),
    'pack_release', current_setting('kc.release', true),
    'recorded_as_of', p_recorded_as_of,
    'next_step', v_next);
END $$;

-- get_obligations: deadlines, with both the standard and the expedited clock.
CREATE FUNCTION kc_api.get_obligations(p_coverage jsonb, p_code text, p_as_of date, p_facts jsonb DEFAULT '{}',
                                      p_recorded_as_of timestamptz DEFAULT now(),
                                      p_rule_kind text DEFAULT 'decision_timeframe') RETURNS jsonb
LANGUAGE sql STABLE AS $$ SELECT kc_api.resolve_rules(p_coverage, p_rule_kind, p_code, p_facts, p_as_of, p_recorded_as_of) $$;

-- get_payer_capabilities: routes to an insurer in preference order, and whether each works on the date
CREATE FUNCTION kc_api.get_payer_capabilities(p_payer text, p_as_of date, p_item_type text DEFAULT NULL) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT coalesce(jsonb_agg(jsonb_build_object(
           'interface', e.interface, 'preference', e.preference, 'version', e.version,
           'live', e.live AND e.valid @> p_as_of,
           'usable_for_item', NOT (p_item_type = 'drug' AND e.excludes_drugs),
           'available_from', lower(e.valid), 'last_verified', e.verified_at) ORDER BY e.preference), '[]')
  FROM kc_core.endpoint e JOIN kc_core.entity p ON p.entity_id = e.payer_id
  WHERE p.entity_type = 'payer' AND p.natural_key = p_payer
$$;

-- map_reason_code: meaning and category of a denial reason code on a date
CREATE FUNCTION kc_api.map_reason_code(p_code_set text, p_code text, p_as_of date) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT coalesce((SELECT jsonb_build_object('code_set', r.code_set, 'code', r.code, 'description', r.description,
                     'category', r.category, 'result', 'valid', 'valid', r.valid::text)
                   FROM kc_core.reason_code r
                   WHERE r.code_set = p_code_set AND r.code = p_code AND r.valid @> p_as_of AND upper_inf(r.recorded)),
                  jsonb_build_object('code_set', p_code_set, 'code', p_code, 'result', 'unknown'))
$$;

-- get_documentation_form: what to send when an insurer asks for documentation, or asks for more after a request.
-- Returns the insurer's own questionnaire when one is on file, and always a form built from the reviewed
-- documentation rules: each item with its LOINC code and the US Core profile used to pre-fill it from the EHR.
-- p_requested holds the LOINC codes an insurer asked for in a request for more information. Codes the pack has
-- no reviewed rule for are listed separately and sent to a person.
CREATE FUNCTION kc_api.get_documentation_form(
  p_coverage jsonb, p_code text, p_as_of date, p_facts jsonb DEFAULT '{}',
  p_requested text[] DEFAULT NULL, p_recorded_as_of timestamptz DEFAULT now()
) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_doc jsonb; v_appr jsonb; v_items jsonb; v_q jsonb; v_extra jsonb; v_rt text; v_payer uuid; v_route text;
  v_status text; v_next text; v_actionable boolean;
BEGIN
  v_doc  := kc_api.resolve_rules(p_coverage, 'documentation_requirement', p_code, p_facts, p_as_of, p_recorded_as_of);
  IF v_doc ->> 'status' IN ('invalid_input','needs_facts') THEN RETURN v_doc; END IF;
  v_appr := kc_api.resolve_rules(p_coverage, 'approval_required', p_code, p_facts, p_as_of, p_recorded_as_of);
  v_rt   := p_facts ->> 'request_type';
  v_payer := (SELECT entity_id FROM kc_core.entity WHERE entity_type = 'payer' AND natural_key = p_coverage ->> 'payer');

  -- Items from every winning documentation rule, filtered to this kind of request (initial or continuation).
  SELECT jsonb_agg(jsonb_build_object(
           'rule_citation', rq ->> 'citation', 'rule_logic', rq -> 'logic',
           'group', (g ->> 'group')::int, 'group_logic', g ->> 'logic',
           'element', it ->> 'element', 'code_system', de.code_system_id, 'code', de.code,
           'value_type', de.value_type, 'unit', de.unit,
           'test', it -> 'test', 'lookback', it ->> 'lookback', 'when_needed', it ->> 'when',
           -- how the agent reads the item from the EHR through FHIR: a document, a diagnosis, or an observation
           'prefill', jsonb_build_object(
               'fhir_resource', CASE WHEN de.value_type = 'attachment' THEN 'DocumentReference'
                                     WHEN de.code_system_id = 'ICD10CM' THEN 'Condition' ELSE 'Observation' END,
               'profile', de.us_core_profile, 'code', coalesce(cs.canonical_uri || '|', '') || de.code),
           'requested_by_insurer', coalesce(de.code = ANY (p_requested), false))
           ORDER BY rq ->> 'citation', (g ->> 'group')::int, de.code)
    INTO v_items
    FROM jsonb_array_elements(coalesce(v_doc -> 'result' -> 'requirements', '[]')) rq,
         jsonb_array_elements(rq -> 'groups') g,
         jsonb_array_elements(g -> 'items') it
    JOIN kc_core.data_element de ON de.entity_id = (it ->> 'element_id')::uuid
    LEFT JOIN kc_core.code_system cs ON cs.code_system_id = de.code_system_id
   WHERE v_rt IS NULL OR it ->> 'when' IN ('both','always', v_rt);

  -- The insurer's own questionnaire, if a winning rule points to one (relation documented_by).
  SELECT jsonb_agg(DISTINCT jsonb_build_object('canonical_url', ka.canonical_url, 'version', ka.version, 'format', ka.format))
    INTO v_q
    FROM kc_api.all_rule r JOIN kc_core.knowledge_artifact ka ON ka.entity_id = r.knowledge_artifact_id
   WHERE r.rule_id::text IN (SELECT w ->> 'rule_id' FROM jsonb_array_elements(coalesce(v_doc -> 'winning_rules','[]') ||
                                                                               coalesce(v_appr -> 'winning_rules','[]')) w);

  -- Codes the insurer asked for that no reviewed rule covers.
  SELECT jsonb_agg(c ORDER BY c) INTO v_extra FROM unnest(p_requested) c
   WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(v_items,'[]')) i WHERE i ->> 'code' = c);

  -- How to send it back: the insurer's questionnaire service if it is live, otherwise the first live route.
  SELECT CASE e.interface WHEN 'dtr' THEN 'payer_dtr' WHEN 'pas' THEN 'payer_pas' WHEN 'payer_pa_api' THEN 'payer_pa_api'
                          WHEN 'x12_278' THEN 'clearinghouse_275' WHEN 'clearinghouse' THEN 'clearinghouse_275'
                          WHEN 'portal' THEN 'payer_portal_upload' WHEN 'fax' THEN 'fax' ELSE 'human_review' END
    INTO v_route
    FROM kc_core.endpoint e
   WHERE e.payer_id = v_payer AND e.live AND e.valid @> p_as_of
     AND e.interface IN ('dtr','pas','payer_pa_api','x12_278','clearinghouse','portal','fax')
     -- a route that excludes drugs (the federal prior authorization API rule) is never picked for a drug
     AND NOT (e.excludes_drugs AND coalesce((SELECT k.item_type = 'drug' FROM kc_core.code k
                                               WHERE k.code_system_id = split_part(p_code,'|',1) AND k.code = split_part(p_code,'|',2)
                                                 AND k.recorded @> p_recorded_as_of
                                               ORDER BY (k.valid @> p_as_of) DESC LIMIT 1), false))
   ORDER BY (e.interface = 'dtr') DESC, e.preference LIMIT 1;

  v_status := CASE WHEN v_items IS NULL AND v_q IS NULL THEN 'no_rule_found' ELSE v_doc ->> 'status' END;
  v_actionable := v_status = 'resolved' AND coalesce((v_doc ->> 'actionable')::boolean, false) AND v_extra IS NULL;
  v_next := CASE WHEN v_extra IS NOT NULL OR v_status <> 'resolved' THEN 'human_review'
                 ELSE coalesce(v_route, 'human_review') END;

  RETURN jsonb_build_object(
    'code', p_code, 'as_of', p_as_of, 'status', v_status,
    'reason', CASE WHEN v_status = 'no_rule_found' THEN 'no_reviewed_documentation_rule' ELSE v_doc ->> 'reason' END,
    'actionable', v_actionable,
    'form_source', CASE WHEN v_q IS NOT NULL THEN 'insurer_questionnaire'
                        WHEN v_items IS NOT NULL THEN 'built_from_rules' ELSE 'none' END,
    'insurer_questionnaire', v_q,
    'items', coalesce(v_items, '[]'),
    'requested_not_in_pack', coalesce(v_extra, '[]'),
    'return_route', v_route,
    'sources', v_doc -> 'sources', 'freshness', v_doc -> 'freshness',
    'pack_release', current_setting('kc.release', true), 'recorded_as_of', p_recorded_as_of,
    'next_step', v_next);
END $$;
