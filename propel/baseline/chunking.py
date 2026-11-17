"""E1: search (RAG) with other ways of cutting the library, to rule out "RAG failed because of the chunking".

Same six orders, same model, same prompt and the same passage budget per call (about 9,500 tokens, filled greedily
with the best-ranked passages), so the strategies are comparable:

  current      fixed 450-word passages with 60-word overlap, ranked by embeddings (what lane A's search used)
  paragraph    the document's own paragraphs and headings, merged up to a size limit, ranked by embeddings
  semantic     cut where the meaning shifts (sentence embeddings, cut at low similarity between neighbours)
  hybrid       paragraph chunks ranked by embeddings and BM25 together (reciprocal rank fusion)
  hybrid_state hybrid, over the documents for the order's state only. Half of what Kanini Context does (it picks the
               region's policy), so it is a reference, not plain RAG

Measured per run: right answer (against the rules, lane C); tokens; whether the deciding page was retrieved; whether
the answer changed between runs; and unsupported answers, two ways:
  invented     the answer names a code or a number that is in none of the passages it was given (nor in the order)
  misapplied   a wrong answer resting on real text taken out of its scope: it names another region's policy, uses a
               number found only in another region's passages, or applies a cancer-only listing to a patient with no
               cancer diagnosis
Counting only invented text would show "0" and miss the problem seen in the recorded runs: the numbers and codes were
in the passages, applied where they do not govern.

    python -m propel.baseline.chunking --runs 5            # needs the model key and an embedding model
    python -m propel.baseline.chunking --runs 1 --only paragraph hybrid_state

A pre-flight check refuses to start without an embedding model (it would fall back to keyword search unnoticed) and
prints which one is used. Writes propel/baseline/results/e1-<stamp>.json and .md; the Testing page reads the newest.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from kanini_context import env  # noqa: E402,F401

from propel.baseline import library  # noqa: E402

STRATEGIES = ["current", "paragraph", "semantic", "hybrid", "hybrid_state"]
NAMES = {"current": "Fixed 450-word passages (as tested)", "paragraph": "Paragraphs and headings",
         "semantic": "Cut where the meaning shifts", "hybrid": "Paragraphs, embeddings + BM25",
         "hybrid_state": "Hybrid, the state's documents only (reference)"}
BUDGET = 9500          # tokens of passages per call, the same for every strategy
MAX_WORDS, MIN_WORDS = 450, 120
OUT = ROOT / "propel/baseline/results"
_HEADER = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}, \d{1,2}:\d{2} [AP]M\b.*|https?://\S+.*|\d+ of \d+)$")
_ITEM = re.compile(r"^([A-Z]|\d{1,2}|[ivx]{1,4})[.)]\s")


# --- chunking ---------------------------------------------------------------------------------------------------------

def _lines(doc: dict):
    """(page, line) for a document, without the browser's print header and footer lines."""
    for n, text in enumerate(doc["pages"], 1):
        for ln in text.splitlines():
            ln = ln.strip()
            if ln and not _HEADER.match(ln):
                yield n, ln


def paragraphs(doc: dict) -> list[dict]:
    """The document's own blocks: a heading (a short line ending in a colon, or with no closing stop) or a list item
    starts a new block; a line ending a sentence well short of the margin ends one."""
    out, cur, pages_, width = [], [], set(), 0
    lines = list(_lines(doc))
    width = statistics.median([len(x) for _, x in lines]) if lines else 80

    def flush():
        if cur:
            out.append({"text": " ".join(cur), "pages": sorted(pages_)})
        cur.clear()
        pages_.clear()
    for page, ln in lines:
        heading = (ln.endswith(":") and len(ln.split()) <= 12) or (len(ln.split()) <= 8 and not re.search(r"[.,;]$", ln) and ln[:1].isupper())
        if heading or _ITEM.match(ln):
            flush()
        cur.append(ln)
        pages_.add(page)
        if heading or (re.search(r"[.;]$", ln) and len(ln) < 0.7 * width):
            flush()
    flush()
    return out


