"""Put the saved source copies where the pack expects them, matching by file hash, not by name.

    python -m kanini_context.sources.place ~/Downloads     # finds each manifest file among the PDFs there

Each copy is checked against the SHA-256 recorded in the manifest, so the pack is built from the same copies
that were reviewed. Missing files are listed with the URL to download them from.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
MANIFEST = json.loads((ROOT / "manifest.json").read_text())


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    RAW.mkdir(exist_ok=True)
    want = {m["sha256"]: m for m in MANIFEST}
    for folder in map(Path, argv):
        for p in folder.rglob("*.pdf"):
            m = want.get(sha(p))
            if m and not (RAW / m["file"]).exists():
                shutil.copy2(p, RAW / m["file"])
                print(f"placed {m['file']}  (from {p.name})")
    missing = 0
    for m in MANIFEST:
        f = RAW / m["file"]
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
