"""Load reviewed rule files into the Kanini build database (spec section 7.1, steps 6 and 7).

    python -m kanini_context.curation.load                  # every file in kanini_context/rules/
    python -m kanini_context.curation.load --test-review    # local tests only: see below

Everything goes in one transaction: sources, their checks and snapshots (with the file hash), change
flags, authorities and editions, bindings, codes, concepts and their maps, endpoints, rules and their
requirement items, closed-list screen rules, and one review row per approver. If any file fails the
automated checks (checks.py), or a rule has no approver, nothing is loaded.

Approval. Two different approvers are the rule (spec 7.1). One named approver is accepted only with a recorded
review_exception, which every answer shows; curation/approve.py records the prototype scope's one-engineer check
that way. --test-review fills an empty review with a test approver and the exception 'TEST build: not reviewed',
so the tests can run before approval; a database built that way is labelled TEST and must never be released.

Connection: KC_DSN, default the local test server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .checks import check_file

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules"
RAW = ROOT / "sources" / "raw"
MANIFEST = json.loads((ROOT / "sources" / "manifest.json").read_text())
from kanini_context import env  # noqa: F401,E402  (loads .env)

DSN = os.environ.get("KC_DSN", f"dbname={env.BUILD_DB}")

# Proposal appendix B.3, proposed starting values: how often each source is checked, and when an
# answer that rests on it becomes aging (still answered, with a warning) or stale (withheld).
FRESHNESS = {
    "medicare_policy": ("7 days", "14 days", "45 days"),
    "federal":         ("1 day", "14 days", "45 days"),
    "payer_list":      ("7 days", "14 days", "45 days"),
    "payer_policy":    ("7 days", "14 days", "45 days"),
}
def _git_commit() -> str | None:
    """The commit that holds the approved rule files (spec 7.1: Git is where approval happens). None outside Git,
    and marked +changes when the rule files differ from the commit."""
    try:
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "rules"], capture_output=True, text=True).stdout.strip()
        return head + ("+changes" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return None


GIT_COMMIT = _git_commit()
TEST_APPROVER = "test-approver"
TEST_EXCEPTION = "TEST build: not reviewed; for local tests only, never released"


class LoadError(Exception):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def eid(cur, etype: str, key: str):
    return cur.execute("SELECT kc_core.eid(%s, %s)", (etype, key)).fetchone()[0]


def one(cur, sql: str, args=()):
    row = cur.execute(sql, args).fetchone()
    if row is None:
        raise LoadError(f"not found: {sql} {args}")
    return row[0]


def load_sources(cur) -> dict[str, object]:
    """Sources, every recorded check, one snapshot per file (hash verified), and open change flags."""
    snaps = {}
    for m in MANIFEST:
        f = RAW / m["file"]
        if not f.exists():
            raise LoadError(f"{m['source_id']}: saved copy missing at {f}; download it from {m['url']}")
        digest = sha256(f)
        if digest != m["sha256"]:
            raise LoadError(f"{m['source_id']}: file hash {digest[:10]} differs from the manifest {m['sha256'][:10]}; "
                            "the copy is not the one reviewed")
        every, aging, stale = FRESHNESS[m["source_class"]]
        last_ok = max((c["at"] for c in m["checks"] if c["ok"]), default=None)
        cur.execute("""INSERT INTO kc_core.source VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (m["source_id"], m["title"], m["publisher"], m["source_class"], m["access_method"], m["url"],
                     m["license_class"], every, aging, stale, last_ok))
        for c in m["checks"]:
            cur.execute("INSERT INTO kc_core.source_check VALUES (%s,%s,%s)", (m["source_id"], c["at"], c["ok"]))
        snaps[m["source_id"]] = one(cur, """INSERT INTO kc_core.snapshot
              (source_id, retrieved_at, content_sha256, blob_uri, media_type, stated_effective)
              VALUES (%s,%s,%s,%s,'application/pdf',%s) RETURNING snapshot_id""",
              (m["source_id"], m["retrieved_at"], digest, f"file://kanini_context/sources/raw/{m['file']}", m["edition"]))
        for fl in m.get("change_flags", []):
            cur.execute("""INSERT INTO kc_gov.change_flag (source_id, snapshot_id, detected_at, applies_from, state)
                           VALUES (%s,%s,%s,%s,'open')""", (m["source_id"], snaps[m["source_id"]], fl["detected_at"], fl["applies_from"]))
    return snaps


