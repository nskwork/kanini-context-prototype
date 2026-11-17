"""Check each source for a new edition, and record what was found.

    python -m kanini_context.sources.check_sources --dry-run          # read every source, print what it would do, write nothing
    python -m kanini_context.sources.check_sources                    # check every source and record the result
    python -m kanini_context.sources.check_sources --due              # only the sources due today (schedule.json)
    python -m kanini_context.sources.check_sources --due --then-build # as the scheduler runs it: then rebuild the release
    python -m kanini_context.sources.check_sources mcd-lcd-l39237     # one source

How a change is decided. A source counts as changed only on evidence from the source itself, never on a timestamp
alone: a date can move without the content changing, and the content without the date. Each kind of source has a
cheap first look (its edition signal), and the content is compared only where that look says to:

    mcd        Medicare coverage policies and articles. The current version number (Coverage API report, else the
               policy page), against the version pinned in the saved URL (ver=13). A higher version is a change.
    ecfr       Federal regulations. The section's latest amendment date (eCFR versioner API), against the saved
               edition's date or, where the saved copy states none, the last check. A later amendment is a change.
    payer_pdf  Payer lists and policies. The saved file's URL is downloaded and its SHA-256 compared with the saved
               copy's; and because a payer publishes a new edition under a new file name, the next months' file names
               (and the payer's index page, when schedule.json names one) are looked for. Either is a change.
    pdf        Other direct PDFs. Downloaded, SHA-256 compared with the saved copy's.
    cms_page   CMS program pages. A fingerprint of the page's text (its "Page Last Modified" date is read and shown, but
               not trusted alone). The first run records the fingerprint as the baseline; later runs compare with it.

What is recorded (not in a dry run). Every source read is a dated check in the manifest, which resets its freshness
clock. A change adds an entry to the source's change_flags (detected_at, applies_from, url, signal), the form
curation/load.py turns into kc_gov.change_flag: from applies_from, answers resting on that source go to staff as
"pending review" until new rules are drafted, checked and approved. A change already recorded is not raised again.
New content is saved to sources/raw/incoming/ for the drafting step; the saved copy the rules rest on is not touched.
A source that cannot be read (site down, page layout changed) records nothing and is listed for a check by hand.

How sites are read. Where a source offers an API (the Coverage API for Medicare policies, the eCFR versioner), that
is used; pages and files are read like a polite program: it says who it is, keeps to each site's robots.txt (with
the wildcard rules CMS's file uses), pauses between requests to one site, backs off when asked (429, 503,
Retry-After), and sends the file's ETag so an unchanged file is not downloaded again. A site that answers with its
bot protection (401, 403, 429, or a challenge page instead of the PDF) is reported as BLOCKED and left alone: the
check never disguises itself or tries to get round a block (UnitedHealthcare's terms of use forbid it). Those
sources go by the manual route the spec keeps for them: save the file from a browser, then place --record, or
record a look with place --checked.
The next release.build carries the checks and flags into the database (--then-build runs it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
SCHEDULE_PATH = ROOT / "schedule.json"
INCOMING = ROOT / "raw" / "incoming"
BY = "check_sources"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UA = "Kanini-Context-source-check/1.0 (prototype; reads public policy pages once a week)"


@dataclass
class Page:
    status: int
    body: bytes = b""
    content_type: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


Get = Callable[..., Page]   # get(url, *, head=False, etag=None) -> Page

BLOCK_MARKS = (b"access denied", b"captcha", b"are you a robot", b"request unsuccessful", b"incapsula", b"cf-chl",
               b"challenge-platform", b"bot detection", b"_abck", b"reference #")


def blocked(p: Page) -> bool:
    """A site's bot protection answered instead of the document: 401/403/429, or a challenge page."""
    if p.status in (401, 403, 429) or p.status == 999:
        return True
    head = p.body[:6000].lower()
    return p.ok and b"<html" in head and any(m in head for m in BLOCK_MARKS) and len(p.body) < 60000


