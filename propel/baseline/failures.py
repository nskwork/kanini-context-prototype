"""What broke in testing: order by order, how the baselines without Kanini Context answered and why that was wrong.

Worked out from a comparison report (propel/baseline/compare.py) and the release itself, never written by hand:

- the rules' answer for each order, and the source page it rests on (the deciding rule's cited page);
- each baseline's answers across the runs, and every wrong answer set against what the rules require;
- for search (RAG), whether the deciding page was among the passages retrieved, and how many passages came from
  another region's policy than the one that governs the order.

The README section "What broke in testing" and the dashboard's Testing tab both come from explain().

    python -m propel.baseline.failures propel/baseline/results/compare-20261113-0850.json    # prints the markdown
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from kanini_context import env  # noqa: E402,F401

import psycopg  # noqa: E402

MANIFEST = json.loads((ROOT / "kanini_context/sources/manifest.json").read_text())
BY_SHA = {m["sha256"]: m for m in MANIFEST if m.get("sha256")}
PATIENTS = json.loads((ROOT / "propel/journey/patients.json").read_text())["patients"]
LABEL = {"no_pa": "No PA", "pa_required": "PA required", "exempt": "Exempt", "other_gate": "Other gate",
         "needs_facts": "Not yet known", "unknown": "Not known", "unparseable": "no readable answer", "no card": "no decision card"}
# the policy documents of each Medicare contractor region in the library (by source id)
REGION_DOCS = {"Tennessee": {"mcd-lcd-l39237", "mcd-article-a58982"}, "Ohio": {"mcd-lcd-l34356", "mcd-article-a56462"}}
STATE_NAME = {"TN": "Tennessee", "OH": "Ohio"}
TITLE_TO_ID = {m["title"]: m["source_id"] for m in MANIFEST}


def rules_answer(pid: str) -> dict:
    """The approved rules' answer for the patient's open order, as Kanini Context gives it."""
    p = PATIENTS[pid]
    o = p["orders"][0]
    f = {**p["facts"], "encounter_type": o["encounter_type"], "urgency": o["urgency"], "request_type": o["request_type"]}
    with psycopg.connect(f"dbname={env.CLIENT_DB}") as c:
        return c.execute("SELECT kc_api.evaluate_order(%s,%s,%s,%s::date,%s::jsonb)", (p["coverage"]["plan"], p["coverage"]["state"],
                         "HCPCS|" + o["code"], o["date_of_service"], json.dumps(f))).fetchone()[0]


def deciding_page(ev: dict) -> dict | None:
    """The source page the deciding rule rests on: its document (as in the library) and page."""
    cit = (ev.get("primary_rule") or {}).get("citation")
    src = next((s for s in ev.get("sources") or [] if s.get("citation") == cit), None)
    m = BY_SHA.get((src or {}).get("sha256"))
    if not src or not m:
        return None
    return {"source_id": m["source_id"], "title": m["title"], "citation": cit, "page": src.get("page")}


def why_wrong(rules: str, ai: str, ev: dict) -> str:
    """What a wrong answer gets wrong, set against what the rules require for the order."""
    plain = {"hgb_g_dl": "hemoglobin", "hct_pct": "hematocrit", "egfr": "eGFR", "creatinine_date": "creatinine date"}
    names = [plain.get(k, k.replace("_", " ")) for k in ((x.get("fact") or x.get("name")) if isinstance(x, dict) else str(x)
                                                         for x in ev.get("missing_facts") or [])]
    missing = ("a " + " or ".join(sorted(names)) + " result") if names else ""
    table = {
        ("no_pa", "needs_facts"): "every fact the rule tests is on the order",
        ("no_pa", "other_gate"): "the order meets the coverage criteria that govern it",
        ("no_pa", "pa_required"): "no approval is needed for this order",
        ("other_gate", "no_pa"): "the order does not meet the coverage criteria",
        ("pa_required", "needs_facts"): "the plan's list requires PA for this code whatever the facts",
        ("pa_required", "no_pa"): "the plan's list requires PA for this code",
        ("unknown", "pa_required"): "the plan's list names this drug only under cancer supportive care, with PA \"for a cancer diagnosis\" "
                                    "(page 7), and this patient has none, so the documents do not settle this order",
        ("unknown", "no_pa"): "the documents do not settle this order",
        ("needs_facts", "no_pa"): f"the order lacks {missing or 'a fact the rule requires'}, so it cannot be decided yet",
        ("needs_facts", "other_gate"): f"the order lacks {missing or 'a fact the rule requires'}, so it cannot be decided yet",
    }
    return table.get((rules, ai), f"the rules say {LABEL.get(rules, rules)}")


def sources(card: str) -> list[str]:
    """Every source line on a card ("Source: …", "- Source: …", "6) Source: …", and the bullets under a bare heading)."""
    out, lines = [], (card or "").splitlines()
    rx = re.compile(r"^\s*(?:\d+[.)]\s*|[-*>]+\s*)?\**Sources?\b[^:]*:\**\s*(.*)$", re.I)
    for k, ln in enumerate(lines):
        m = rx.match(ln)
        if m:
            if m.group(1).strip():
                out.append(m.group(1).strip())
            else:
                out += [x.strip(" -*") for x in lines[k + 1:k + 4] if x.strip().startswith(("-", "*"))]
    return out


def _quote(run: dict) -> dict:
    """The first reason the AI gave on its card, and the source line it cited, shortened."""
    card = run.get("card") or ""
    lines = card.splitlines()
    head = lambda word: re.compile(r"^\s*(?:\d+[.)]\s*|[-*>]+\s*)?\**" + word + r"\b[^:]*:\**\s*(.*)$", re.I)   # noqa: E731
    why_re = head("Why")
    why = ""
    for k, ln in enumerate(lines):   # "Why: …", "- Why: …", "4) Why (from the documents):" with bullets below
        m = why_re.match(ln)
        if m:
            why = m.group(1).strip(" -") or next((x.strip(" -*") for x in lines[k + 1:] if x.strip()), "")
            break
    if run.get("outcome") == "needs_facts":   # what it asked for is what went wrong
        nxt = next((ln.split("Next step:", 1)[1].strip() for ln in card.splitlines() if "Next step:" in ln), "")
        if nxt.endswith(":"):   # "…includes either:" with the items as bullets below
            k = next((i for i, ln in enumerate(card.splitlines()) if "Next step:" in ln), None)
            items = [x.strip(" -*") for x in card.splitlines()[k + 1:k + 4] if x.strip().startswith(("-", "*"))] if k is not None else []
            nxt = (nxt + " " + " / ".join(items)).strip()
        why = nxt or why
    if not card and run.get("reasons"):   # a one-call answer: its list of reasons
        rs = [str(r) for r in run["reasons"]]
        if run.get("outcome") == "needs_facts":   # the reason that names what it said was missing
            import re as _re
            hit = [r for r in rs if _re.search(r"missing|not (on file|present|provided|documented|available)|absent|no (documented|record)|obtain|needed|required to", r, _re.I)]
            rs = hit[-1:] or rs[-1:]
        why = "; ".join(rs)
    src = "; ".join(sources(card))
    cut = lambda t, n: t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"   # noqa: E731
    return {"why": cut(why, 260), "source": cut(src, 170)}


def explain(report: dict) -> dict:
    """Per order: the rules' answer and deciding page, and for each baseline its answers and what broke."""
    out = {"at": report.get("at"), "orders": []}
    for sc, e in report.get("scenarios", {}).items():
        pid = e["patient"]
        ev = rules_answer(pid)
        rules = e.get("reference_outcome") or ev["outcome"]   # the rules' answer when the test ran
        dp = deciding_page(ev) if rules != "unknown" else None   # "not known" rests on no page: nothing settles it
        state = STATE_NAME.get(PATIENTS[pid]["coverage"]["state"])
        row = {"order": pid, "rules": rules, "deciding": dp, "state": state, "plan": PATIENTS[pid]["coverage"]["plan"]}
        for key, name in (("A_rag", "search"), ("A_whole", "whole"), ("A_agent", "agent")):
            s = e.get(key) or (e.get("A") if key == "A_agent" and "A_rag" in e else None)   # the dashboard's merged report names it A
            if not s:
                continue
            wrong = {o: n for o, n in s["outcomes"].items() if o != rules}
            item = {"runs": s["runs"], "right": s["matches_rules"], "answers": s["outcomes"], "tokens": s["tokens_in"],
                    "broke": [{"answer": o, "runs": n, "why": why_wrong(rules, o, ev)} for o, n in sorted(wrong.items(), key=lambda x: -x[1])]}
            got = e.get("A_rag_sent") or e.get("A_sent")
            if key == "A_rag" and got:
                sent = [TITLE_TO_ID.get(p["title"]) for p in got["passages"]]
                item["passages"] = len(sent)
                item["deciding_page_found"] = bool(dp) and any(p["page"] == dp["page"] and TITLE_TO_ID.get(p["title"]) == dp["source_id"]
                                                           for p in got["passages"])
                if state in REGION_DOCS and row["plan"] == "original-medicare":
                    other = next(r for r in REGION_DOCS if r != state)
                    item["own_region"] = sum(1 for x in sent if x in REGION_DOCS[state])
                    item["other_region"] = sum(1 for x in sent if x in REGION_DOCS[other])
                    item["other_region_name"] = other
            if row["plan"] == "original-medicare" and state == "Ohio":   # what a wrong "Other gate" on the Ohio order rested on
                runs_o = e.get(f"{key}_runs") or (e.get("A_runs") if key == "A_agent" else None) or []
                for b in item["broke"]:
                    if b["answer"] != "other_gate":
                        continue
                    texts = [(r.get("card") or "") + " ".join(map(str, r.get("reasons") or [])) for r in runs_o if r.get("outcome") == "other_gate"]
                    tn = sum(1 for t in texts if re.search(r"A58982|L39237|<\s*45|below 45|less than 45", t))
                    st3a = sum(1 for t in texts if re.search(r"N18\.31|3a|IIIa", t, re.I))
                    if not runs_o or tn:
                        b["why"] += ("; Other gate is the answer under Tennessee's eGFR limit (below 45), not Ohio's (below 60)"
                                     + (f" ({tn} of {len(texts)} runs used it)" if texts else ""))
                    if st3a and st3a > tn:
                        b["why"] += ("; the stage 3a (N18.31) exclusion is in Tennessee's article A58982, while Ohio's article A56462 "
                                     "lists N18.31" + (f" ({st3a} of {len(texts)} runs used it)" if texts else ""))
            runs = e.get(f"{key}_runs") or (e.get("A_runs") if key == "A_agent" else None)
            if runs:   # the AI's own words for each wrong answer, and how many of those runs cited another region's policy
                other_docs = {"Ohio": ("L39237", "A58982"), "Tennessee": ("L34356", "A56462")}.get(state) if row["plan"] == "original-medicare" else None
                for b in item["broke"]:
                    mine = [r for r in runs if r.get("outcome") == b["answer"] and (r.get("card") or r.get("reasons"))]
                    if mine:
                        b["quote"] = _quote(mine[0])
                    if other_docs:
                        b["cited_other_region"] = sum(1 for r in mine if any(d in " ".join(sources(r.get("card") or "")) for d in other_docs))
                        b["other_region_name"] = "Tennessee" if state == "Ohio" else "Ohio"
            row[name] = item
        out["orders"].append(row)
    return out


