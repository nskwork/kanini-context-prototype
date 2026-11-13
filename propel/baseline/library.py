"""The policy library as a plain LLM application would see it, with no Kanini Context.

Mode A of the comparison answers from this library in one of two ways:

- whole: every saved document, given whole, in one prompt (long-context reading);
- rag:   the library split into passages, the passages ranked against the order, and the best ones sent
         (retrieval-augmented generation, as most LLM applications are built).

Nothing here uses the pack: no rule, binding or page citation decides what A reads. The two billing articles
carry the AMA CPT notice of the MCD site, but their code tables list HCPCS and ICD-10-CM codes only, so they are
given whole like the other documents.

Ranking uses OpenAI embeddings (text-embedding-3-small) when a key is set, cached on disk so the library is
embedded once; without a key, or with PROPEL_RAG=bm25 (tests, offline), it uses BM25 keyword ranking. The report
names which ran.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from kanini_context.curation.pdftext import RAW, pages  # noqa: E402

MANIFEST = json.loads((ROOT / "kanini_context" / "sources" / "manifest.json").read_text())
CACHE = ROOT / "propel" / "baseline" / "cache"
EMBED_MODEL = "text-embedding-3-small"
PASSAGE_WORDS, OVERLAP_WORDS = 450, 60      # about 600 tokens a passage
TOP_K = 16                                  # passages sent to the model, about 9,000 to 10,000 tokens


@functools.lru_cache(maxsize=1)   # the library does not change while a process runs; read it once
def documents() -> list[dict]:
    """Every saved source document: id, title, edition and page texts."""
    out = []
    for m in MANIFEST:
        if (Path(RAW) / m["file"]).exists():
            out.append({"source_id": m["source_id"], "title": m["title"], "edition": m.get("edition", ""),
                        "pages": pages(m["file"])})
    return out


def whole_text() -> str:
    return "\n\n".join(f"=== {d['title']} ({d['edition']})\n" + "\n".join(d["pages"]) for d in documents())


@functools.lru_cache(maxsize=1)
def passages() -> list[dict]:
    """The library cut into overlapping passages, each with its document and page."""
    out = []
    for d in documents():
        for n, text in enumerate(d["pages"], 1):
            words = text.split()
            step = PASSAGE_WORDS - OVERLAP_WORDS
            for i in range(0, max(len(words), 1), step):
                chunk = " ".join(words[i:i + PASSAGE_WORDS])
                if len(chunk) > 40:
                    out.append({"source_id": d["source_id"], "title": d["title"], "page": n, "text": chunk})
                if i + PASSAGE_WORDS >= len(words):
                    break
    return out


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)?", s.lower())


def _bm25(query: str, items: list[dict], k1: float = 1.5, b: float = 0.75) -> list[float]:
    docs = [_tokens(p["text"]) for p in items]
    avg = sum(map(len, docs)) / max(len(docs), 1)
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    q = set(_tokens(query))
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in q:
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        scores.append(s)
    return scores


_STORE: dict = {}


def _embed(texts: list[str]) -> list[list[float]]:
    import litellm
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"embeddings-{EMBED_MODEL}.json"
    if not _STORE:   # the embeddings file is read once per process, then kept in memory
        _STORE.update(json.loads(path.read_text()) if path.exists() else {})
    store = _STORE
    keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
    todo = [(k, t) for k, t in zip(keys, texts) if k not in store]
    for i in range(0, len(todo), 96):
        batch = todo[i:i + 96]
        resp = litellm.embedding(model=EMBED_MODEL, input=[t for _, t in batch])
        for (k, _), row in zip(batch, resp.data):
            store[k] = row["embedding"] if isinstance(row, dict) else row.embedding
    if todo:
        path.write_text(json.dumps(store))
    return [store[k] for k in keys]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


def retrieve(query: str, k: int = TOP_K) -> tuple[list[dict], str]:
    """The k passages that best match the query, in library order, and the ranking method used."""
    items = passages()
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("PROPEL_RAG", "auto") != "bm25":
        vecs = _embed([p["text"] for p in items] + [query])
        q = vecs[-1]
        scores, method = [_cos(v, q) for v in vecs[:-1]], f"embeddings ({EMBED_MODEL})"
    else:
        scores, method = _bm25(query, items), "BM25 keyword ranking"
    best = sorted(range(len(items)), key=lambda i: -scores[i])[:k]
    return [items[i] for i in sorted(best)], method


def rag_text(found: list[dict]) -> str:
    return "\n\n".join(f"=== {p['title']}, page {p['page']}\n{p['text']}" for p in found)