def _robots_rules(text: str) -> list[tuple[bool, str]]:
    """The rules for every crawler (User-agent: *), as (allow, pattern)."""
    rules, mine, in_rules = [], False, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if in_rules:                 # a new group starts; stacked User-agent lines share the group below them
                mine, in_rules = False, False
            mine = mine or v == "*"
        elif k in ("allow", "disallow"):
            in_rules = True
            if mine and v:
                rules.append((k == "allow", v))
    return rules


def robots_allows(rules: list[tuple[bool, str]], path_and_query: str) -> bool:
    """Google's reading of robots.txt, which CMS's file relies on: * matches anything, $ ends the path, the longest
    matching rule wins and Allow wins a tie. (Python's own parser ignores wildcards.)"""
    best = None
    for allow, pat in rules:
        rx = "^" + re.escape(pat).replace(r"\*", ".*").replace(r"\$", "$")
        if re.match(rx, path_and_query):
            key = (len(pat), allow)
            if best is None or key > best[0]:
                best = (key, allow)
    return True if best is None else best[1]


class Fetcher:
    """Reads public pages the way a polite program should: identifies itself, keeps to robots.txt, pauses between
    requests to the same site, backs off when asked (429, 503, Retry-After), and never tries to get round a block."""

    def __init__(self, pause: float = 1.5, retries: int = 2):
        import httpx
        self.client = httpx.Client(headers={"User-Agent": UA}, timeout=45, follow_redirects=True)
        self.pause, self.retries, self.robots, self.last = pause, retries, {}, {}

    def _allowed(self, url: str) -> bool:
        u = urlparse(url)
        if u.netloc not in self.robots:
            try:
                r = self.client.get(f"{u.scheme}://{u.netloc}/robots.txt")
                self.robots[u.netloc] = _robots_rules(r.text) if r.status_code == 200 else []
            except Exception:
                self.robots[u.netloc] = []
        return robots_allows(self.robots[u.netloc], u.path + (f"?{u.query}" if u.query else ""))

    def __call__(self, url: str, *, head: bool = False, etag: str | None = None) -> Page:
        import time
        import httpx
        if not self._allowed(url):
            return Page(-1, b"not allowed by the site's robots.txt", "robots")
        host = urlparse(url).netloc
        for attempt in range(self.retries + 1):
            wait = self.pause - (time.monotonic() - self.last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self.last[host] = time.monotonic()
            try:
                r = self.client.request("HEAD" if head else "GET", url, headers={"If-None-Match": etag} if etag else None)
            except httpx.HTTPError as ex:
                return Page(0, str(ex).encode(), "error")
            if head and r.status_code in (403, 405, 501):   # some servers refuse HEAD: ask for the file instead
                head = False
                continue
            if r.status_code in (429, 503) and attempt < self.retries:
                ra = r.headers.get("retry-after", "")
                time.sleep(min(60, int(ra)) if ra.isdigit() else 5 * (attempt + 1))
                continue
            return Page(r.status_code, r.content, r.headers.get("content-type", ""), dict(r.headers))
        return Page(r.status_code, r.content, r.headers.get("content-type", ""), dict(r.headers))


@dataclass
class Result:
    source_id: str
    kind: str
    signal: str = ""                 # what was read, in plain words
    outcome: str = "unreadable"      # unchanged | changed | baseline | unreadable | already_flagged
    note: str = ""
    applies_from: str | None = None
    new_url: str | None = None
    content: bytes | None = None     # new content, saved to raw/incoming for drafting
    suffix: str = ".bin"
    watch: dict | None = None        # baseline stored on the source (cms_page)
    detail: list[str] = field(default_factory=list)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _date_in(text: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text or "")
    return m.group(1) if m else None


def _last_check_date(m: dict) -> str | None:
    oks = [c["at"][:10] for c in m.get("checks", []) if c.get("ok")]
    return max(oks) if oks else None


# --- mcd: Medicare coverage policies and articles --------------------------------------------------------------------

def _mcd_ids(url: str) -> tuple[str, str, int | None]:
    q = parse_qs(urlparse(url).query)
    doc = "lcd" if "lcdid" in q else "article"
    num = (q.get("lcdid") or q.get("articleid"))[0]
    ver = int(q["ver"][0]) if "ver" in q else None
    return doc, num, ver


_REPORTS: dict = {}


def _mcd_version_api(doc: str, num: str, get: Get) -> tuple[int | None, str]:
    """The Coverage API's report of every final LCD (or article). Field names are read defensively: the entry is the one
    naming this document (L39237, A58982 or the bare number), the version the first integer field named *version*."""
    url = f"https://api.coverage.cms.gov/v1/reports/local-coverage-{'final-lcds' if doc == 'lcd' else 'articles'}"
    # one report per run serves every policy; keyed by the fetcher itself (an id() can be reused once it is freed)
    p = _REPORTS.get((get, url)) or get(url)
    _REPORTS[(get, url)] = p
    if not p.ok:
        return None, f"Coverage API not read (HTTP {p.status})"
    try:
        rows = p.json()
        rows = rows.get("data", rows) if isinstance(rows, dict) else rows
    except ValueError:
        return None, "Coverage API answer was not JSON"
    want = {num, f"{'L' if doc == 'lcd' else 'A'}{num}"}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not any(str(v).strip() in want for v in row.values()):
            continue
        for k, v in row.items():
            if "version" in k.lower():
                try:
                    return int(str(v).strip()), f"Coverage API {k} = {v}"
                except ValueError:
                    continue
    return None, "Coverage API: document or version field not found"


def _revision_effective(text: str) -> str | None:
    """The date a policy version applies from, as the policy page states it (Revision Effective Date)."""
    eff = re.search(r"Revision Effective Date[^0-9]{0,80}(\d{1,2})/(\d{1,2})/(\d{4})", text or "", re.I)
    return f"{eff.group(3)}-{int(eff.group(1)):02d}-{int(eff.group(2)):02d}" if eff else None


def _mcd_version_page(doc: str, num: str, get: Get) -> tuple[int | None, str, Page]:
    """The policy page without a pinned version links to its current version (…?lcdid=39237&ver=14)."""
    p = get(f"https://www.cms.gov/medicare-coverage-database/view/{doc}.aspx?{doc}id={num}")
    if not p.ok or blocked(p):
        return None, f"policy page not read (HTTP {p.status})", p
    vers = [int(v) for v in re.findall(rf"{doc}id={num}(?:&|&amp;)ver=(\d+)", p.text, re.I)]
    if not vers:
        return None, "policy page: no version link found", p
    return max(vers), f"policy page links to version {max(vers)}", p


def check_mcd(m: dict, cfg: dict, get: Get) -> Result:
    r = Result(m["source_id"], "mcd")
    doc, num, saved = _mcd_ids(m["url"])
    cur, how = _mcd_version_api(doc, num, get)
    r.detail.append(how)
    page = None
    if cur is None:
        cur, how, page = _mcd_version_page(doc, num, get)
        r.detail.append(how)
        if _stop(r, page, "the policy page"):
            return r
    if cur is None or saved is None:
        r.note = "version not readable; check the policy page by hand"
        return r
    r.signal = f"version {cur} (saved copy: version {saved})"
    if cur <= saved:
        r.outcome, r.note = "unchanged", f"version {saved} still current"
        return r
    r.new_url = re.sub(r"ver=\d+", f"ver={cur}", m["url"])
    if any(f.get("url") == r.new_url for f in m.get("change_flags", [])):
        r.outcome, r.note = "already_flagged", f"version {cur} already recorded as a change"
        return r
    r.outcome, r.note = "changed", f"new version {cur} published (saved copy is version {saved})"
    # The new version's own page gives the date it applies from (CMS often publishes weeks ahead) and is saved for
    # drafting. If it cannot be read, the change still stands; it then applies now, the safe side.
    q = get(r.new_url)
    if q.ok and not blocked(q):
        r.content, r.suffix = q.body, ".html"
        r.applies_from = _revision_effective(q.text)
    elif page is not None and page.ok:
        r.content, r.suffix = page.body, ".html"
        r.applies_from = _revision_effective(page.text)
    if r.applies_from is None:
        r.detail.append("revision effective date not read from the policy page; the change applies now")
    return r


# --- ecfr: federal regulations ---------------------------------------------------------------------------------------

def check_ecfr(m: dict, cfg: dict, get: Get) -> Result:
    r = Result(m["source_id"], "ecfr")
    sec = cfg["section"]
    p = get(f"https://www.ecfr.gov/api/versioner/v1/versions/title-42.json?part={sec.split('.')[0]}&section={sec}")
    if _stop(r, p, "the section"):
        return r
    if not p.ok:
        r.note = f"eCFR versioner not read (HTTP {p.status}); check the section by hand"
        return r
    try:
        j = p.json()
    except ValueError:
        r.note = "eCFR versioner answer was not JSON; check by hand"
        return r
    dates = [v.get("amendment_date") for v in j.get("content_versions", []) if v.get("amendment_date")]
    latest = max(dates) if dates else (j.get("meta") or {}).get("latest_amendment_date")
    if not latest:
        r.note = "eCFR versioner gave no amendment date; check by hand"
        return r
    cutoff = _date_in(m.get("edition", "")) or _last_check_date(m) or (m.get("retrieved_at") or "")[:10]
    r.signal = f"latest amendment {latest} (saved copy current to {cutoff})"
    if latest <= cutoff:
        r.outcome, r.note = "unchanged", f"no amendment since {cutoff}"
        return r
    if any(f.get("applies_from") == latest for f in m.get("change_flags", [])):
        r.outcome, r.note = "already_flagged", f"the amendment effective {latest} is already recorded"
        return r
    r.outcome, r.note, r.applies_from = "changed", f"section {sec} amended, effective {latest}", latest
    r.new_url = f"https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-42?part={sec.split('.')[0]}&section={sec}"
    page = get(r.new_url)
    if page.ok:
        r.content, r.suffix = page.body, ".html"
    return r


# --- payer_pdf: payer lists and policies -----------------------------------------------------------------------------

def _next_editions(url: str, after: date, today: date | None = None, ahead: int = 3, cap: int = 12) -> list[tuple[str, str]]:
    """The file names a payer gives the next editions, effective the first of each month after the saved one, through
    `ahead` months past today (payers publish ahead), the latest `cap` at most:
    Med-Adv-Dual-Eff-10-1-26.pdf -> Med-Adv-Dual-Eff-11-1-26.pdf …; …-programs-07012026.pdf -> …-programs-08012026.pdf …"""
    out = []
    firsts = []
    end = max(after, today or date.today())
    end_y, end_mo = end.year + (end.month + ahead - 1) // 12, (end.month + ahead - 1) % 12 + 1
    y, mo = after.year, after.month
    while (y, mo) < (end_y, end_mo):
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1
        firsts.append(date(y, mo, 1))
    firsts = firsts[-cap:]
    short = re.match(r"(.*-Eff(?:ective)?-)(\d{1,2})-(\d{1,2})-(\d{2,4})\.pdf$", url, re.I)
    long_ = re.match(r"(.*-)(\d{2})(\d{2})(\d{4})\.pdf$", url)
    for d in firsts:
        if short:
            yy = str(d.year)[2:] if len(short.group(4)) == 2 else str(d.year)
            out.append((f"{short.group(1)}{d.month}-1-{yy}.pdf", d.isoformat()))
        elif long_:
            out.append((f"{long_.group(1)}{d.month:02d}01{d.year}.pdf", d.isoformat()))
    return out


def _name_date(name: str) -> str | None:
    """The effective date a payer writes into a file name: …-Eff-10-1-26.pdf, …-Effective-1-1-2025.pdf, …-07012026.pdf."""
    m = re.search(r"-Eff(?:ective)?-(\d{1,2})-(\d{1,2})-(\d{2,4})\.pdf$", name, re.I)
    if m:
        y = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"-(\d{2})(\d{2})(\d{4})\.pdf$", name)
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else None


