-- Kanini Context: physical schema, draft 0.9
-- Target: Azure Database for PostgreSQL, Flexible Server. Tested on PostgreSQL 16.
-- Field names follow Appendix B of the research proposal.
--
-- Valid time:  when a fact is true in the real world. Stored as four bounds so an
--              uncertain start or end is never turned into an invented date:
--              valid_from_lo <= true start <= valid_from_hi, valid_to_lo <= true end <= valid_to_hi.
--              valid          = confirmed range  [valid_from_hi, valid_to_lo)
--              valid_possible = possible range   [valid_from_lo, valid_to_hi)
-- System time: recorded = [recorded_at, superseded_at). Nothing is updated in place or deleted.

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA kc_meta;    -- the ontology definition itself
CREATE SCHEMA kc_core;    -- entities, relationships and rules
CREATE SCHEMA kc_api;     -- the agent tools and the resolver, shared by every workflow
CREATE SCHEMA kc_stage;   -- curation drafts: the same tables as kc_core, created from it by the pipeline
CREATE SCHEMA kc_gov;     -- change flags, reviews, releases
CREATE SCHEMA kc_client;  -- exists only in a client copy: sync state, audit, feedback, client rules (T5)

-- Number of distinct approvers (so '{alice,alice}' counts as one)
CREATE FUNCTION kc_core.n_approvers(a text[]) RETURNS int LANGUAGE sql IMMUTABLE AS
  $$ SELECT count(DISTINCT x)::int FROM unnest(coalesce(a,'{}')) x WHERE x <> '' $$;

-------------------------------------------------------------------------------
-- 1. Ontology registry
-------------------------------------------------------------------------------
CREATE TABLE kc_meta.entity_type (
  entity_type   text PRIMARY KEY,
  parent_type   text REFERENCES kc_meta.entity_type,   -- class hierarchy
  module        text NOT NULL,
  description   text NOT NULL,
  fhir_resource text
);

CREATE TABLE kc_meta.relation_type (
  relation_type text PRIMARY KEY,
  domain_type   text NOT NULL REFERENCES kc_meta.entity_type,
  range_type    text NOT NULL REFERENCES kc_meta.entity_type,
  cardinality   text NOT NULL CHECK (cardinality IN ('1:1','1:n','n:1','n:m')),
  stored_as     text NOT NULL CHECK (stored_as IN ('edge','column','table')),
  description   text NOT NULL
);

CREATE TABLE kc_meta.rule_kind (
  rule_kind      text PRIMARY KEY,
  parent_kind    text REFERENCES kc_meta.rule_kind,       -- e.g. decision_timeframe is an obligation
  use_case       text NOT NULL,                          -- core | prior_auth | denials
  aspect         text NOT NULL,                          -- approval | documentation | timeframe | validity
  default_effect text NOT NULL CHECK (default_effect IN ('override','ceiling','floor','additive')),
  measures       text[],                                 -- payload fields compared by ceiling/floor
  payload_schema jsonb NOT NULL,
  schema_version int NOT NULL DEFAULT 1,
  conflict_on    text[]                                  -- payload fields that must agree within one layer (additive kinds)
);

-- Layer precedence as a number, so ordering never depends on text collation.
-- T0 (federal law) ranks first; CG (clinical guidance track) ranks last and never outranks a T layer.
CREATE FUNCTION kc_meta.layer_rank(l text) RETURNS int LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE l WHEN 'T0' THEN 0 WHEN 'T1' THEN 1 WHEN 'T2' THEN 2 WHEN 'T3' THEN 3
                WHEN 'T4' THEN 4 WHEN 'T5' THEN 5 WHEN 'CG' THEN 99 ELSE 100 END $$;

CREATE TABLE kc_meta.fact_type (                        -- facts a caller may pass; no free text
  fact        text PRIMARY KEY,
  value_type  text NOT NULL CHECK (value_type IN ('enum','code_list','integer','number','boolean','date')),
  allowed     jsonb,                                     -- allowed values for enums
  description text NOT NULL
);