def _when(at: str | None) -> str:
    if not at:
        return "date not recorded"
    t = datetime.fromisoformat(at).astimezone(ZoneInfo("America/Chicago"))
    return t.strftime("%d %b %Y, %H:%M Central time")


def to_markdown(x: dict, report_name: str = "", when: dict | None = None) -> str:
    if when:   # baselines tested in different runs: one line each
        lines = ["Each order was answered 5 times by each way; GPT-5 mini, reasoning effort low. "
                 + " ".join(f"{k}: test run started {v}." for k, v in when.items()), ""]
    else:
        lines = [f"Test run started {_when(x['at'])}" + (f" (`propel/baseline/results/{report_name}`)" if report_name else "")
                 + ". Each order was answered 5 times by each baseline; GPT-5 mini, reasoning effort low.", ""]
    for r in x["orders"]:
        dp = r["deciding"]
        lines.append(f"**`{r['order']}`**: the rules say **{LABEL.get(r['rules'], r['rules'])}**"
                     + (f", resting on {dp['citation']}, page {dp['page']}." if dp
                        else ": the documents do not settle it, so the order goes to staff." if r["rules"] == "unknown" else "."))
        for key, name in (("agent", "Same agent, no context"), ("search", "Search (RAG)"), ("whole", "Every document")):
            it = r.get(key)
            if not it:
                continue
            head = f"- {name}: right {it['right']} of {it['runs']}"
            if not it["broke"]:
                lines.append(head + ".")
                continue
            lines.append(head + ". " + " ".join(f"{b['runs']} run{'s' if b['runs'] > 1 else ''} answered {LABEL.get(b['answer'], b['answer'])}: "
                                               f"{b['why']}." + (f" {b['cited_other_region']} of them cited {b['other_region_name']}'s policy."
                                                                if b.get("cited_other_region") else "") for b in it["broke"]))
            for b in it["broke"]:
                if b.get("quote") and b["quote"]["why"]:
                    lines.append(f"  - In its own words ({LABEL.get(b['answer'], b['answer'])}{', what it said was missing' if b['answer'] == 'needs_facts' else ''}): “{b['quote']['why']}”"
                                 + (f" Source it cited: {b['quote']['source']}" if b["quote"]["source"] else ""))
            if key == "search" and dp and "deciding_page_found" in it:
                note = (f"  The deciding page was {'among' if it['deciding_page_found'] else '**not** among'} the {it['passages']} passages retrieved"
                        + (f"; {it['other_region']} came from {it['other_region_name']}'s policy and {it['own_region']} from {r['state']}'s"
                           if "other_region" in it else "") + ".")
                lines.append(note)
        lines.append("")
    return "\n".join(lines)


