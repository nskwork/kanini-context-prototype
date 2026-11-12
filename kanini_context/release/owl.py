"""The ontology as an OWL file (Turtle), generated from the registry and the table columns of a built pack, so the
formal ontology and the physical schema come from one source (spec sections 6.1 and 6.2, Appendix E).

    python -m kanini_context.release.owl            # writes kanini_context/release/kanini-context.ttl from kc_build

Run by release/build.py after each build. Classes come from kc_meta.entity_type, rule kinds become subclasses of
Rule, the kind lists (authority, program, endpoint, knowledge artifact) come from the tables' check constraints,
relations from kc_meta.relation_type, and datatype properties from the columns of each class table.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg
from rdflib import OWL, RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

from kanini_context import env

OUT = Path(__file__).resolve().parent / "kanini-context.ttl"
KC = Namespace("https://kanini.com/ontology/kanini-context#")
XS = {"text": XSD.string, "date": XSD.date, "timestamp with time zone": XSD.dateTime, "boolean": XSD.boolean,
      "integer": XSD.integer, "numeric": XSD.decimal, "interval": XSD.duration}
TABLES = {"code": "Code", "value_set": "ValueSet", "concept": "Concept", "program": "Program", "payer": "Payer", "plan": "Plan",
          "jurisdiction": "Jurisdiction", "authority": "Authority", "data_element": "DataElement",
          "knowledge_artifact": "KnowledgeArtifact", "endpoint": "Endpoint", "rule": "Rule", "source": "Source", "snapshot": "Snapshot"}
KIND_LISTS = {("authority", "kind"): "Authority", ("program", "kind"): "Program", ("endpoint", "interface"): "Endpoint",
              ("knowledge_artifact", "kind"): "KnowledgeArtifact"}
SKIP = {"entity_id", "rule_id", "scope_key", "subject_id", "authority_id", "authority_version_id", "program_id", "payer_id", "plan_id",
        "jurisdiction_id", "knowledge_artifact_id", "issuer_id", "parent_payer_id", "owner_authority", "snapshot_id", "source_id",
        "code_system_id"}


def cname(s: str) -> str:
    return "".join(p.capitalize() for p in s.split("_"))


def pname(s: str) -> str:
    p = s.split("_")
    return p[0] + "".join(x.capitalize() for x in p[1:])


def check_values(cur, table: str, column: str) -> list[str]:
    """The allowed values of a column, read from its CHECK (... IN (...)) constraint."""
    for (d,) in cur.execute("""SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                                JOIN pg_namespace n ON n.oid = t.relnamespace
                               WHERE n.nspname = 'kc_core' AND t.relname = %s AND c.contype = 'c'""", (table,)).fetchall():
        if re.search(rf"\b{column}\b", d) and "ANY" in d:
            return re.findall(r"'([a-z0-9_]+)'::text", d)
    return []


def export(db: str, out: Path = OUT) -> tuple[int, int, int, int]:
    g = Graph()
    g.bind("kc", KC)
    g.bind("owl", OWL)
    onto = URIRef("https://kanini.com/ontology/kanini-context")
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, OWL.versionInfo, Literal("prototype 1.0, from schema specification v1.5")))
    g.add((onto, RDFS.label, Literal("Kanini Context ontology")))
    with psycopg.connect(f"dbname={db}") as con:
        cur = con.cursor()
        for t, parent, _mod, desc, fhir in cur.execute(
                "SELECT entity_type, parent_type, module, description, fhir_resource FROM kc_meta.entity_type ORDER BY 1").fetchall():
            c = KC[cname(t)]
            g.add((c, RDF.type, OWL.Class))
            g.add((c, RDFS.label, Literal(t.replace("_", " "))))
            g.add((c, RDFS.comment, Literal(desc)))
            if parent and parent != "thing":
                g.add((c, RDFS.subClassOf, KC[cname(parent)]))
            if fhir:
                g.add((c, KC.nearestFhirResource, Literal(fhir)))
        for k, parent, aspect, eff in cur.execute(
                "SELECT rule_kind, parent_kind, aspect, default_effect FROM kc_meta.rule_kind ORDER BY 1").fetchall():
            c = KC[cname(k)]
            g.add((c, RDF.type, OWL.Class))
            g.add((c, RDFS.subClassOf, KC[cname(parent)] if parent else KC.Rule))
            g.add((c, RDFS.comment, Literal(f"aspect: {aspect}; default effect: {eff}")))
        for (table, column), parent in KIND_LISTS.items():
            for kk in check_values(cur, table, column):
                c = KC[cname(kk) + ("" if parent == "Authority" else parent)]
                g.add((c, RDF.type, OWL.Class))
                g.add((c, RDFS.subClassOf, KC[parent]))
                g.add((c, RDFS.label, Literal(kk.replace("_", " "))))
        for r, d, rg, card, stored, desc in cur.execute(
                "SELECT relation_type, domain_type, range_type, cardinality, stored_as, description FROM kc_meta.relation_type ORDER BY 1").fetchall():
            p = KC[pname(r)]
            g.add((p, RDF.type, OWL.ObjectProperty))
            g.add((p, RDFS.domain, KC[cname(d)]))
            g.add((p, RDFS.range, KC[cname(rg)]))
            g.add((p, RDFS.comment, Literal(f"{desc} ({card}; stored as {stored})")))
            if card in ("n:1", "1:1"):
                g.add((p, RDF.type, OWL.FunctionalProperty))
        for tbl, cls in TABLES.items():
            for col, dt in cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                                           WHERE table_schema = 'kc_core' AND table_name = %s ORDER BY ordinal_position""", (tbl,)).fetchall():
                if col in SKIP:
                    continue
                p = KC[pname(tbl + "_" + col)]
                g.add((p, RDF.type, OWL.DatatypeProperty))
                g.add((p, RDFS.domain, KC[cls]))
                g.add((p, RDFS.range, XS.get(dt, XSD.string)))
                g.add((p, RDFS.label, Literal(col.replace("_", " "))))
    g.add((KC.nearestFhirResource, RDF.type, OWL.AnnotationProperty))
    out.write_text(g.serialize(format="turtle"))
    h = Graph().parse(out, format="turtle")   # the file must load back cleanly
    return (len(h), len(set(h.subjects(RDF.type, OWL.Class))), len(set(h.subjects(RDF.type, OWL.ObjectProperty))),
            len(set(h.subjects(RDF.type, OWL.DatatypeProperty))))


if __name__ == "__main__":
    t, c, o, d = export(env.BUILD_DB)
    print(f"{OUT.name}: {t} triples, {c} classes, {o} relations, {d} attributes")
    sys.exit(0)