-------------------------------------------------------------------------------
-- 2. Provenance
-------------------------------------------------------------------------------
CREATE TABLE kc_core.source (
  source_id       text PRIMARY KEY,
  name            text NOT NULL,
  publisher       text NOT NULL,
  source_class    text NOT NULL CHECK (source_class IN
                   ('federal','medicare_policy','state','payer_list','payer_policy','code_set','interface')),
  access_method   text NOT NULL CHECK (access_method IN
                   ('file_download','rest_api','web_page','pdf','terminology_api','manual_upload')),
  url             text,
  license_class   text NOT NULL CHECK (license_class IN
                   ('public','free_with_attribution','free_registration','client_license')),
  check_every     interval NOT NULL,
  aging_after     interval NOT NULL,
  stale_after     interval NOT NULL,
  last_success_at timestamptz
);

CREATE TABLE kc_core.source_check (     -- every check of a source, changed or not (freshness history)
  source_id  text NOT NULL REFERENCES kc_core.source,
  checked_at timestamptz NOT NULL,
  ok         boolean NOT NULL,
  PRIMARY KEY (source_id, checked_at)
);

CREATE TABLE kc_core.snapshot (
  snapshot_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id        text NOT NULL REFERENCES kc_core.source,
  retrieved_at     timestamptz NOT NULL,
  content_sha256   char(64) NOT NULL,
  blob_uri         text NOT NULL,
  media_type       text NOT NULL,
  stated_effective text,
  UNIQUE (source_id, content_sha256)
);

-------------------------------------------------------------------------------
-- 3. Entities (one supertable gives every entity a global id)
-------------------------------------------------------------------------------
CREATE TABLE kc_core.entity (
  entity_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type text NOT NULL REFERENCES kc_meta.entity_type,
  natural_key text NOT NULL,
  UNIQUE (entity_type, natural_key)
);

CREATE TABLE kc_core.code_system (
  code_system_id text PRIMARY KEY,
  canonical_uri  text NOT NULL UNIQUE,
  publisher      text NOT NULL,
  license_class  text NOT NULL,
  cadence        text NOT NULL
);

CREATE TABLE kc_core.code_system_release (
  code_system_id text NOT NULL REFERENCES kc_core.code_system,
  version_label  text NOT NULL,
  released_on    date NOT NULL,
  valid          daterange NOT NULL,
  next_change_on date,                      -- next scheduled edition not yet loaded
  snapshot_id    uuid REFERENCES kc_core.snapshot,
  PRIMARY KEY (code_system_id, version_label)
);

CREATE TABLE kc_core.code (
  entity_id      uuid NOT NULL REFERENCES kc_core.entity,
  code_system_id text NOT NULL REFERENCES kc_core.code_system,
  code           text NOT NULL,
  display        text,                      -- empty where the display text is licensed
  item_type      text NOT NULL CHECK (item_type IN ('diagnosis','service','drug','supply','lab')),
  valid          daterange NOT NULL,
  first_release  text NOT NULL,
  replaced_by    text[],
  recorded       tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
  snapshot_id    uuid REFERENCES kc_core.snapshot,
  PRIMARY KEY (entity_id, valid, recorded),
  EXCLUDE USING gist (code_system_id WITH =, code WITH =, valid WITH &&) WHERE (upper_inf(recorded))
);
CREATE INDEX code_lookup ON kc_core.code (code_system_id, code);

CREATE TABLE kc_core.value_set (
  entity_id       uuid PRIMARY KEY REFERENCES kc_core.entity,
  canonical_url   text NOT NULL UNIQUE,
  version         text NOT NULL,
  name            text NOT NULL,
  definition      text NOT NULL CHECK (definition IN ('extensional','intensional')),
  filter          jsonb,                    -- intensional only, e.g. {"item_type":"drug"}
  owner_authority uuid REFERENCES kc_core.entity
);

CREATE TABLE kc_core.value_set_member (     -- relation: belongs_to (code -> value set)
  member_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  value_set_id   uuid NOT NULL REFERENCES kc_core.value_set,
  code_system_id text NOT NULL REFERENCES kc_core.code_system,
  code           text NOT NULL,
  valid          daterange NOT NULL,
  recorded       tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
  snapshot_id    uuid REFERENCES kc_core.snapshot,
  EXCLUDE USING gist (value_set_id WITH =, code_system_id WITH =, code WITH =, valid WITH &&)
    WHERE (upper_inf(recorded))
);
CREATE INDEX value_set_member_code ON kc_core.value_set_member (code_system_id, code);

