-- Kanini Context prototype: the agent-facing functions. Run after resolver.sql.
-- The MCP server calls only these. Each answers from the reviewed pack, never from free text:
--   kc_api.coverage_for         a plan key and state as a coverage the resolver understands
--   kc_api.evaluate_requirements one rule's criteria checked against registered facts
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
BEGIN
  FOR g IN SELECT * FROM jsonb_array_elements(coalesce(p_rule -> 'groups', '[]')) LOOP
    v_items := '[]'; v_states := '{}'; v_gmissing := '{}'; v_greasons := '{}';
    FOR it IN SELECT * FROM jsonb_array_elements(g -> 'items') LOOP
      v_t := it -> 'test'; v_op := v_t ->> 'op'; v_detail := NULL;
      IF (it ->> 'when' = 'initial' AND v_req = 'continuation') OR (it ->> 'when' = 'continuation' AND v_req = 'initial') THEN
        v_state := 'not_applicable';
      ELSIF it ->> 'fact' IS NULL THEN
        v_state := 'unknown'; v_detail := 'document needed: ' || (it ->> 'element');
      ELSE
        v_val := p_facts -> (it ->> 'fact');
        IF v_val IS NULL OR v_val = 'null'::jsonb THEN
          v_state := 'unknown'; v_gmissing := array_append(v_gmissing, it ->> 'fact');
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
    IF v_gstate = 'unknown' THEN v_missing := v_missing || v_gmissing; END IF;
    IF v_gstate = 'not_met' THEN v_reasons := v_reasons || v_greasons; END IF;
    v_groups := v_groups || jsonb_build_object('group', g -> 'group', 'logic', g -> 'logic', 'status', v_gstate, 'items', v_items);
  END LOOP;
  v_status := CASE
    WHEN cardinality(v_gstates) = 0 THEN 'met'
    WHEN v_logic = 'any' THEN CASE WHEN 'met' = ANY (v_gstates) THEN 'met' WHEN 'unknown' = ANY (v_gstates) THEN 'unknown' ELSE 'not_met' END
    ELSE CASE WHEN 'not_met' = ANY (v_gstates) THEN 'not_met' WHEN 'unknown' = ANY (v_gstates) THEN 'unknown' ELSE 'met' END END;
  RETURN jsonb_build_object('status', v_status,
    'missing_facts', CASE WHEN v_status = 'unknown' THEN (SELECT coalesce(jsonb_agg(DISTINCT m), '[]') FROM unnest(v_missing) m)
                          ELSE '[]'::jsonb END,
    'not_met', to_jsonb(v_reasons), 'groups', v_groups);
END $$;

-------------------------------------------------------------------------------
-- Pack coverage of the date asked about: past the release's last covered date, or before any loaded
-- edition, the pack says so instead of answering from an edition that may not apply.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.pack_date_check(p_as_of date) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN p_as_of > (SELECT covers_through FROM kc_gov.release WHERE release_id = current_setting('kc.release', true))
              THEN 'pack_not_current_for_date' END $$;