def review_of(d: dict, test_review: bool) -> tuple[list[str], str | None, str | None]:
    """(approvers, exception, approval date). Two different approvers are the rule; one approver needs a recorded
    exception (curation/approve.py records the prototype scope's one-engineer check)."""
    rv = d["review"]
    approvers = [x for x in rv.get("reviewed_by", []) if x]
    exception = rv.get("review_exception")
    if not approvers:
        if not test_review:
            raise LoadError("not approved: reviewed_by is empty (run curation/approve.py; --test-review only for a local TEST build)")
        return [TEST_APPROVER], TEST_EXCEPTION, None
    if len(set(approvers)) == 1 and not exception:
        raise LoadError("one approver needs a recorded review_exception")
    return approvers, exception, rv.get("reviewed_at")


def scope_ids(cur, scope: dict) -> dict:
    out = {"program": None, "payer": None, "plan": None, "jurisdiction": None}
    if scope.get("program"):
        out["program"] = one(cur, "SELECT entity_id FROM kc_core.program WHERE kind = %s", (scope["program"],))
    for k in ("payer", "plan", "jurisdiction"):
        if scope.get(k):
            out[k] = one(cur, "SELECT entity_id FROM kc_core.entity WHERE entity_type = %s AND natural_key = %s", (k, scope[k]))
    return out


def subject_id(cur, subject: dict):
    if "concept" in subject:
        return one(cur, "SELECT entity_id FROM kc_core.entity WHERE entity_type='concept' AND natural_key=%s", (subject["concept"],))
    if "value_set" in subject:
        return one(cur, "SELECT entity_id FROM kc_core.entity WHERE entity_type='value_set' AND natural_key=%s", (subject["value_set"],))
    system, code = subject["code"].split("|", 1)
    return one(cur, "SELECT entity_id FROM kc_core.code WHERE code_system_id=%s AND code=%s AND upper_inf(recorded)", (system, code))


