"""Every test run, one command: what goes on the Testing page, the README figures and the deck.

In order, each step's output kept in propel/baseline/results/run_all-<stamp>.log, carrying on past a step that fails:
  1. A, B and C on the six orders (A as the agent without Kanini Context, plus search and every document)
  2. B reading the raw chart (B_raw)
  3. E1: search with other chunking
  4. E2: tests built to make B fail (raw chart mode)
  5. what broke, order by order (propel/baseline/failures.py), from the new report

Before anything runs it checks the model key, the embedding model (E1 and search refuse to run without one, rather than
fall back to keyword search unnoticed) and the Kanini Context server, and prints which provider and models are used.
The reports carry the model name, so OpenAI and Azure runs are never added into one total.

    python -m kanini_context.server.mcp_server --http 8765     # terminal 1
    python -m propel.baseline.run_all --runs 5                  # terminal 2; about 1 to 2 hours on Azure
    python -m propel.baseline.run_all --runs 5 --skip e1        # leave out a step: abc, raw, e1, e2
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from kanini_context import env  # noqa: E402,F401

OUT = ROOT / "propel/baseline/results"


def steps(runs: int, e2_runs: int) -> list[tuple[str, list[str]]]:
    py = [sys.executable, "-m"]
    return [("abc", py + ["propel.baseline.compare", "--runs", str(runs), "--a-docs", "agent,rag,whole"]),
            ("raw", py + ["propel.baseline.compare", "--runs", str(runs), "--only", "B", "--b-mode", "raw"]),
            ("e1", py + ["propel.baseline.chunking", "--runs", str(runs)]),
            ("e2", py + ["propel.baseline.negatives", "--runs", str(e2_runs)])]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--e2-runs", type=int, default=3)
    ap.add_argument("--skip", nargs="*", default=[], choices=["abc", "raw", "e1", "e2"])
    ap.add_argument("--dry-run", action="store_true", help="print the checks and the commands; run nothing")
    a = ap.parse_args(argv)
    from propel import llm
    from propel.baseline.compare import _server_up
    problems = []
    if not llm.has_key():
        problems.append("no model key: set the AZURE_OPENAI_* settings (or OPENAI_API_KEY) in .env")
    if not llm.embed_model():
        problems.append("no embedding model: set AZURE_OPENAI_EMBEDDING_DEPLOYMENT (search and E1 need one)")
    if not _server_up():
        problems.append("Kanini Context server not running: python -m kanini_context.server.mcp_server --http 8765")
    print(f"provider {llm.PROVIDER}; model {llm.MODEL}; embeddings {llm.embed_model() or 'none'}")
    if problems:
        print("Not starting:\n  " + "\n  ".join(problems))
        return 1
    todo = [(k, cmd) for k, cmd in steps(a.runs, a.e2_runs) if k not in a.skip]
    if a.dry_run:
        print("\n".join(" ".join(cmd[1:]) for _, cmd in todo))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / f"run_all-{datetime.now():%Y%m%d-%H%M}.log"
    failed = []
    with log.open("w", encoding="utf-8") as fh:
        for key, cmd in todo:
            t = time.perf_counter()
            print(f"\n== {key}: {' '.join(cmd[2:])}", flush=True)
            fh.write(f"\n== {key}: {' '.join(cmd[2:])}\n")
            fh.flush()
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace")
            for line in p.stdout:
                sys.stdout.write(line)
                fh.write(line)
            p.wait()
            took = (time.perf_counter() - t) / 60
            fh.write(f"-- {key}: exit {p.returncode}, {took:.1f} min\n")
            if p.returncode:
                failed.append(key)
        newest = max(OUT.glob("compare-*.json"), key=lambda f: f.stat().st_mtime, default=None)
        if newest and "abc" not in a.skip:
            r = subprocess.run([sys.executable, "-m", "propel.baseline.failures", str(newest)], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8")
            (OUT / f"{newest.stem}-what-broke.md").write_text(r.stdout, encoding="utf-8")
    print(f"\nlog: {log.relative_to(ROOT)}" + (f"; steps that failed: {', '.join(failed)}" if failed else "; every step finished"))
    print("The dashboard's Testing page reads the newest reports; restart it to see them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
