"""Environment for every entry point: the repository's .env (if present), then the PostgreSQL defaults.

On a laptop, .env points at the Docker database (PGHOST=localhost, PGPORT=5432).
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

os.environ.setdefault("PGUSER", "postgres")   # the rest (PGHOST, PGPORT, PGPASSWORD) come from .env or the shell
