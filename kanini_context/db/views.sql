-- Kanini Context: read-only views of the release, for people rather than agents.
--
-- Loaded after api.sql. Nothing here changes a table, a tool or an answer.
--
-- Five views in their own schema, kc_views, each one row per thing a reader would point at:
--   kc_views.documents          one row per source document
--   kc_views.rules              one row per approved rule, with what it says in plain words
--   kc_views.policy_scopes      one row per place, plan or program where a policy applies
--   kc_views.medicare_programs  one row per CMS prior authorization program, with what its list says
--   kc_views.codes              one row per code in a code group the rules use
--   kc_views.releases           one row per release of the pack, with its sync to this copy
-- and one function in kc_api, explain_order, which traces one answer step by step.
-- Read access for the agent role is granted in release/build.py.
--
-- Conventions, the same in every view:
--   * names are plural nouns; columns are lower snake_case words, never abbreviations;
--   * only current records: rows superseded in a later release and retired rules are left out;
--   * effective_from and effective_to are both inclusive dates; an empty effective_to means no end date;
--   * true/false columns start with is_;
--   * every view and column carries a description (COMMENT ON), shown by \d+ in psql.

CREATE SCHEMA kc_views;
COMMENT ON SCHEMA kc_views IS 'Read-only views of the release in plain words, for people. Agents use kc_api.';

