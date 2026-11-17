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
    def get(url, head=False, etag=None):
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


def test_mcd_new_version_from_the_api_reads_its_effective_date_and_saves_the_page(tmp_path):
    """CMS publishes versions weeks ahead: the flag applies from the revision's effective date, not today."""
    api = Page(200, json.dumps({"data": [{"document_display_id": "L39237", "document_version": "14"}]}).encode())
    v14 = Page(200, b"<html><body>LCD L39237 Revision Effective Date 01/01/2027 text</body></html>")
    pages = {"https://api.coverage.cms.gov/v1/reports/local-coverage-final-lcds": api,
             "https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=39237&ver=14": v14}
    r, m, _ = one("mcd-lcd-l39237", pages, incoming=tmp_path)
    assert r.outcome == "changed" and r.applies_from == "2027-01-01" and m["change_flags"][-1]["applies_from"] == "2027-01-01"
    assert (tmp_path / "mcd-lcd-l39237-20261121.html").read_bytes() == v14.body
    assert m["checks"][-1]["ok"] is False          # a change does not reset the freshness clock


def test_mcd_policy_page_blocked_is_blocked(tmp_path):
    r, m, w = one("mcd-lcd-l39237", {"https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=39237": Page(403, b"denied")},
                  incoming=tmp_path)
    assert r.outcome == "blocked" and w == 0


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
    # the baseline stays at the reviewed text, and the same edited page is not raised twice
    base = m["watch"]["text_sha256"]
    r4, _ = cs.run([m], SCHEDULE, web({m0["url"]: Page(200, edited.encode())}), now=NOW, only=["cms-asc-pa-demo"], incoming=tmp_path)
    assert r4[0].outcome == "already_flagged" and m["watch"]["text_sha256"] == base and len(m["change_flags"]) == 1


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


CMS_ROBOTS = """User-agent: *
Disallow: /core/
Disallow: /*?
Allow: /medicare-coverage-database/*?
Allow: /sitemap.xml?
"""


def test_robots_reads_wildcards_the_way_cms_writes_them():
    rules = cs._robots_rules(CMS_ROBOTS)
    assert cs.robots_allows(rules, "/medicare-coverage-database/view/lcd.aspx?lcdid=39237")      # allowed by the longer rule
    assert not cs.robots_allows(rules, "/search?q=lcd")                                         # any other query: not allowed
    assert cs.robots_allows(rules, "/files/document/services-list-asc-pa-demonstration.pdf")
    assert not cs.robots_allows(rules, "/core/misc/x.js")


@pytest.mark.parametrize("page", [Page(403, b"Access Denied"), Page(429), Page(200, b"<html><body>Access Denied Reference #18.abc</body></html>")])
def test_a_bot_block_is_reported_and_records_nothing(tmp_path, page):
    m0 = src("uhc-ma-pa-list")
    r, m, w = one("uhc-ma-pa-list", {m0["url"]: page}, incoming=tmp_path)
    assert r.outcome == "blocked" and w == 0 and m == src("uhc-ma-pa-list")
    assert "BLOCKED" in cs.report([r], False) and "from a browser" in cs.report([r], False)


def test_a_challenge_page_is_never_fingerprinted_as_the_pdf(tmp_path):
    m0 = src("cms-wiser-factsheet")
    fake = Page(200, b"<html>please verify you are human: captcha</html>", "application/pdf")
    r, m, _ = one("cms-wiser-factsheet", {m0["url"]: fake}, incoming=tmp_path)
    assert r.outcome == "blocked" and not m.get("change_flags")


def test_etag_avoids_downloading_an_unchanged_file(tmp_path, monkeypatch):
    m0 = src("cms-wiser-factsheet")
    monkeypatch.setattr(cs, "sha", lambda b: m0["sha256"] if b == PDF else "f" * 64)
    first = {m0["url"]: Page(200, PDF, "application/pdf", {"etag": '"v1"'})}
    r, m, _ = one("cms-wiser-factsheet", first, incoming=tmp_path)
    assert r.outcome == "unchanged" and m["watch"]["etag"] == '"v1"'
    sent = {}

    def get(url, head=False, etag=None):
        sent["etag"] = etag
        return Page(304)
    r2, _ = cs.run([m], SCHEDULE, get, now=NOW, only=["cms-wiser-factsheet"], incoming=tmp_path)
    assert sent["etag"] == '"v1"' and r2[0].outcome == "unchanged" and "not downloaded" in r2[0].note