def _merge(blocks: list[dict], doc: dict) -> list[dict]:
    """Small blocks merged with their neighbours up to MAX_WORDS; long ones split at sentence ends."""
    out, cur, pg = [], [], set()

    def emit():
        if cur:
            out.append({"source_id": doc["source_id"], "title": doc["title"], "pages": sorted(pg), "page": min(pg),
                        "text": " ".join(cur)})
        cur.clear()
        pg.clear()
    for b in blocks:
        words = b["text"].split()
        if len(words) > MAX_WORDS:   # a long block: by sentences
            emit()
            sent = re.split(r"(?<=[.;])\s+(?=[A-Z0-9(])", b["text"])
            for s in sent:
                if len(" ".join(cur).split()) + len(s.split()) > MAX_WORDS:
                    pg.update(b["pages"])
                    emit()
                cur.append(s)
            pg.update(b["pages"])
            emit()
            continue
        if cur and len(" ".join(cur).split()) + len(words) > MAX_WORDS:
            emit()
        cur.append(b["text"])
        pg.update(b["pages"])
        if len(" ".join(cur).split()) >= MIN_WORDS:
            emit()
    emit()
    return [c for c in out if len(c["text"]) > 40]


@functools.lru_cache(maxsize=None)
def chunks(strategy: str) -> tuple:
    if strategy == "current":
        return tuple({**p, "pages": [p["page"]]} for p in library.passages())
    if strategy in ("paragraph", "hybrid", "hybrid_state"):
        return tuple(c for d in library.documents() for c in _merge(paragraphs(d), d))
    if strategy == "semantic":
        return tuple(c for d in library.documents() for c in semantic(d))
    raise ValueError(strategy)