CREATE TABLE kc_core.concept (
  entity_id  uuid PRIMARY KEY REFERENCES kc_core.entity,
  label      text NOT NULL,
  definition text NOT NULL
);

CREATE TABLE kc_core.concept_map (          -- relation: maps_to (concept -> code)
  map_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  concept_id     uuid NOT NULL REFERENCES kc_core.concept,
  code_system_id text NOT NULL REFERENCES kc_core.code_system,
  code           text NOT NULL,
  map_kind       text NOT NULL CHECK (map_kind IN ('equivalent','narrower','broader','related')),
  basis          text NOT NULL CHECK (basis IN ('stated','interpreted')),
  confidence     numeric(3,2),
  ships_in       text NOT NULL CHECK (ships_in IN ('shared_pack','client_license_only')),
  valid          daterange NOT NULL,
  recorded       tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
  snapshot_id    uuid REFERENCES kc_core.snapshot,
  reviewed_by    text[] NOT NULL DEFAULT '{}',
  CHECK (kc_core.n_approvers(reviewed_by) >= 2),
  EXCLUDE USING gist (concept_id WITH =, code_system_id WITH =, code WITH =, valid WITH &&)
    WHERE (upper_inf(recorded))
);
CREATE INDEX concept_map_code ON kc_core.concept_map (code_system_id, code);

CREATE TABLE kc_core.program (
  entity_id uuid PRIMARY KEY REFERENCES kc_core.entity,
  kind      text NOT NULL CHECK (kind IN ('medicare_ffs','medicare_advantage','medicaid_ffs',
                                          'medicaid_managed_care','exchange','employer_group')),
  name      text NOT NULL
);