def _is_pdf(p: Page) -> bool:
    """A real PDF, not a challenge page served with a PDF's name."""
    return p.ok and p.body[:5] == b"%PDF-"


def _stop(r: Result, p: Page, what: str) -> bool:
    """Blocked or not allowed: record nothing, say so, and leave it to a person. Never a change."""
    if p.status == -1:
        r.outcome, r.note = "blocked", f"the site's robots.txt does not allow this address; check {what} by hand"
        return True
    if blocked(p):
        r.outcome, r.note = "blocked", f"the site's bot protection answered (HTTP {p.status}); check {what} by hand or save it from a browser"
        return True
    return False


def check_payer_pdf(m: dict, cfg: dict, get: Get) -> Result:
    r = Result(m["source_id"], "payer_pdf", suffix=".pdf")
    flagged = {f.get("url") for f in m.get("change_flags", [])}
    w = m.get("watch") or {}
    p = get(m["url"], etag=w.get("etag") if w.get("sha256") == m.get("sha256") else None)
    if _stop(r, p, "the payer's page"):
        return r
    same = None
    if p.status == 304:
        same = True
        r.detail.append("saved file's URL: the server confirms it is unchanged (ETag), not downloaded")
    elif _is_pdf(p):
        same = sha(p.body) == m.get("sha256")
        r.detail.append(f"saved file's URL: SHA-256 {'matches' if same else 'differs from'} the saved copy")
        if same and p.headers.get("etag"):
            r.watch = {"etag": p.headers["etag"], "sha256": m.get("sha256")}
    else:
        r.detail.append(f"saved file's URL not read (HTTP {p.status})")
    eff = _date_in(m.get("edition", "")) or (m.get("retrieved_at") or "")[:10]
    found = []
    for url, applies in _next_editions(m["url"], date.fromisoformat(eff), cfg.get("_today")):
        h = get(url, head=True)                          # does the file exist? ask without downloading
        if h.ok and "html" not in h.content_type.lower() and not blocked(h):
            q = get(url)
            if _is_pdf(q):
                found.append((url, applies, q))
    if cfg.get("index_url"):
        ip = get(cfg["index_url"])
        if ip.ok:
            stem = re.sub(r"\d.*$", "", Path(urlparse(m["url"]).path).name)
            for href in sorted(set(re.findall(r'href="([^"]+\.pdf)"', ip.text, re.I))):
                name = Path(urlparse(href).path).name
                when = _name_date(name)
                full = href if href.startswith("http") else f"{urlparse(cfg['index_url']).scheme}://{urlparse(cfg['index_url']).netloc}{href}"
                # only a later edition of the same document: older editions linked from the page are not changes
                if name.startswith(stem) and when and when > eff and all(full != f[0] for f in found):
                    found.append((full, when, None))
            r.detail.append(f"index page read: {len(found)} newer file name(s)")
    new = [f for f in found if f[0] not in flagged]
    known = [f for f in found if f[0] in flagged]
    r.signal = (f"{len(found)} newer edition(s) published" if found else "no newer edition found") + (
        "" if same is None else ("; saved file unchanged" if same else "; saved file's content changed"))
    if new:
        url, applies, q = new[0]
        r.outcome, r.note, r.applies_from, r.new_url = "changed", f"new edition published: {Path(url).name}", applies, url
        if q is None:                                    # found on the index page: download it, and keep it only if it is a PDF
            q = get(url)
            if not _is_pdf(q):
                r.detail.append(f"{Path(url).name} not downloaded (HTTP {q.status}); save it by hand")
                q = None
        if q is not None:
            r.content = q.body
        return r
    if same is False:
        r.outcome, r.note, r.new_url, r.content = "changed", "the file at the saved URL has changed", m["url"], p.body
        return r
    if same is None and not found:
        r.note = "file not readable; check the payer's page by hand"
        return r
    r.outcome = "already_flagged" if known else "unchanged"
    r.note = ("newer edition already recorded: " + ", ".join(Path(k[0]).name for k in known)) if known else "saved edition still current"
    return r


