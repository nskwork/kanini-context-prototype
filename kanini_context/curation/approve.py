"""Record an engineer's approval of rule files (spec section 7.1 step 6, as the prototype scope sets it).

The prototype scope has one engineer check each drafted rule against its source, with formal review at a later
stage. The spec's two-approver rule stays in the database; one named approver is accepted with this recorded
exception, and every answer that rests on the rule shows it.

    python -m kanini_context.curation.approve --by "NANDHU S K"                 # every rule file, one at a time
    python -m kanini_context.curation.approve --by "NANDHU S K" rules/x.json    # selected files

For each file the automated checks run first (a failure stops it). Then each rule is shown with its page, quote,
payload, conditions and tests, to compare with the source copy. Approval is recorded in the file's review block;
commit the files afterwards, since Git is where approval is kept.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .checks import check_file

RULES = Path(__file__).resolve().parents[1] / "rules"
SCOPE_EXCEPTION = ("Prototype scope: one engineer checked each rule against its source; "
                   "formal two-person review at a later stage")


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
    ap.add_argument("--by", required=True, help="the approving engineer's name")
    ap.add_argument("--yes", action="store_true", help="approve without asking (only after reading the files)")
    args = ap.parse_args(argv)
    approved = 0
    for p in args.files or sorted(RULES.glob("*.json")):
        rep = check_file(p)
        if rep.errors:
            print(f"\n{p.name}: automated checks fail, not approved:\n  " + "\n  ".join(rep.errors))
            continue
        d = json.loads(p.read_text())
        print(f"\n=== {p.name}")
        show(d)
        if not args.yes and input(f"\nApprove {p.name} as {args.by}? [y/N] ").strip().lower() != "y":
            print("  not approved")
            continue
        d["review"].update({"reviewed_by": [args.by], "reviewed_at": date.today().isoformat(), "review_exception": SCOPE_EXCEPTION})
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        approved += 1
        print("  approved")
    print(f"\n{approved} file(s) approved. Commit them (git commit kanini_context/rules), then build without --test-review.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
