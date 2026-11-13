"""The README's two figures, drawn from the comparison reports so they cannot drift from the data.

    python docs/assets/make_figures.py

writes docs/assets/hero.svg (the banner) and docs/assets/results.svg (the three lanes side by side). Lane A comes
from its newest report, B and C from the newest report holding B.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "propel" / "baseline" / "results"
OUT = Path(__file__).resolve().parent

BG0, BG1 = "#0b0820", "#1a1040"
INK, INK2, MUTE = "#ede9fe", "#cfc9ef", "#9d97c4"
ROSE, CYAN, LIME, VIOLET = "#fb7185", "#22d3ee", "#a3e635", "#a78bfa"
FONT = "'Segoe UI', Inter, Helvetica, Arial, sans-serif"
MONO = "'Cascadia Code', 'JetBrains Mono', Consolas, monospace"


def newest(key: str) -> dict:
    for f in sorted(RESULTS.glob("compare-*.json"), reverse=True):
        rep = json.loads(f.read_text())
        if all(e.get(key) for e in rep["scenarios"].values()):
            return rep
    raise SystemExit(f"no report holds {key}")


def lane(rep: dict, key: str) -> dict:
    sc = [e[key] for e in rep["scenarios"].values()]
    n = len(sc)
    return {"right": sum(s["matches_rules"] for s in sc), "runs": sum(s["runs"] for s in sc),
            "tokens": sum(s["tokens_in"] for s in sc) / n, "calls": sum(s["llm_calls"] for s in sc) / n,
            "seconds": sum(s["seconds"] for s in sc) / n,
            "varied": sum(1 for s in sc if len(s["outcomes"]) > 1), "orders": n}


def numbers() -> dict:
    a, b = newest("A_agent"), newest("B")
    return {"A": lane(a, "A_agent"), "B": lane(b, "B"), "C": lane(b, "C")}


def hero(x: dict) -> str:
    A, B = x["A"], x["B"]
    pills = [(f"{A['right']} of {A['runs']}", "right without the context layer", ROSE),
             (f"{B['right']} of {B['runs']}", "from the rules, with Kanini Context", CYAN),
             (f"{A['tokens'] / 1000:.1f}k → {B['tokens'] / 1000:.1f}k → 0", "input tokens per order: A, B, C", LIME)]
    w, h = 1200, 330
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
           f'aria-label="Kanini Context: read the rules once, answer every order from them">',
           f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG1}"/><stop offset="1" stop-color="{BG0}"/></linearGradient>'
           f'<radialGradient id="g1" cx=".15" cy=".2" r=".5"><stop offset="0" stop-color="{VIOLET}" stop-opacity=".45"/><stop offset="1" stop-color="{VIOLET}" stop-opacity="0"/></radialGradient>'
           f'<radialGradient id="g2" cx=".9" cy=".85" r=".45"><stop offset="0" stop-color="{CYAN}" stop-opacity=".22"/><stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></radialGradient>'
           f'<linearGradient id="t" x1="0" x2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#c4b5fd"/></linearGradient></defs>',
           f'<rect width="{w}" height="{h}" rx="22" fill="url(#bg)"/><rect width="{w}" height="{h}" rx="22" fill="url(#g1)"/><rect width="{w}" height="{h}" rx="22" fill="url(#g2)"/>',
           f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="21" fill="none" stroke="#ffffff" stroke-opacity=".12"/>']
    # small constellation, top right
    pts = [(960, 70), (1010, 52), (1060, 88), (1110, 60), (1035, 128), (985, 112), (1135, 120)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{VIOLET}" stroke-opacity=".35"/>')
    for i, (px, py) in enumerate(pts):
        c = [LIME, CYAN, VIOLET, ROSE][i % 4]
        out.append(f'<circle cx="{px}" cy="{py}" r="{5 if i % 2 else 3.5}" fill="{c}"/><circle cx="{px}" cy="{py}" r="11" fill="{c}" opacity=".15"/>')
    out += [f'<rect x="56" y="58" width="26" height="26" rx="7" fill="none" stroke="{VIOLET}" stroke-width="3"/><circle cx="69" cy="71" r="4.5" fill="{LIME}"/>',
            f'<text x="96" y="81" font-family="{FONT}" font-size="22" font-weight="600" fill="{VIOLET}" letter-spacing="3">KANINI CONTEXT · PROTOTYPE</text>',
            f'<text x="56" y="146" font-family="{FONT}" font-size="46" font-weight="800" fill="url(#t)">Read the rules once.</text>',
            f'<text x="56" y="198" font-family="{FONT}" font-size="46" font-weight="800" fill="url(#t)">Answer every order from them.</text>',
            f'<text x="56" y="232" font-family="{FONT}" font-size="18" fill="{INK2}">A context layer for prior authorization: reviewed payer and Medicare rules, served to AI agents and workflows through MCP.</text>']
    x0 = 56
    for big, small, c in pills:
        wid = 18 + max(len(big) * 13.5, len(small) * 7.4) + 18
        out.append(f'<rect x="{x0}" y="254" width="{wid:.0f}" height="54" rx="12" fill="{c}" fill-opacity=".1" stroke="{c}" stroke-opacity=".55"/>'
                   f'<text x="{x0 + 18}" y="279" font-family="{MONO}" font-size="20" font-weight="700" fill="{c}">{big}</text>'
                   f'<text x="{x0 + 18}" y="298" font-family="{FONT}" font-size="12.5" fill="{INK2}">{small}</text>')
        x0 += wid + 14
    out.append("</svg>")
    return "\n".join(out)


def results(x: dict) -> str:
    A, B, C = x["A"], x["B"], x["C"]
    lanes = [("A", "Same agent, no context", "reads the policy documents itself", ROSE, A),
             ("B", "Agent + Kanini Context", "reduce: short calls, no document text", CYAN, B),
             ("C", "Kanini Context, no AI", "eliminate: the approved rules, by code", LIME, C)]
    rows = [("Right against the rules", lambda L, s: s["right"] / s["runs"] if L != "C" else 1.0,
             lambda L, s: f"{s['right']} of {s['runs']}" if L != "C" else "the answer key"),
            ("Input tokens per order", lambda L, s: s["tokens"] / max(A["tokens"], 1), lambda L, s: f"{s['tokens']:,.0f}"),
            ("AI calls per order", lambda L, s: s["calls"] / max(A["calls"], 1), lambda L, s: f"{s['calls']:.1f}"),
            ("Answer changed between runs", lambda L, s: s["varied"] / s["orders"], lambda L, s: f"on {s['varied']} of {s['orders']} orders" if s["varied"] else "never")]
    w, col0, colw, top, rowh = 1200, 300, 290, 150, 74
    h = top + rowh * len(rows) + 40
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Results: the same agent with and without Kanini Context">',
           f'<defs><linearGradient id="bg2" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#140d33"/><stop offset="1" stop-color="{BG0}"/></linearGradient></defs>',
           f'<rect width="{w}" height="{h}" rx="20" fill="url(#bg2)"/><rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="19" fill="none" stroke="#ffffff" stroke-opacity=".1"/>',
           f'<text x="40" y="48" font-family="{FONT}" font-size="15" font-weight="700" fill="{MUTE}" letter-spacing="2.5">SAME SIX ORDERS · FIVE RUNS EACH · GPT-5 MINI</text>']
    for i, (L, name, sub, c, _) in enumerate(lanes):
        x0 = col0 + i * colw
        out.append(f'<rect x="{x0}" y="70" width="{colw - 22}" height="58" rx="12" fill="{c}" fill-opacity=".1" stroke="{c}" stroke-opacity=".5"/>'
                   f'<text x="{x0 + 16}" y="106" font-family="{FONT}" font-size="30" font-weight="800" fill="{c}">{L}</text>'
                   f'<text x="{x0 + 48}" y="95" font-family="{FONT}" font-size="15" font-weight="700" fill="{INK}">{name}</text>'
                   f'<text x="{x0 + 48}" y="115" font-family="{FONT}" font-size="12" fill="{MUTE}">{sub}</text>')
    for r, (label, frac, txt) in enumerate(rows):
        y = top + r * rowh
        out.append(f'<line x1="40" y1="{y}" x2="{w - 40}" y2="{y}" stroke="#ffffff" stroke-opacity=".07"/>'
                   f'<text x="40" y="{y + 42}" font-family="{FONT}" font-size="16" font-weight="600" fill="{INK2}">{label}</text>')
        for i, (L, _, _, c, s) in enumerate(lanes):
            x0 = col0 + i * colw
            f = max(0.0, min(1.0, frac(L, s)))
            bw = (colw - 22) * f
            out.append(f'<rect x="{x0}" y="{y + 18}" width="{colw - 22}" height="12" rx="6" fill="#ffffff" fill-opacity=".06"/>')
            if bw > 0:
                out.append(f'<rect x="{x0}" y="{y + 18}" width="{max(bw, 6):.0f}" height="12" rx="6" fill="{c}"/>')
            out.append(f'<text x="{x0}" y="{y + 54}" font-family="{MONO}" font-size="16" font-weight="700" fill="{c}">{txt(L, s)}</text>')
    out.append(f'<text x="40" y="{h - 16}" font-family="{FONT}" font-size="12" fill="{MUTE}">C is the approved rules applied by plain code: the answer key the other lanes are checked against. '
               f'Bars for tokens and calls are scaled to lane A. Figures from propel/baseline/results/.</text>')
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- every test run, order by order

LABEL = {"no_pa": "No PA", "pa_required": "PA required", "other_gate": "Other gate", "needs_facts": "Not yet known",
         "unknown": "Not known", "exempt": "Exempt", "unparseable": "no card", "none": "no card"}
ORDERS = ["maria-tn", "maria-tn-egfr52", "maria-oh", "maria-uhc", "maria-uhc-esa", "maria-nohgb"]
ORDER_NOTE = {"maria-tn": "Original Medicare · TN · eGFR 38", "maria-tn-egfr52": "same · eGFR 52",
              "maria-oh": "Original Medicare · OH · eGFR 52", "maria-uhc": "UHC Medicare Advantage · IV iron",
              "maria-uhc-esa": "UHC · anemia drug, no cancer dx", "maria-nohgb": "TN · hemoglobin missing"}


def _central(at: str) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    t = datetime.fromisoformat(at).astimezone(ZoneInfo("America/Chicago"))
    return t.strftime("%d %b, %H:%M").lstrip("0")


def _from_json(name: str, key: str) -> dict:
    rep = json.loads((RESULTS / f"{name}.json").read_text())
    cells = {}
    for e in rep["scenarios"].values():
        s = e[key]
        cells[e["patient"]] = {"right": s["matches_rules"], "runs": s["runs"], "outcomes": s["outcomes"],
                               "key": e["reference_outcome"], "tokens": s["tokens_in"]}
    return {"when": _central(rep["at"]), "cells": cells}


def _from_md(name: str) -> dict:
    """The two pack-picked-pages runs kept their markdown report only: read the per-scenario table and answers."""
    import ast
    import re
    text = (RESULTS / f"{name}.md").read_text()
    at = re.search(r"\((\d{4}-\d\d-\d\dT[\d:]+\+00:00)", text).group(1)
    rows = re.findall(r"^\| \d \(([\w-]+)\) \| (\w+) \| (\d)/(\d) match the key; [\d.]+ LLM calls; ([\d,]+) in", text, re.M)
    answers = dict(re.findall(r"(\d) (\{[^}]*\})", text.split("A's answers per scenario:")[1].split("\n")[0]))
    cells = {}
    for i, (pid, key, right, runs, tok) in enumerate(rows, 1):
        cells[pid] = {"right": int(right), "runs": int(runs), "outcomes": ast.literal_eval(answers[str(i)]),
                      "key": key, "tokens": int(tok.replace(",", ""))}
    return {"when": _central(at), "cells": cells}


def history_runs() -> list[dict]:
    """Every comparison test kept in propel/baseline/results/, as columns: family, label, report."""
    cols = [("A", "Same agent, no context", "run 1", _from_json("compare-20261113-1017", "A_agent"), ROSE),
            ("A", "Same agent, no context", "run 2", _from_json("compare-20261113-1056", "A_agent"), ROSE),
            ("S", "Search (RAG), one call", "run 1", _from_json("compare-20261113-0850", "A_rag"), "#f59e0b"),
            ("S", "Search (RAG), one call", "run 2", _from_json("compare-20261113-1031", "A_rag"), "#f59e0b"),
            ("W", "Every document, one call", "run 1", _from_json("compare-20261113-0850", "A_whole"), "#e879f9"),
            ("W", "Every document, one call", "run 2", _from_json("compare-20261113-1031", "A_whole"), "#e879f9"),
            ("P", "Pages Kanini Context picked", "low effort", _from_md("compare-20261112-2210"), "#94a3b8"),
            ("P", "Pages Kanini Context picked", "medium effort", _from_md("compare-20261112-2238"), "#94a3b8"),
            ("B", "B · with context", "run", _from_json("compare-20261112-2256", "B"), CYAN)]
    return [{"fam": f, "name": n, "sub": s, "when": d["when"], "cells": d["cells"], "color": c} for f, n, s, d, c in cols]


def history(cols: list[dict]) -> str:
    w, left, colw, top, rowh = 1400, 262, 124, 150, 66
    h = top + rowh * len(ORDERS) + 2 * 46 + 76
    good, mid, bad = "#22c55e", "#f59e0b", "#f43f5e"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Every test run, order by order">',
           f'<defs><linearGradient id="bg3" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#140d33"/><stop offset="1" stop-color="{BG0}"/></linearGradient></defs>',
           f'<rect width="{w}" height="{h}" rx="20" fill="url(#bg3)"/><rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="19" fill="none" stroke="#ffffff" stroke-opacity=".1"/>',
           f'<text x="32" y="44" font-family="{FONT}" font-size="15" font-weight="700" fill="{MUTE}" letter-spacing="2.5">EVERY TEST RUN · SAME SIX ORDERS · FIVE ANSWERS PER CELL · GPT-5 MINI</text>']
    # family headers, spanning their columns
    i = 0
    while i < len(cols):
        j = i
        while j + 1 < len(cols) and cols[j + 1]["fam"] == cols[i]["fam"]:
            j += 1
        x0, x1 = left + i * colw, left + (j + 1) * colw - 8
        c = cols[i]["color"]
        out.append(f'<rect x="{x0}" y="62" width="{x1 - x0}" height="34" rx="9" fill="{c}" fill-opacity=".12" stroke="{c}" stroke-opacity=".5"/>'
                   f'<text x="{(x0 + x1) / 2}" y="84" text-anchor="middle" font-family="{FONT}" font-size="13.5" font-weight="700" fill="{c}">{cols[i]["name"]}</text>')
        i = j + 1
    for k, col in enumerate(cols):
        cx = left + k * colw + (colw - 8) / 2
        out.append(f'<text x="{cx}" y="116" text-anchor="middle" font-family="{FONT}" font-size="12.5" font-weight="600" fill="{INK2}">{col["sub"]}</text>'
                   f'<text x="{cx}" y="134" text-anchor="middle" font-family="{FONT}" font-size="11" fill="{MUTE}">{col["when"]}</text>')
    for r, pid in enumerate(ORDERS):
        y = top + r * rowh
        key = cols[-1]["cells"][pid]["key"]
        out.append(f'<text x="32" y="{y + 26}" font-family="{MONO}" font-size="14" font-weight="700" fill="{INK}">{pid}</text>'
                   f'<text x="32" y="{y + 44}" font-family="{FONT}" font-size="11.5" fill="{MUTE}">{ORDER_NOTE[pid]}</text>'
                   f'<text x="32" y="{y + 59}" font-family="{FONT}" font-size="11.5" fill="{INK2}">rules say <tspan font-weight="700" fill="{LIME}">{LABEL[key]}</tspan></text>')
        for k, col in enumerate(cols):
            c = col["cells"][pid]
            x0 = left + k * colw
            tone = good if c["right"] == c["runs"] else mid if c["right"] >= 3 else bad
            out.append(f'<rect x="{x0}" y="{y + 6}" width="{colw - 8}" height="{rowh - 10}" rx="10" fill="{tone}" fill-opacity="{.1 + .22 * (1 - c["right"] / c["runs"]) if tone != good else .14}" stroke="{tone}" stroke-opacity=".55"/>'
                       f'<text x="{x0 + (colw - 8) / 2}" y="{y + 32}" text-anchor="middle" font-family="{MONO}" font-size="18" font-weight="800" fill="{tone}">{c["right"]}/{c["runs"]}</text>')
            wrong = sorted(((n, o) for o, n in c["outcomes"].items() if o != c["key"]), reverse=True)
            if wrong and col["fam"] != "B":
                n, o = wrong[0]
                note = f"{LABEL.get(o, o)} ×{n}" + (" +" if len(wrong) > 1 else "")
                out.append(f'<text x="{x0 + (colw - 8) / 2}" y="{y + 50}" text-anchor="middle" font-family="{FONT}" font-size="11" fill="{INK2}">{note}</text>')
    y = top + rowh * len(ORDERS) + 8
    out.append(f'<line x1="32" y1="{y}" x2="{w - 32}" y2="{y}" stroke="#ffffff" stroke-opacity=".12"/>')
    for t, (label, fn) in enumerate([("Right, all six orders", lambda col: f"{sum(c['right'] for c in col['cells'].values())} of {sum(c['runs'] for c in col['cells'].values())}"),
                                     ("Input tokens per order", lambda col: f"{sum(c['tokens'] for c in col['cells'].values()) / len(col['cells']) / 1000:.1f}k")]):
        yy = y + 30 + t * 40
        out.append(f'<text x="32" y="{yy}" font-family="{FONT}" font-size="14" font-weight="700" fill="{INK2}">{label}</text>')
        for k, col in enumerate(cols):
            out.append(f'<text x="{left + k * colw + (colw - 8) / 2}" y="{yy}" text-anchor="middle" font-family="{MONO}" font-size="15" font-weight="800" fill="{col["color"]}">{fn(col)}</text>')
    out.append(f'<text x="32" y="{h - 38}" font-family="{FONT}" font-size="12" fill="{MUTE}">Each cell: answers that matched the approved rules, out of five, and the most common wrong answer ("+" when there was another). Times are Central.</text>'
               f'<text x="32" y="{h - 18}" font-family="{FONT}" font-size="12" fill="{MUTE}">"Pages Kanini Context picked" was handed the documents the pack bound to each order, so it is not a fair no-context baseline; it is kept to show that even then one call misses.</text>')
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- the schema, from the release's OWL file

TTL = ROOT / "kanini_context" / "release" / "kanini-context.ttl"
TYPE = {"string": "text", "date": "date", "dateTime": "time", "boolean": "bool", "decimal": "num", "integer": "int", "duration": "span"}
SHOW = {   # the fields each box lists first; the rest are counted as "+N more"
    "Rule": ["rule_key", "rule_kind", "effect", "aspect", "conditions", "payload", "source_quote", "source_locator",
             "valid_from_lo", "valid_to_hi", "recorded", "review_exception", "reviewed_by"],
    "Authority": ["citation", "kind", "layer", "title", "closed_list"],
    "Plan": ["plan_type", "external_id", "dsnp_integration", "valid"],
    "Payer": ["legal_name", "short_name", "naic_code", "clearinghouse_ids"],
    "Endpoint": ["interface", "base_url", "version", "live", "verified_at"],
    "Program": ["kind", "name"], "Jurisdiction": ["kind", "code", "name", "contractor"],
    "Concept": ["label", "definition"], "Code": ["code", "display", "item_type", "valid", "replaced_by"],
    "ValueSet": ["name", "canonical_url", "version", "filter"],
    "DataElement": ["name", "fact", "code", "unit", "value_type", "us_core_profile"],
    "KnowledgeArtifact": ["kind", "format", "canonical_url", "version"],
    "Snapshot": ["retrieved_at", "content_sha256", "stated_effective", "blob_uri"],
    "Source": ["name", "publisher", "url", "check_every", "aging_after", "stale_after"],
}


def ontology() -> dict:
    import re
    from rdflib import OWL, RDF, RDFS, Graph
    g = Graph()
    g.parse(TTL)
    nm = lambda u: str(u).split("#")[-1]  # noqa: E731
    classes = {nm(c) for c in g.subjects(RDF.type, OWL.Class)}
    parent = {nm(a): nm(b) for a, b in g.subject_objects(RDFS.subClassOf) if nm(b) in classes}
    fields: dict[str, dict[str, str]] = {}
    for p in g.subjects(RDF.type, OWL.DatatypeProperty):
        rng = TYPE.get(next((nm(r) for r in g.objects(p, RDFS.range)), ""), "")
        for d in g.objects(p, RDFS.domain):
            cls = nm(d)
            short = nm(p)[len(cls[0].lower() + cls[1:]):]
            fields.setdefault(cls, {})[re.sub(r"(?<!^)(?=[A-Z])", "_", short).lower()] = rng
    kinds: dict[str, list[str]] = {}
    for c, b in parent.items():
        kinds.setdefault(b, []).append(c)
    rels = [(nm(d), nm(p), nm(r)) for p in g.subjects(RDF.type, OWL.ObjectProperty)
            for d in g.objects(p, RDFS.domain) for r in g.objects(p, RDFS.range)]
    return {"classes": classes, "fields": fields, "kinds": kinds, "rels": rels, "parent": parent}


def schema(o: dict) -> str:
    w = 1400
    AMBER = "#f59e0b"
    boxes: dict[str, tuple[float, float, float, float]] = {}
    out: list[str] = []
    arrows: list[str] = []

    def chips(names: list[str], x: float, y: float, width: float, c: str) -> tuple[list[str], float]:
        parts, cx, cy = [], x, y
        for n in names:
            cw = len(n) * 6.6 + 16
            if cx + cw > x + width:
                cx, cy = x, cy + 24
            parts.append(f'<rect x="{cx:.0f}" y="{cy}" width="{cw:.0f}" height="19" rx="9.5" fill="{c}" fill-opacity=".14" stroke="{c}" stroke-opacity=".45"/>'
                         f'<text x="{cx + 8:.0f}" y="{cy + 13.5}" font-family="{FONT}" font-size="11" fill="{INK}">{n}</text>')
            cx += cw + 6
        return parts, cy + 24 - y

    def box(cls: str, x: float, y: float, bw: float, c: str, kind_names: list[str] | None = None, note: str = "") -> float:
        f = o["fields"].get(cls, {})
        shown = [k for k in SHOW.get(cls, []) if k in f]
        more = len(f) - len(shown)
        kn = kind_names if kind_names is not None else [k.replace(cls, "") or k for k in sorted(o["kinds"].get(cls, []))]
        bh = 36 + 18 * len(shown) + (18 if more > 0 else 0) + 8
        kparts, kh = chips(kn, x + 12, y + bh + 2, bw - 24, c) if kn else ([], 0)
        bh += kh + (8 if kn else 0)
        out.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh:.0f}" rx="11" fill="#0f0a26" stroke="{c}" stroke-opacity=".75" stroke-width="1.4"/>'
                   f'<path d="M{x} {y + 11}a11 11 0 0 1 11 -11h{bw - 22}a11 11 0 0 1 11 11v19h-{bw}z" fill="{c}" fill-opacity=".2"/>'
                   f'<text x="{x + 12}" y="{y + 21}" font-family="{FONT}" font-size="14.5" font-weight="800" fill="{INK}">{cls}</text>'
                   + (f'<text x="{x + bw - 12}" y="{y + 21}" text-anchor="end" font-family="{FONT}" font-size="11" fill="{c}">{note}</text>' if note else ""))
        for i, k in enumerate(shown):
            fy = y + 48 + 18 * i
            out.append(f'<text x="{x + 14}" y="{fy}" font-family="{MONO}" font-size="12" fill="{INK2}">{k}</text>'
                       f'<text x="{x + bw - 14}" y="{fy}" text-anchor="end" font-family="{MONO}" font-size="11" fill="{MUTE}">{f[k]}</text>')
        if more > 0:
            out.append(f'<text x="{x + 14}" y="{y + 48 + 18 * len(shown)}" font-family="{FONT}" font-size="11.5" font-style="italic" fill="{MUTE}">+{more} more fields</text>')
        out.extend(kparts)
        boxes[cls] = (x, y, bw, bh)
        return bh

    def frame(x: float, y: float, fw: float, fh: float, c: str, title: str, sub: str) -> None:
        out.insert(0, f'<rect x="{x}" y="{y}" width="{fw}" height="{fh}" rx="16" fill="{c}" fill-opacity=".05" stroke="{c}" stroke-opacity=".38" stroke-dasharray="5 4"/>'
                      f'<text x="{x + 16}" y="{y + 24}" font-family="{FONT}" font-size="13" font-weight="800" fill="{c}" letter-spacing="1.5">{title}</text>'
                      f'<text x="{x + 16}" y="{y + 42}" font-family="{FONT}" font-size="11.5" fill="{MUTE}">{sub}</text>')

    def label(x: float, y: float, text: str, c: str, vertical: bool = False) -> str:
        tw = len(text) * 6.5 + 12
        if vertical:
            return (f'<g transform="rotate(-90 {x} {y})"><rect x="{x - tw / 2:.0f}" y="{y - 9}" width="{tw:.0f}" height="18" rx="9" fill="{BG0}" stroke="{c}" stroke-opacity=".5"/>'
                    f'<text x="{x}" y="{y + 4}" text-anchor="middle" font-family="{MONO}" font-size="11" fill="{c}">{text}</text></g>')
        return (f'<rect x="{x - tw / 2:.0f}" y="{y - 9}" width="{tw:.0f}" height="18" rx="9" fill="{BG0}" stroke="{c}" stroke-opacity=".5"/>'
                f'<text x="{x}" y="{y + 4}" text-anchor="middle" font-family="{MONO}" font-size="11" fill="{c}">{text}</text>')

    def path(pts: list[tuple[float, float]], c: str, dashed: bool = False) -> None:
        d = "M" + " L".join(f"{px:.0f} {py:.0f}" for px, py in pts)
        mid = {CYAN: "ac", VIOLET: "av", LIME: "al", AMBER: "aa", ROSE: "ar"}[c]
        arrows.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="1.6" stroke-opacity=".85"{" stroke-dasharray=\"4 4\"" if dashed else ""} marker-end="url(#{mid})"/>')

    def loop(cls: str, c: str, text: str, dy: float = 40) -> None:
        x, y, bw, _ = boxes[cls]
        r = x + bw
        arrows.append(f'<path d="M{r} {y + 12} C{r + 46} {y + 4} {r + 46} {y + dy + 8} {r} {y + dy}" fill="none" stroke="{c}" stroke-width="1.5" stroke-opacity=".8" marker-end="url(#{ {CYAN: "ac", VIOLET: "av", LIME: "al"}[c]})"/>')
        arrows.append(label(r + 34, y + dy / 2 + 6, text, c, vertical=True))

    # column 1: coverage
    X1, BW1 = 82, 300
    y = 120
    for cls in ["Plan", "Payer", "Endpoint", "Program", "Jurisdiction"]:
        y += box(cls, X1, y, BW1, CYAN) + 34
    cov_bottom = y - 34 + 20
    # column 2: authorities and rules, then provenance
    X2, BW2 = 520, 340
    ya = 120
    ha = box("Authority", X2, ya, BW2, VIOLET, note="sets the rules")
    yr = ya + ha + 56
    rk = ["ApprovalRequired", "Exemption", "DocumentationRequirement", "CoverageCriteria", "Obligation",
          "↳ DecisionTimeframe", "↳ AppealWindow"]
    hr = box("Rule", X2, yr, BW2, VIOLET, kind_names=rk, note="one reviewed statement")
    rules_bottom = yr + hr + 20
    yp = rules_bottom + 50
    hs = box("Snapshot", X2 - 10, yp + 54, 178, ROSE)
    hsrc = box("Source", X2 + 182, yp + 54, 178, ROSE)
    prov_bottom = yp + 54 + max(hs, hsrc) + 18
    # column 3: terminology, then facts and artifacts
    X3, BW3 = 1030, 300
    yt = 120
    yt += box("Concept", X3, yt, BW3, LIME) + 34
    yt += box("Code", X3, yt, BW3, LIME) + 34
    yt += box("ValueSet", X3, yt, BW3, LIME) + 20
    term_bottom = yt
    yf = term_bottom + 50
    hd = box("DataElement", X3, yf + 54, BW3, AMBER, note="a fact the rule tests")
    yk = yf + 54 + hd + 34
    hk = box("KnowledgeArtifact", X3, yk, BW3, AMBER, kind_names=["Library", "Questionnaire"])
    facts_bottom = yk + hk + 18
    h = max(cov_bottom, prov_bottom, facts_bottom) + 60

    frame(32, 66, 400, cov_bottom - 66, CYAN, "COVERAGE · WHO PAYS, AND WHERE", "CoverageItem kinds: Payer · Plan · Program · Jurisdiction")
    frame(490, 66, 400, rules_bottom - 66, VIOLET, "AUTHORITY AND RULES · WHAT DOCUMENTS SAY", "each rule is drafted, checked against its page, then approved")
    frame(490, yp, 400, prov_bottom - yp, ROSE, "PROVENANCE · WHERE IT CAME FROM", "the exact copy each rule was read from, and its freshness limits")
    frame(1000, 66, 368, term_bottom - 66, LIME, "TERMINOLOGY · WHAT THE ORDER IS ABOUT", "TerminologyItem kinds: Concept · Code · ValueSet")
    frame(1000, yf, 368, facts_bottom - yf, AMBER, "FACTS · WHAT A RULE NEEDS", "the data elements it tests, and any linked DTR content")

    def L(c): x, y, bw, bh = boxes[c]; return x, y + 26          # noqa: E702
    def R(c, dy=26): x, y, bw, bh = boxes[c]; return x + bw, y + dy  # noqa: E702
    def T(c, dx=.5): x, y, bw, bh = boxes[c]; return x + bw * dx, y  # noqa: E702
    def B(c, dx=.5): x, y, bw, bh = boxes[c]; return x + bw * dx, y + bh  # noqa: E702

    # coverage relations
    path([B("Plan", .3), T("Payer", .3)], CYAN); arrows.append(label(B("Plan", .3)[0] + 70, (B("Plan")[1] + T("Payer")[1]) / 2, "operatedBy", CYAN))
    path([B("Payer", .3), T("Endpoint", .3)], CYAN); arrows.append(label(B("Payer", .3)[0] + 62, (B("Payer")[1] + T("Endpoint")[1]) / 2, "exposes", CYAN))
    for gx, tgt, text in ((66, "Program", "offeredUnder"), (48, "Jurisdiction", "locatedIn")):
        sx, sy = L("Plan")
        sy += 8 if tgt == "Program" else 22
        tx, ty = L(tgt)
        path([(sx, sy), (gx, sy), (gx, ty), (tx, ty)], CYAN)
        arrows.append(label(gx, (sy + ty) / 2 + (40 if tgt == "Program" else -40), text, CYAN, vertical=True))
    loop("Jurisdiction", CYAN, "within")
    # authority and rules
    path([B("Authority"), T("Rule")], VIOLET); arrows.append(label(B("Authority")[0] + 40, (B("Authority")[1] + T("Rule")[1]) / 2, "states", VIOLET))
    loop("Authority", VIOLET, "supersedes · preempts · defersTo", dy=200)
    ax, ay = L("Authority")
    path([(ax, ay + 40), (432, ay + 40)], VIOLET); arrows.append(label(476, ay + 26, "binds", VIOLET))
    sx, sy = L("Snapshot")
    path([(ax, ay + 70), (470, ay + 70), (470, sy), (sx, sy)], VIOLET)
    arrows.append(label(470, (ay + 70 + sy) / 2 + 60, "issuedIn", VIOLET, vertical=True))
    s1, s2 = R("Snapshot"), L("Source")
    path([s1, (s2[0], s1[1])], ROSE, dashed=True)
    # rule to terminology and facts
    rx, ry, rbw, rbh = boxes["Rule"]
    for i, (tgt, text, frame_edge) in enumerate((("Concept", "about", True), ("DataElement", "requires", False), ("KnowledgeArtifact", "documentedBy", False))):
        sy = ry + 40 + 34 * i
        gx = 930 + 22 * i
        if frame_edge:
            ty = 200
            path([(rx + rbw, sy), (gx, sy), (gx, ty), (1000, ty)], LIME)
        else:
            tx, ty = L(tgt)
            path([(rx + rbw, sy), (gx, sy), (gx, ty), (tx, ty)], AMBER)
        arrows.append(label(rx + rbw + 36, sy - 1, text, LIME if frame_edge else AMBER) if False else "")
        arrows.append(label(gx, (sy + ty) / 2, text, LIME if frame_edge else AMBER, vertical=True))
    path([B("Concept", .3), T("Code", .3)], LIME); arrows.append(label(B("Concept", .3)[0] + 62, (B("Concept")[1] + T("Code")[1]) / 2, "mapsTo", LIME))
    path([B("Code", .3), T("ValueSet", .3)], LIME); arrows.append(label(B("Code", .3)[0] + 72, (B("Code")[1] + T("ValueSet")[1]) / 2, "belongsTo", LIME))
    loop("Code", LIME, "replacedBy")

    n_cls = len(o["classes"])
    n_fields = sum(len(v) for v in o["fields"].values())
    head = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h:.0f}" viewBox="0 0 {w} {h:.0f}" role="img" aria-label="The Kanini Context schema">',
            "<defs>" + "".join(f'<marker id="{i}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{c}"/></marker>'
                               for i, c in (("ac", CYAN), ("av", VIOLET), ("al", LIME), ("aa", AMBER), ("ar", ROSE)))
            + f'<linearGradient id="bg4" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#140d33"/><stop offset="1" stop-color="{BG0}"/></linearGradient></defs>',
            f'<rect width="{w}" height="{h:.0f}" rx="20" fill="url(#bg4)"/><rect x="1" y="1" width="{w - 2}" height="{h - 3:.0f}" rx="19" fill="none" stroke="#ffffff" stroke-opacity=".1"/>',
            f'<text x="32" y="44" font-family="{FONT}" font-size="15" font-weight="700" fill="{MUTE}" letter-spacing="2.5">THE SCHEMA · READ FROM kanini-context.ttl · {n_cls} CLASSES · {len(o["rels"])} RELATIONS · {n_fields} FIELDS</text>']
    foot = [f'<text x="32" y="{h - 22:.0f}" font-family="{FONT}" font-size="12" fill="{MUTE}">Each box lists its main fields and their types; chips are its kinds (subclasses). Arrows are the {len(o["rels"])} named relations of the ontology; '
            f'the dashed line is the snapshot\'s source key in the database.</text>', "</svg>"]
    return "\n".join(head + out + arrows + foot)


if __name__ == "__main__":
    x = numbers()
    (OUT / "hero.svg").write_text(hero(x))
    (OUT / "results.svg").write_text(results(x))
    (OUT / "history.svg").write_text(history(history_runs()))
    (OUT / "schema.svg").write_text(schema(ontology()))
    print({k: {kk: round(v, 1) for kk, v in s.items()} for k, s in x.items()})