# --- pdf: other direct PDFs ------------------------------------------------------------------------------------------

def check_pdf(m: dict, cfg: dict, get: Get) -> Result:
    r = Result(m["source_id"], "pdf", suffix=".pdf")
    w = m.get("watch") or {}
    p = get(m["url"], etag=w.get("etag") if w.get("sha256") == m.get("sha256") else None)
    if _stop(r, p, "the file"):
        return r
    if p.status == 304:
        r.outcome, r.note, r.signal = "unchanged", "the server confirms the file is unchanged (ETag); not downloaded", "ETag match"
        return r
    if not _is_pdf(p):
        r.note = f"file not read (HTTP {p.status}); check by hand"
        return r
    now = sha(p.body)
    r.signal = f"SHA-256 {now[:12]} (saved copy {(m.get('sha256') or '')[:12]})"
    if now == m.get("sha256"):
        r.outcome, r.note = "unchanged", "same fingerprint as the saved copy"
        if p.headers.get("etag"):
            r.watch = {"etag": p.headers["etag"], "sha256": now}
    elif any(f.get("sha256") == now for f in m.get("change_flags", [])):
        r.outcome, r.note = "already_flagged", "this new content is already recorded as a change"
    else:
        r.outcome, r.note, r.new_url, r.content = "changed", "the file has changed", m["url"], p.body
    return r