def test_new_editions_are_probed_without_downloading(tmp_path):
    m0 = src("uhc-ma-pa-list")
    heads, gets = [], []

    def get(url, head=False, etag=None):
        (heads if head else gets).append(url)
        return Page(200, PDF, "application/pdf") if url == m0["url"] else Page(404)
    cs.run([src("uhc-ma-pa-list")], SCHEDULE, get, now=NOW, only=["uhc-ma-pa-list"], incoming=tmp_path)
    assert len(heads) == 4 and heads[-1].endswith("Eff-2-1-27.pdf") and all(u in (m0["url"], SCHEDULE["sources"]["uhc-ma-pa-list"]["index_url"]) for u in gets)


def test_index_page_counts_only_later_editions(tmp_path):
    m0 = src("uhc-ma-pa-list")
    idx = SCHEDULE["sources"]["uhc-ma-pa-list"]["index_url"]
    base = "/content/dam/provider/docs/public/prior-auth/pa-requirements/medicare/"
    html = f'<a href="{base}Med-Adv-Dual-Eff-8-1-26.pdf">Aug</a><a href="{base}Med-Adv-Dual-Eff-10-1-26.pdf">Oct</a>'.encode()
    r, m, _ = one("uhc-ma-pa-list", {m0["url"]: Page(200, PDF, "application/pdf"), idx: Page(200, html)}, incoming=tmp_path)
    assert r.outcome in ("unchanged", "changed") and not any("Eff-8-1-26" in f.get("url", "") for f in m.get("change_flags", []))
    later = html + f'<a href="{base}Med-Adv-Dual-Eff-11-15-26.pdf">Nov</a>'.encode()
    r2, m2, _ = one("uhc-ma-pa-list", {m0["url"]: Page(200, PDF, "application/pdf"), idx: Page(200, later)}, incoming=tmp_path)
    assert r2.outcome == "changed" and r2.applies_from == "2026-11-15" and r2.new_url.startswith("https://www.uhcprovider.com/")
    # a file named on the index page is downloaded and kept only when it is a real PDF
    nov = f"https://www.uhcprovider.com{base}Med-Adv-Dual-Eff-11-15-26.pdf"
    r3, _, _ = one("uhc-ma-pa-list", {m0["url"]: Page(200, PDF, "application/pdf"), idx: Page(200, later),
                                      nov: Page(200, b"%PDF-1.7 november", "application/pdf")}, incoming=tmp_path)
    assert r3.content == b"%PDF-1.7 november"
    assert r2.content is None and any("save it by hand" in d for d in r2.detail)


def test_next_editions_reach_past_today_even_for_an_old_saved_edition():
    from datetime import date
    names = [a for _, a in cs._next_editions("https://x/Med-Adv-Dual-Eff-1-1-26.pdf", date(2026, 1, 1), date(2026, 11, 17))]
    assert names[-1] == "2027-02-01" and "2026-12-01" in names and len(names) <= 12


def test_robots_stacked_user_agents_share_one_group():
    rules = cs._robots_rules("User-agent: *\nUser-agent: SomeBot\nDisallow: /x\n\nUser-agent: Other\nDisallow: /y\n")
    assert rules == [(False, "/x")]


def test_resolve_closes_open_flags_and_needs_a_name_and_note():
    m = src("uhc-ma-partb-step-therapy")
    with pytest.raises(ValueError):
        cs.resolve([m], m["source_id"], "dismissed", "", "x", NOW)
    n = cs.resolve([m], m["source_id"], "dismissed", "Curator", "statistics only; lists unchanged", NOW)
    assert n == len(m["change_flags"]) >= 1
    assert all(f["state"] == "dismissed" and f["resolved_at"].startswith("2026-11-21") for f in m["change_flags"])
    assert cs.resolve([m], m["source_id"], "reviewed", "Curator", "again", NOW) == 0
