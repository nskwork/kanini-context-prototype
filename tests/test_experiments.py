"""E1 (search with other chunking) and its grading, without a model: chunk shapes, the token budget, the state filter,
and the two kinds of unsupported answer. The runs themselves need the model key (python -m propel.baseline.chunking)."""
from __future__ import annotations

import pytest

from propel.baseline import chunking as K
from propel.baseline import library


@pytest.fixture(autouse=True)
def keyword_ranking(monkeypatch):   # no embedding model here: rank by BM25 so the budget and filters can be checked
    monkeypatch.setattr(K, "_rank_embed", lambda items, q: library._bm25(q, items))


def test_paragraph_chunks_cover_every_document_within_the_size_limit():
    c = K.chunks("paragraph")
    assert {x["source_id"] for x in c} == {d["source_id"] for d in library.documents()}
    long = [x for x in c if len(x["text"].split()) > K.MAX_WORDS]
    assert len(long) <= len(c) * 0.02          # only single sentences longer than the limit
    assert all(x["pages"] and x["page"] == min(x["pages"]) for x in c)
    assert not any(K._HEADER.match(x["text"]) for x in c)   # the browser's print header is not a passage


@pytest.mark.parametrize("strategy", ["current", "paragraph", "hybrid", "hybrid_state"])
def test_every_strategy_fills_the_same_budget(strategy):
    found = K.retrieve(strategy, "erythropoiesis stimulating agent hemoglobin eGFR chronic kidney disease Ohio", "OH")
    used = sum(K._tokens(f"=== {c['title']}, page {c['page']}\n{c['text']}") for c in found)
    assert 0.8 * K.BUDGET < used <= K.BUDGET


def test_state_filter_keeps_the_other_region_out():
    found = K.retrieve("hybrid_state", "erythropoiesis stimulating agent eGFR creatinine", "OH")
    assert not {c["source_id"] for c in found} & {"mcd-lcd-l39237", "mcd-article-a58982"}


def test_invented_and_misapplied():
    psg = [{"source_id": "mcd-lcd-l39237", "text": "eGFR less than 45 and Hgb less than 10"},
           {"source_id": "mcd-lcd-l34356", "text": "Hgb less than 10 or Hct less than 30"}]
    ans = {"reasons": ["eGFR 52 is above the 45 limit", "Hgb 9.4 below 10"], "citation": "LCD L39237"}
    assert K.invented(ans, psg, "Patient facts: hgb 9.4 egfr 52") == []
    assert K.invented({"reasons": ["eGFR must be below 60"]}, psg, "egfr 52") == ["60"]
    assert "Tennessee" in K.misapplied(ans, False, psg, "OH", ["D63.1", "N18.31"])
    assert K.misapplied(ans, True, psg, "OH", []) is None
    cancer = {"reasons": ["Listed with PA for a cancer diagnosis"], "citation": "UHC list"}
    assert "cancer" in K.misapplied(cancer, False, [], "TN", ["D63.1", "N18.32"])


def test_refuses_to_run_without_an_embedding_model(monkeypatch, capsys):
    from propel import llm
    monkeypatch.setattr(llm, "embed_model", lambda: None)
    assert K.main(["--runs", "1"]) == 1 and "keyword search" in capsys.readouterr().out