# --- cms_page: CMS program pages -------------------------------------------------------------------------------------

def page_text(html: str) -> str:
    """The page's own words: the <main> region where there is one, without scripts, styles or tags."""
    m = re.search(r"<main\b.*?</main>", html, re.S | re.I)
    s = m.group(0) if m else html
    s = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def check_cms_page(m: dict, cfg: dict, get: Get) -> Result:
    r = Result(m["source_id"], "cms_page", suffix=".html")
    p = get(m["url"])
    if _stop(r, p, "the page"):
        return r
    if not p.ok:
        r.note = f"page not read (HTTP {p.status}); check by hand"
        return r
    text = page_text(p.text)
    if len(text) < 200:
        r.note = "page read but almost no text found; check by hand"
        return r
    lm = re.search(r"Page Last Modified[^0-9]{0,80}(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2}\s*[AP]M)?)", p.text, re.I)
    lm = lm.group(1) if lm else None
    fp = sha(text.encode("utf-8"))
    w = m.get("watch") or {}
    r.watch = {"text_sha256": fp, "last_modified": lm}   # recorded on baseline and unchanged only (see record)
    r.signal = f"page text {fp[:12]}" + (f", last modified {lm}" if lm else "")
    if not w.get("text_sha256"):
        r.outcome, r.note = "baseline", "first check: page fingerprint recorded as the baseline"
    elif w["text_sha256"] == fp:
        r.outcome = "unchanged"
        r.note = "page text unchanged" + (f" (its date moved {w.get('last_modified')} → {lm}, the text did not)"
                                          if lm and w.get("last_modified") and lm != w.get("last_modified") else "")
    elif any(f.get("text_sha256") == fp for f in m.get("change_flags", [])):
        r.outcome, r.note = "already_flagged", "this page change is already recorded"
    else:
        r.outcome, r.note, r.new_url, r.content = "changed", "page text changed" + (
            f" (last modified {w.get('last_modified')} → {lm})" if lm else ""), m["url"], p.body
    return r


