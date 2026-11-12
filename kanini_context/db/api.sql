-- Kanini Context prototype: the agent-facing functions. Run after resolver.sql.
-- The MCP server calls only these. Each answers from the reviewed pack, never from free text:
--   kc_api.coverage_for         a plan key and state as a coverage the resolver understands
--   kc_api.evaluate_requirements one rule's criteria checked against registered facts
--   kc_api.fact_errors          what is wrong with the facts passed, field by field
--   kc_api.item_codes           the codes behind a HCPCS code or a service concept key
--   kc_api.coverage_gap         why no coverage criteria apply, when the pack holds some for the item
--   kc_api.applicable_rules     which reviewed rules apply to an order, and the facts they need
--   kc_api.evaluate_order       one decision packet: prior authorization, coverage criteria, documentation,
--                               decision deadline, route, rule references, sources, freshness, safe to act
--   kc_api.policy_cite          the source text behind one rule reference
--   kc_api.policy_search        rule references whose source text or citation matches a phrase
-- A rule reference is rule_key@edition, the edition being the date the rule's edition takes effect,
-- e.g. kc:rule/esa-ckd-nd/L39237@2026-09-17.

-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.coverage_for(p_plan text, p_state text) RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object('plan', e.natural_key, 'program', pr.kind, 'payer', pe.natural_key, 'state', upper(p_state),
                            'plan_name', p.plan_type)
    FROM kc_core.plan p
    JOIN kc_core.entity e  ON e.entity_id = p.entity_id
    JOIN kc_core.program pr ON pr.entity_id = p.program_id
    JOIN kc_core.entity pe ON pe.entity_id = p.payer_id
   WHERE e.natural_key = p_plan
     AND EXISTS (SELECT 1 FROM kc_core.jurisdiction j WHERE j.kind = 'state' AND j.code = upper(p_state)) $$;

CREATE FUNCTION kc_api.rule_ref(p_rule_key text, p_valid text) RETURNS text LANGUAGE sql IMMUTABLE AS
  $$ SELECT p_rule_key || '@' || lower(p_valid::daterange)::text $$;

-------------------------------------------------------------------------------
-- evaluate_requirements: one rule's groups (as resolve_rules returns them) against the facts.
-- Item: met | not_met | unknown (fact missing) | not_applicable (initial-only item on a continuation, and the reverse).
-- Group 'all': not_met if any item is not met, else unknown if any is unknown, else met.
-- Group 'any': met if any item is met, else unknown if any is unknown, else not_met. The rule combines groups the same way.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.evaluate_requirements(p_rule jsonb, p_facts jsonb, p_as_of date) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  g jsonb; it jsonb; v_req text := coalesce(p_facts ->> 'request_type', 'initial');
  v_items jsonb; v_groups jsonb := '[]'; v_state text; v_gstate text; v_val jsonb; v_t jsonb; v_op text;
  v_missing text[] := '{}'; v_gmissing text[]; v_greasons text[]; v_reasons text[] := '{}'; v_states text[]; v_gstates text[] := '{}'; v_detail text;
  v_sym text; v_logic text := coalesce(p_rule ->> 'logic', 'all'); v_status text;
  v_met text[] := '{}'; v_gmet text[]; v_obtain text[] := '{}'; v_gnames text[]; v_repeat text[] := '{}'; v_grepeat text[];