-------------------------------------------------------------------------------
-- Plain-words helpers
-------------------------------------------------------------------------------
CREATE FUNCTION kc_views.document_type_label(p_kind text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
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

CREATE FUNCTION kc_views.rule_type_label(p_kind text, p_basis text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
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

CREATE FUNCTION kc_views.unit_text(p_unit text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN p_unit IS NULL OR p_unit = '' THEN ''
              WHEN p_unit = '%' THEN '%'
              WHEN p_unit = 'mL/min/{1.73_m2}' THEN ' mL/min/1.73 m²'
              ELSE ' ' || p_unit END $$;

CREATE FUNCTION kc_views.list_text(p_values jsonb, p_join text DEFAULT ', ') RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE jsonb_typeof(p_values)
    WHEN 'array' THEN (SELECT string_agg(replace(v, '_', ' '), p_join ORDER BY o)
                         FROM jsonb_array_elements_text(p_values) WITH ORDINALITY AS t(v, o))
    ELSE replace(p_values #>> '{}', '_', ' ') END $$;

-- one requirement on one data element, e.g. "Hemoglobin below 10 g/dL"; p_when adds "on continuation" where the
-- requirement applies only then (a requirement only at initiation is marked once for the whole rule, in rule_summary)
CREATE FUNCTION kc_views.requirement_text(p_name text, p_unit text, p_test jsonb, p_lookback interval, p_when text DEFAULT 'both')
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_test ->> 'op'
    WHEN 'lt'  THEN p_name || ' below '   || (p_test ->> 'value') || kc_views.unit_text(coalesce(p_test ->> 'unit', p_unit))
    WHEN 'lte' THEN p_name || ' at most ' || (p_test ->> 'value') || kc_views.unit_text(coalesce(p_test ->> 'unit', p_unit))
    WHEN 'gt'  THEN p_name || ' above '   || (p_test ->> 'value') || kc_views.unit_text(coalesce(p_test ->> 'unit', p_unit))
    WHEN 'gte' THEN p_name || ' '         || (p_test ->> 'value') || kc_views.unit_text(coalesce(p_test ->> 'unit', p_unit)) || ' or more'
    WHEN 'present' THEN p_name || CASE WHEN p_lookback IS NULL THEN ' on file'
                                       ELSE ' from the past ' || replace(replace(p_lookback::text, ' mons', ' months'), ' mon', ' month') END
    WHEN 'includes' THEN p_name || ' (' || (p_test ->> 'value') || ')'
    WHEN 'includes_any' THEN p_name || ' (one of ' || kc_views.list_text(p_test -> 'value') || ')'
    ELSE p_name || ' ' || p_test::text END
  || CASE p_when WHEN 'continuation' THEN ' on continuation' ELSE '' END $$;

-- one condition on when a rule applies, e.g. "the patient is not on dialysis"
CREATE FUNCTION kc_views.condition_text(p_cond jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_cond ->> 'fact' = 'dialysis_status' AND p_cond -> 'value' = '["none"]' THEN 'the patient is not on dialysis'
    WHEN p_cond ->> 'fact' = 'encounter_type' THEN 'the encounter is ' || kc_views.list_text(p_cond -> 'value', ' or ')
    WHEN p_cond ->> 'fact' = 'diagnosis_codes' AND p_cond ->> 'op' = 'prefix_overlaps'
      THEN 'a diagnosis code starts with ' || kc_views.list_text(p_cond -> 'value', ' or ')
           -- ICD-10-CM: C is a malignant neoplasm, D0 a neoplasm in situ; said in words for a non-clinical reader
           || CASE WHEN (SELECT bool_and(v IN ('C','D0')) FROM jsonb_array_elements_text(p_cond -> 'value') v) THEN ' (a cancer diagnosis)' ELSE '' END
    WHEN p_cond ->> 'op' = 'in' THEN replace(p_cond ->> 'fact', '_', ' ') || ' is ' || kc_views.list_text(p_cond -> 'value', ' or ')
    ELSE replace(p_cond ->> 'fact', '_', ' ') || ' ' || (p_cond ->> 'op') || ' ' || kc_views.list_text(p_cond -> 'value') END $$;

CREATE FUNCTION kc_views.conditions_text(p_conds jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT string_agg(kc_views.condition_text(c), ' and ' ORDER BY o)
    FROM jsonb_array_elements(coalesce(p_conds, '[]')) WITH ORDINALITY AS t(c, o) $$;

-- what a rule says, in one plain sentence
CREATE FUNCTION kc_views.rule_summary(p_rule_id uuid) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE r kc_core.rule; v_groups text; v_when text; v_init text;
BEGIN
  SELECT * INTO r FROM kc_core.rule WHERE rule_id = p_rule_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  v_when := kc_views.conditions_text(r.conditions);
  -- requirements that apply only when therapy starts: one note for the rule when all do, else a mark on each
  SELECT CASE WHEN count(*) > 0 AND bool_and(when_needed = 'initial') THEN ' These apply at initiation of therapy.' ELSE '' END
    INTO v_init FROM kc_core.rule_requirement WHERE rule_id = p_rule_id;
  SELECT CASE WHEN count(*) > 1 AND r.rule_kind = 'coverage_criteria'
              THEN 'all of these hold: ' || string_agg(group_no || ') ' || g, '; ' ORDER BY group_no)
              ELSE string_agg(g, '; ' ORDER BY group_no) END INTO v_groups FROM (
    SELECT q.group_no,
           string_agg(kc_views.requirement_text(d.name, d.unit, q.test, q.lookback, q.when_needed)
                        || CASE WHEN q.when_needed = 'initial' AND v_init = '' THEN ' at initiation' ELSE '' END,
                      CASE WHEN q.group_logic = 'any' AND r.rule_kind = 'documentation_requirement' THEN '; or '
                           WHEN q.group_logic = 'any' THEN ' or ' ELSE ' and ' END ORDER BY d.name) AS g
      FROM kc_core.rule_requirement q JOIN kc_core.data_element d ON d.entity_id = q.data_element_id
     WHERE q.rule_id = p_rule_id GROUP BY q.group_no) x;
  RETURN CASE r.rule_kind
    WHEN 'coverage_criteria' THEN
      'Covered when ' || coalesce(v_groups, 'its criteria are met')
        || CASE WHEN r.payload ->> 'logic' = 'any' THEN ' (any one group is enough)' ELSE '' END
        || CASE WHEN v_when IS NOT NULL THEN '. Applies when ' || v_when ELSE '' END || '.' || v_init
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
        || CASE WHEN v_when IS NOT NULL THEN '. Applies when ' || v_when ELSE '' END || '.' || v_init
    WHEN 'decision_timeframe' THEN
      'Decision due within ' || concat_ws(' or ',
         (r.payload ->> 'standard_hours') || ' hours for a standard request',
         (r.payload ->> 'expedited_hours') || ' hours for an expedited request')
        || coalesce(', from ' || (r.payload ->> 'clock_starts'), '') || '.'
    WHEN 'appeal_window' THEN 'Appeal within ' || coalesce(r.payload ->> 'days', '?') || ' days'
        || coalesce(' of ' || (r.payload ->> 'clock_starts'), '') || '.'
    ELSE r.payload::text END;
END $$;

-- what a rule is about: the concept's label, the code list's name, or the code
CREATE FUNCTION kc_views.subject_label(p_entity uuid) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT coalesce(c.label, vs.name, replace(e.natural_key, 'HCPCS|', 'HCPCS '))
    FROM kc_core.entity e
    LEFT JOIN kc_core.concept c ON c.entity_id = e.entity_id
    LEFT JOIN kc_core.value_set vs ON vs.entity_id = e.entity_id
   WHERE e.entity_id = p_entity $$;

-- a place in plain words: "nationwide", "Ohio", or a contractor region with its states
CREATE FUNCTION kc_views.place_label(p_jurisdiction uuid) RETURNS text LANGUAGE sql STABLE AS $$
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

-- a plan in plain words: its name without the list of product types
CREATE FUNCTION kc_views.plan_label(p_plan uuid) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT split_part(p.plan_type, ' (', 1) FROM kc_core.plan p WHERE p.entity_id = p_plan $$;

-------------------------------------------------------------------------------
-- documents
-------------------------------------------------------------------------------
CREATE VIEW kc_views.documents AS
WITH latest AS (
  SELECT DISTINCT ON (source_id) source_id, snapshot_id, retrieved_at, content_sha256, stated_effective
    FROM kc_core.snapshot ORDER BY source_id, retrieved_at DESC),
checked AS (
  SELECT source_id, max(checked_at) FILTER (WHERE ok) AS last_ok FROM kc_core.source_check GROUP BY source_id),
types AS (   -- by source, across every saved copy, so a new copy not yet curated keeps its type
  SELECT sn.source_id, string_agg(DISTINCT kc_views.document_type_label(a.kind), ', ') AS document_type
    FROM kc_core.authority_version av JOIN kc_core.authority a ON a.entity_id = av.authority_id
    JOIN kc_core.snapshot sn ON sn.snapshot_id = av.snapshot_id
   WHERE upper_inf(av.recorded) GROUP BY sn.source_id),
counts AS (  -- likewise, so its rules still count while a new copy waits for review
  SELECT sn.source_id, count(*) AS n FROM kc_core.rule r JOIN kc_core.snapshot sn ON sn.snapshot_id = r.snapshot_id
   WHERE r.status = 'active' AND upper_inf(r.recorded) GROUP BY sn.source_id),
flags AS (
  SELECT source_id, min(applies_from) AS applies_from, min(detected_at) AS detected_at
    FROM kc_gov.change_flag WHERE state = 'open' GROUP BY source_id)
SELECT s.source_id,
       s.name                                              AS title,
       s.publisher,
       CASE WHEN s.source_class = 'medicare_policy' THEN 'Medicare coverage policies'
            WHEN s.source_class IN ('payer_list', 'payer_policy') THEN 'Payer documents'
            WHEN s.publisher LIKE 'Office of the Federal Register%' THEN 'Federal regulations'
            WHEN s.source_id LIKE 'cms-%' THEN 'CMS program lists'
            ELSE initcap(replace(s.source_class, '_', ' ')) END AS document_group,
       coalesce(t.document_type, CASE
         WHEN s.source_id LIKE '%-codes' THEN 'CMS program code list'
         WHEN s.source_id = 'cms-pa-initiatives-index' THEN 'CMS index of prior authorization programs'
         WHEN s.source_id LIKE 'cms-%' THEN 'CMS prior authorization program'
         ELSE initcap(replace(s.source_class, '_', ' ')) END) AS document_type,
       l.stated_effective                                  AS edition,
       s.url,
       l.retrieved_at                                      AS saved_at,
       l.content_sha256                                    AS sha256,
       left(l.content_sha256, 12)                          AS fingerprint,
       c.last_ok                                           AS last_checked_at,
       CASE WHEN l.snapshot_id IS NULL THEN 'not saved'
            WHEN c.last_ok IS NULL THEN 'never checked'
            WHEN now() - c.last_ok > s.stale_after THEN 'stale'
            WHEN now() - c.last_ok > s.aging_after THEN 'aging'
            ELSE 'current' END                             AS freshness,
       s.aging_after,
       s.stale_after,
       f.source_id IS NOT NULL                             AS is_change_pending,
       f.applies_from                                      AS change_applies_from,
       coalesce(n.n, 0)::int                               AS rule_count,
       CASE
         WHEN l.snapshot_id IS NULL THEN 'Not saved in this copy of the pack'
         WHEN coalesce(n.n, 0) > 0 THEN 'Source of ' || n.n || ' rule' || CASE WHEN n.n = 1 THEN '' ELSE 's' END
         WHEN s.source_id = 'cms-asc-pa-demo-codes' THEN 'Code list for the CMS ASC demonstration check'
         WHEN s.source_id = 'cms-dmepos-ppa-codes' THEN 'Code list for the CMS DMEPOS probationary check'
         WHEN s.source_id = 'cms-pa-initiatives-index' THEN 'Confirms the full set of CMS prior authorization programs'
         ELSE 'Supporting document' END                    AS role
  FROM kc_core.source s
  LEFT JOIN latest l ON l.source_id = s.source_id
  LEFT JOIN checked c ON c.source_id = s.source_id
  LEFT JOIN types t ON t.source_id = s.source_id
  LEFT JOIN counts n ON n.source_id = s.source_id
  LEFT JOIN flags f ON f.source_id = s.source_id;

COMMENT ON VIEW kc_views.documents IS 'One row per source document: what it is, which edition is saved, when it was last checked, and what it is used for.';
COMMENT ON COLUMN kc_views.documents.source_id IS 'Short identifier of the document, as in the sources manifest.';
COMMENT ON COLUMN kc_views.documents.title IS 'Title of the document.';
COMMENT ON COLUMN kc_views.documents.publisher IS 'Who publishes it.';
COMMENT ON COLUMN kc_views.documents.document_group IS 'Medicare coverage policies, Payer documents, Federal regulations or CMS program lists; any other kind of source shows its class.';
COMMENT ON COLUMN kc_views.documents.document_type IS 'What kind of document it is, in plain words.';
COMMENT ON COLUMN kc_views.documents.edition IS 'The edition or effective date stated in the saved copy.';
COMMENT ON COLUMN kc_views.documents.url IS 'Where the original is published.';
COMMENT ON COLUMN kc_views.documents.saved_at IS 'When the saved copy was taken.';
COMMENT ON COLUMN kc_views.documents.sha256 IS 'SHA-256 fingerprint of the saved copy. A change here means the document changed.';
COMMENT ON COLUMN kc_views.documents.fingerprint IS 'First 12 characters of sha256, for display.';
COMMENT ON COLUMN kc_views.documents.last_checked_at IS 'Last time the original was checked against the saved copy and found unchanged.';
COMMENT ON COLUMN kc_views.documents.freshness IS 'current, aging (due for a check), stale (answers resting on it are withheld), never checked, or not saved.';
COMMENT ON COLUMN kc_views.documents.aging_after IS 'How long after a check the document counts as aging.';
COMMENT ON COLUMN kc_views.documents.stale_after IS 'How long after a check the document counts as stale.';
COMMENT ON COLUMN kc_views.documents.is_change_pending IS 'true when a change to the document has been detected and is waiting for review; answers resting on it go to staff from change_applies_from.';
COMMENT ON COLUMN kc_views.documents.change_applies_from IS 'The date the pending change takes effect, when the document states one.';
COMMENT ON COLUMN kc_views.documents.rule_count IS 'Number of approved rules taken from this document.';
COMMENT ON COLUMN kc_views.documents.role IS 'What the document is used for, in plain words.';

-------------------------------------------------------------------------------
-- rules
-------------------------------------------------------------------------------
CREATE VIEW kc_views.rules AS
SELECT kc_api.rule_ref(r.rule_key, r.valid::text)         AS rule_ref,
       r.rule_key,
       kc_views.rule_type_label(r.rule_kind, r.basis)     AS rule_type,
       kc_views.subject_label(r.subject_id)               AS subject,
       kc_views.rule_summary(r.rule_id)                   AS summary,
       coalesce(
         (SELECT kc_views.place_label(r.jurisdiction_id) WHERE r.jurisdiction_id IS NOT NULL),
         (SELECT kc_views.plan_label(r.plan_id) WHERE r.plan_id IS NOT NULL),
         (SELECT string_agg(DISTINCT coalesce(kc_views.plan_label(b.plan_id) || coalesce(', ' || kc_views.place_label(b.jurisdiction_id), ''),
                                              kc_views.place_label(b.jurisdiction_id)), '; ')
            FROM kc_core.authority_binding b
           WHERE b.authority_id = r.authority_id AND upper_inf(b.recorded)
             AND (r.program_id IS NULL OR b.program_id IS NULL OR b.program_id = r.program_id)
             AND (b.plan_id IS NOT NULL OR b.jurisdiction_id IS NOT NULL)))  AS applies_in,
       coalesce(
         (SELECT pr.name FROM kc_core.program pr WHERE pr.entity_id = r.program_id),
         (SELECT string_agg(DISTINCT pr.name, ', ') FROM kc_core.authority_binding b
            JOIN kc_core.program pr ON pr.entity_id = b.program_id
           WHERE b.authority_id = r.authority_id AND upper_inf(b.recorded)
             AND (r.plan_id IS NULL OR b.plan_id IS NULL OR b.plan_id = r.plan_id)))   AS programs,
       a.citation,
       kc_views.document_type_label(a.kind)               AS document_type,
       a.layer,
       sn.source_id,
       (r.source_locator ->> 'page')::int                 AS page,
       r.source_quote                                     AS quote,
       sn.stated_effective                                AS edition,
       lower(r.valid)                                     AS effective_from,
       upper(r.valid) - 1                                 AS effective_to,
       nullif(array_to_string(ARRAY(SELECT jsonb_array_elements_text(coalesce(r.payload -> 'not_modelled', '[]'))), '; '), '')
                                                          AS not_checked,
       r.basis,
       array_to_string(r.reviewed_by, ', ')               AS approved_by,
       r.reviewed_at                                      AS approved_at,
       r.review_exception                                 AS approval_note,
       r.release_id
  FROM kc_core.rule r
  JOIN kc_core.authority a ON a.entity_id = r.authority_id
  JOIN kc_core.snapshot sn ON sn.snapshot_id = r.snapshot_id
 WHERE r.status = 'active' AND upper_inf(r.recorded);

COMMENT ON VIEW kc_views.rules IS 'One row per approved rule in force or scheduled, with what it says in one plain sentence and the exact quote it rests on.';
COMMENT ON COLUMN kc_views.rules.rule_ref IS 'The rule and its edition (rule_key@date), as the tools and the decision log cite it.';
COMMENT ON COLUMN kc_views.rules.rule_key IS 'Stable identifier of the rule across editions.';
COMMENT ON COLUMN kc_views.rules.rule_type IS 'Coverage criteria, Prior authorization, Program list checked, Exemption, Evidence needed, Decision deadline, Appeal window or Obligation.';
COMMENT ON COLUMN kc_views.rules.subject IS 'The drug, service or code group the rule is about.';
COMMENT ON COLUMN kc_views.rules.summary IS 'What the rule says, in one plain sentence generated from its stored conditions.';
COMMENT ON COLUMN kc_views.rules.applies_in IS 'Where the rule applies: nationwide, a state, a contractor region with its states, or a plan by name.';
COMMENT ON COLUMN kc_views.rules.programs IS 'The programs it applies to, such as Original Medicare or Medicare Advantage.';
COMMENT ON COLUMN kc_views.rules.citation IS 'The policy or regulation the rule comes from.';
COMMENT ON COLUMN kc_views.rules.document_type IS 'What kind of document that is, in plain words.';
COMMENT ON COLUMN kc_views.rules.layer IS 'Rank of the source when sources conflict: T0 law and regulation, T1 Medicare policy, T3 payer documents.';
COMMENT ON COLUMN kc_views.rules.not_checked IS 'Parts of the policy the rule does not check, which staff confirm themselves.';
COMMENT ON COLUMN kc_views.rules.source_id IS 'The saved document the quote is in (kc_views.documents.source_id).';
COMMENT ON COLUMN kc_views.rules.page IS 'Page of the saved document the quote is on.';
COMMENT ON COLUMN kc_views.rules.quote IS 'The exact words of the document the rule rests on.';
COMMENT ON COLUMN kc_views.rules.edition IS 'The edition of the document the rule was taken from.';
COMMENT ON COLUMN kc_views.rules.effective_from IS 'First day the rule is in force.';
COMMENT ON COLUMN kc_views.rules.effective_to IS 'Last day the rule is in force; empty means no end date.';
COMMENT ON COLUMN kc_views.rules.basis IS 'stated (the document says it), stated_by_exhaustion (the item is absent from a complete list) or interpreted.';
COMMENT ON COLUMN kc_views.rules.approved_by IS 'Who approved the rule.';
COMMENT ON COLUMN kc_views.rules.approved_at IS 'When it was approved.';
COMMENT ON COLUMN kc_views.rules.approval_note IS 'Any exception recorded with the approval, such as a single reviewer in the prototype.';
COMMENT ON COLUMN kc_views.rules.release_id IS 'The release the rule first shipped in.';

-------------------------------------------------------------------------------
-- policy_scopes
-------------------------------------------------------------------------------
CREATE VIEW kc_views.policy_scopes AS
SELECT a.citation,
       kc_views.document_type_label(a.kind)               AS document_type,
       pr.name                                            AS program,
       kc_views.plan_label(b.plan_id)                     AS plan,
       pe.natural_key                                     AS plan_code,
       j.code                                             AS place_code,
       kc_views.place_label(b.jurisdiction_id)            AS place_name,
       CASE j.kind WHEN 'nation' THEN 'nation' WHEN 'state' THEN 'state'
                   WHEN 'mac_ab' THEN 'Medicare contractor region' ELSE j.kind END AS place_type,
       CASE b.item_scope WHEN 'all' THEN 'all items' WHEN 'drugs' THEN 'drugs'
                         ELSE 'items and services' END    AS items,
       lower(b.valid)                                     AS effective_from,
       upper(b.valid) - 1                                 AS effective_to
  FROM kc_core.authority_binding b
  JOIN kc_core.authority a ON a.entity_id = b.authority_id
  LEFT JOIN kc_core.program pr ON pr.entity_id = b.program_id
  LEFT JOIN kc_core.entity pe ON pe.entity_id = b.plan_id
  LEFT JOIN kc_core.jurisdiction j ON j.entity_id = b.jurisdiction_id
 WHERE upper_inf(b.recorded);

COMMENT ON VIEW kc_views.policy_scopes IS 'One row per place, plan or program where a policy applies, with the dates.';
COMMENT ON COLUMN kc_views.policy_scopes.citation IS 'The policy, regulation or program.';
COMMENT ON COLUMN kc_views.policy_scopes.document_type IS 'What kind of document it is, in plain words.';
COMMENT ON COLUMN kc_views.policy_scopes.program IS 'The program it applies to, such as Original Medicare.';
COMMENT ON COLUMN kc_views.policy_scopes.plan IS 'The plan it applies to, by name, when it is bound to one plan.';
COMMENT ON COLUMN kc_views.policy_scopes.plan_code IS 'The plan''s identifier, as the tools take it.';
COMMENT ON COLUMN kc_views.policy_scopes.place_code IS 'Code of the place: US, a state code, or a Medicare contractor region code.';
COMMENT ON COLUMN kc_views.policy_scopes.place_name IS 'The place in plain words; a contractor region lists its states.';
COMMENT ON COLUMN kc_views.policy_scopes.place_type IS 'nation, state or Medicare contractor region.';
COMMENT ON COLUMN kc_views.policy_scopes.items IS 'Which items it covers: all items, drugs, or items and services.';
COMMENT ON COLUMN kc_views.policy_scopes.effective_from IS 'First day it applies there.';
COMMENT ON COLUMN kc_views.policy_scopes.effective_to IS 'Last day it applies there; empty means no end date.';

-------------------------------------------------------------------------------
-- medicare_programs
-------------------------------------------------------------------------------
CREATE VIEW kc_views.medicare_programs AS
SELECT a.citation                                         AS program,
       a.title,
       a.closed_list                                      AS is_complete_list,
       (SELECT string_agg(DISTINCT CASE j.kind WHEN 'nation' THEN 'nationwide' ELSE j.code END
                          || ' (from ' || lower(b.valid)
                          || CASE WHEN upper(b.valid) IS NOT NULL THEN ' until ' || (upper(b.valid) - 1) ELSE '' END || ')', ', ')
          FROM kc_core.authority_binding b JOIN kc_core.jurisdiction j ON j.entity_id = b.jurisdiction_id
         WHERE b.authority_id = a.entity_id AND upper_inf(b.recorded))  AS applies_in,
       s.n > 0                                            AS is_checked,
       s.subjects                                         AS subject,
       coalesce(s.findings, 'Not checked: the program''s list is not saved in this copy of the pack') AS finding,
       s.page,
       s.quote,
       s.rule_refs                                        AS rule_ref
  FROM kc_core.authority a
  LEFT JOIN LATERAL (   -- every drug group checked against this program's list, in one row
    SELECT count(*) AS n,
           string_agg(DISTINCT kc_views.subject_label(r.subject_id), '; ') AS subjects,
           string_agg(DISTINCT r.source_locator ->> 'screen_finding', ' ') AS findings,
           min((r.source_locator ->> 'page')::int) AS page,
           string_agg(DISTINCT r.source_quote, ' … ') AS quote,
           string_agg(kc_api.rule_ref(r.rule_key, r.valid::text), ', ' ORDER BY r.rule_key) AS rule_refs
      FROM kc_core.rule r
     WHERE r.authority_id = a.entity_id AND r.basis = 'stated_by_exhaustion'
       AND r.status = 'active' AND upper_inf(r.recorded)) s ON true
 WHERE a.kind = 'pa_program';

COMMENT ON VIEW kc_views.medicare_programs IS 'One row per CMS prior authorization program: where it applies and what its list says about the drugs in the pack.';
COMMENT ON COLUMN kc_views.medicare_programs.program IS 'Short name of the program.';
COMMENT ON COLUMN kc_views.medicare_programs.title IS 'Full title of the program.';
COMMENT ON COLUMN kc_views.medicare_programs.is_complete_list IS 'true when the program publishes a complete list, so an item absent from it does not need prior authorization under it.';
COMMENT ON COLUMN kc_views.medicare_programs.applies_in IS 'Where the program applies, from which date and, when it ends, until which date.';
COMMENT ON COLUMN kc_views.medicare_programs.is_checked IS 'true when the program''s list was checked for the drugs in the pack.';
COMMENT ON COLUMN kc_views.medicare_programs.subject IS 'The drug groups checked against the list, separated by semicolons.';
COMMENT ON COLUMN kc_views.medicare_programs.finding IS 'What the check found, or why the program is not checked.';
COMMENT ON COLUMN kc_views.medicare_programs.page IS 'Page of the saved document the finding rests on.';
COMMENT ON COLUMN kc_views.medicare_programs.quote IS 'The exact words of that page.';
COMMENT ON COLUMN kc_views.medicare_programs.rule_ref IS 'The rules recording the findings (kc_views.rules.rule_ref), separated by commas.';

-------------------------------------------------------------------------------
-- codes
-------------------------------------------------------------------------------
CREATE VIEW kc_views.codes AS
SELECT c.label                                            AS code_group,
       'concept'                                          AS group_type,
       m.code_system_id                                   AS code_system,
       m.code,
       (SELECT k.display FROM kc_core.code k WHERE k.code_system_id = m.code_system_id AND k.code = m.code
          AND upper_inf(k.recorded) ORDER BY lower(k.valid) DESC LIMIT 1) AS description,
       m.map_kind                                         AS relation,
       lower(m.valid)                                     AS effective_from,
       upper(m.valid) - 1                                 AS effective_to
  FROM kc_core.concept_map m JOIN kc_core.concept c ON c.entity_id = m.concept_id
 WHERE upper_inf(m.recorded)
UNION ALL
SELECT vs.name, 'code list', vm.code_system_id, vm.code,
       (SELECT k.display FROM kc_core.code k WHERE k.code_system_id = vm.code_system_id AND k.code = vm.code
          AND upper_inf(k.recorded) ORDER BY lower(k.valid) DESC LIMIT 1),
       'member', lower(vm.valid), upper(vm.valid) - 1
  FROM kc_core.value_set_member vm JOIN kc_core.value_set vs ON vs.entity_id = vm.value_set_id
 WHERE upper_inf(vm.recorded)
UNION ALL
SELECT vs.name, 'code list', k.code_system_id, k.code, k.display,
       'member', lower(k.valid), upper(k.valid) - 1
  FROM kc_core.value_set vs
  JOIN kc_core.code k ON upper_inf(k.recorded)
   AND (vs.filter ->> 'item_type' = k.item_type OR vs.filter ? 'any')
 WHERE vs.definition = 'intensional';

COMMENT ON VIEW kc_views.codes IS 'One row per code in a group the rules use: the codes a concept maps to, and the members of each code list (a list defined by a rule, such as all drug codes, is expanded over the codes in the pack).';
COMMENT ON COLUMN kc_views.codes.code_group IS 'The concept (such as "ESA, CKD not on dialysis") or code list the code belongs to.';
COMMENT ON COLUMN kc_views.codes.group_type IS 'concept or code list.';
COMMENT ON COLUMN kc_views.codes.code_system IS 'The code system, such as HCPCS or ICD-10-CM.';
COMMENT ON COLUMN kc_views.codes.code IS 'The code.';
COMMENT ON COLUMN kc_views.codes.description IS 'The code''s description; empty where the description is licensed.';
COMMENT ON COLUMN kc_views.codes.relation IS 'How the code relates to the group: equivalent, narrower, broader, related, or member.';
COMMENT ON COLUMN kc_views.codes.effective_from IS 'First day the code belongs to the group.';
COMMENT ON COLUMN kc_views.codes.effective_to IS 'Last day the code belongs to the group; empty means no end date.';

-------------------------------------------------------------------------------
-- releases
-------------------------------------------------------------------------------
CREATE VIEW kc_views.releases AS
SELECT r.release_id,
       r.built_at,
       r.status,
       r.covers_through,
       r.git_commit,
       r.digest,
       (SELECT max(l.synced_at) FROM kc_client.sync_log l WHERE l.release_id = r.release_id) AS last_synced_at
  FROM kc_gov.release r;

COMMENT ON VIEW kc_views.releases IS 'One row per release of the pack, with the last date answers are good for and when this copy last synced it.';
COMMENT ON COLUMN kc_views.releases.release_id IS 'The release, as every answer reports it (pack_release).';
COMMENT ON COLUMN kc_views.releases.built_at IS 'When the release was built.';
COMMENT ON COLUMN kc_views.releases.status IS 'The release''s status.';
COMMENT ON COLUMN kc_views.releases.covers_through IS 'Last service date the release answers for; later dates go to staff.';
COMMENT ON COLUMN kc_views.releases.git_commit IS 'The code and rules the release was built from.';
COMMENT ON COLUMN kc_views.releases.digest IS 'Fingerprint of the release contents.';
COMMENT ON COLUMN kc_views.releases.last_synced_at IS 'When this copy last synced the release; answers stop being safe to act on when it lapses.';

-------------------------------------------------------------------------------
-- kc_api.explain_order: one answer, step by step
-------------------------------------------------------------------------------
-- Returns the answer exactly as kc_api.evaluate_order gives it (key "answer"), plus:
--   coverage  the plan, program and state, with the state's name and its Medicare contractor region
--   policies  the approved rules in force on the date (kc_api.applicable_rules)
--   programs  every CMS prior authorization program that applies to this coverage on the date, with what its list
--             says, the page and quote, and whether the answer used it
-- It calls the same function the tools call, so the trace cannot disagree with the answer.
CREATE FUNCTION kc_api.explain_order(p_plan text, p_state text, p_code text, p_as_of date, p_facts jsonb DEFAULT '{}',
                                     p_recorded_as_of timestamptz DEFAULT now()) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE v_ans jsonb; v_cov jsonb; v_place jsonb; v_pol jsonb; v_prog jsonb := '[]'; v_type text;
BEGIN
  v_ans := kc_api.evaluate_order(p_plan, p_state, p_code, p_as_of, p_facts, p_recorded_as_of);
  v_cov := coalesce(nullif(v_ans -> 'coverage', 'null'), kc_api.coverage_for(p_plan, p_state));
  SELECT jsonb_build_object('state_name', s.name,
           'contractor', (SELECT jsonb_build_object('code', m.code, 'name', m.name, 'contractor', m.contractor)
                            FROM kc_core.edge e JOIN kc_core.jurisdiction m ON m.entity_id = e.to_id
                           WHERE e.from_id = s.entity_id AND e.relation_type = 'within' AND m.kind = 'mac_ab'
                             AND e.valid @> p_as_of AND e.recorded @> p_recorded_as_of LIMIT 1))
    INTO v_place
    FROM kc_core.jurisdiction s WHERE s.kind = 'state' AND s.code = upper(p_state);
  v_pol := kc_api.applicable_rules(p_plan, p_state, p_code, p_as_of, p_recorded_as_of);
  SELECT k.item_type INTO v_type FROM kc_core.code k
   WHERE k.code_system_id || '|' || k.code = v_ans ->> 'code' AND k.recorded @> p_recorded_as_of LIMIT 1;
  -- a program list counts only when it was checked for this item: its rule is among the item's applicable rules
  IF v_cov IS NOT NULL AND v_type IS NOT NULL AND coalesce(v_ans ->> 'reason', '') <> 'pack_not_current_for_date' THEN
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
      FROM jsonb_array_elements(kc_api.bound_pa_programs(v_cov, v_type, p_as_of, p_recorded_as_of)) bp
      JOIN kc_core.authority a ON a.entity_id = (bp ->> 'authority_id')::uuid
      LEFT JOIN kc_core.rule s ON s.authority_id = a.entity_id AND s.basis = 'stated_by_exhaustion'
                              AND s.status = 'active' AND s.recorded @> p_recorded_as_of AND s.valid @> p_as_of
                              AND EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(v_pol -> 'rules', '[]')) pr
                                           WHERE pr ->> 'rule_ref' = kc_api.rule_ref(s.rule_key, s.valid::text));
  END IF;
  RETURN jsonb_build_object(
    'answer', v_ans,
    'coverage', CASE WHEN v_cov IS NULL THEN '{}'::jsonb ELSE v_cov || coalesce(v_place, '{}') END,
    'policies', coalesce(v_pol -> 'rules', '[]'),
    'programs', v_prog);
END $$;
COMMENT ON FUNCTION kc_api.explain_order(text, text, text, date, jsonb, timestamptz) IS
  'One answer step by step: the answer from evaluate_order, the coverage with its contractor region, the policies in force and the CMS programs checked.';