KINDS = {"mcd": check_mcd, "ecfr": check_ecfr, "payer_pdf": check_payer_pdf, "pdf": check_pdf, "cms_page": check_cms_page}


# --- schedule, recording, report -------------------------------------------------------------------------------------

def is_due(m: dict, cfg: dict, today: date) -> bool:
    mine = sorted(c["at"][:10] for c in m.get("checks", []) if c.get("by") == BY)
    last = date.fromisoformat(mine[-1]) if mine else None
    if last == today:
        return False
    if cfg.get("every") == "daily":
        return True
    if last is None or (today - last).days >= 7:
        return True
    return DAYS[today.weekday()] == cfg.get("day", "sunday")


def record(manifest: list[dict], results: list[Result], now: datetime, incoming: Path) -> int:
    """Write the checks, change flags and baselines into the manifest; save new content. Returns the number of writes."""
    by_id = {m["source_id"]: m for m in manifest}
    at = now.isoformat(timespec="seconds")
    writes = 0
    for r in results:
        m = by_id[r.source_id]
        if r.outcome in ("unreadable", "blocked"):
            continue
        if r.watch is not None and r.outcome in ("baseline", "unchanged"):
            m["watch"] = {**(m.get("watch") or {}), **r.watch, "at": at}
        # ok (which resets the freshness clock) only when the saved copy was confirmed current. A change, new or
        # already recorded, and a first baseline are logged as checks but leave the clock where it was.
        check = {"at": at, "ok": r.outcome == "unchanged", "by": BY,
                 "note": f"automatic check: {r.note}" + (f" [{r.signal}]" if r.signal else "")}
        if r.outcome == "changed":
            saved = None
            if r.content is not None:
                incoming.mkdir(parents=True, exist_ok=True)
                saved = incoming / f"{r.source_id}-{now:%Y%m%d}{r.suffix}"
                saved.write_bytes(r.content)
            flag = {"detected_at": at, "applies_from": r.applies_from, "url": r.new_url, "signal": r.note, "by": BY}
            if r.content is not None:
                flag["sha256"] = sha(r.content)
            if r.kind == "cms_page" and r.watch:
                flag["text_sha256"] = r.watch["text_sha256"]
            if saved is not None:
                flag["saved_as"] = (str(saved.relative_to(ROOT)) if saved.is_relative_to(ROOT) else str(saved)).replace("\\", "/")
            m.setdefault("change_flags", []).append(flag)
            check["note"] = f"automatic check: CHANGE recorded, {r.note}"
        m.setdefault("checks", []).append(check)
        writes += 1
    return writes


