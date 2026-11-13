"""Build a Kanini Context release and the client copy the agent reads (spec sections 7.1 step 7 and 6.6).

    python -m kanini_context.release.build                 # needs every rule file reviewed
    python -m kanini_context.release.build --test-review   # local TEST build (see curation/load.py)

Steps:
  1. a fresh build database (kc_build): schema, registry, resolver, agent functions, then the loader
  2. a digest of every active rule, so two builds of the same files can be compared
  3. the client copy (kc_client), cloned from the build: the release row with the last date it answers
     for, the first sync, the pack's release id as a database setting, and every rule stamped with it
  4. the graph view of the copy in Apache AGE (when the extension is installed), named ref_YYYY_MM: every
     entity, rule, snapshot and source as a vertex, every relation from kc_core.graph_edges as an edge, and
     (Rule)-[:SOURCED_FROM]->(Source) for each rule; answers always come from the relational tables

Connection: the usual PostgreSQL variables (PGHOST, PGPORT, PGUSER, PGPASSWORD); default the local test server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from kanini_context.release.owl import export as owl_export

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "kanini_context" / "db"
from kanini_context import env  # noqa: F401,E402  (loads .env)

# At the client the pack is read-only (spec 5.5 and 7.2). The tool server works as kc_agent, which may read the
# pack, run the kc_api tools and add audit rows, and nothing else. The role is shared by the server; its grants are
# per database.
AGENT_ROLE_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kc_agent') THEN CREATE ROLE kc_agent NOLOGIN; END IF;
END $$;
GRANT kc_agent TO CURRENT_USER;
GRANT USAGE ON SCHEMA kc_meta, kc_core, kc_api, kc_gov, kc_client TO kc_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA kc_meta, kc_core, kc_api, kc_gov, kc_client TO kc_agent;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA kc_api TO kc_agent;
GRANT INSERT ON kc_client.tool_audit TO kc_agent;
GRANT USAGE ON SEQUENCE kc_client.tool_audit_audit_id_seq TO kc_agent;
"""

# The last date of service a release answers for: the day before the earliest change the pack already knows is
# coming (an open change flag, or the end of a loaded edition). With none known, the end of the following month.
COVERS_THROUGH_SQL = """
SELECT coalesce(least(
         (SELECT min(applies_from) FROM kc_gov.change_flag WHERE state = 'open' AND applies_from > %(d)s::date),
         (SELECT min(valid_to_lo) FROM kc_core.authority_version WHERE valid_to_lo > %(d)s::date AND upper_inf(recorded))) - 1,
       (date_trunc('month', %(d)s::date) + interval '2 months' - interval '1 day')::date)"""
GRAPH = "ref"
SQL_FILES = ["schema.sql", "registry.sql", "resolver.sql", "api.sql"]


def dsn(db: str) -> str:
    return f"dbname={db}"


def run_sql(db: str, files: list[str]):
    """Each file runs as one script (no psql needed); the first error stops the build."""
    with psycopg.connect(dsn(db), autocommit=True) as c:
        for f in files:
            try:
                c.execute((DB / f).read_text())
            except psycopg.Error as ex:
                raise SystemExit(f"{f}: {ex}")