CREATE TABLE kc_core.payer (
  entity_id         uuid PRIMARY KEY REFERENCES kc_core.entity,
  legal_name        text NOT NULL,
  short_name        text NOT NULL,
  parent_payer_id   uuid REFERENCES kc_core.payer,
  naic_code         text,
  clearinghouse_ids jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE kc_core.plan (                 -- relations offered_under, operated_by as columns;
  entity_id        uuid PRIMARY KEY REFERENCES kc_core.entity,  -- located_in as edges
  payer_id         uuid NOT NULL REFERENCES kc_core.payer,
  program_id       uuid NOT NULL REFERENCES kc_core.program,
  external_id      text,                     -- CMS contract and plan number, e.g. 'H1234-001'
  plan_type        text,                     -- HMO, PPO, D-SNP, ...
  dsnp_integration text CHECK (dsnp_integration IN ('fide','hide','coordination_only')),
  valid            daterange NOT NULL
);

CREATE TABLE kc_core.jurisdiction (
  entity_id  uuid PRIMARY KEY REFERENCES kc_core.entity,
  kind       text NOT NULL CHECK (kind IN ('nation','state','county','mac_ab','mac_dme','mac_hhh')),
                                             -- Medicare contractor regions differ for Part A/B, DME and home health
  code       text NOT NULL,
  name       text NOT NULL,
  contractor text                            -- Medicare contractor for a MAC jurisdiction
);

CREATE TABLE kc_core.authority (
  entity_id uuid PRIMARY KEY REFERENCES kc_core.entity,
  kind      text NOT NULL CHECK (kind IN ('statute','regulation','ncd','lcd','billing_article',
                 'state_rule','state_manual','payer_policy','payer_list','plan_document','guideline')),
  issuer_id uuid REFERENCES kc_core.entity,
  citation  text NOT NULL,
  layer     text NOT NULL CHECK (layer IN ('T0','T1','T2','T3','T4','T5','CG')),
  title     text NOT NULL
);

CREATE TABLE kc_core.authority_version (    -- relation: issued_in (authority -> snapshot)
  authority_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  authority_id     uuid NOT NULL REFERENCES kc_core.authority,
  version_label    text NOT NULL,
  valid_from_basis text NOT NULL CHECK (valid_from_basis IN ('stated','plan_year','document_date','unknown')),
  valid_from_lo    date NOT NULL,
  valid_from_hi    date NOT NULL,
  valid_to_lo      date,
  valid_to_hi      date,
  valid            daterange GENERATED ALWAYS AS (daterange(valid_from_hi, valid_to_lo)) STORED,
  valid_possible   daterange GENERATED ALWAYS AS (daterange(valid_from_lo, valid_to_hi)) STORED,
  snapshot_id      uuid NOT NULL REFERENCES kc_core.snapshot,
  recorded         tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
  CHECK (valid_from_lo <= valid_from_hi),
  CHECK (valid_from_basis = 'unknown' OR valid_from_lo = valid_from_hi),
  CHECK (valid_to_lo IS NULL OR valid_to_hi IS NULL OR valid_to_lo <= valid_to_hi),
  CHECK (valid_to_lo IS NULL OR valid_from_hi < valid_to_lo),
  EXCLUDE USING gist (authority_id WITH =, valid WITH &&) WHERE (upper_inf(recorded))
);

CREATE TABLE kc_core.authority_binding (    -- relation: binds (authority -> scope)
  binding_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  authority_id           uuid NOT NULL REFERENCES kc_core.authority,
  program_id             uuid REFERENCES kc_core.program,
  plan_id                uuid REFERENCES kc_core.plan,
  jurisdiction_id        uuid REFERENCES kc_core.jurisdiction,
  item_scope             text NOT NULL CHECK (item_scope IN ('all','items_and_services','drugs')),
  applies_when_secondary boolean NOT NULL DEFAULT false,  -- e.g. a Medicaid plan list when Medicare pays first
  valid                  daterange NOT NULL,
  recorded               tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
  snapshot_id            uuid REFERENCES kc_core.snapshot,
  reviewed_by            text[] NOT NULL DEFAULT '{}',
  CHECK (kc_core.n_approvers(reviewed_by) >= 2)
);
CREATE INDEX authority_binding_auth ON kc_core.authority_binding (authority_id);

CREATE TABLE kc_core.data_element (
  entity_id       uuid PRIMARY KEY REFERENCES kc_core.entity,
  name            text NOT NULL,
  code_system_id  text REFERENCES kc_core.code_system,
  code            text,                       -- LOINC for labs, ICD-10-CM for diagnoses
  value_type      text NOT NULL CHECK (value_type IN ('quantity','coded','boolean','date','attachment')),
  unit            text,                       -- UCUM
  us_core_profile text
);

CREATE TABLE kc_core.knowledge_artifact (   -- an insurer questionnaire or logic library
  entity_id     uuid PRIMARY KEY REFERENCES kc_core.entity,
  kind          text NOT NULL CHECK (kind IN ('questionnaire','library')),
  format        text NOT NULL,              -- 'FHIR SDC Questionnaire', 'CQL'
  canonical_url text NOT NULL,
  version       text NOT NULL,
  payer_id      uuid REFERENCES kc_core.payer,
  snapshot_id   uuid REFERENCES kc_core.snapshot
);

CREATE TABLE kc_core.endpoint (             -- relation: exposes (payer -> endpoint)
  entity_id      uuid PRIMARY KEY REFERENCES kc_core.entity,
  payer_id       uuid NOT NULL REFERENCES kc_core.payer,
  interface      text NOT NULL CHECK (interface IN ('crd','dtr','pas','payer_pa_api','x12_278',
                                                    'clearinghouse','portal','fax','phone')),
  preference     int NOT NULL,              -- lower is tried first
  base_url       text,
  version        text,
  excludes_drugs boolean NOT NULL DEFAULT false,
  live           boolean NOT NULL,
  verified_at    timestamptz,
  valid          daterange NOT NULL
);

-------------------------------------------------------------------------------
-- 4. Relationship table: the graph, stored relationally
-------------------------------------------------------------------------------
CREATE TABLE kc_core.edge (
  edge_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  relation_type text NOT NULL REFERENCES kc_meta.relation_type,
  from_id       uuid NOT NULL REFERENCES kc_core.entity,
  to_id         uuid NOT NULL REFERENCES kc_core.entity,
  attrs         jsonb NOT NULL DEFAULT '{}',
  valid         daterange NOT NULL,
  recorded      tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
  snapshot_id   uuid REFERENCES kc_core.snapshot,
  EXCLUDE USING gist (relation_type WITH =, from_id WITH =, to_id WITH =, valid WITH &&)
    WHERE (upper_inf(recorded))
);
CREATE INDEX edge_from ON kc_core.edge (from_id, relation_type);
CREATE INDEX edge_to   ON kc_core.edge (to_id, relation_type);

-- true when class c is d or a subclass of d
CREATE FUNCTION kc_core.is_a(c text, d text) RETURNS boolean LANGUAGE sql STABLE AS $$
  WITH RECURSIVE up(t) AS (SELECT c UNION SELECT e.parent_type FROM kc_meta.entity_type e JOIN up ON e.entity_type = up.t
                           WHERE e.parent_type IS NOT NULL)
  SELECT EXISTS (SELECT 1 FROM up WHERE t = d) $$;

CREATE FUNCTION kc_core.check_edge_types() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d text; r text; s text; fd text; td text;
BEGIN
  SELECT domain_type, range_type, stored_as INTO d, r, s FROM kc_meta.relation_type
   WHERE relation_type = NEW.relation_type;
  IF s <> 'edge' THEN
    RAISE EXCEPTION 'relation % is stored as a %, not as an edge', NEW.relation_type, s;
  END IF;
  SELECT entity_type INTO fd FROM kc_core.entity WHERE entity_id = NEW.from_id;
  SELECT entity_type INTO td FROM kc_core.entity WHERE entity_id = NEW.to_id;
  IF NOT kc_core.is_a(fd, d) OR NOT kc_core.is_a(td, r) THEN
    RAISE EXCEPTION 'edge % expects % -> %, got % -> %', NEW.relation_type, d, r, fd, td;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER edge_types BEFORE INSERT OR UPDATE ON kc_core.edge
  FOR EACH ROW EXECUTE FUNCTION kc_core.check_edge_types();

-------------------------------------------------------------------------------
-- 5. Rules (relations about, states, requires, documented_by live here)
-------------------------------------------------------------------------------
CREATE TABLE kc_core.rule (
  rule_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_kind            text NOT NULL REFERENCES kc_meta.rule_kind,
  aspect               text NOT NULL,
  subject_id           uuid NOT NULL REFERENCES kc_core.entity,     -- about
  authority_id         uuid NOT NULL REFERENCES kc_core.authority,  -- states
  authority_version_id uuid NOT NULL REFERENCES kc_core.authority_version,
  program_id           uuid REFERENCES kc_core.program,             -- scope; empty = any
  payer_id             uuid REFERENCES kc_core.payer,
  plan_id              uuid REFERENCES kc_core.plan,
  jurisdiction_id      uuid REFERENCES kc_core.jurisdiction,
  effect               text NOT NULL CHECK (effect IN ('override','ceiling','floor','additive')),
  conditions           jsonb NOT NULL DEFAULT '[]',  -- [{"fact":"encounter_type","op":"in","value":["emergency"]}]
  payload              jsonb NOT NULL DEFAULT '{}',
  basis                text NOT NULL CHECK (basis IN ('stated','interpreted')),
  mapping_confidence   numeric(3,2),                 -- set when basis = 'interpreted'
  not_stated           text[] NOT NULL DEFAULT '{}',
  date_basis           text NOT NULL DEFAULT 'date_of_service'
                         CHECK (date_basis IN ('date_of_service','order','discharge','remittance')),
  knowledge_artifact_id uuid REFERENCES kc_core.knowledge_artifact,  -- documented_by
  -- valid time
  valid_from_basis     text NOT NULL CHECK (valid_from_basis IN ('stated','plan_year','document_date','unknown')),
  valid_from_lo        date NOT NULL,
  valid_from_hi        date NOT NULL,
  valid_to_lo          date,
  valid_to_hi          date,
  valid                daterange GENERATED ALWAYS AS (daterange(valid_from_hi, valid_to_lo)) STORED,
  valid_possible       daterange GENERATED ALWAYS AS (daterange(valid_from_lo, valid_to_hi)) STORED,
  -- system time and provenance
  recorded             tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
  status               text NOT NULL CHECK (status IN ('draft','in_review','active','retired')),
  snapshot_id          uuid NOT NULL REFERENCES kc_core.snapshot,
  source_locator       jsonb NOT NULL,
  source_quote         text NOT NULL,
  extracted_by         text NOT NULL CHECK (extracted_by IN ('parser','llm_assisted','manual')),
  reviewed_by          text[] NOT NULL DEFAULT '{}',
  reviewed_at          timestamptz,
  release_id           text,                         -- first release that shipped this row
  license_class        text NOT NULL DEFAULT 'public',
  scope_key            text GENERATED ALWAYS AS (
                         coalesce(program_id::text,'*')||'|'||coalesce(payer_id::text,'*')||'|'||
                         coalesce(plan_id::text,'*')||'|'||coalesce(jurisdiction_id::text,'*')) STORED,
  CHECK (valid_from_lo <= valid_from_hi),
  CHECK (valid_from_basis = 'unknown' OR valid_from_lo = valid_from_hi),
  CHECK (valid_to_lo IS NULL OR valid_to_hi IS NULL OR valid_to_lo <= valid_to_hi),
  CHECK (valid_to_lo IS NULL OR valid_from_hi < valid_to_lo),
  CHECK (status <> 'active' OR kc_core.n_approvers(reviewed_by) >= 2),
  CHECK (basis = 'stated' OR mapping_confidence IS NOT NULL),
  -- one authority, subject, rule kind, aspect and scope: no two current active versions
  -- may both be confirmed valid on the same date
  EXCLUDE USING gist (authority_id WITH =, subject_id WITH =, rule_kind WITH =, aspect WITH =,
                      scope_key WITH =, valid WITH &&)
    WHERE (status = 'active' AND upper_inf(recorded))
);
CREATE INDEX rule_lookup  ON kc_core.rule (subject_id, aspect) WHERE status = 'active';
CREATE INDEX rule_possible ON kc_core.rule USING gist (valid_possible);
CREATE INDEX rule_recorded ON kc_core.rule USING gist (recorded);
CREATE INDEX rule_payload ON kc_core.rule USING gin (payload);

-- relation: requires (rule -> data element). Items sit in numbered groups.
-- A rule is met when every group is met (or any group, if the rule's payload logic is 'any');
-- a group is met when all or any of its items are met, per group_logic.
-- L39237 example: group 1 = any(Hb < 10, Hct < 30); group 2 = all(GFR < 45); group 3 = all(creatinine within 1 month).
CREATE TABLE kc_core.rule_requirement (
  requirement_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_id         uuid NOT NULL,             -- a kc_core.rule or a client rule (T5)
  group_no        int  NOT NULL DEFAULT 1,
  group_logic     text NOT NULL DEFAULT 'all' CHECK (group_logic IN ('all','any')),
  data_element_id uuid NOT NULL REFERENCES kc_core.data_element,
  test            jsonb NOT NULL,            -- {"op":"<","value":10,"unit":"g/dL"} or {"op":"present"}
  lookback        interval,
  when_needed     text NOT NULL CHECK (when_needed IN ('initial','continuation','both','appeal','always'))
);
CREATE INDEX rule_requirement_rule ON kc_core.rule_requirement (rule_id, group_no);

-------------------------------------------------------------------------------
-- 6. Governance
-------------------------------------------------------------------------------
CREATE TABLE kc_gov.change_flag (
  flag_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id    text NOT NULL REFERENCES kc_core.source,
  snapshot_id  uuid NOT NULL REFERENCES kc_core.snapshot,
  detected_at  timestamptz NOT NULL,
  resolved_at  timestamptz,                -- when it was reviewed or dismissed; open until then
  applies_from date,
  state        text NOT NULL CHECK (state IN ('open','reviewed','dismissed'))
);

CREATE TABLE kc_gov.review (
  review_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  target_table text NOT NULL,
  target_id    uuid NOT NULL,
  reviewer     text NOT NULL,
  decision     text NOT NULL CHECK (decision IN ('approve','request_changes','reject')),
  reviewed_at  timestamptz NOT NULL DEFAULT now(),
  git_commit   text
);

CREATE TABLE kc_gov.release (
  release_id   text PRIMARY KEY,
  built_at     timestamptz NOT NULL,
  git_commit   text NOT NULL,
  record_as_of timestamptz NOT NULL,
  digest       text NOT NULL,
  status       text NOT NULL CHECK (status IN ('built','tested','published','withdrawn'))
);

-- History is never deleted. A correction or a retirement closes the old row in record time and adds a new one,
-- so any past answer can be replayed. The database refuses DELETE on every table that holds that history.
CREATE FUNCTION kc_core.refuse_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'history is not deleted in %: close the row in record time instead', TG_TABLE_NAME;
END $$;
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.rule              FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.rule_requirement  FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.authority_version FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.authority_binding FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.snapshot          FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.code              FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.value_set_member  FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.concept_map       FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.edge              FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();

-- Every pair of current active rules that might overlap once uncertain dates are known.
CREATE VIEW kc_gov.possible_overlap AS
SELECT a.rule_id AS rule_a, b.rule_id AS rule_b, a.rule_kind, a.valid_possible AS a_possible, b.valid_possible AS b_possible
  FROM kc_core.rule a JOIN kc_core.rule b
    ON a.rule_id < b.rule_id AND a.authority_id = b.authority_id AND a.subject_id = b.subject_id
   AND a.rule_kind = b.rule_kind AND a.scope_key = b.scope_key
   AND a.valid_possible && b.valid_possible
 WHERE a.status = 'active' AND b.status = 'active' AND upper_inf(a.recorded) AND upper_inf(b.recorded);

-------------------------------------------------------------------------------
-- 7. Client copy only
-------------------------------------------------------------------------------
CREATE TABLE kc_client.sync_log (           -- every successful sync, so past freshness can be reproduced
  synced_at timestamptz PRIMARY KEY,
  release_id text NOT NULL
);
CREATE TABLE kc_client.sync_limit (         -- how a copy becomes cautious when updates stop
  source_class text PRIMARY KEY,
  aging_after  interval NOT NULL,
  stop_after   interval NOT NULL
);
CREATE TABLE kc_client.tool_audit (
  audit_id    bigserial PRIMARY KEY,
  called_at   timestamptz NOT NULL DEFAULT now(),
  tool        text NOT NULL,
  release_id  text NOT NULL,
  status      text NOT NULL,
  rule_ids    uuid[] NOT NULL DEFAULT '{}',
  duration_ms numeric NOT NULL
);
CREATE TABLE kc_client.feedback_count (
  rule_id     uuid NOT NULL,
  outcome     text NOT NULL CHECK (outcome IN ('agreed','disagreed')),
  n           int NOT NULL,
  period      daterange NOT NULL
);

-------------------------------------------------------------------------------
-- 8. Denial reason codes (read by map_reason_code; used by denial management later)
-------------------------------------------------------------------------------
CREATE TABLE kc_core.reason_code (
  reason_code_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code_set    text NOT NULL CHECK (code_set IN ('CARC','RARC')),  -- X12 claim adjustment / remark codes
  code        text NOT NULL,
  description text NOT NULL,
  category    text NOT NULL,                 -- e.g. authorization, coding, eligibility
  valid       daterange NOT NULL,
  recorded    tstzrange NOT NULL DEFAULT tstzrange(now(), NULL),
  snapshot_id uuid REFERENCES kc_core.snapshot,
  EXCLUDE USING gist (code_set WITH =, code WITH =, valid WITH &&) WHERE (upper_inf(recorded))
);
CREATE TRIGGER no_delete BEFORE DELETE ON kc_core.reason_code       FOR EACH ROW EXECUTE FUNCTION kc_core.refuse_delete();

-------------------------------------------------------------------------------
-- 9. Client overlay (T5): a client's own policies, contracts and gold-card status.
--    Same shape as the shared tables; kept only in the client copy, never shipped to Kanini.
-------------------------------------------------------------------------------
CREATE TABLE kc_client.client_authority (LIKE kc_core.authority INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
ALTER TABLE kc_client.client_authority ADD CHECK (layer = 'T5');
CREATE TABLE kc_client.client_authority_version (LIKE kc_core.authority_version INCLUDING DEFAULTS INCLUDING GENERATED INCLUDING CONSTRAINTS);
CREATE TABLE kc_client.client_binding (LIKE kc_core.authority_binding INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
CREATE TABLE kc_client.client_rule (LIKE kc_core.rule INCLUDING DEFAULTS INCLUDING GENERATED INCLUDING CONSTRAINTS);
ALTER TABLE kc_client.client_rule ALTER COLUMN snapshot_id DROP NOT NULL;   -- client documents stay with the client

-- rule_requirement.rule_id can point to a shared rule or to a client rule, so it cannot be one foreign key.
-- This trigger checks it instead: the rule must exist in one of the two tables.
CREATE FUNCTION kc_core.check_requirement_rule() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM kc_core.rule WHERE rule_id = NEW.rule_id)
     AND NOT EXISTS (SELECT 1 FROM kc_client.client_rule WHERE rule_id = NEW.rule_id) THEN
    RAISE EXCEPTION 'rule_requirement points to rule % which does not exist', NEW.rule_id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER requirement_rule_exists BEFORE INSERT OR UPDATE OF rule_id ON kc_core.rule_requirement
  FOR EACH ROW EXECUTE FUNCTION kc_core.check_requirement_rule();

-- What the resolver reads: shared release plus the client's own layer.
CREATE VIEW kc_api.all_authority AS
  SELECT * FROM kc_core.authority UNION ALL SELECT * FROM kc_client.client_authority;
CREATE VIEW kc_api.all_authority_version AS
  SELECT * FROM kc_core.authority_version UNION ALL SELECT * FROM kc_client.client_authority_version;
CREATE VIEW kc_api.all_binding AS
  SELECT * FROM kc_core.authority_binding UNION ALL SELECT * FROM kc_client.client_binding;
CREATE VIEW kc_api.all_rule AS
  SELECT * FROM kc_core.rule UNION ALL SELECT * FROM kc_client.client_rule;

-------------------------------------------------------------------------------
-- 10. One view of the whole graph. Hot-path relations are stored as columns and link tables
--     so PostgreSQL can enforce keys and date constraints on each; this view puts them back
--     together as one edge list for traversal, visualisation and export.
-------------------------------------------------------------------------------
CREATE VIEW kc_core.graph_edges AS
  SELECT relation_type, from_id, to_id, valid, recorded FROM kc_core.edge
  UNION ALL SELECT 'belongs_to', c.entity_id, m.value_set_id, m.valid, m.recorded
    FROM kc_core.value_set_member m JOIN kc_core.code c ON c.code_system_id = m.code_system_id AND c.code = m.code AND upper_inf(c.recorded)
  UNION ALL SELECT 'maps_to', cm.concept_id, c.entity_id, cm.valid, cm.recorded
    FROM kc_core.concept_map cm JOIN kc_core.code c ON c.code_system_id = cm.code_system_id AND c.code = cm.code AND upper_inf(c.recorded)
  UNION ALL SELECT 'offered_under', p.entity_id, p.program_id, p.valid, tstzrange(NULL,NULL) FROM kc_core.plan p
  UNION ALL SELECT 'operated_by', p.entity_id, p.payer_id, p.valid, tstzrange(NULL,NULL) FROM kc_core.plan p
  UNION ALL SELECT 'states', r.authority_id, r.rule_id, r.valid, r.recorded FROM kc_core.rule r
  UNION ALL SELECT 'about', r.rule_id, r.subject_id, r.valid, r.recorded FROM kc_core.rule r
  UNION ALL SELECT 'requires', q.rule_id, q.data_element_id, daterange(NULL,NULL), tstzrange(NULL,NULL) FROM kc_core.rule_requirement q
  UNION ALL SELECT 'binds', b.authority_id, coalesce(b.plan_id, b.program_id, b.jurisdiction_id), b.valid, b.recorded
    FROM kc_core.authority_binding b
  UNION ALL SELECT 'exposes', e.payer_id, e.entity_id, e.valid, tstzrange(NULL,NULL) FROM kc_core.endpoint e;