def load_authority(cur, e: dict, snap) -> tuple:
    a = e["authority"]
    aid = eid(cur, "authority", a["key"])
    issuer = one(cur, "SELECT entity_id FROM kc_core.entity WHERE entity_type='payer' AND natural_key=%s", (a["issuer"],))
    cur.execute("INSERT INTO kc_core.authority VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (aid, a["kind"], issuer, a["citation"], a["layer"], a["title"], a.get("closed_list", False)))
    ed = e["edition"]
    avid = one(cur, """INSERT INTO kc_core.authority_version
          (authority_id, version_label, valid_from_basis, valid_from_lo, valid_from_hi, valid_to_lo, valid_to_hi, snapshot_id)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING authority_version_id""",
          (aid, ed["label"], ed["valid_from_basis"], ed["valid_from"], ed["valid_from"], ed.get("valid_to"), ed.get("valid_to"), snap))
    return aid, avid, ed


def load_bindings(cur, aid, e: dict, ed: dict, snap, approvers, exception):
    for b in e["bindings"]:
        s = scope_ids(cur, b)
        cur.execute("""INSERT INTO kc_core.authority_binding
              (authority_id, program_id, plan_id, jurisdiction_id, item_scope, valid, snapshot_id, reviewed_by, review_exception)
              VALUES (%s,%s,%s,%s,%s,daterange(%s,%s),%s,%s,%s)""",
              (aid, s["program"], s["plan"], s["jurisdiction"], b["item_scope"], ed["valid_from"], ed.get("valid_to"),
               snap, approvers, exception))


def load_codes(cur, e: dict, snap, approvers, exception):
    for c in e.get("codes", []):
        system, code = c["code"].split("|", 1)
        if cur.execute("SELECT 1 FROM kc_core.code WHERE code_system_id=%s AND code=%s", (system, code)).fetchone():
            continue   # the same code drafted from two sources is loaded once, from the first
        # The policy lists the code but does not say when the code set created it, and the HCPCS file is not loaded in the
        # prototype, so the code's own dates stay open. The policy's dates go on its concept maps and rules instead.
        cur.execute("""INSERT INTO kc_core.code (entity_id, code_system_id, code, display, item_type, valid, first_release, snapshot_id)
                       VALUES (kc_core.eid('code', %s), %s, %s, %s, %s, daterange(NULL, NULL), %s, %s)""",
                    (c["code"], system, code, c["display"], c["item_type"], f"listed in {e['authority']['citation']}", snap))
    for cp in e.get("concepts", []):
        cid = eid(cur, "concept", cp["key"])
        cur.execute("INSERT INTO kc_core.concept VALUES (%s,%s,%s)", (cid, cp["label"], cp["definition"]))
        for m in cp["maps"]:
            system, code = m["code"].split("|", 1)
            cur.execute("""INSERT INTO kc_core.concept_map
                  (concept_id, code_system_id, code, map_kind, basis, ships_in, valid, snapshot_id, reviewed_by, review_exception)
                  VALUES (%s,%s,%s,%s,'stated','shared_pack',daterange(%s,NULL),%s,%s,%s)""",
                  (cid, system, code, m["map_kind"], e["codes_valid_from"], snap, approvers, exception))


def load_endpoints(cur, e: dict):
    for ep in e.get("endpoints", []):
        payer = one(cur, "SELECT entity_id FROM kc_core.entity WHERE entity_type='payer' AND natural_key=%s", (ep["payer"],))
        cur.execute("""INSERT INTO kc_core.endpoint
              (entity_id, payer_id, interface, preference, base_url, excludes_drugs, live, verified_at, valid)
              VALUES (kc_core.eid('endpoint', %s),%s,%s,%s,%s,%s,%s,%s,daterange(%s,NULL))""",
              (ep["key"], payer, ep["interface"], ep["preference"], ep["base_url"], ep["excludes_drugs"], ep["live"],
               ep["verified_at"], ep["valid_from"]))


def insert_rule(cur, *, r: dict, aid, avid, ed: dict, snap, approvers, exception, license_class: str,
                reviewed_at: str | None = None) -> object:
    kind = cur.execute("SELECT aspect, default_effect FROM kc_meta.rule_kind WHERE rule_kind=%s", (r["rule_kind"],)).fetchone()
    if kind is None:
        raise LoadError(f"{r['rule_key']}: unknown rule kind {r['rule_kind']}")
    s = scope_ids(cur, r.get("scope", {}))
    locator = dict(r["locator"])
    quote = r["quote"]
    if "quote_continued" in r:
        locator["continued"] = r["quote_continued"]
        quote = f"{quote} {r['quote_continued']['quote']}"
    # A rule may start later than its edition (its own "valid_from", stated by the source)
    rid = one(cur, """INSERT INTO kc_core.rule
          (rule_key, rule_kind, aspect, subject_id, authority_id, authority_version_id, program_id, payer_id, plan_id,
           jurisdiction_id, effect, conditions, payload, basis, mapping_confidence, not_stated,
           valid_from_basis, valid_from_lo, valid_from_hi, valid_to_lo, valid_to_hi,
           status, snapshot_id, source_locator, source_quote, extracted_by, reviewed_by, review_exception, reviewed_at, license_class)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,coalesce(%s::timestamptz, now()),%s)
          RETURNING rule_id""",
          (r["rule_key"], r["rule_kind"], kind[0], subject_id(cur, r["subject"]), aid, avid,
           s["program"], s["payer"], s["plan"], s["jurisdiction"], r.get("effect", kind[1]),
           Jsonb(r.get("conditions", [])), Jsonb(r["payload"]), r["basis"], r.get("mapping_confidence"),
           r.get("not_stated", []), "stated" if "valid_from" in r else ed["valid_from_basis"],
           r.get("valid_from", ed["valid_from"]), r.get("valid_from", ed["valid_from"]), ed.get("valid_to"), ed.get("valid_to"),
           snap, Jsonb(locator), quote, r["extracted_by"], approvers, exception, reviewed_at, license_class))
    for rq in r.get("requirements", []):
        de = one(cur, "SELECT entity_id FROM kc_core.entity WHERE entity_type='data_element' AND natural_key=%s", (rq["element"],))
        cur.execute("""INSERT INTO kc_core.rule_requirement (rule_id, group_no, group_logic, data_element_id, test, lookback, when_needed)
                       VALUES (%s,%s,%s,%s,%s,%s::interval,%s)""",
                    (rid, rq["group"], rq["logic"], de, Jsonb(rq["test"]), rq.get("lookback"), rq["when"]))
    for who in dict.fromkeys(approvers):
        cur.execute("""INSERT INTO kc_gov.review (target_table, target_id, reviewer, decision, git_commit)
                       VALUES ('kc_core.rule',%s,%s,'approve',%s)""", (rid, who, GIT_COMMIT))
    return rid


def load_file(cur, path: Path, snaps: dict, test_review: bool) -> int:
    d = json.loads(path.read_text())
    approvers, exception, reviewed_at = review_of(d, test_review)
    entries = d["programs"] if "programs" in d else [d]
    n = 0
    for e in entries:
        snap = snaps[e["source_id"]]
        lic = next(m["license_class"] for m in MANIFEST if m["source_id"] == e["source_id"])
        aid, avid, ed = load_authority(cur, e, snap)
        load_bindings(cur, aid, e, ed, snap, approvers, exception)
        load_endpoints(cur, e)
        for r in e.get("rules", []):
            insert_rule(cur, r=r, aid=aid, avid=avid, ed=ed, snap=snap, approvers=approvers, exception=exception, license_class=lic, reviewed_at=reviewed_at)
            n += 1
        # Closed-list screens: the program's list was checked and the concept is not on it, so the program
        # states "not required" for it by not listing it (proposal section 10.4).
        for sc in d.get("screens", []):
            if not e["authority"].get("closed_list"):
                raise LoadError(f"{e['authority']['key']}: a screen needs an authority recorded as a closed list")
            insert_rule(cur, r={
                "rule_key": f"{sc['rule_key_prefix']}/{e['authority']['key'].lower()}",
                "rule_kind": "approval_required", "subject": {"concept": sc["concept"]},
                "scope": {"program": e["bindings"][0]["program"]},
                "payload": {"required": False}, "basis": "stated_by_exhaustion",
                "locator": {**e["list_quote"]["locator"], "screen_finding": sc["finding"]},
                "quote": e["list_quote"]["quote"], "extracted_by": "manual"},
                aid=aid, avid=avid, ed=ed, snap=snap, approvers=approvers, exception=exception, license_class=lic, reviewed_at=reviewed_at)
            n += 1
    return n


def file_order(paths: list[Path]) -> list[Path]:
    """Files that define codes and concepts load first, so rules about them can find them."""
    return sorted(paths, key=lambda p: (0 if '"codes"' in p.read_text() else 1, p.name))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--test-review", action="store_true", help="fill empty reviews with a TEST approver (local tests only)")
    args = ap.parse_args(argv)
    paths = file_order(args.files or sorted(RULES.glob("*.json")))

    failed = [(p.name, rep.errors) for p in paths if (rep := check_file(p)).errors]
    if failed:
        for name, errs in failed:
            print(f"FAIL {name}: " + "; ".join(errs))
        print("nothing loaded: fix the files above")
        return 1

    try:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            snaps = load_sources(cur)
            for p in paths:   # codes and concepts first
                d = json.loads(p.read_text())
                approvers, exception, _ = review_of(d, args.test_review)
                for e in (d["programs"] if "programs" in d else [d]):
                    load_codes(cur, e, snaps[e["source_id"]], approvers, exception)
            total = 0
            for p in paths:
                n = load_file(cur, p, snaps, args.test_review)
                total += n
                print(f"loaded {p.name}: {n} rules")
            # Editions whose uncertain dates might overlap are allowed (the source really is vague) and listed for
            # a reviewer to settle (spec 6.5, test T12). Confirmed overlaps are refused by the database itself.
            for a, b in cur.execute("""SELECT ra.rule_key, rb.rule_key FROM kc_gov.possible_overlap o
                                         JOIN kc_core.rule ra ON ra.rule_id = o.rule_a JOIN kc_core.rule rb ON rb.rule_id = o.rule_b""").fetchall():
                print(f"for review: {a} and {b} may overlap once their uncertain dates are known (kc_gov.possible_overlap)")
            print(f"{total} rules loaded{' (TEST build)' if args.test_review else ''}")
    except psycopg.errors.ExclusionViolation as ex:
        # two current active versions of one rule (same authority, subject, kind and scope) valid on the same dates
        print(f"nothing loaded: overlapping active versions ({ex.diag.constraint_name}): {ex.diag.message_detail or ex}")
        return 1
    except (LoadError, psycopg.Error) as ex:
        print(f"nothing loaded: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