WORD = {"unchanged": "unchanged", "changed": "CHANGED", "baseline": "baseline", "unreadable": "CHECK BY HAND",
        "blocked": "BLOCKED", "already_flagged": "already flagged"}


def report(results: list[Result], dry: bool) -> str:
    lines = []
    for r in results:
        lines.append(f"{r.source_id:28} {r.kind:10} {WORD[r.outcome]:16} {r.note}")
        if r.signal:
            lines.append(f"{'':28} {'':10} {'':16} read: {r.signal}")
        for d in r.detail:
            lines.append(f"{'':28} {'':10} {'':16} · {d}")
        if r.outcome == "changed":
            act = (f"would raise a change flag" if dry else "change flag raised") + (
                f", applying from {r.applies_from}" if r.applies_from else ", applying now")
            lines.append(f"{'':28} {'':10} {'':16} → {act}")
    n = {k: sum(1 for r in results if r.outcome == k) for k in WORD}
    lines.append("")
    lines.append(f"{len(results)} checked: {n['unchanged']} unchanged, {n['changed']} changed, {n['already_flagged']} already flagged, "
                 f"{n['baseline']} baseline recorded, {n['unreadable'] + n['blocked']} to check by hand"
                 + (f" ({n['blocked']} blocked by the site: save those from a browser, then place --record or --checked)" if n["blocked"] else "")
                 + "." + (" Dry run: nothing written." if dry else ""))
    return "\n".join(lines)