BEGIN
  FOR g IN SELECT * FROM jsonb_array_elements(coalesce(p_rule -> 'groups', '[]')) LOOP
    v_items := '[]'; v_states := '{}'; v_gmissing := '{}'; v_greasons := '{}'; v_gmet := '{}'; v_gnames := '{}'; v_grepeat := '{}';
    FOR it IN SELECT * FROM jsonb_array_elements(g -> 'items') LOOP
      v_t := it -> 'test'; v_op := v_t ->> 'op'; v_detail := NULL;
      IF (it ->> 'when' = 'initial' AND v_req = 'continuation') OR (it ->> 'when' = 'continuation' AND v_req = 'initial') THEN
        v_state := 'not_applicable';
      ELSIF it ->> 'fact' IS NULL THEN
        v_state := 'unknown'; v_detail := 'document needed: ' || (it ->> 'element'); v_gnames := array_append(v_gnames, it ->> 'element');
      ELSE
        v_val := p_facts -> (it ->> 'fact');
        IF v_val IS NULL OR v_val = 'null'::jsonb THEN
          v_state := 'unknown'; v_gmissing := array_append(v_gmissing, it ->> 'fact'); v_gnames := array_append(v_gnames, it ->> 'element');
          v_detail := (it ->> 'element') || ' not supplied';
        ELSIF v_op IN ('lt','lte','gt','gte') THEN
          v_sym := CASE v_op WHEN 'lt' THEN '<' WHEN 'lte' THEN '<=' WHEN 'gt' THEN '>' ELSE '>=' END;
          v_state := CASE WHEN CASE v_op WHEN 'lt'  THEN (v_val #>> '{}')::numeric <  (v_t ->> 'value')::numeric
                                         WHEN 'lte' THEN (v_val #>> '{}')::numeric <= (v_t ->> 'value')::numeric
                                         WHEN 'gt'  THEN (v_val #>> '{}')::numeric >  (v_t ->> 'value')::numeric
                                         ELSE            (v_val #>> '{}')::numeric >= (v_t ->> 'value')::numeric END
                          THEN 'met' ELSE 'not_met' END;
          v_detail := format('%s %s %s %s %s', it ->> 'element', v_val #>> '{}',
                             CASE v_state WHEN 'met' THEN 'is' ELSE 'is not' END, v_sym, v_t ->> 'value');
        ELSIF v_op = 'present' THEN
          IF it ->> 'lookback' IS NOT NULL THEN
            v_state := CASE WHEN (v_val #>> '{}')::date <= p_as_of
                             AND (v_val #>> '{}')::date >= p_as_of - (it ->> 'lookback')::interval THEN 'met' ELSE 'not_met' END;
            v_detail := format('%s %s %s within %s before %s', it ->> 'element', v_val #>> '{}',
                               CASE v_state WHEN 'met' THEN 'is' ELSE 'is not' END, regexp_replace(it ->> 'lookback', 'mon(s?)$', 'month\1'), p_as_of);
          ELSE
            v_state := 'met'; v_detail := (it ->> 'element') || ' supplied';
          END IF;
        ELSIF v_op = 'includes' THEN
          v_state := CASE WHEN v_val ? (v_t ->> 'value') THEN 'met' ELSE 'not_met' END;
          v_detail := format('diagnosis %s %s on the order', v_t ->> 'value', CASE v_state WHEN 'met' THEN 'is' ELSE 'is not' END);
        ELSIF v_op = 'includes_any' THEN
          v_state := CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_t -> 'value') x WHERE v_val ? x)
                          THEN 'met' ELSE 'not_met' END;
          v_detail := format('%s of the listed diagnoses (%s) is on the order', CASE v_state WHEN 'met' THEN 'one' ELSE 'none' END,
                             (SELECT string_agg(x, ', ') FROM jsonb_array_elements_text(v_t -> 'value') x));
        ELSE
          v_state := 'unknown'; v_detail := 'unsupported test ' || v_op;
        END IF;
      END IF;
      IF v_state = 'not_met' THEN v_greasons := array_append(v_greasons, v_detail); END IF;
      IF v_state = 'met' THEN v_gmet := array_append(v_gmet, v_detail); END IF;
      IF v_state = 'not_met' AND it ->> 'lookback' IS NOT NULL THEN
        v_grepeat := array_append(v_grepeat, regexp_replace(it ->> 'element', '^Date of most recent ', '')); END IF;
      v_states := array_append(v_states, v_state);
      v_items := v_items || jsonb_build_object('element', it ->> 'element', 'fact', it ->> 'fact', 'status', v_state, 'detail', v_detail);
    END LOOP;
    v_states := array_remove(v_states, 'not_applicable');
    v_gstate := CASE
      WHEN cardinality(v_states) = 0 THEN 'met'
      WHEN g ->> 'logic' = 'any' THEN CASE WHEN 'met' = ANY (v_states) THEN 'met'
                                           WHEN 'unknown' = ANY (v_states) THEN 'unknown' ELSE 'not_met' END
      ELSE CASE WHEN 'not_met' = ANY (v_states) THEN 'not_met' WHEN 'unknown' = ANY (v_states) THEN 'unknown' ELSE 'met' END END;
    v_gstates := array_append(v_gstates, v_gstate);
    IF v_gstate = 'unknown' THEN
      v_missing := v_missing || v_gmissing;
      v_obtain := array_append(v_obtain, array_to_string(v_gnames, CASE WHEN g ->> 'logic' = 'any' THEN ' or ' ELSE ' and ' END));
    END IF;
    IF v_gstate = 'not_met' THEN v_reasons := v_reasons || v_greasons; v_repeat := v_repeat || v_grepeat; END IF;
    IF v_gstate = 'met' THEN v_met := v_met || v_gmet; END IF;
    v_groups := v_groups || jsonb_build_object('group', g -> 'group', 'logic', g -> 'logic', 'status', v_gstate, 'items', v_items);
  END LOOP;
  v_status := CASE
    WHEN cardinality(v_gstates) = 0 THEN 'met'
    WHEN v_logic = 'any' THEN CASE WHEN 'met' = ANY (v_gstates) THEN 'met' WHEN 'unknown' = ANY (v_gstates) THEN 'unknown' ELSE 'not_met' END
    ELSE CASE WHEN 'not_met' = ANY (v_gstates) THEN 'not_met' WHEN 'unknown' = ANY (v_gstates) THEN 'unknown' ELSE 'met' END END;
  RETURN jsonb_build_object('status', v_status,
    'missing_facts', CASE WHEN v_status = 'unknown' THEN (SELECT coalesce(jsonb_agg(DISTINCT m), '[]') FROM unnest(v_missing) m)
                          ELSE '[]'::jsonb END,
    'not_met', to_jsonb(v_reasons), 'met', to_jsonb(v_met),
    'to_obtain', CASE WHEN v_status = 'unknown' THEN to_jsonb(v_obtain) ELSE '[]'::jsonb END,
    'to_repeat', CASE WHEN v_status = 'not_met' THEN to_jsonb(v_repeat) ELSE '[]'::jsonb END,
    'groups', v_groups);
END $$;

-------------------------------------------------------------------------------
-- Pack coverage of the date asked about: past the release's last covered date, or before any loaded
-- edition, the pack says so instead of answering from an edition that may not apply.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.pack_date_check(p_as_of date) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN p_as_of > (SELECT covers_through FROM kc_gov.release WHERE release_id = current_setting('kc.release', true))
              THEN 'pack_not_current_for_date' END $$;

-------------------------------------------------------------------------------
-- What is wrong with the facts, field by field: an unexpected fact, a value out of its plausible range,
-- or a value of the wrong kind. NULL when every fact is acceptable.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.fact_errors(p_facts jsonb) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT string_agg(CASE
           WHEN f.fact IS NULL THEN 'unexpected fact: ' || k
           WHEN f.value_type IN ('integer','number') AND jsonb_typeof(p_facts -> k) = 'number' THEN k || ' out of range'
           WHEN f.value_type = 'enum' THEN k || ' must be one of ' || (SELECT string_agg(x, ', ') FROM jsonb_array_elements_text(f.allowed) x)
           WHEN f.value_type = 'date' THEN k || ' must be YYYY-MM-DD'
           WHEN f.value_type = 'code_list' THEN k || ' must be a list of codes'
           ELSE k || ' has the wrong type' END, '; ' ORDER BY k)
    FROM jsonb_object_keys(coalesce(p_facts, '{}')) k
    LEFT JOIN kc_meta.fact_type f ON f.fact = k
   WHERE k = ANY (string_to_array(coalesce(kc_api.check_facts(p_facts), ''), ', ')) $$;

-------------------------------------------------------------------------------
-- The codes an order item stands for: a HCPCS code ('HCPCS|J0885' or 'J0885'), or a service concept key
-- ('esa-ckd-nd'), which stands for every code mapped to it on the date.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.item_codes(p_item text, p_as_of date) RETURNS text[] LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN p_item ~ '^[A-Z]+\|' THEN
      (SELECT array_agg(DISTINCT c.code_system_id || '|' || c.code) FROM kc_core.code c
        WHERE c.code_system_id = split_part(p_item, '|', 1) AND c.code = split_part(p_item, '|', 2) AND upper_inf(c.recorded))
    ELSE
      (SELECT array_agg(DISTINCT cm.code_system_id || '|' || cm.code ORDER BY cm.code_system_id || '|' || cm.code)
         FROM kc_core.concept_map cm JOIN kc_core.entity e ON e.entity_id = cm.concept_id
        WHERE e.entity_type = 'concept' AND e.natural_key = p_item AND upper_inf(cm.recorded)
          AND (cm.valid @> p_as_of OR NOT EXISTS (SELECT 1 FROM kc_core.concept_map c2 WHERE c2.concept_id = cm.concept_id
                                                    AND c2.valid @> p_as_of)))
  END $$;

-------------------------------------------------------------------------------
-- coverage_gap: silence about coverage criteria is never read as "no criteria". When the pack holds
-- coverage criteria for this item but none apply here, the answer says why:
--   no_edition_loaded_for_date            a policy that applies to this coverage has no edition loaded for the date
--   no_coverage_policy_loaded_for_region  the pack holds criteria for this item, but none bound to this coverage and place
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.coverage_gap(p_cov jsonb, p_code text, p_as_of date, p_recorded_as_of timestamptz DEFAULT now())
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE v_dated text[]; v_any text[]; c record;
BEGIN
  SELECT array_agg(x ->> 'rule_key') INTO v_dated
    FROM jsonb_array_elements(coalesce(kc_api.candidate_rules(p_cov, 'coverage_criteria', p_code, p_as_of, p_recorded_as_of), '[]')) x;
  SELECT array_agg(x ->> 'rule_key') INTO v_any
    FROM jsonb_array_elements(coalesce(kc_api.candidate_rules(p_cov, 'coverage_criteria', p_code, p_as_of, p_recorded_as_of, true), '[]')) x;
  IF EXISTS (SELECT 1 FROM unnest(coalesce(v_any, '{}')) k WHERE k <> ALL (coalesce(v_dated, '{}'))) THEN
    RETURN 'no_edition_loaded_for_date';
  END IF;
  IF v_dated IS NULL THEN
    SELECT * INTO c FROM kc_core.code WHERE code_system_id = split_part(p_code,'|',1) AND code = split_part(p_code,'|',2)
       AND upper_inf(recorded) LIMIT 1;
    IF EXISTS (SELECT 1 FROM kc_core.rule r WHERE r.rule_kind = 'coverage_criteria' AND r.status = 'active' AND upper_inf(r.recorded)
                  AND (r.subject_id = c.entity_id OR r.subject_id IN (SELECT cm.concept_id FROM kc_core.concept_map cm
                        WHERE cm.code_system_id = c.code_system_id AND cm.code = c.code))) THEN
      RETURN 'no_coverage_policy_loaded_for_region';
    END IF;
  END IF;
  RETURN NULL;
END $$;

CREATE FUNCTION kc_api.gap_text(p_gap text, p_cov jsonb, p_as_of date) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_gap
    WHEN 'no_edition_loaded_for_date' THEN 'no edition loaded for ' || p_as_of
    WHEN 'no_coverage_policy_loaded_for_region' THEN 'no coverage policy loaded for ' || (p_cov ->> 'state')
    WHEN 'pack_not_current_for_date' THEN 'pack not current for ' || p_as_of
    WHEN 'plan_or_state_not_in_pack' THEN 'plan or state not in the pack'
    WHEN 'item_not_in_pack' THEN 'service or code not in the pack'
    WHEN 'no_reviewed_rule_in_scope' THEN 'no reviewed rule for this plan, place and date'
    WHEN 'source_aging' THEN 'a source was last confirmed more than 14 days ago'
    WHEN 'sync_aging' THEN 'this copy has not synced recently'
    WHEN 'basis_interpreted' THEN 'the rule rests on an interpretation, not a stated rule'
    WHEN 'needs_facts' THEN 'facts needed'
    WHEN 'pa_program_not_screened' THEN 'a prior authorization program bound here was not screened for this service'
    ELSE replace(p_gap, '_', ' ') END $$;

-------------------------------------------------------------------------------
-- applicable_rules: before any patient facts, which reviewed rules apply to this order and what each needs.
-- p_item: a HCPCS code or a service concept key.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.applicable_rules(p_plan text, p_state text, p_item text, p_as_of date,
                                        p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE v_cov jsonb; v_codes text[]; v_code text; v_rules jsonb := '[]'; v_kind text; v_c jsonb; v_gap text; v_any jsonb;
        v_rel text := current_setting('kc.release', true);
BEGIN
  v_cov := kc_api.coverage_for(p_plan, p_state);
  IF v_cov IS NULL THEN v_gap := 'plan_or_state_not_in_pack'; END IF;
  v_codes := kc_api.item_codes(p_item, p_as_of);
  IF v_gap IS NULL AND v_codes IS NULL THEN v_gap := 'item_not_in_pack'; END IF;
  IF v_gap IS NOT NULL THEN
    RETURN jsonb_build_object('status','no_rule_found','reason',v_gap,'reason_text',kc_api.gap_text(v_gap, v_cov, p_as_of),
      'item',p_item,'safe_to_act',false,'next_step','human_review','pack_release',v_rel);
  END IF;
  v_gap := kc_api.pack_date_check(p_as_of);
  IF v_gap IS NOT NULL THEN
    RETURN jsonb_build_object('status','unavailable','reason',v_gap,'reason_text',kc_api.gap_text(v_gap, v_cov, p_as_of),
      'item',p_item,'as_of',p_as_of,'safe_to_act',false,'next_step','human_review','pack_release',v_rel);
  END IF;

  FOREACH v_code IN ARRAY v_codes LOOP
    FOREACH v_kind IN ARRAY ARRAY['approval_required','coverage_criteria','documentation_requirement','decision_timeframe'] LOOP
      FOR v_c IN SELECT * FROM jsonb_array_elements(coalesce(kc_api.candidate_rules(v_cov, v_kind, v_code, p_as_of, p_recorded_as_of), '[]')) LOOP
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_rules) x WHERE x ->> 'rule_ref' = kc_api.rule_ref(v_c ->> 'rule_key', v_c ->> 'confirmed_valid')) THEN
          v_rules := (SELECT jsonb_agg(CASE WHEN x ->> 'rule_ref' = kc_api.rule_ref(v_c ->> 'rule_key', v_c ->> 'confirmed_valid')
                                            AND NOT (x -> 'codes') ? v_code
                                            THEN jsonb_set(x, '{codes}', (x -> 'codes') || to_jsonb(v_code)) ELSE x END)
                        FROM jsonb_array_elements(v_rules) x);
          CONTINUE;
        END IF;
        v_rules := v_rules || jsonb_build_object(
          'rule_ref', kc_api.rule_ref(v_c ->> 'rule_key', v_c ->> 'confirmed_valid'),
          'edition', lower((v_c ->> 'confirmed_valid')::daterange),
          'rule_kind', v_c ->> 'rule_kind', 'layer', v_c ->> 'layer', 'citation', v_c ->> 'citation', 'basis', v_c ->> 'basis',
          'source', (v_c ->> 'citation') || coalesce(', ' || (v_c -> 'locator' ->> 'item'), '') || ', page ' || (v_c -> 'locator' ->> 'page'),
          'codes', jsonb_build_array(v_code),
          'applies_when', v_c -> 'conditions',
          'facts_needed', (SELECT coalesce(jsonb_agg(DISTINCT f), '[]') FROM (
               SELECT x ->> 'fact' AS f FROM jsonb_array_elements(v_c -> 'conditions') x
               UNION SELECT i ->> 'fact' FROM jsonb_array_elements(v_c -> 'requirements') gg, jsonb_array_elements(gg -> 'items') i
                WHERE i ->> 'fact' IS NOT NULL) ff),
          'documents_needed', (SELECT coalesce(jsonb_agg(DISTINCT i ->> 'element'), '[]')
               FROM jsonb_array_elements(v_c -> 'requirements') gg, jsonb_array_elements(gg -> 'items') i WHERE i ->> 'fact' IS NULL),
          'review_exception', v_c -> 'review_exception');
      END LOOP;
    END LOOP;
    v_gap := coalesce(v_gap, kc_api.coverage_gap(v_cov, v_code, p_as_of, p_recorded_as_of));
  END LOOP;

  IF jsonb_array_length(v_rules) = 0 THEN
    v_any := kc_api.candidate_rules(v_cov, 'approval_required', v_codes[1], p_as_of, p_recorded_as_of, true);
    v_gap := coalesce(v_gap, CASE WHEN v_any IS NULL THEN 'no_reviewed_rule_in_scope' ELSE 'no_edition_loaded_for_date' END);
  END IF;
  RETURN jsonb_build_object(
    'status', CASE WHEN v_gap IS NULL THEN 'resolved' WHEN v_gap = 'no_edition_loaded_for_date' THEN 'unavailable' ELSE 'no_rule_found' END,
    'reason', v_gap, 'reason_text', kc_api.gap_text(v_gap, v_cov, p_as_of),
    'coverage', v_cov, 'item', p_item, 'codes', to_jsonb(v_codes), 'as_of', p_as_of, 'rules', v_rules,
    'facts_needed', (SELECT coalesce(jsonb_agg(DISTINCT f), '[]') FROM jsonb_array_elements(v_rules) r, jsonb_array_elements_text(r -> 'facts_needed') f),
    'safe_to_act', CASE WHEN v_gap IS NULL THEN NULL ELSE false END,   -- known only once rules_evaluate has the facts
    'next_step', CASE WHEN v_gap IS NULL THEN 'evaluate_with_facts' ELSE 'human_review' END,
    'pack_release', v_rel);
END $$;

-------------------------------------------------------------------------------
-- evaluate_order: the decision packet for one order.
--   status   resolved | needs_facts | no_rule_found | unavailable | conflict | invalid_input
--   outcome  pa_required | no_pa | exempt | other_gate (no approval needed or approval needed, but the coverage
--            criteria are not met) | needs_facts | unknown
--   safe_to_act true only for pa_required, no_pa or exempt with nothing holding it back
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.evaluate_order(p_plan text, p_state text, p_code text, p_as_of date, p_facts jsonb DEFAULT '{}',
                                      p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_cov jsonb; v_gap text; v_bad text; v_appr jsonb; v_crit jsonb; v_doc jsonb; v_time jsonb; v_codes text[];
  v_pa text; v_outcome text; v_status text; v_cstatus text := 'none_found'; v_ceval jsonb := '[]'; v_e jsonb; r jsonb;
  v_missing jsonb := '[]'; v_obtain text[] := '{}'; v_repeat text[] := '{}'; v_notmet text[] := '{}'; v_met text[] := '{}';
  v_hold text[] := '{}'; v_safe boolean; v_next text; v_detail text; v_route jsonb; v_clock jsonb; v_explain text[] := '{}';
  v_all jsonb; v_refs jsonb; v_sources jsonb; v_fresh text; v_any jsonb; v_primary jsonb;
  v_payer uuid; v_item text; v_rel text := current_setting('kc.release', true); v_appr_win jsonb; v_aging text[];
BEGIN
  v_bad := kc_api.fact_errors(p_facts);
  IF v_bad IS NOT NULL THEN
    RETURN jsonb_build_object('status','invalid_input','outcome','unknown','error',v_bad,'safe_to_act',false,'next_step','fix_input');
  END IF;
  v_cov := kc_api.coverage_for(p_plan, p_state);
  v_codes := kc_api.item_codes(p_code, p_as_of);
  v_gap := CASE WHEN v_cov IS NULL THEN 'plan_or_state_not_in_pack'
                WHEN v_codes IS NULL OR cardinality(v_codes) <> 1 THEN 'item_not_in_pack' END;
  IF v_gap IS NULL THEN v_gap := kc_api.pack_date_check(p_as_of); END IF;
  IF v_gap IS NOT NULL THEN
    RETURN jsonb_build_object('status', CASE WHEN v_gap = 'pack_not_current_for_date' THEN 'unavailable' ELSE 'no_rule_found' END,
      'outcome','unknown','reason',v_gap,'reason_text',kc_api.gap_text(v_gap, v_cov, p_as_of),'code',p_code,'as_of',p_as_of,
      'coverage',v_cov,'safe_to_act',false,'next_step','human_review',
      'next_step_detail','Route to staff: ' || kc_api.gap_text(v_gap, v_cov, p_as_of),'pack_release',v_rel);
  END IF;
  p_code := v_codes[1];

  v_appr := kc_api.resolve_rules(v_cov, 'approval_required', p_code, p_facts, p_as_of, p_recorded_as_of);
  v_crit := kc_api.resolve_rules(v_cov, 'coverage_criteria', p_code, p_facts, p_as_of, p_recorded_as_of);
  v_doc  := kc_api.resolve_rules(v_cov, 'documentation_requirement', p_code, p_facts, p_as_of, p_recorded_as_of);
  v_time := kc_api.resolve_rules(v_cov, 'decision_timeframe', p_code, p_facts, p_as_of, p_recorded_as_of);
  v_appr_win := coalesce(nullif(v_appr -> 'winning_rules', 'null'), '[]');

  -- 1. Prior authorization
  IF v_appr ->> 'status' = 'no_rule_found' AND v_appr ->> 'reason' = 'no_reviewed_rule_in_scope' THEN
    v_any := kc_api.candidate_rules(v_cov, 'approval_required', p_code, p_as_of, p_recorded_as_of, true);
    IF v_any IS NOT NULL AND kc_api.candidate_rules(v_cov, 'approval_required', p_code, p_as_of, p_recorded_as_of) IS NULL THEN
      v_appr := v_appr || jsonb_build_object('status','unavailable','reason','no_edition_loaded_for_date');
    END IF;
  END IF;
  v_pa := CASE v_appr ->> 'status'
    WHEN 'resolved' THEN CASE v_appr -> 'result' ->> 'value' WHEN 'required' THEN 'pa_required' WHEN 'exempt' THEN 'exempt' ELSE 'no_pa' END
    WHEN 'needs_facts' THEN 'needs_facts' ELSE 'unknown' END;
  IF v_pa = 'needs_facts' THEN
    v_missing := v_missing || (v_appr -> 'missing_facts');
    v_obtain := v_obtain || ARRAY(SELECT jsonb_array_elements_text(v_appr -> 'missing_facts'));
  ELSIF v_pa = 'unknown' THEN
    v_hold := array_append(v_hold, coalesce(v_appr ->> 'reason', v_appr ->> 'status'));
  ELSE
    v_hold := v_hold || ARRAY(SELECT jsonb_array_elements_text(v_appr -> 'actionable_reasons'));
    v_explain := array_append(v_explain, CASE v_pa
      WHEN 'pa_required' THEN 'Prior authorization required: ' || (SELECT string_agg(DISTINCT w ->> 'citation', '; ') FROM jsonb_array_elements(v_appr -> 'sources') w)
      WHEN 'exempt' THEN 'Prior authorization not required for emergency or urgent care: ' || (SELECT string_agg(DISTINCT w ->> 'citation', '; ') FROM jsonb_array_elements(v_appr -> 'sources') w)
      ELSE 'Prior authorization not required: not on any of the ' || jsonb_array_length(v_appr_win)
           || ' Original Medicare prior authorization program lists that apply here' END);
  END IF;

  -- 2. Coverage criteria, checked against the facts; every applicable policy's criteria must be met
  v_gap := kc_api.coverage_gap(v_cov, p_code, p_as_of, p_recorded_as_of);
  IF v_gap IS NOT NULL THEN
    v_cstatus := 'unknown'; v_hold := array_append(v_hold, v_gap);
  ELSIF v_crit ->> 'status' = 'needs_facts' THEN
    v_cstatus := 'unknown'; v_missing := v_missing || (v_crit -> 'missing_facts');
    v_obtain := v_obtain || ARRAY(SELECT jsonb_array_elements_text(v_crit -> 'missing_facts'));
  ELSIF v_crit ->> 'status' = 'resolved' THEN
    FOR r IN SELECT * FROM jsonb_array_elements(v_crit -> 'result' -> 'requirements') LOOP
      v_e := kc_api.evaluate_requirements(r, p_facts, p_as_of);
      v_ceval := v_ceval || (jsonb_build_object('citation', r ->> 'citation',
        'rule_ref', (SELECT kc_api.rule_ref(w ->> 'rule_key', w ->> 'confirmed_valid') FROM jsonb_array_elements(v_crit -> 'winning_rules') w
                      WHERE w ->> 'rule_id' = r ->> 'rule_id')) || v_e);
      v_missing := v_missing || (v_e -> 'missing_facts');
      v_obtain := v_obtain || ARRAY(SELECT jsonb_array_elements_text(v_e -> 'to_obtain'));
      v_repeat := v_repeat || ARRAY(SELECT jsonb_array_elements_text(v_e -> 'to_repeat'));
      v_notmet := v_notmet || ARRAY(SELECT jsonb_array_elements_text(v_e -> 'not_met'));
      v_met := v_met || ARRAY(SELECT jsonb_array_elements_text(v_e -> 'met'));
    END LOOP;
    v_cstatus := CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_ceval) x WHERE x ->> 'status' = 'not_met') THEN 'not_met'
                      WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_ceval) x WHERE x ->> 'status' = 'unknown') THEN 'unknown'
                      ELSE 'met' END;
    v_hold := v_hold || ARRAY(SELECT jsonb_array_elements_text(v_crit -> 'actionable_reasons'));
  ELSIF v_crit ->> 'status' IN ('conflict','unavailable') THEN
    v_cstatus := 'unknown'; v_hold := array_append(v_hold, 'coverage_criteria_' || (v_crit ->> 'status'));
  END IF;
  SELECT coalesce(jsonb_agg(DISTINCT m), '[]') INTO v_missing FROM jsonb_array_elements(v_missing) m;

  -- 3. One outcome
  v_outcome := CASE
    WHEN v_pa = 'unknown' OR v_gap IS NOT NULL THEN 'unknown'
    WHEN v_cstatus = 'not_met' THEN 'other_gate'
    WHEN jsonb_array_length(v_missing) > 0 OR v_pa = 'needs_facts' OR (v_cstatus = 'unknown' AND cardinality(v_obtain) > 0) THEN 'needs_facts'
    ELSE v_pa END;
  v_status := CASE
    WHEN v_outcome = 'unknown' THEN CASE WHEN v_pa = 'unknown' THEN v_appr ->> 'status'
                                         WHEN v_gap = 'no_edition_loaded_for_date' THEN 'unavailable' ELSE 'no_rule_found' END
    WHEN v_outcome = 'needs_facts' THEN 'needs_facts' ELSE 'resolved' END;
  IF v_outcome = 'other_gate' THEN v_hold := array_append(v_hold, 'coverage_criteria_not_met'); END IF;
  IF v_outcome = 'needs_facts' THEN v_hold := array_append(v_hold, 'needs_facts'); END IF;
  SELECT coalesce(array_agg(DISTINCT x), '{}') INTO v_hold FROM unnest(v_hold) x WHERE x IS NOT NULL;
  v_safe := v_outcome IN ('pa_required','no_pa','exempt') AND cardinality(v_hold) = 0;

  -- 4. Decision deadline (Medicare Advantage), when a request will go in
  IF v_time ->> 'status' = 'resolved' AND v_pa IN ('pa_required','unknown','needs_facts') THEN
    v_clock := jsonb_build_object(
      'applies', CASE WHEN p_facts ->> 'urgency' = 'expedited' THEN 'expedited' ELSE 'standard' END,
      'hours', CASE WHEN p_facts ->> 'urgency' = 'expedited' THEN v_time -> 'result' -> 'value' -> 'expedited_hours'
                    ELSE v_time -> 'result' -> 'value' -> 'standard_hours' END,
      'standard_hours', v_time -> 'result' -> 'value' -> 'standard_hours',
      'expedited_hours', v_time -> 'result' -> 'value' -> 'expedited_hours',
      'clock_starts', v_time -> 'result' -> 'value' -> 'clock_starts',
      'text', concat_ws(', ', (v_time -> 'result' -> 'value' ->> 'standard_hours') || ' hours standard',
                              (v_time -> 'result' -> 'value' ->> 'expedited_hours') || ' hours expedited'));
  END IF;

  -- 5. Route: the insurer's live channels, and what the rule says to use
  v_payer := (SELECT entity_id FROM kc_core.entity WHERE entity_type = 'payer' AND natural_key = v_cov ->> 'payer');
  v_item  := (SELECT item_type FROM kc_core.code WHERE code_system_id = split_part(p_code,'|',1) AND code = split_part(p_code,'|',2)
                AND upper_inf(recorded) LIMIT 1);
  IF v_pa IN ('pa_required','unknown') THEN
    SELECT jsonb_build_object('channels', jsonb_agg(jsonb_build_object('interface', e.interface, 'address', e.base_url,
                                'preference', e.preference) ORDER BY e.preference),
                              'submit_via', (SELECT w -> 'payload' ->> 'submit_via' FROM jsonb_array_elements(v_appr_win) w
                                              WHERE w -> 'payload' ? 'submit_via' LIMIT 1))
      INTO v_route FROM kc_core.endpoint e
     WHERE e.payer_id = v_payer AND e.live AND e.valid @> p_as_of AND NOT (v_item = 'drug' AND e.excludes_drugs)
    HAVING count(*) > 0;
  END IF;

  -- 6. Next step, as a code and in words
  v_next := CASE
    WHEN v_outcome = 'needs_facts' THEN 'supply_facts'
    WHEN NOT v_safe THEN 'human_review'
    WHEN v_outcome = 'pa_required' THEN 'submit_prior_authorization'
    ELSE 'proceed' END;
  v_detail := CASE
    WHEN v_outcome = 'needs_facts' THEN 'Obtain ' || array_to_string(ARRAY(SELECT DISTINCT x FROM unnest(v_obtain) x), '; ')
    WHEN v_outcome = 'other_gate' AND cardinality(v_repeat) > 0 THEN
      'Repeat ' || array_to_string(ARRAY(SELECT DISTINCT x FROM unnest(v_repeat) x), ', ') || ' and re-evaluate, or hold the order'
    WHEN v_outcome = 'other_gate' THEN 'Coverage criteria not met: document further or hold the order'
    WHEN v_outcome = 'unknown' THEN 'Route to staff: ' || kc_api.gap_text(coalesce(v_gap, v_appr ->> 'reason', v_appr ->> 'status'), v_cov, p_as_of)
    WHEN NOT v_safe THEN 'Route to staff: ' || array_to_string(ARRAY(SELECT kc_api.gap_text(h, v_cov, p_as_of) FROM unnest(v_hold) h), '; ')
    WHEN v_outcome = 'pa_required' THEN 'Submit prior authorization' || coalesce(' via ' || (v_route ->> 'submit_via'), '')
         || coalesce(' (' || (v_route -> 'channels' -> 0 ->> 'interface') || ')', '')
         || coalesce('; decision due within ' || (v_clock ->> 'hours') || ' hours of receipt', '')
    ELSE 'Proceed: no prior authorization needed' || CASE WHEN v_cstatus = 'met' THEN ' and coverage criteria met' ELSE '' END END;

  -- 7. Evidence: every rule the answer rests on, and the one that decides it
  v_all := v_appr_win || coalesce(nullif(v_crit -> 'winning_rules', 'null'), '[]')
        || coalesce(nullif(v_doc -> 'winning_rules', 'null'), '[]') || coalesce(nullif(v_time -> 'winning_rules', 'null'), '[]');
  SELECT coalesce(jsonb_agg(DISTINCT kc_api.rule_ref(w ->> 'rule_key', w ->> 'confirmed_valid')), '[]') INTO v_refs
    FROM jsonb_array_elements(v_all) w;
  SELECT coalesce(jsonb_agg(DISTINCT jsonb_build_object('citation', s ->> 'citation', 'edition', s ->> 'version',
            'page', s -> 'locator' -> 'page', 'quote', s ->> 'quote', 'url', src.url, 'sha256', sn.content_sha256)), '[]')
    INTO v_sources
    FROM jsonb_array_elements(coalesce(nullif(v_appr -> 'sources','null'),'[]') || coalesce(nullif(v_crit -> 'sources','null'),'[]')
                              || coalesce(nullif(v_doc -> 'sources','null'),'[]') || coalesce(nullif(v_time -> 'sources','null'),'[]')) s
    LEFT JOIN kc_core.snapshot sn ON sn.snapshot_id = (s ->> 'snapshot_id')::uuid
    LEFT JOIN kc_core.source src ON src.source_id = sn.source_id;
  -- the deciding rule: the approval rule when approval is required or exempt; otherwise the coverage policy (LCD first)
  v_primary := CASE
    WHEN v_pa IN ('pa_required','exempt') THEN (SELECT jsonb_build_object('rule_ref', kc_api.rule_ref(w ->> 'rule_key', w ->> 'confirmed_valid'))
                                                  FROM jsonb_array_elements(v_appr_win) w LIMIT 1)
    WHEN jsonb_array_length(v_ceval) > 0 THEN (SELECT jsonb_build_object('rule_ref', x ->> 'rule_ref', 'citation', x ->> 'citation')
                                                 FROM jsonb_array_elements(v_ceval) x ORDER BY (x ->> 'citation') NOT LIKE 'LCD%', x ->> 'citation' LIMIT 1)
    ELSE (SELECT jsonb_build_object('rule_ref', kc_api.rule_ref(w ->> 'rule_key', w ->> 'confirmed_valid'))
            FROM jsonb_array_elements(v_appr_win) w LIMIT 1) END;
  IF v_primary IS NOT NULL AND NOT v_primary ? 'citation' THEN
    v_primary := v_primary || jsonb_build_object('citation', (SELECT w ->> 'citation' FROM jsonb_array_elements(v_appr -> 'sources') w LIMIT 1));
  END IF;
  SELECT array_agg(DISTINCT x ->> 'citation' ORDER BY x ->> 'citation') INTO v_aging
    FROM jsonb_array_elements(v_sources) x JOIN kc_core.snapshot sn ON sn.content_sha256 = x ->> 'sha256'
    JOIN kc_core.source src ON src.source_id = sn.source_id
   WHERE p_recorded_as_of - (SELECT max(checked_at) FROM kc_core.source_check sc WHERE sc.source_id = src.source_id AND sc.ok) > src.aging_after;
  v_fresh := CASE WHEN 'stale' IN (v_appr ->> 'freshness', v_crit ->> 'freshness', v_doc ->> 'freshness', v_time ->> 'freshness') THEN 'stale'
                  WHEN 'out_of_sync' IN (v_appr ->> 'freshness', v_crit ->> 'freshness', v_doc ->> 'freshness', v_time ->> 'freshness') THEN 'out_of_sync'
                  WHEN 'aging' IN (v_appr ->> 'freshness', v_crit ->> 'freshness', v_doc ->> 'freshness', v_time ->> 'freshness') THEN 'aging'
                  ELSE 'current' END;
  IF v_aging IS NOT NULL AND 'source_aging' = ANY (v_hold) THEN
    v_detail := v_detail || ' (' || array_to_string(v_aging, ', ') || ')';
  END IF;

  RETURN jsonb_build_object(
    'status', v_status, 'outcome', v_outcome,
    'reason', CASE WHEN v_outcome = 'unknown' THEN coalesce(v_gap, v_appr ->> 'reason') END,
    'reason_text', CASE WHEN v_outcome = 'unknown' THEN kc_api.gap_text(coalesce(v_gap, v_appr ->> 'reason', v_appr ->> 'status'), v_cov, p_as_of) END,
    'reasons', to_jsonb(v_explain || CASE WHEN v_outcome = 'other_gate' THEN v_notmet ELSE v_met END),
    'code', p_code, 'as_of', p_as_of, 'coverage', v_cov,
    'safe_to_act', v_safe, 'not_safe_because', to_jsonb(v_hold),
    'next_step', v_next, 'next_step_detail', v_detail,
    'missing_facts', v_missing,
    'primary_rule', v_primary,
    'prior_authorization', jsonb_build_object('outcome', v_pa, 'status', v_appr ->> 'status', 'reason', v_appr ->> 'reason',
        'notes', v_appr -> 'notes',
        'rule_refs', (SELECT coalesce(jsonb_agg(kc_api.rule_ref(w ->> 'rule_key', w ->> 'confirmed_valid')), '[]') FROM jsonb_array_elements(v_appr_win) w),
        'basis', (SELECT jsonb_agg(DISTINCT w ->> 'basis') FROM jsonb_array_elements(v_appr_win) w)),
    'coverage_criteria', jsonb_build_object('status', v_cstatus, 'gap', v_gap, 'policies', v_ceval),
    'evidence', CASE WHEN v_doc ->> 'status' = 'resolved' THEN
        (SELECT jsonb_agg(jsonb_build_object('citation', rq ->> 'citation', 'logic', rq -> 'logic',
                   'documents', (SELECT jsonb_agg(i ->> 'element') FROM jsonb_array_elements(rq -> 'groups') gg, jsonb_array_elements(gg -> 'items') i)))
           FROM jsonb_array_elements(v_doc -> 'result' -> 'requirements') rq) ELSE '[]'::jsonb END,
    'decision_clock', v_clock,
    'route', v_route,
    'rule_refs', v_refs,
    'sources', v_sources,
    'review_exceptions', (SELECT coalesce(jsonb_agg(DISTINCT w ->> 'review_exception'), '[]') FROM jsonb_array_elements(v_all) w
                           WHERE w ->> 'review_exception' IS NOT NULL),
    'freshness', v_fresh, 'aging_sources', to_jsonb(v_aging),
    'pack_release', v_rel,
    'recorded_as_of', p_recorded_as_of);