def merged(paths: list[Path]) -> tuple[dict, dict]:
    """Several reports as one, each baseline from the newest report holding it, and when each was tested."""
    reps = sorted((json.loads(p.read_text()) | {"_file": p.name} for p in paths), key=lambda r: r["at"], reverse=True)
    out, when = {"at": reps[0]["at"], "scenarios": {}}, {}
    names = {"A_agent": "Same agent, no context", "A_rag": "Search (RAG)", "A_whole": "Every document"}
    for r in reps:
        for sc, e in r["scenarios"].items():
            tgt = out["scenarios"].setdefault(sc, {"patient": e["patient"], "reference_outcome": e["reference_outcome"]})
            for k in ("A_agent", "A_rag", "A_whole"):
                if e.get(k) and k not in tgt:
                    tgt[k] = e[k]
                    for extra in (f"{k}_runs", f"{k}_sent"):
                        if extra in e:
                            tgt[extra] = e[extra]
                    when.setdefault(names[k], f"{_when(r['at'])} (`{r['_file']}`)")
    return out, when


if __name__ == "__main__":
    paths = [Path(p) for p in sys.argv[1:]]
    if len(paths) == 1:
        print(to_markdown(explain(json.loads(paths[0].read_text())), paths[0].name))
    else:
        rep, when = merged(paths)
        print(to_markdown(explain(rep), when=when))