def run(manifest: list[dict], schedule: dict, get: Get, *, now: datetime, only: list[str] | None = None,
        due_only: bool = False, dry: bool = False, incoming: Path = INCOMING) -> tuple[list[Result], int]:
    results = []
    for m in manifest:
        sid = m["source_id"]
        cfg = schedule["sources"].get(sid)
        if cfg is None or (only and sid not in only) or (due_only and not is_due(m, cfg, now.date())):
            continue
        try:
            results.append(KINDS[cfg["kind"]](m, {**cfg, "_today": now.date()}, get))
        except Exception as ex:   # one source failing never stops the others, and never raises a change
            results.append(Result(sid, cfg["kind"], note=f"check failed ({type(ex).__name__}: {ex}); check by hand"))
    writes = 0 if dry else record(manifest, results, now, incoming)
    return results, writes


def resolve(manifest: list[dict], source_id: str, state: str, by: str, note: str, now: datetime) -> int:
    """Close a source's open change flags after a person has looked at the new copy: 'dismissed' when the change does
    not touch any rule (the answers resting on the source come back at once), 'reviewed' when the rules were updated
    from it. Returns the number of flags closed."""
    if state not in ("reviewed", "dismissed"):
        raise ValueError("state must be reviewed or dismissed")
    if not by.strip() or not note.strip():
        raise ValueError("a resolution needs the person's name (--by) and a note saying what was checked")
    m = next((x for x in manifest if x["source_id"] == source_id), None)
    if m is None:
        raise ValueError(f"{source_id} is not in sources/manifest.json")
    n = 0
    for fl in m.get("change_flags", []):
        if fl.get("state", "open") == "open":
            fl.update(state=state, resolved_at=now.isoformat(timespec="seconds"), resolved_by=by.strip(), resolution=note.strip())
            n += 1
    return n


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Check each source for a new edition, and record what was found.")
    ap.add_argument("sources", nargs="*", help="source ids (default: all in schedule.json)")
    ap.add_argument("--dry-run", action="store_true", help="read and report; write nothing")
    ap.add_argument("--due", action="store_true", help="only the sources due today by schedule.json")
    ap.add_argument("--then-build", action="store_true", help="after recording, rebuild the release")
    ap.add_argument("--resolve", nargs=2, metavar=("SOURCE_ID", "reviewed|dismissed"),
                    help="close the source's open change flags after looking at the new copy (needs --by and --note)")
    ap.add_argument("--by", default="", help="with --resolve: who looked at the new copy")
    ap.add_argument("--note", default="", help="with --resolve: what was checked, e.g. 'new FY2025 statistics; program lists unchanged'")
    a = ap.parse_args(argv)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    schedule = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    if a.resolve:
        try:
            n = resolve(manifest, a.resolve[0], a.resolve[1], a.by, a.note, datetime.now(timezone.utc).astimezone())
        except ValueError as ex:
            print(ex)
            return 1
        if not n:
            print(f"{a.resolve[0]} has no open change flag.")
            return 0
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{n} change flag(s) on {a.resolve[0]} marked {a.resolve[1]}. Run python -m kanini_context.release.build to carry it into the release.")
        return 0
    unknown = [s for s in a.sources if s not in schedule["sources"]]
    if unknown:
        print(f"not in schedule.json: {', '.join(unknown)}")
        return 1
    now = datetime.now(timezone.utc).astimezone()
    results, writes = run(manifest, schedule, Fetcher(), now=now, only=a.sources or None, due_only=a.due, dry=a.dry_run)
    text = report(results, a.dry_run) if results else "Nothing due today."
    print(text)
    if not a.dry_run:   # each real run's report is kept, so a scheduled run can be read the next morning
        with (ROOT / "check_log.txt").open("a", encoding="utf-8") as log:
            log.write(f"\n=== {now:%Y-%m-%d %H:%M %Z}{' (due only)' if a.due else ''}\n{text}\n")
    if writes:
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Recorded {writes} check(s) in sources/manifest.json.")
        if a.then_build:
            return subprocess.call([sys.executable, "-m", "kanini_context.release.build"])
        print("Run python -m kanini_context.release.build to carry them into the release.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