END $$;

-------------------------------------------------------------------------------
-- policy_cite: the source text behind one rule reference (rule_key@edition, or rule_key for the current edition)
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.policy_cite(p_rule_ref text, p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT coalesce(
    (SELECT jsonb_build_object('status','resolved',
        'rule_ref', kc_api.rule_ref(r.rule_key, r.valid::text), 'rule_kind', r.rule_kind, 'citation', a.citation,
        'authority', a.title, 'layer', a.layer, 'edition', av.version_label, 'valid', r.valid::text,
        'payload', r.payload, 'conditions', r.conditions, 'basis', r.basis, 'mapping_confidence', r.mapping_confidence,
        'locator', r.source_locator, 'quote', r.source_quote,
        'source', jsonb_build_object('title', src.name, 'publisher', src.publisher, 'url', src.url,
                                     'retrieved_at', sn.retrieved_at, 'sha256', sn.content_sha256,
                                     'last_checked', (SELECT max(checked_at) FROM kc_core.source_check sc
                                                       WHERE sc.source_id = src.source_id AND sc.ok)),
        'reviewed_by', to_jsonb(r.reviewed_by), 'review_exception', r.review_exception)
       FROM kc_api.all_rule r
       JOIN kc_api.all_authority a ON a.entity_id = r.authority_id
       JOIN kc_api.all_authority_version av ON av.authority_version_id = r.authority_version_id
       LEFT JOIN kc_core.snapshot sn ON sn.snapshot_id = r.snapshot_id
       LEFT JOIN kc_core.source src ON src.source_id = sn.source_id
      WHERE r.rule_key = split_part(p_rule_ref, '@', 1) AND r.status = 'active' AND r.recorded @> p_recorded_as_of
        AND (split_part(p_rule_ref, '@', 2) = '' OR lower(r.valid)::text = split_part(p_rule_ref, '@', 2))
      ORDER BY lower(r.valid) DESC LIMIT 1),
    jsonb_build_object('status','no_rule_found','reason','unknown_rule_ref','rule_ref',p_rule_ref)) $$;

-------------------------------------------------------------------------------
-- policy_search: rule references whose quote, citation, indication or subject matches every word of a phrase.
-- Plain matching over reviewed text, so the same phrase always gives the same list. Up to 10 results.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.policy_search(p_text text, p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb LANGUAGE sql STABLE AS $$
  WITH words AS (SELECT w FROM regexp_split_to_table(lower(trim(p_text)), '\s+') w WHERE length(w) > 1),
  docs AS (
    SELECT r.rule_key, r.valid, r.rule_kind, a.citation, a.title, r.source_quote, r.source_locator,
           lower(concat_ws(' ', r.rule_key, a.citation, a.title, r.source_quote, r.payload::text, e.natural_key,
                           (SELECT string_agg(cm.code, ' ') FROM kc_core.concept_map cm WHERE cm.concept_id = r.subject_id))) AS body
      FROM kc_api.all_rule r
      JOIN kc_api.all_authority a ON a.entity_id = r.authority_id
      JOIN kc_core.entity e ON e.entity_id = r.subject_id
     WHERE r.status = 'active' AND r.recorded @> p_recorded_as_of AND upper_inf(r.valid))
  SELECT jsonb_build_object('query', p_text, 'results', coalesce(jsonb_agg(jsonb_build_object(
           'rule_ref', kc_api.rule_ref(d.rule_key, d.valid::text), 'rule_kind', d.rule_kind, 'citation', d.citation,
           'page', d.source_locator -> 'page', 'quote', left(d.source_quote, 300)) ORDER BY d.citation, d.rule_key), '[]'))
    FROM (SELECT * FROM docs WHERE NOT EXISTS (SELECT 1 FROM words WHERE position(w IN body) = 0)
           AND EXISTS (SELECT 1 FROM words) ORDER BY citation, rule_key LIMIT 10) d $$;
