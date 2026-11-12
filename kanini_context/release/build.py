"""Build a Kanini Context release and the client copy the agent reads (spec sections 7.1 step 7 and 6.6).

    python -m kanini_context.release.build                 # needs every rule file reviewed
    python -m kanini_context.release.build --test-review   # local TEST build (see curation/load.py)

Steps:
  1. a fresh build database (kc_build): schema, registry, resolver, agent functions, then the loader
  2. a digest of every active rule, so two builds of the same files can be compared
  3. the client copy (kc_client), cloned from the build: the release row with the last date it answers
     for, the first sync, the pack's release id as a database setting, and every rule stamped with it
  4. the graph view of the copy in Apache AGE (when the extension is installed): every entity, rule and
     snapshot as a vertex and every relation from kc_core.graph_edges as an edge, so the same pack can be
     browsed as a graph; answers always come from the relational tables

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

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "kanini_context" / "db"
ENV = {"PGHOST": "/var/lib/postgresql/kctest", "PGPORT": "5544", "PGUSER": "postgres"}
for k, v in ENV.items():
    os.environ.setdefault(k, v)

COVERS_THROUGH = "2026-12-31"   # the next known changes start 2027-01-01 (UHC step therapy edition, WISeR year 2)
SQL_FILES = ["schema.sql", "registry.sql", "resolver.sql", "api.sql"]


def dsn(db: str) -> str:
    return f"dbname={db}"


def run(cmd: list[str]):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} failed:\n{r.stderr or r.stdout}")
    return r.stdout


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
    verts = cur.execute("""
        SELECT entity_id::text, entity_type, natural_key FROM kc_core.entity
        UNION ALL SELECT rule_id::text, 'rule', rule_key FROM kc_core.rule WHERE upper_inf(recorded)
        UNION ALL SELECT snapshot_id::text, 'snapshot', source_id FROM kc_core.snapshot
        UNION ALL SELECT 'source:' || source_id, 'source', source_id FROM kc_core.source""").fetchall()
    kinds = {}
    for vid, etype, key in verts:
        kinds[vid] = label(etype)
        cur.execute(f"SELECT * FROM cypher('{graph}', $$ CREATE (:{label(etype)} {{id: $id, key: $key}}) $$, %s) AS (v agtype)",
                    (json.dumps({"id": vid, "key": key}),))
    edges = cur.execute("""
        SELECT relation_type, from_id::text, to_id::text FROM kc_core.graph_edges WHERE upper_inf(recorded)
        UNION ALL SELECT 'sourced_from', rule_id::text, snapshot_id::text FROM kc_core.rule WHERE upper_inf(recorded)
        UNION ALL SELECT 'copy_of', snapshot_id::text, 'source:' || source_id FROM kc_core.snapshot""").fetchall()
    n = 0
    for rel, a, b in edges:
        if a not in kinds or b not in kinds:
            continue
        cur.execute(f"""SELECT * FROM cypher('{graph}', $$ MATCH (a:{kinds[a]} {{id: $a}}), (b:{kinds[b]} {{id: $b}})
                        CREATE (a)-[:{rel.upper()}]->(b) $$, %s) AS (e agtype)""", (json.dumps({"a": a, "b": b}),))
        n += 1
    cur.execute('SET search_path = "$user", public')
    return len(verts), n


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--test-review", action="store_true")
    ap.add_argument("--release", help="release id, default YYYY.MM.DD (TEST- prefix for a test build)")
    ap.add_argument("--no-graph", action="store_true")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    release = args.release or now.strftime("%Y.%m.%d")
    if args.test_review and not release.startswith("TEST-"):
        release = "TEST-" + release

    # 1. build database
    fresh_db("kc_build")
    for f in SQL_FILES:
        run(["psql", "-q", "-X", "-v", "ON_ERROR_STOP=1", "-d", "kc_build", "-f", str(DB / f)])
    os.environ["KC_DSN"] = dsn("kc_build")
    load = [sys.executable, "-m", "kanini_context.curation.load"] + (["--test-review"] if args.test_review else [])
    r = subprocess.run(load, cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip())
        return 1

    # 2. digest
    with psycopg.connect(dsn("kc_build")) as c:
        digest = rule_digest(c.cursor())

    # 3. client copy
    with psycopg.connect(dsn("postgres"), autocommit=True) as c:
        c.execute('DROP DATABASE IF EXISTS "kc_client" WITH (FORCE)')
        c.execute('CREATE DATABASE "kc_client" TEMPLATE "kc_build"')
        c.execute(f"ALTER DATABASE kc_client SET kc.release = '{release}'")
    with psycopg.connect(dsn("kc_client")) as c, c.cursor() as cur:
        cur.execute("INSERT INTO kc_gov.release VALUES (%s, %s, %s, %s, %s, 'built', %s)",
                    (release, now, git_commit(), now, digest, COVERS_THROUGH))
        cur.execute("UPDATE kc_core.rule SET release_id = %s WHERE release_id IS NULL", (release,))
        cur.execute("INSERT INTO kc_client.sync_log VALUES (%s, %s)", (now, release))
        n_rules = cur.execute("SELECT count(*) FROM kc_core.rule WHERE status = 'active'").fetchone()[0]
        graph = None if args.no_graph else build_graph(cur, "kc_" + "".join(ch if ch.isalnum() else "_" for ch in release.lower()))

    print(f"release {release}: {n_rules} active rules, digest {digest[:12]}, answers dates of service through {COVERS_THROUGH}")
    if graph:
        print(f"graph: {graph[0]} vertices, {graph[1]} edges")
    elif not args.no_graph:
        print("graph: Apache AGE not installed; skipped (answers do not depend on it)")
    print("client copy: database kc_client")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
