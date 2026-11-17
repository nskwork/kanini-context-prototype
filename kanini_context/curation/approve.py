"""Record approval of rule files (spec section 7.1 step 6): two different people, or one with a recorded exception.

The spec asks for two approvers on every rule. Each person runs this with their own name, at their own desk; the
second approval is added to the first as long as the rule file has not changed since (a fingerprint of the file
without its review block is kept with the approval). Two people can also approve together, with --by twice.
With one approver the prototype scope's exception is recorded, and every answer that rests on the rule shows it.

    python -m kanini_context.curation.approve --by "NANDHU S K"                 # every rule file, one at a time
    python -m kanini_context.curation.approve --by "NANDHU S K" rules/x.json    # selected files
    python -m kanini_context.curation.approve --by "Second Reviewer" rules/x.json   # the second approval, later
    python -m kanini_context.curation.approve --by "A" --by "B" rules/x.json    # two people together

For each file the automated checks run first (a failure stops it). Then each rule is shown with its page, quote,
payload, conditions and tests, to compare with the source copy. Approval is recorded in the file's review block;
commit the files afterwards, since Git is where approval is kept.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from .checks import check_file

RULES = Path(__file__).resolve().parents[1] / "rules"
SCOPE_EXCEPTION = ("Prototype scope: one engineer checked each rule against its source; "
                   "formal two-person review at a later stage")


def content_sha(d: dict) -> str:
    """The fingerprint of what was approved: the rule file without its review block."""
    return hashlib.sha256(json.dumps({k: v for k, v in d.items() if k != "review"}, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class ReviewError(ValueError):
    pass


def approvers_after(review: dict, current_sha: str, by: list[str]) -> tuple[list[str], str]:
    """The approvers once these names are added, and a note on what happened. Earlier approvals count only when the
    file is unchanged since; a name given twice, or a person approving again, is refused."""
    names = [b.strip() for b in by]
    if any(not n for n in names):
        raise ReviewError("an approver's name is empty")
    if len({n.casefold() for n in names}) != len(names):
        raise ReviewError("the same name is given twice; two approvers must be two different people")
    earlier = [x for x in review.get("reviewed_by", []) if x]
    note = ""
    if earlier and review.get("reviewed_sha") != current_sha:
        note = f"the file changed since {', '.join(earlier)} approved it, so that approval no longer counts"
        earlier = []
    again = [n for n in names if n.casefold() in {e.casefold() for e in earlier}]
    if again:
        raise ReviewError(f"{', '.join(again)} already approved this file; the second approval must be someone else's")
    return earlier + names, note


def record(d: dict, by: list[str]) -> str:
    """Add the approval to the file's review block. Returns a note for the person approving."""
    sha, rv, today = content_sha(d), d["review"], date.today().isoformat()
    names, note = approvers_after(rv, sha, by)
    kept = [] if note else [a for a in rv.get("approvals", []) if a.get("by") in names]
    rv["approvals"] = kept + [{"by": n, "at": today} for n in by]   # who approved, and when, one by one
    rv.update({"reviewed_by": names, "reviewed_at": today, "reviewed_sha": sha})
    if len(names) >= 2:
        rv.pop("review_exception", None)   # the spec's rule is met: no exception
    else:
        rv["review_exception"] = SCOPE_EXCEPTION
    status = ("approved by two people: " + " and ".join(names)) if len(names) >= 2 else f"approved by {names[0]} (one approver, exception recorded)"
    return status + (f"; {note}" if note else "")


def show(d: dict):
    for e in d.get("programs", [d]):
        a, ed = e["authority"], e["edition"]
        print(f"\n  {a['citation']} ({a['kind']}, layer {a['layer']}{', closed list' if a.get('closed_list') else ''})")
        print(f"    edition: {ed['label']}, from {ed['valid_from']} ({ed['valid_from_basis']}), page {ed['locator']['page']}: \"{ed['quote']}\"")
        print("    binds:   " + "; ".join(", ".join(f"{k}={v}" for k, v in b.items() if k != "note") for b in e["bindings"]))
        for r in e.get("rules", []):
            loc = r["locator"]
            print(f"\n    {r['rule_key']}  [{r['rule_kind']}, {r['basis']}"
                  + (f" {r['mapping_confidence']}" if r.get("mapping_confidence") else "") + "]")
            print(f"      page {loc['page']}{' column ' + str(loc['band']) if 'band' in loc else ''}: \"{r['quote']}\"")
            if "quote_continued" in r:
                print(f"      page {r['quote_continued']['page']}: \"{r['quote_continued']['quote']}\"")
            print(f"      says:  {json.dumps(r['payload'])}")
            for c in r.get("conditions", []):
                print(f"      when:  {c['fact']} {c['op']} {c['value']}")
            for q in r.get("requirements", []):
                print(f"      test:  group {q['group']} ({q['logic']}): {q['element']} {json.dumps(q['test'])}"
                      + (f" within {q['lookback']}" if q.get("lookback") else "") + f" [{q['when']}]")
            if r.get("interpretation"):
                print(f"      interpretation: {r['interpretation']}")
    for s in d.get("screens", []):
        print(f"\n    screen: {s['concept']}: {s['finding']}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--by", required=True, action="append", help="the approver's name; give it twice for two people approving together")
    ap.add_argument("--yes", action="store_true", help="approve without asking (only after reading the files)")
    args = ap.parse_args(argv)
    approved = 0
    for p in args.files or sorted(RULES.glob("*.json")):
        rep = check_file(p)
        if rep.errors:
            print(f"\n{p.name}: automated checks fail, not approved:\n  " + "\n  ".join(rep.errors))
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        print(f"\n=== {p.name}")
        show(d)
        try:
            approvers_after(d["review"], content_sha(d), args.by)   # refuse before asking
        except ReviewError as ex:
            print(f"  not approved: {ex}")
            continue
        if not args.yes and input(f"\nApprove {p.name} as {' and '.join(args.by)}? [y/N] ").strip().lower() != "y":
            print("  not approved")
            continue
        status = record(d, args.by)
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        approved += 1
        print(f"  {status}")
    print(f"\n{approved} file(s) approved. Commit them (git commit kanini_context/rules), then build without --test-review.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
