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

# Database names: the build database (Kanini side) and the client copy the agent reads. The tests use their own.
BUILD_DB = os.environ.setdefault("KC_BUILD_DB", "kc_build")
CLIENT_DB = os.environ.setdefault("KC_CLIENT_DB", "kc_client")
