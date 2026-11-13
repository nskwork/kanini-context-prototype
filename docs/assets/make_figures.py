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


if __name__ == "__main__":
    x = numbers()
    (OUT / "hero.svg").write_text(hero(x))
    (OUT / "results.svg").write_text(results(x))
    print({k: {kk: round(v, 1) for kk, v in s.items()} for k, s in x.items()})
