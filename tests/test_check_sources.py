"""The source check (kanini_context/sources/check_sources.py) against a fake web: what it reads for each kind of source,
when it records a change, and that a dry run writes nothing. No network."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

from conftest import ROOT
from kanini_context.sources import check_sources as cs
from kanini_context.sources.check_sources import Page

MANIFEST = json.loads((ROOT / "kanini_context/sources/manifest.json").read_text(encoding="utf-8"))
SCHEDULE = json.loads((ROOT / "kanini_context/sources/schedule.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 21, 2, 0, tzinfo=timezone.utc)   # a Saturday
PDF = b"%PDF-1.7 fake"


def src(sid):
    return copy.deepcopy(next(m for m in MANIFEST if m["source_id"] == sid))


def web(pages: dict):
    """A fake web: url (or url prefix ending in *) -> Page; anything else 404."""
    def get(url):
        for k, v in pages.items():
            if url == k or (k.endswith("*") and url.startswith(k[:-1])):
                return v
        return Page(404)
    return get


def one(sid, pages, **kw):
    m = [src(sid)]
    results, writes = cs.run(m, SCHEDULE, web(pages), now=NOW, only=[sid], **kw)
    return results[0], m[0], writes


def test_every_source_in_the_manifest_has_a_schedule():
    assert {m["source_id"] for m in MANIFEST} == set(SCHEDULE["sources"])
    assert {c["kind"] for c in SCHEDULE["sources"].values()} <= set(cs.KINDS)


def test_mcd_same_version_is_unchanged_and_resets_the_clock(tmp_path):
    page = Page(200, b'<a href="lcd.aspx?lcdid=39237&amp;ver=13">current</a>')
    r, m, w = one("mcd-lcd-l39237", {"https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=39237": page}, incoming=tmp_path)
    assert r.outcome == "unchanged" and w == 1
    assert m["checks"][-1]["by"] == "check_sources" and m["checks"][-1]["ok"] is True
    assert not m.get("change_flags")


def test_mcd_new_version_raises_one_flag(tmp_path):
    page = Page(200, b'<a href="lcd.aspx?lcdid=39237&ver=14">v14</a> Revision Effective Date 12/01/2026')
    r, m, _ = one("mcd-lcd-l39237", {"https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=39237": page}, incoming=tmp_path)
    assert r.outcome == "changed" and r.applies_from == "2026-12-01"
    assert [f["url"].endswith("ver=14") for f in m["change_flags"]] == [True]
    assert {"detected_at", "applies_from"} <= set(m["change_flags"][0])          # the two keys load.py reads
    # run again: already recorded, not raised twice
    again, _ = cs.run([m], SCHEDULE, web({"https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=39237": page}),
                      now=NOW, only=["mcd-lcd-l39237"], incoming=tmp_path)
    assert again[0].outcome == "already_flagged" and len(m["change_flags"]) == 1


def test_mcd_reads_the_coverage_api_first(tmp_path):
    api = Page(200, json.dumps({"data": [{"document_display_id": "L39237", "document_version": "13"}]}).encode())
    r, _, _ = one("mcd-lcd-l39237", {"https://api.coverage.cms.gov/v1/reports/local-coverage-final-lcds": api}, incoming=tmp_path)
    assert r.outcome == "unchanged" and "Coverage API" in r.detail[0]


def test_ecfr_amendment_after_the_saved_edition_is_a_change(tmp_path):
    later = Page(200, json.dumps({"content_versions": [{"amendment_date": "2027-01-01", "identifier": "422.568"}]}).encode())
    r, m, _ = one("ecfr-42-422-568", {"https://www.ecfr.gov/api/versioner/v1/versions/title-42.json*": later}, incoming=tmp_path)
    assert r.outcome == "changed" and m["change_flags"][-1]["applies_from"] == "2027-01-01"
    old = Page(200, json.dumps({"content_versions": [{"amendment_date": "2024-01-01"}]}).encode())
    r2, m2, _ = one("ecfr-42-422-568", {"https://www.ecfr.gov/api/versioner/v1/versions/title-42.json*": old}, incoming=tmp_path)
    assert r2.outcome == "unchanged" and not m2.get("change_flags")


def test_payer_known_new_edition_is_not_raised_again(tmp_path):
    """UHC's step-therapy edition from 1 Jan 2027 is already recorded in the manifest: found again, not raised again."""
    m0 = src("uhc-ma-partb-step-therapy")
    pages = {m0["url"]: Page(200, b"%PDF-different", "application/pdf"),
             m0["change_flags"][0]["url"]: Page(200, PDF, "application/pdf")}
    r, m, _ = one("uhc-ma-partb-step-therapy", pages, incoming=tmp_path)
    # the saved URL now serves other bytes, so that is the one change; the known edition is not added again
    assert [f["url"] for f in m["change_flags"]].count(m0["change_flags"][0]["url"]) == 1
    assert r.outcome == "changed" and r.new_url == m0["url"]


