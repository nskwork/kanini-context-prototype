"""Put the saved source copies where the pack expects them, matching by file hash, not by name.

    python -m kanini_context.sources.place ~/Downloads            # finds each manifest file among the PDFs there
    python -m kanini_context.sources.place --record ~/Downloads   # also records a newly saved source (no hash yet)
    python -m kanini_context.sources.place --checked mcd-article-a56462 "live MCD page: version 38 still current"
                                                                  # records today's recheck of a source on its URL

Each copy is checked against the SHA-256 recorded in the manifest, so the pack is built from the same copies
that were reviewed. Missing files are listed with the URL to download them from.

A source listed in the manifest without a hash has not been saved yet. With --record, a file of that source's
name found in the folders (or already in raw/) is copied in, and its hash, size, retrieval time and a dated check
are written to the manifest. Its rules then go through the checks and approval like any other.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
MANIFEST = json.loads((ROOT / "manifest.json").read_text())


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def record(folders: list[Path]) -> None:
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    changed = False
    for m in MANIFEST:
        if m.get("sha256"):
            continue
        found = next((p for f in folders for p in f.rglob(m["file"])), None) or (RAW / m["file"] if (RAW / m["file"]).exists() else None)
        if not found:
            continue
        if found != RAW / m["file"]:
            shutil.copy2(found, RAW / m["file"])
        m.update({"sha256": sha(RAW / m["file"]), "bytes": (RAW / m["file"]).stat().st_size, "retrieved_at": now})
        m.setdefault("checks", []).append({"at": now, "ok": True, "note": "saved copy recorded"})
        m.pop("open_question", None)
        changed = True
        print(f"recorded {m['file']}  sha256 {m['sha256'][:12]}...  (now run the checks and approve its rule file)")
    if changed:
        (ROOT / "manifest.json").write_text(json.dumps(MANIFEST, indent=2, ensure_ascii=False) + "\n")


def checked(source_id: str, note: str) -> int:
    """Record that a person rechecked a source on its URL today and found the saved edition still current."""
    m = next((x for x in MANIFEST if x["source_id"] == source_id), None)
    if not m:
        print(f"unknown source {source_id}; known: {', '.join(x['source_id'] for x in MANIFEST)}")
        return 1
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    m.setdefault("checks", []).append({"at": now, "ok": True, "note": note})
    if m.get("open_question", "").startswith("the live page could not be confirmed"):
        m.pop("open_question")
    (ROOT / "manifest.json").write_text(json.dumps(MANIFEST, indent=2, ensure_ascii=False) + "\n")
    print(f"recorded check of {source_id} at {now}: {note}")
    return 0


def main(argv: list[str]) -> int:
    RAW.mkdir(exist_ok=True)
    if argv[:1] == ["--checked"]:
        if len(argv) != 3:
            print('usage: place --checked SOURCE_ID "what you saw"')
            return 1
        return checked(argv[1], argv[2])
    record_new = "--record" in argv
    folders = [Path(a).expanduser() for a in argv if a != "--record"]   # "~" works in every shell, PowerShell too
    for f in folders:
        if not f.is_dir():
            print(f"folder not found: {f}")
            return 1
    argv = [str(f) for f in folders]
    if record_new:
        record(folders)
    want = {m["sha256"]: m for m in MANIFEST if m.get("sha256")}
    for folder in map(Path, argv):
        for p in folder.rglob("*.pdf"):
            m = want.get(sha(p))
            if m and not (RAW / m["file"]).exists():
                shutil.copy2(p, RAW / m["file"])
                print(f"placed {m['file']}  (from {p.name})")
    missing = 0
    for m in MANIFEST:
        f = RAW / m["file"]
        if not m.get("sha256"):
            print(f"not saved yet {m['file']}: save {m['url']} under this name, then run with --record")
            missing += 1
            continue
        if not f.exists():
            print(f"missing {m['file']}: download {m['url']}")
            missing += 1
        elif sha(f) != m["sha256"]:
            print(f"differs {m['file']}: not the reviewed copy (expected sha256 {m['sha256'][:12]}...)")
            missing += 1
    print("all source copies in place" if not missing else f"{missing} of {len(MANIFEST)} need attention")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