def fresh_db(name: str):
    with psycopg.connect(dsn("postgres"), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')


def rule_digest(cur) -> str:
    rows = cur.execute("""SELECT rule_key, lower(valid)::text, rule_kind, payload::text, conditions::text, basis, source_quote
                            FROM kc_core.rule WHERE status = 'active' AND upper_inf(recorded) ORDER BY 1, 2""").fetchall()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        return (out or "uncommitted") + ("+changes" if dirty else "")
    except FileNotFoundError:
        return "no-git"


def label(entity_type: str) -> str:
    return "".join(p.capitalize() for p in entity_type.split("_"))


def build_graph(cur, graph: str) -> tuple[int, int] | None:
    if not cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'age'").fetchone():
        return None
    cur.execute("CREATE EXTENSION IF NOT EXISTS age")
    cur.execute("LOAD 'age'")
    cur.execute('SET search_path = ag_catalog, "$user", public')
    cur.execute("SELECT create_graph(%s)", (graph,))
    # vertices: every entity, every current rule (id = rule_key), every snapshot and source
    verts = cur.execute("""
        SELECT e.entity_id::text, e.entity_type, jsonb_build_object('id', e.natural_key)
               || CASE WHEN e.entity_type = 'concept'   -- the scope's "Service": a service concept such as esa-ckd-nd
                       THEN jsonb_build_object('kind', 'service', 'label', (SELECT label FROM kc_core.concept c WHERE c.entity_id = e.entity_id))
                       ELSE '{}' END
          FROM kc_core.entity e
        UNION ALL SELECT r.rule_id::text, 'rule', jsonb_build_object('id', r.rule_key, 'valid_from', lower(r.valid)::text,
                         'kind', r.rule_kind, 'basis', r.basis, 'release', r.release_id, 'citation', a.citation,
                         'page', r.source_locator ->> 'page', 'quote', r.source_quote)   -- the cited passage
          FROM kc_core.rule r JOIN kc_core.authority a ON a.entity_id = r.authority_id WHERE upper_inf(r.recorded)
        UNION ALL SELECT snapshot_id::text, 'snapshot', jsonb_build_object('id', source_id, 'sha256', content_sha256,
                         'retrieved_at', retrieved_at::text) FROM kc_core.snapshot
        UNION ALL SELECT 'source:' || s.source_id, 'source', jsonb_build_object('id', s.source_id, 'url', s.url,
                         'doc', coalesce((SELECT min(a.citation) FROM kc_core.authority_version v JOIN kc_core.authority a ON a.entity_id = v.authority_id
                                          JOIN kc_core.snapshot sn ON sn.snapshot_id = v.snapshot_id WHERE sn.source_id = s.source_id
                                         HAVING count(DISTINCT a.entity_id) = 1), s.name))
          FROM kc_core.source s""").fetchall()
    kinds = {}
    for vid, etype, props in verts:
        kinds[vid] = label(etype)
        props = {k: v for k, v in {**props, "uid": vid}.items() if v is not None}
        body = ", ".join(f"{k}: ${k}" for k in props)   # keys are fixed names above; values go in as parameters
        cur.execute(f"SELECT * FROM cypher('{graph}', $$ CREATE (v:{label(etype)} {{{body}}}) $$, %s) AS (v agtype)",
                    (json.dumps(props),))
    # The links view gives a binding one target (its plan, else its program, else its jurisdiction); in the graph a
    # binding scoped to a program and a state also links the authority to that state, so where a rule applies can be
    # followed as edges. Repeated bindings (one per state or date) give one edge per target.
    edges = cur.execute("""
        SELECT relation_type, from_id::text, to_id::text FROM kc_core.graph_edges WHERE upper_inf(recorded)
        UNION SELECT 'binds', b.authority_id::text, b.jurisdiction_id::text FROM kc_core.authority_binding b
          WHERE upper_inf(b.recorded) AND b.jurisdiction_id IS NOT NULL AND coalesce(b.plan_id, b.program_id) IS NOT NULL
        UNION SELECT 'sourced_from', r.rule_id::text, 'source:' || sn.source_id
          FROM kc_core.rule r JOIN kc_core.snapshot sn ON sn.snapshot_id = r.snapshot_id WHERE upper_inf(r.recorded)
        UNION SELECT 'copy_of', snapshot_id::text, 'source:' || source_id FROM kc_core.snapshot""").fetchall()
    n = 0
    for rel, a, b in edges:
        if a not in kinds or b not in kinds:
            continue
        cur.execute(f"""SELECT * FROM cypher('{graph}', $$ MATCH (a:{kinds[a]} {{uid: $a}}), (b:{kinds[b]} {{uid: $b}})
                        CREATE (a)-[:{rel.upper()}]->(b) $$, %s) AS (e agtype)""", (json.dumps({"a": a, "b": b}),))
        n += 1
    cur.execute('SET search_path = "$user", public')
    return len(verts), n


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--test-review", action="store_true")
    ap.add_argument("--release", help="release id, default YYYY.MM.0 as in spec 6.6 (TEST- prefix for a test build)")
    ap.add_argument("--no-graph", action="store_true")
    ap.add_argument("--covers-through", help="override the last date of service the release answers for (YYYY-MM-DD)")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    release = args.release or now.strftime("%Y.%m.0")   # e.g. 2026.11.0
    if args.test_review and not release.startswith("TEST-"):
        release = "TEST-" + release
    global GRAPH
    GRAPH = now.strftime("ref_%Y_%m")   # one graph per monthly reference pack, e.g. ref_2026_11

    # 1. build database
    fresh_db(env.BUILD_DB)
    run_sql(env.BUILD_DB, SQL_FILES)
    os.environ["KC_DSN"] = dsn(env.BUILD_DB)
    load = [sys.executable, "-m", "kanini_context.curation.load"] + (["--test-review"] if args.test_review else [])
    r = subprocess.run(load, cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip())
        return 1

    # 2. digest
    with psycopg.connect(dsn(env.BUILD_DB)) as c:
        digest = rule_digest(c.cursor())

    # 3. client copy
    with psycopg.connect(dsn("postgres"), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{env.CLIENT_DB}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{env.CLIENT_DB}" TEMPLATE "{env.BUILD_DB}"')
        c.execute(f"ALTER DATABASE \"{env.CLIENT_DB}\" SET kc.release = '{release}'")
    with psycopg.connect(dsn(env.CLIENT_DB)) as c, c.cursor() as cur:
        covers = args.covers_through or cur.execute(COVERS_THROUGH_SQL, {"d": now.date()}).fetchone()[0]
        cur.execute("INSERT INTO kc_gov.release VALUES (%s, %s, %s, %s, %s, 'built', %s)",
                    (release, now, git_commit(), now, digest, covers))
        cur.execute("UPDATE kc_core.rule SET release_id = %s WHERE release_id IS NULL", (release,))
        cur.execute("INSERT INTO kc_client.sync_log VALUES (%s, %s)", (now, release))
        cur.execute(AGENT_ROLE_SQL)
        n_rules = cur.execute("SELECT count(*) FROM kc_core.rule WHERE status = 'active'").fetchone()[0]
        graph = None if args.no_graph else build_graph(cur, GRAPH)

    t, c, o, d = owl_export(env.BUILD_DB)
    print(f"ontology: release/kanini-context.ttl, {c} classes, {o} relations, {d} attributes")
    print(f"release {release}: {n_rules} active rules, digest {digest[:12]}, answers dates of service through {covers}")
    if graph:
        print(f"graph: {graph[0]} vertices, {graph[1]} edges")
    elif not args.no_graph:
        print("graph: Apache AGE not installed; skipped (answers do not depend on it)")
    print(f"client copy: database {env.CLIENT_DB}{f', graph {GRAPH}' if graph else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