def semantic(doc: dict) -> list[dict]:
    """Sentences, cut where neighbouring sentences are least alike (below the document's 20th percentile of
    neighbour similarity), within MIN_WORDS and MAX_WORDS."""
    sents = []
    for b in paragraphs(doc):
        for s in re.split(r"(?<=[.;:])\s+(?=[A-Z0-9(])", b["text"]):
            if s.strip():
                sents.append((s.strip(), b["pages"]))
    if not sents:
        return []
    from propel import llm
    vecs = library._embed([s for s, _ in sents], llm.embed_model())
    sims = [library._cos(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
    cut = sorted(sims)[max(0, int(len(sims) * 0.2) - 1)] if sims else 0
    out, cur, pg = [], [], set()
    for i, (s, pages_) in enumerate(sents):
        cur.append(s)
        pg.update(pages_)
        n = len(" ".join(cur).split())
        if n >= MAX_WORDS or (n >= MIN_WORDS and i < len(sims) and sims[i] <= cut):
            out.append({"source_id": doc["source_id"], "title": doc["title"], "pages": sorted(pg), "page": min(pg), "text": " ".join(cur)})
            cur, pg = [], set()
    if cur:
        out.append({"source_id": doc["source_id"], "title": doc["title"], "pages": sorted(pg), "page": min(pg), "text": " ".join(cur)})
    return out


# --- ranking and the budget -------------------------------------------------------------------------------------------

def _rank_embed(items, query: str) -> list[float]:
    from propel import llm
    vecs = library._embed([p["text"] for p in items] + [query], llm.embed_model())
    return [library._cos(v, vecs[-1]) for v in vecs[:-1]]


def _rrf(*score_lists: list[float], k: int = 60) -> list[float]:
    out = [0.0] * len(score_lists[0])
    for scores in score_lists:
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        for rank, i in enumerate(order, 1):
            out[i] += 1 / (k + rank)
    return out


@functools.lru_cache(maxsize=None)
def _tokens(text: str) -> int:
    import litellm
    from propel import llm
    return litellm.token_counter(model=llm.TOKEN_MODEL, text=text)


def retrieve(strategy: str, query: str, state: str) -> list[dict]:
    """The best-ranked chunks until the token budget is spent, in library order."""
    items = list(chunks(strategy))
    if strategy == "hybrid_state":
        from propel.baseline.failures import REGION_DOCS, STATE_NAME
        other = set().union(*[ids for name, ids in REGION_DOCS.items() if name != STATE_NAME.get(state)])
        items = [c for c in items if c["source_id"] not in other]
    if strategy in ("hybrid", "hybrid_state"):
        scores = _rrf(_rank_embed(items, query), library._bm25(query, items))
    else:
        scores = _rank_embed(items, query)
    picked, used = [], 0
    for i in sorted(range(len(items)), key=lambda i: -scores[i]):
        t = _tokens(f"=== {items[i]['title']}, page {items[i]['page']}\n{items[i]['text']}")
        if used + t > BUDGET:
            continue
        picked.append(i)
        used += t
    order = {id(c): n for n, c in enumerate(chunks(strategy))}
    return sorted((items[i] for i in picked), key=lambda c: order.get(id(c), 0))


# --- grading ----------------------------------------------------------------------------------------------------------

_CODE = re.compile(r"\b(?:[A-TV-Z]\d{2}\.\d{1,4}|[A-Z]\d{4})\b")
_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")


def claims(answer: dict) -> set[str]:
    """The codes and numbers an answer states in its reasons and citation."""
    text = " ".join([*(str(x) for x in answer.get("reasons") or []), str(answer.get("citation") or "")])
    nums = {n for n in _NUM.findall(text) if not re.fullmatch(r"(19|20)\d\d", n) and n not in {"0", "1", "2", "3"}}
    return set(_CODE.findall(text)) | nums


def _norm_num(x: str) -> str:
    return x.rstrip("0").rstrip(".") if "." in x else x


def invented(answer: dict, passages: list[dict], order_text: str) -> list[str]:
    """Codes and numbers in the answer that appear in none of its passages and not in the order."""
    pool = " ".join(p["text"] for p in passages) + " " + order_text
    have = set(_CODE.findall(pool)) | {_norm_num(n) for n in _NUM.findall(pool)}
    return sorted(c for c in claims(answer) if (_norm_num(c) if _NUM.fullmatch(c) else c) not in have)


def misapplied(answer: dict, right: bool, passages: list[dict], state: str, diagnoses: list[str]) -> str | None:
    """Why a wrong answer rests on real text out of its scope, or None."""
    if right:
        return None
    from propel.baseline.failures import REGION_DOCS, STATE_NAME
    text = " ".join([*(str(x) for x in answer.get("reasons") or []), str(answer.get("citation") or "")])
    mine = STATE_NAME.get(state)
    for name, ids in REGION_DOCS.items():
        if name == mine:
            continue
        nums = {i.split("-")[-1].upper() for i in ids}   # l39237 -> L39237
        if any(n in text.upper() for n in nums):
            return f"cites {name}'s policy"
        theirs = " ".join(p["text"] for p in passages if p["source_id"] in ids)
        ours = " ".join(p["text"] for p in passages if p["source_id"] not in ids)
        only = [c for c in claims(answer) if c in theirs and c not in ours and len(c) > 1]
        if only:
            return f"uses {', '.join(sorted(only))} from {name}'s policy only"
    if re.search(r"cancer|chemotherap|oncolog|malignan", text, re.I) and not any(d.startswith("C") for d in diagnoses):
        return "applies a cancer-only listing to a patient with no cancer diagnosis"
    return None


# --- one run ----------------------------------------------------------------------------------------------------------

def run_one(strategy: str, pid: str, ref: dict, model: str) -> dict:
    import litellm
    from propel.baseline import compare as C
    from propel.baseline.failures import deciding_page
    p, o, f = C.order(pid)
    found = retrieve(strategy, C.rag_query(pid), p["coverage"]["state"])
    prompt = C.PROMPT.format(plan=C.PLAN_NAMES.get(p["coverage"]["plan"], p["coverage"]["plan"]),
                             state=C.STATE_NAMES.get(p["coverage"]["state"], p["coverage"]["state"]), code=o["code"],
                             desc=o["description"], dos=o["date_of_service"], enc=o["encounter_type"], urg=o["urgency"],
                             facts=json.dumps(p["facts"]), outcomes=C.OUTCOMES,
                             docs="\n\n".join(f"=== {c['title']}, page {c['page']}\n{c['text']}" for c in found))
    effort = os.environ.get("PROPEL_REASONING_EFFORT", "low").strip()
    t = time.perf_counter()
    from propel import llm
    resp = llm.completion(model=model, messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"},
                          **({"reasoning_effort": effort} if effort else {}))
    ms = (time.perf_counter() - t) * 1000
    try:
        ans = json.loads(resp.choices[0].message.content)
    except (ValueError, TypeError):
        ans = {"outcome": "unparseable"}
    right = ans.get("outcome") == ref["outcome"]
    dp = deciding_page(ref["eval"])
    order_text = prompt.split("DOCUMENTS", 1)[0]
    inv = invented(ans, found, order_text)
    mis = misapplied(ans, right, found, p["coverage"]["state"], p["facts"].get("diagnosis_codes", []))
    return {"outcome": ans.get("outcome"), "right": right, "reasons": ans.get("reasons"), "citation": ans.get("citation"),
            "tokens_in": resp.usage.prompt_tokens, "tokens_out": resp.usage.completion_tokens, "ms": ms,
            "passages": len(found), "deciding_found": (any(c["source_id"] == dp["source_id"] and dp["page"] in c["pages"] for c in found)
                                                       if dp else None),
            "invented": inv, "misapplied": mis}


def summarise(runs: list[dict]) -> dict:
    n = len(runs)
    return {"runs": n, "right": sum(r["right"] for r in runs), "outcomes": dict(Counter(str(r["outcome"]) for r in runs)),
            "changed_between_runs": len({r["outcome"] for r in runs}) > 1,
            "tokens_in": statistics.mean(r["tokens_in"] for r in runs), "seconds": statistics.mean(r["ms"] for r in runs) / 1000,
            "deciding_found": sum(1 for r in runs if r["deciding_found"]), "invented": sum(1 for r in runs if r["invented"]),
            "misapplied": sum(1 for r in runs if r["misapplied"])}


def markdown(rep: dict) -> str:
    lines = [f"# E1: search with other chunking ({rep['at']}, {rep['model']}, embeddings {rep['embeddings']}, {rep['runs']} runs per order)", "",
             f"Same prompt, same model, about {BUDGET:,} tokens of passages per call. Right means the same answer as the approved rules.", "",
             "| Strategy | Right | Deciding page retrieved | Invented | Misapplied | Orders whose answer changed | Tokens in |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for s, by in rep["strategies"].items():
        tot = lambda k: sum(v[k] for v in by.values())  # noqa: E731
        n = tot("runs")
        lines.append(f"| {NAMES[s]} | {tot('right')}/{n} | {tot('deciding_found')}/{n} | {tot('invented')} | {tot('misapplied')} | "
                     f"{sum(1 for v in by.values() if v['changed_between_runs'])}/{len(by)} | {statistics.mean(v['tokens_in'] for v in by.values()):,.0f} |")
    lines += ["", "Per order (right / runs):", "", "| Order | Rules say | " + " | ".join(NAMES[s] for s in rep["strategies"]) + " |",
              "| --- | --- | " + "--- | " * len(rep["strategies"])]
    for pid, ref in rep["reference"].items():
        lines.append(f"| {pid} | {ref} | " + " | ".join(f"{rep['strategies'][s][pid]['right']}/{rep['strategies'][s][pid]['runs']}"
                                                       for s in rep["strategies"] if pid in rep["strategies"][s]) + " |")
    lines += ["", "Invented: the answer names a code or number found in none of its passages nor in the order. Misapplied: a wrong "
              "answer resting on real text out of its scope (another region's policy, or a cancer-only listing for a patient "
              "with no cancer). Every run's answer and the reason it was counted are in the JSON report.", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--only", nargs="*", choices=STRATEGIES, help="strategies to run (default all)")
    a = ap.parse_args(argv)
    from propel import llm
    emb = llm.embed_model()
    if not emb or os.environ.get("PROPEL_RAG") == "bm25":
        print("No embedding model: E1 would fall back to keyword search without saying so. Set AZURE_OPENAI_EMBEDDING_DEPLOYMENT "
              "(or OPENAI_API_KEY for text-embedding-3-small) and run again.")
        return 1
    if not llm.has_key():
        print("No model key (Azure OpenAI or OpenAI in .env).")
        return 1
    print(f"model {llm.MODEL}; embeddings {emb}; budget {BUDGET:,} tokens of passages per call")
    from propel.baseline import compare as C
    from propel.baseline.failures import rules_answer
    pids = [pid for _, pid in C.SCENARIOS]
    ref = {pid: rules_answer(pid) for pid in pids}
    rep = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": llm.MODEL, "embeddings": emb,
           "runs": a.runs, "budget_tokens": BUDGET, "reference": {pid: r["outcome"] for pid, r in ref.items()},
           "strategies": {}, "detail": {}}
    for s in a.only or STRATEGIES:
        n = len(chunks(s))
        print(f"\n{NAMES[s]}: {n} chunks")
        rep["strategies"][s], rep["detail"][s] = {}, {}
        for pid in pids:
            runs = [run_one(s, pid, {"outcome": ref[pid]["outcome"], "eval": ref[pid]}, llm.MODEL) for _ in range(a.runs)]
            rep["strategies"][s][pid] = summarise(runs)
            rep["detail"][s][pid] = runs
            v = rep["strategies"][s][pid]
            print(f"  {pid:16} rules {ref[pid]['outcome']:12} right {v['right']}/{a.runs}  deciding page {v['deciding_found']}/{a.runs}"
                  f"  invented {v['invented']}  misapplied {v['misapplied']}  {v['outcomes']}")
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (OUT / f"e1-{stamp}.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    (OUT / f"e1-{stamp}.md").write_text(markdown(rep), encoding="utf-8")
    print(f"\nreport: propel/baseline/results/e1-{stamp}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
