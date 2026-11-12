"""Page text for source documents, used by the quote checks and the approval view.

Each PDF in sources/raw/ is read page by page with pdfplumber. The text is kept as extracted
(for reading) and in a normalised form (for quote matching): whitespace collapsed, dashes and
quotes unified, so a quote copied from the PDF matches regardless of line breaks.
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

import logging

import pdfplumber

logging.getLogger("pdfminer").setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "sources" / "raw"
CACHE = ROOT / "sources" / "text"

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"', ord(" "): " "}


def normalise(text: str) -> str:
    text = text.translate(_DASHES).translate(_QUOTES)
    text = text.replace("≤", "<=").replace("≥", ">=")
    return re.sub(r"\s+", " ", text).strip()


def _columns(file_name: str) -> int:
    """Two-column printed pages (such as a printed CFR page) are read left column first, then right."""
    manifest = json.loads((ROOT / "sources" / "manifest.json").read_text())
    return next((m.get("columns", 1) for m in manifest if m["file"] == file_name), 1)


@lru_cache(maxsize=None)
def pages(file_name: str) -> list[str]:
    """Raw text of every page (1-based page n is pages(f)[n-1]). Cached on disk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cols = _columns(file_name)
    cache = CACHE / f"{file_name}.c{cols}.pages.json"
    src = RAW / file_name
    if cache.exists() and cache.stat().st_mtime >= src.stat().st_mtime:
        return json.loads(cache.read_text())
    with pdfplumber.open(src) as pdf:
        if cols == 2:
            out = [((p.crop((0, 0, p.width / 2, p.height)).extract_text() or "") + "\n" +
                    (p.crop((p.width / 2, 0, p.width, p.height)).extract_text() or "")) for p in pdf.pages]
        else:
            out = [(p.extract_text() or "") for p in pdf.pages]
    cache.write_text(json.dumps(out))
    return out


@lru_cache(maxsize=None)
def band_text(file_name: str, page: int, x0: float, x1: float) -> str:
    """Text of one column band of a page (tables printed as columns, such as an insurer list)."""
    with pdfplumber.open(RAW / file_name) as pdf:
        p = pdf.pages[page - 1]
        return p.crop((x0, 0, x1, p.height)).extract_text() or ""


def locate_text(file_name: str, locator: dict) -> str:
    """The text a locator points at: one page, or one column band of a page."""
    page = int(locator["page"])
    if "band" in locator:
        x0, x1 = locator["band"]
        return band_text(file_name, page, x0, x1)
    return pages(file_name)[page - 1]


def find(file_name: str, quote: str) -> list[int]:
    """Pages (1-based) whose normalised text contains the normalised quote."""
    q = normalise(quote)
    return [i + 1 for i, t in enumerate(pages(file_name)) if q in normalise(t)]


if __name__ == "__main__":
    # usage: python pdftext.py <file> <regex>   prints matching normalised snippets with page numbers
    f, pat = sys.argv[1], sys.argv[2]
    for i, t in enumerate(pages(f)):
        n = normalise(t)
        for m in re.finditer(pat, n, flags=re.I):
            print(f"p{i + 1}: ...{n[max(0, m.start() - 150): m.end() + 250]}...")