-------------------------------------------------------------------------------
-- applicable_rules: before any patient facts, which reviewed rules apply to this order and what each needs.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.applicable_rules(p_plan text, p_state text, p_code text, p_as_of date,
                                        p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE v_cov jsonb; v_rules jsonb := '[]'; v_kind text; v_c jsonb; v_date text; v_any jsonb;
BEGIN
  v_cov := kc_api.coverage_for(p_plan, p_state);
  IF v_cov IS NULL THEN
    RETURN jsonb_build_object('status','no_rule_found','reason','plan_or_state_not_in_pack','plan',p_plan,'state',p_state,
                              'next_step','human_review','pack_release', current_setting('kc.release', true));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM kc_core.code WHERE code_system_id = split_part(p_code,'|',1) AND code = split_part(p_code,'|',2)
                   AND recorded @> p_recorded_as_of) THEN
    RETURN jsonb_build_object('status','no_rule_found','reason','code_not_in_pack','code',p_code,'coverage',v_cov,
                              'next_step','human_review','pack_release', current_setting('kc.release', true));
  END IF;
  v_date := kc_api.pack_date_check(p_as_of);
  IF v_date IS NOT NULL THEN
    RETURN jsonb_build_object('status','unavailable','reason',v_date,'code',p_code,'as_of',p_as_of,'next_step','human_review',
                              'pack_release', current_setting('kc.release', true));
  END IF;
  FOREACH v_kind IN ARRAY ARRAY['approval_required','coverage_criteria','documentation_requirement','decision_timeframe'] LOOP
    FOR v_c IN SELECT * FROM jsonb_array_elements(coalesce(kc_api.candidate_rules(v_cov, v_kind, p_code, p_as_of, p_recorded_as_of), '[]')) LOOP
      v_rules := v_rules || jsonb_build_object(
        'rule_ref', kc_api.rule_ref(v_c ->> 'rule_key', v_c ->> 'confirmed_valid'),
        'rule_kind', v_c ->> 'rule_kind', 'layer', v_c ->> 'layer', 'citation', v_c ->> 'citation', 'basis', v_c ->> 'basis',
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
  IF jsonb_array_length(v_rules) = 0 THEN
    -- nothing for the date: is there an edition for another date (so the gap is the date, not the order)?
    v_any := kc_api.candidate_rules(v_cov, 'approval_required', p_code, p_as_of, p_recorded_as_of, true);
    RETURN jsonb_build_object('status', CASE WHEN v_any IS NULL THEN 'no_rule_found' ELSE 'unavailable' END,
      'reason', CASE WHEN v_any IS NULL THEN 'no_reviewed_rule_in_scope' ELSE 'no_edition_loaded_for_date' END,
      'coverage', v_cov, 'code', p_code, 'as_of', p_as_of, 'rules', '[]'::jsonb, 'next_step','human_review',
      'pack_release', current_setting('kc.release', true));
  END IF;
  RETURN jsonb_build_object('status','resolved','coverage',v_cov,'code',p_code,'as_of',p_as_of,'rules',v_rules,
    'facts_needed', (SELECT coalesce(jsonb_agg(DISTINCT f), '[]') FROM jsonb_array_elements(v_rules) r, jsonb_array_elements_text(r -> 'facts_needed') f),
    'pack_release', current_setting('kc.release', true));
END $$;

-------------------------------------------------------------------------------
-- evaluate_order: the decision packet for one order.
-------------------------------------------------------------------------------
CREATE FUNCTION kc_api.evaluate_order(p_plan text, p_state text, p_code text, p_as_of date, p_facts jsonb DEFAULT '{}',
                                      p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_cov jsonb; v_date text; v_bad text; v_appr jsonb; v_crit jsonb; v_doc jsonb; v_time jsonb;
  v_outcome text; v_cstatus text := 'none_found'; v_ceval jsonb := '[]'; v_e jsonb; r jsonb;
  v_missing jsonb := '[]'; v_reasons text[] := '{}'; v_safe boolean; v_next text; v_route jsonb; v_clock jsonb;
  v_all jsonb; v_refs jsonb; v_sources jsonb; v_fresh text; v_any jsonb; v_reason text;
  v_payer uuid; v_item text;
BEGIN
  v_cov := kc_api.coverage_for(p_plan, p_state);
  IF v_cov IS NULL THEN
    RETURN jsonb_build_object('outcome','unknown','status','no_rule_found','reason','plan_or_state_not_in_pack',
      'safe_to_act', false, 'next_step','human_review','pack_release', current_setting('kc.release', true));
  END IF;
  v_bad := kc_api.check_facts(p_facts);
  IF v_bad IS NOT NULL THEN
    RETURN jsonb_build_object('outcome','unknown','status','invalid_input','reason','unregistered_or_invalid_fact: ' || v_bad,
      'safe_to_act', false, 'next_step','fix_input');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM kc_core.code WHERE code_system_id = split_part(p_code,'|',1) AND code = split_part(p_code,'|',2)
                   AND recorded @> p_recorded_as_of) THEN
    RETURN jsonb_build_object('outcome','unknown','status','no_rule_found','reason','code_not_in_pack','code',p_code,
      'coverage', v_cov, 'safe_to_act', false,'next_step','human_review','pack_release', current_setting('kc.release', true));
  END IF;
  v_date := kc_api.pack_date_check(p_as_of);
  IF v_date IS NOT NULL THEN
    RETURN jsonb_build_object('outcome','unknown','status','unavailable','reason',v_date,'code',p_code,'as_of',p_as_of,
      'safe_to_act', false,'next_step','human_review','pack_release', current_setting('kc.release', true));
  END IF;

  v_appr := kc_api.resolve_rules(v_cov, 'approval_required', p_code, p_facts, p_as_of, p_recorded_as_of);
  v_crit := kc_api.resolve_rules(v_cov, 'coverage_criteria', p_code, p_facts, p_as_of, p_recorded_as_of);
  v_doc  := kc_api.resolve_rules(v_cov, 'documentation_requirement', p_code, p_facts, p_as_of, p_recorded_as_of);
  v_time := kc_api.resolve_rules(v_cov, 'decision_timeframe', p_code, p_facts, p_as_of, p_recorded_as_of);

  -- Prior authorization outcome
  v_reason := v_appr ->> 'reason';
  v_outcome := CASE v_appr ->> 'status'
    WHEN 'resolved' THEN CASE v_appr -> 'result' ->> 'value' WHEN 'required' THEN 'pa_required' WHEN 'exempt' THEN 'exempt' ELSE 'no_pa' END
    WHEN 'needs_facts' THEN 'needs_facts' ELSE 'unknown' END;
  IF v_appr ->> 'status' = 'no_rule_found' AND v_reason = 'no_reviewed_rule_in_scope' THEN
    v_any := kc_api.candidate_rules(v_cov, 'approval_required', p_code, p_as_of, p_recorded_as_of, true);
    IF v_any IS NOT NULL AND kc_api.candidate_rules(v_cov, 'approval_required', p_code, p_as_of, p_recorded_as_of) IS NULL THEN
      v_appr := v_appr || jsonb_build_object('status','unavailable','reason','no_edition_loaded_for_date');
    END IF;
  END IF;
  IF v_appr ->> 'status' = 'needs_facts' THEN v_missing := v_missing || (v_appr -> 'missing_facts'); END IF;
  IF NOT coalesce((v_appr ->> 'actionable')::boolean, false) THEN
    v_reasons := v_reasons || ARRAY(SELECT jsonb_array_elements_text(coalesce(v_appr -> 'actionable_reasons',
                                     jsonb_build_array(v_appr ->> 'status'))));
  END IF;

  -- Coverage criteria, checked against the facts: every applicable policy's criteria must be met
  IF v_crit ->> 'status' = 'needs_facts' THEN
    v_cstatus := 'unknown'; v_missing := v_missing || (v_crit -> 'missing_facts');
  ELSIF v_crit ->> 'status' = 'resolved' THEN
    FOR r IN SELECT * FROM jsonb_array_elements(v_crit -> 'result' -> 'requirements') LOOP
      v_e := kc_api.evaluate_requirements(r, p_facts, p_as_of);
      v_ceval := v_ceval || (jsonb_build_object('citation', r ->> 'citation',
        'rule_ref', (SELECT kc_api.rule_ref(w ->> 'rule_key', w ->> 'confirmed_valid') FROM jsonb_array_elements(v_crit -> 'winning_rules') w
                      WHERE w ->> 'rule_id' = r ->> 'rule_id')) || v_e);
      v_missing := v_missing || (v_e -> 'missing_facts');
    END LOOP;
    v_cstatus := CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_ceval) x WHERE x ->> 'status' = 'not_met') THEN 'not_met'
                      WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(v_ceval) x WHERE x ->> 'status' = 'unknown') THEN 'unknown'
                      ELSE 'met' END;
    v_reasons := v_reasons || ARRAY(SELECT jsonb_array_elements_text(v_crit -> 'actionable_reasons'));
  ELSIF v_crit ->> 'status' IN ('conflict','unavailable') THEN
    v_cstatus := 'unknown'; v_reasons := array_append(v_reasons, 'coverage_criteria_' || (v_crit ->> 'status'));
  END IF;
  IF v_cstatus = 'not_met' THEN v_reasons := array_append(v_reasons, 'coverage_criteria_not_met'); END IF;
  IF v_cstatus = 'unknown' THEN v_reasons := array_append(v_reasons, 'coverage_criteria_unknown'); END IF;
  SELECT coalesce(jsonb_agg(DISTINCT m), '[]') INTO v_missing FROM jsonb_array_elements(v_missing) m;

  -- Decision deadline (Medicare Advantage): the clock that applies to this request, and both clocks
  IF v_time ->> 'status' = 'resolved' AND v_outcome IN ('pa_required','unknown','needs_facts') THEN
    v_clock := jsonb_build_object(
      'applies', CASE WHEN p_facts ->> 'urgency' = 'expedited' THEN 'expedited' ELSE 'standard' END,
      'hours', CASE WHEN p_facts ->> 'urgency' = 'expedited' THEN v_time -> 'result' -> 'value' -> 'expedited_hours'
                    ELSE v_time -> 'result' -> 'value' -> 'standard_hours' END,
      'standard_hours', v_time -> 'result' -> 'value' -> 'standard_hours',
      'expedited_hours', v_time -> 'result' -> 'value' -> 'expedited_hours',
      'clock_starts', v_time -> 'result' -> 'value' -> 'clock_starts');
  END IF;

  -- Route: how to submit, from the insurer's live endpoints, and what the rule says to use
  v_payer := (SELECT entity_id FROM kc_core.entity WHERE entity_type = 'payer' AND natural_key = v_cov ->> 'payer');
  v_item  := (SELECT item_type FROM kc_core.code WHERE code_system_id = split_part(p_code,'|',1) AND code = split_part(p_code,'|',2)
                AND upper_inf(recorded) LIMIT 1);
  IF v_outcome = 'pa_required' OR v_outcome = 'unknown' THEN
    SELECT jsonb_build_object('channels', jsonb_agg(jsonb_build_object('interface', e.interface, 'address', e.base_url,
                                'preference', e.preference) ORDER BY e.preference),
                              'submit_via', (SELECT w -> 'payload' ->> 'submit_via' FROM jsonb_array_elements(coalesce(nullif(v_appr -> 'winning_rules','null'),'[]')) w
                                              WHERE w -> 'payload' ? 'submit_via' LIMIT 1))
      INTO v_route FROM kc_core.endpoint e
     WHERE e.payer_id = v_payer AND e.live AND e.valid @> p_as_of AND NOT (v_item = 'drug' AND e.excludes_drugs);
  END IF;

  SELECT array_agg(DISTINCT x) INTO v_reasons FROM unnest(v_reasons) x WHERE x IS NOT NULL;
  v_reasons := coalesce(v_reasons, '{}');
  v_safe := v_outcome IN ('pa_required','no_pa','exempt') AND cardinality(v_reasons) = 0;
  v_next := CASE
    WHEN jsonb_array_length(v_missing) > 0 THEN 'supply_facts'
    WHEN NOT v_safe THEN 'human_review'
    WHEN v_outcome = 'pa_required' THEN 'submit_prior_authorization'
    ELSE 'proceed' END;

  -- Evidence: every rule the answer rests on, as rule_ref, with its source text
  v_all := coalesce(nullif(v_appr -> 'winning_rules', 'null'), '[]') || coalesce(nullif(v_crit -> 'winning_rules', 'null'), '[]')
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
  v_fresh := CASE WHEN 'stale' IN (v_appr ->> 'freshness', v_crit ->> 'freshness', v_doc ->> 'freshness', v_time ->> 'freshness') THEN 'stale'
                  WHEN 'out_of_sync' IN (v_appr ->> 'freshness', v_crit ->> 'freshness', v_doc ->> 'freshness', v_time ->> 'freshness') THEN 'out_of_sync'
                  WHEN 'aging' IN (v_appr ->> 'freshness', v_crit ->> 'freshness', v_doc ->> 'freshness', v_time ->> 'freshness') THEN 'aging'
                  ELSE 'current' END;

  RETURN jsonb_build_object(
    'code', p_code, 'as_of', p_as_of, 'coverage', v_cov,
    'outcome', v_outcome,
    'prior_authorization', jsonb_build_object('status', v_appr ->> 'status', 'reason', v_appr ->> 'reason',
        'value', v_appr -> 'result' -> 'value', 'notes', v_appr -> 'notes',
        'rule_refs', (SELECT coalesce(jsonb_agg(kc_api.rule_ref(w ->> 'rule_key', w ->> 'confirmed_valid')), '[]')
                        FROM jsonb_array_elements(coalesce(nullif(v_appr -> 'winning_rules','null'),'[]')) w),
        'basis', (SELECT jsonb_agg(DISTINCT w ->> 'basis') FROM jsonb_array_elements(coalesce(nullif(v_appr -> 'winning_rules','null'),'[]')) w)),
    'coverage_criteria', jsonb_build_object('status', v_cstatus, 'policies', v_ceval),
    'documentation', CASE WHEN v_doc ->> 'status' = 'resolved' THEN
        (SELECT jsonb_agg(jsonb_build_object('citation', rq ->> 'citation', 'logic', rq -> 'logic',
                   'documents', (SELECT jsonb_agg(i ->> 'element') FROM jsonb_array_elements(rq -> 'groups') gg, jsonb_array_elements(gg -> 'items') i)))
           FROM jsonb_array_elements(v_doc -> 'result' -> 'requirements') rq) ELSE '[]'::jsonb END,
    'decision_clock', v_clock,
    'route', v_route,
    'missing_facts', v_missing,
    'safe_to_act', v_safe,
    'not_safe_because', to_jsonb(v_reasons),
    'next_step', v_next,
    'rule_refs', v_refs,
    'sources', v_sources,
    'review_exceptions', (SELECT coalesce(jsonb_agg(DISTINCT w ->> 'review_exception'), '[]') FROM jsonb_array_elements(v_all) w
                           WHERE w ->> 'review_exception' IS NOT NULL),
    'freshness', v_fresh,
    'pack_release', current_setting('kc.release', true),
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
