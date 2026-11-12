"""Environment for every entry point: the repository's .env (if present), then the PostgreSQL defaults.

On a laptop, .env points at the Docker database (PGHOST=localhost). Without it, the defaults are the
local test server used to build and test the prototype.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

for _k, _v in {"PGHOST": "/var/lib/postgresql/kctest", "PGPORT": "5544", "PGUSER": "postgres"}.items():
    os.environ.setdefault(_k, _v)