def test_payer_next_edition_by_file_name(tmp_path):
    m0 = src("uhc-ma-pa-list")
    nxt = m0["url"].replace("Eff-10-1-26", "Eff-1-1-27")
    r, m, _ = one("uhc-ma-pa-list", {nxt: Page(200, PDF, "application/pdf")}, incoming=tmp_path)
    assert r.outcome == "changed" and r.applies_from == "2027-01-01" and m["change_flags"][-1]["url"] == nxt
    assert (tmp_path / "uhc-ma-pa-list-20261121.pdf").read_bytes() == PDF   # new edition saved for drafting


def test_pdf_same_and_different_fingerprint(tmp_path, monkeypatch):
    m0 = src("cms-wiser-factsheet")
    monkeypatch.setattr(cs, "sha", lambda b: m0["sha256"] if b == PDF else "f" * 64)
    r, m, _ = one("cms-wiser-factsheet", {m0["url"]: Page(200, PDF, "application/pdf")}, incoming=tmp_path)
    assert r.outcome == "unchanged" and not m.get("change_flags")
    r2, m2, _ = one("cms-wiser-factsheet", {m0["url"]: Page(200, b"%PDF-new", "application/pdf")}, incoming=tmp_path)
    assert r2.outcome == "changed" and len(m2["change_flags"]) == 1


def test_cms_page_first_run_is_a_baseline_then_a_text_change_is_a_change(tmp_path):
    m0 = src("cms-asc-pa-demo")
    body = "<main>" + "Prior authorization demonstration for ASC services. " * 10 + "</main> Page Last Modified: 11/20/2025 03:51 PM"
    r, m, _ = one("cms-asc-pa-demo", {m0["url"]: Page(200, body.encode())}, incoming=tmp_path)
    assert r.outcome == "baseline" and m["watch"]["text_sha256"] and not m.get("change_flags")
    # the date moves, the text does not: unchanged
    moved = body.replace("11/20/2025", "11/25/2026")
    r2, _ = cs.run([m], SCHEDULE, web({m0["url"]: Page(200, moved.encode())}), now=NOW, only=["cms-asc-pa-demo"], incoming=tmp_path)
    assert r2[0].outcome == "unchanged" and "date moved" in r2[0].note
    # the text changes: a change
    edited = moved.replace("ASC services", "ASC services, phase 2 adds Ohio")
    r3, _ = cs.run([m], SCHEDULE, web({m0["url"]: Page(200, edited.encode())}), now=NOW, only=["cms-asc-pa-demo"], incoming=tmp_path)
    assert r3[0].outcome == "changed" and len(m["change_flags"]) == 1


def test_unreadable_source_records_nothing(tmp_path):
    r, m, w = one("cms-wiser-factsheet", {}, incoming=tmp_path)
    assert r.outcome == "unreadable" and w == 0 and m == src("cms-wiser-factsheet")


def test_dry_run_writes_nothing(tmp_path):
    manifest = copy.deepcopy(MANIFEST)
    page = Page(200, b'<a href="lcd.aspx?lcdid=39237&ver=14">v14</a>')
    results, writes = cs.run(manifest, SCHEDULE, web({"https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=39237": page}),
                             now=NOW, dry=True, incoming=tmp_path)
    assert writes == 0 and manifest == MANIFEST and not list(tmp_path.iterdir())
    assert any(r.outcome == "changed" for r in results) and "Dry run: nothing written." in cs.report(results, True)


@pytest.mark.parametrize("every,day,last,expected", [
    ("daily", None, "2026-11-20", True), ("daily", None, "2026-11-21", False),
    ("weekly", "saturday", "2026-11-18", True), ("weekly", "monday", "2026-11-18", False),
    ("weekly", "monday", "2026-11-10", True), ("weekly", "monday", None, True)])
def test_due(every, day, last, expected):
    m = {"checks": [{"at": f"{last}T02:00:00+00:00", "ok": True, "by": "check_sources"}] if last else []}
    assert cs.is_due(m, {"every": every, "day": day}, NOW.date()) is expected
