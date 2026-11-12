"""Automated checks on drafted rule files (spec section 7.1, step 4, and the extraction safeguards).

Run before a rule file can be approved or loaded:

    python -m kanini_context.curation.checks            # all files in kanini_context/rules/
    python -m kanini_context.curation.checks FILE ...   # selected files

Checks, per file:
  quote      every quote appears, word for word after whitespace and dash normalisation, at the page
             (or the column band of the page) its locator names
  numbers    every threshold, hour count and code in a rule appears in that rule's quote, or in the
             located list it points to
  negation   a quote that says "not required" or "exempt" cannot produce required = true
  schema     every payload fits its rule kind's JSON Schema in the registry; every condition and test
             names a registered fact or data element
  codes      every HCPCS-shaped code on a located page is either drafted or counted as outside the slice
A failed check exits non-zero and the file cannot be loaded.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import jsonschema

from .pdftext import locate_text, normalise, pages

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules"
MANIFEST = {m["source_id"]: m for m in json.loads((ROOT / "sources" / "manifest.json").read_text())}
REGISTRY = (ROOT / "db" / "registry.sql").read_text()

NEGATION = re.compile(r"\b(not required|no prior authorization|is not required|exempt)\b", re.I)
HCPCS = re.compile(r"\b[A-V]\d{4}\b")


def _registry_kinds() -> dict[str, dict]:
    """Rule kind payload schemas, read from registry.sql so the checks need no database."""
    kinds = {}
    for m in re.finditer(r"\('([a-z_]+)',(?:NULL|'[a-z_]+'),'[a-z_]+','[a-z_]+','[a-z]+',(?:NULL|'\{[^']*\}'),\s*'(\{.*?\})',1\)",
                         REGISTRY, flags=re.S):
        kinds[m.group(1)] = json.loads(m.group(2))
    return kinds


def _registry_names(table: str) -> set[str]:
    if table == "fact":
        block = REGISTRY.split("INSERT INTO kc_meta.fact_type VALUES", 1)[1].split(";", 1)[0]
        return set(re.findall(r"^\s*\('([a-z_0-9]+)'", block, flags=re.M))
    block = REGISTRY.split("INSERT INTO kc_core.data_element", 1)[1].split(";\n", 1)[0]
    return set(re.findall(r"kc_core\.eid\('data_element','([^']+)'\)", block))


KINDS = _registry_kinds()
FACTS = _registry_names("fact")
ELEMENTS = _registry_names("element")


class Report:
    def __init__(self, name: str):
        self.name, self.errors, self.notes = name, [], []

    def err(self, msg: str):
        self.errors.append(msg)

    def note(self, msg: str):
        self.notes.append(msg)


def check_quote(rep: Report, file_name: str, locator: dict, quote: str, what: str) -> str:
    """The quote must be in the located text. Returns the located text (normalised)."""
    located = normalise(locate_text(file_name, locator))
    if normalise(quote) in located:
        return located
    elsewhere = [i + 1 for i, t in enumerate(pages(file_name)) if normalise(quote) in normalise(t)]
    hint = f" (found on page {elsewhere})" if elsewhere else " (not found anywhere in the document)"
    rep.err(f"{what}: quote not at page {locator.get('page')}{' band ' + str(locator['band']) if 'band' in locator else ''}{hint}: {quote[:90]!r}")
    return located


def _numbers(obj) -> list[str]:
    out = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(f"{obj:g}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("unit",):
                out += _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _numbers(v)
    return out


def check_rule(rep: Report, src: dict, rule: dict, located_codes: set[str]):
    f = src["file"]
    key = rule["rule_key"]
    located = check_quote(rep, f, rule["locator"], rule["quote"], key)
    q = normalise(rule["quote"])
    for extra in ("row_statement", "code_cell"):
        if extra in rule["locator"]:
            loc = rule["locator"][extra]
            check_quote(rep, f, loc, loc["quote"], f"{key} {extra}")
            q += " " + normalise(loc["quote"])
    if "quote_continued" in rule:
        loc = rule["quote_continued"]
        check_quote(rep, f, loc, loc["quote"], f"{key} quote_continued")
        q += " " + normalise(loc["quote"])

    kind = rule["rule_kind"]
    if kind not in KINDS:
        rep.err(f"{key}: unknown rule kind {kind}")
    else:
        try:
            jsonschema.validate(rule["payload"], KINDS[kind])
        except jsonschema.ValidationError as e:
            rep.err(f"{key}: payload does not fit {kind}: {e.message}")

    for c in rule.get("conditions", []):
        if c["fact"] not in FACTS:
            rep.err(f"{key}: condition uses unregistered fact {c['fact']}")

    # numbers in the payload and in requirement tests must be in the quote (or the located list)
    nums = _numbers({k: v for k, v in rule["payload"].items() if k in ("standard_hours", "expedited_hours")})
    for rq in rule.get("requirements", []):
        if rq["element"] not in ELEMENTS:
            rep.err(f"{key}: requirement uses unregistered data element {rq['element']}")
        nums += _numbers(rq["test"].get("value"))
    list_text = q
    for rq in rule.get("requirements", []):
        if "list_locator" in rq:
            ll = rq["list_locator"]
            list_text += " " + normalise(" ".join(pages(f)[ll["page"] - 1: ll["page"] + 1]))
    for rq in rule.get("requirements", []):
        v = rq["test"].get("value")
        for code in ([v] if isinstance(v, str) else v if isinstance(v, list) else []):
            if isinstance(code, str) and code not in list_text:
                rep.err(f"{key}: code {code} in a test is not in the quote or its located list")
    for n in nums:
        if not re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", q):
            rep.err(f"{key}: number {n} is not in the quote")

    if NEGATION.search(q) and rule["payload"].get("required") is True:
        rep.err(f"{key}: the quote states a negation but the rule says required = true")

    if rule.get("basis") == "interpreted" and rule.get("mapping_confidence") is None:
        rep.err(f"{key}: an interpreted rule needs a mapping confidence")

    located_codes.update(HCPCS.findall(located))


def check_file(path: Path) -> Report:
    d = json.loads(path.read_text())
    rep = Report(path.name)
    entries = d["programs"] if "programs" in d else [d]
    drafted: set[str] = set()
    located_codes: set[str] = set()
    for e in entries:
        src = MANIFEST[e["source_id"]]
        f = src["file"]
        ed = e["edition"]
        check_quote(rep, f, ed["locator"], ed["quote"], f"{e['authority']['key']} edition")
        if "list_quote" in e:
            check_quote(rep, f, e["list_quote"]["locator"], e["list_quote"]["quote"], f"{e['authority']['key']} list")
        if "bindings_locator" in e:
            check_quote(rep, f, e["bindings_locator"], e["bindings_locator"]["quote"], f"{e['authority']['key']} bindings")
        if "codes" in e:
            cq = check_quote(rep, f, e["codes_locator"], e["codes_quote"], "codes") and normalise(e["codes_quote"])
            for c in e["codes"]:
                code = c["code"].split("|", 1)[1]
                drafted.add(code)
                if code not in cq:
                    rep.err(f"code {code} is not in the codes quote")
        for ep in e.get("endpoints", []):
            check_quote(rep, f, ep["locator"], ep["quote"], f"endpoint {ep['key']}")
        for r in e.get("rules", []):
            for v in r.get("subject", {}).values():
                if isinstance(v, str) and "|" in v:
                    drafted.add(v.split("|", 1)[1])
            check_rule(rep, src, r, located_codes)
    if "screens" in d:
        for s in d["screens"]:
            if not s.get("finding"):
                rep.err("a closed-list screen needs a reviewer finding")
    outside = sorted(located_codes - drafted)
    if outside:
        rep.note(f"{len(outside)} other HCPCS codes on the located pages are not drafted in this pack: {', '.join(outside[:12])}"
                 + (" ..." if len(outside) > 12 else ""))
    rv = d["review"]
    n = len({x for x in rv.get("reviewed_by", []) if x})
    if n == 0:
        rep.note("not yet reviewed: reviewed_by is empty")
    elif n == 1 and not rv.get("review_exception"):
        rep.err("one reviewer needs a recorded review_exception; two different reviewers are the rule")
    return rep


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or sorted(RULES.glob("*.json"))
    failed = 0
    for p in files:
        rep = check_file(p)
        status = "FAIL" if rep.errors else "ok"
        print(f"{status:4}  {rep.name}")
        for m in rep.errors:
            print(f"      error: {m}")
        for m in rep.notes:
            print(f"      note:  {m}")
        failed += bool(rep.errors)
    print(f"\n{len(files) - failed} of {len(files)} files pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
