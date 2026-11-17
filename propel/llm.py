"""Which LLM provider the demo calls: OpenAI or Azure OpenAI, through LiteLLM.

Azure OpenAI is used when its endpoint and key are set in .env:

    AZURE_OPENAI_ENDPOINT=https://<resource>.cognitiveservices.azure.com
    AZURE_OPENAI_KEY=<key>
    AZURE_OPENAI_API_VERSION=2024-12-01-preview
    AZURE_OPENAI_DEPLOYMENT_NAME=gpt-5-mini
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT=        # optional: an embedding deployment for document search

Otherwise OPENAI_API_KEY is used, with PROPEL_MODEL (default openai/gpt-5-mini).

LiteLLM reaches an Azure deployment as model "azure/<deployment>" and reads AZURE_API_BASE, AZURE_API_KEY and
AZURE_API_VERSION; this module copies the AZURE_OPENAI_* names onto those, so the agents (ADK's LiteLlm), the
comparison and the dashboard all call the same deployment. A deployment whose name contains "gpt-5" is treated by
LiteLLM as a GPT-5 reasoning model: no temperature, and reasoning_effort is passed through.

Document search (propel/baseline/library.py) ranks passages with embeddings: an Azure embedding deployment when
AZURE_OPENAI_EMBEDDING_DEPLOYMENT is set, otherwise OpenAI's text-embedding-3-small when OPENAI_API_KEY is set (with
Azure for chat too). With neither it uses BM25 keyword ranking.
"""
from __future__ import annotations

import os

from kanini_context import env  # noqa: F401  (loads .env)

_AZ_KEY = os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY") or ""
_AZ_BASE = (os.environ.get("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
AZURE = bool(_AZ_KEY.strip() and _AZ_BASE)

if AZURE:
    os.environ["AZURE_API_KEY"] = _AZ_KEY.strip()
    os.environ["AZURE_API_BASE"] = _AZ_BASE
    os.environ["AZURE_API_VERSION"] = os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-12-01-preview"
    DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME") or "gpt-5-mini"
    _set = os.environ.get("PROPEL_MODEL", "")
    MODEL = _set if _set.startswith("azure/") else f"azure/{DEPLOYMENT}"
    PROVIDER = "Azure OpenAI"
else:
    MODEL = os.environ.get("PROPEL_MODEL") or "openai/gpt-5-mini"
    PROVIDER = "OpenAI"

# Token counts use the model family's tokenizer, not the deployment name (which can be anything on Azure).
TOKEN_MODEL = os.environ.get("PROPEL_TOKEN_MODEL") or "gpt-5-mini"


def has_key() -> bool:
    """True when a real model can be called (Azure endpoint and key, or an OpenAI key)."""
    return AZURE or bool(os.environ.get("OPENAI_API_KEY"))


def embed_model() -> str | None:
    """The embedding model for document search, or None (search then ranks by BM25 keywords)."""
    dep = (os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or "").strip()
    if AZURE and dep:
        return f"azure/{dep}"
    # OpenAI's embeddings also serve an Azure chat deployment: the same model the recorded test runs used
    return "text-embedding-3-small" if os.environ.get("OPENAI_API_KEY") else None


def describe() -> str:
    return f"{MODEL} ({PROVIDER})"


# --- Rate limits on long runs ------------------------------------------------------------------------------------------
# A deployment has a tokens-per-minute limit (Azure: the deployment's "Tokens per Minute Rate Limit"). Long test runs
# wait and retry when it answers 429, instead of stopping, and can pace themselves under it (PROPEL_TPM).

import time as _time
from collections import deque as _deque

_SENT: _deque = _deque()   # (time, tokens) of the calls sent in the last minute by this process
BACKOFF = [20, 40, 60, 90, 120, 120, 120]   # seconds: about nine minutes in all before giving up on one call


class TooLargeForLimit(RuntimeError):
    """One request needs more tokens than the deployment allows per minute: it can never succeed there."""


def tpm() -> int | None:
    v = os.environ.get("PROPEL_TPM", "").strip()
    return int(v) if v.isdigit() else None


def is_rate_limit(ex: BaseException) -> bool:
    return "RateLimit" in type(ex).__name__ or "429" in str(ex)[:400] or "rate limit" in str(ex).lower()[:400]


def _retry_after(ex: BaseException, attempt: int) -> float:
    resp = getattr(ex, "response", None)
    try:
        ra = float(resp.headers.get("retry-after")) if resp is not None and resp.headers.get("retry-after") else None
    except (TypeError, ValueError, AttributeError):
        ra = None
    return max(ra or 0, BACKOFF[min(attempt, len(BACKOFF) - 1)])


def pace(tokens: int):
    """Wait until sending this many tokens keeps the last minute under PROPEL_TPM (when it is set)."""
    limit = tpm()
    if not limit:
        return
    if tokens > limit:
        raise TooLargeForLimit(f"one call needs about {tokens:,} tokens; the deployment allows {limit:,} per minute")
    while True:
        now = _time.monotonic()
        while _SENT and now - _SENT[0][0] > 60:
            _SENT.popleft()
        if sum(t for _, t in _SENT) + tokens <= limit:
            _SENT.append((now, tokens))
            return
        _time.sleep(max(1.0, 60 - (now - _SENT[0][0]) + 0.5))


def completion(**kw):
    """litellm.completion, paced under PROPEL_TPM and retried with backoff on 429 (about nine minutes at most)."""
    import litellm
    est = litellm.token_counter(model=TOKEN_MODEL, messages=kw.get("messages") or []) + 2000
    for attempt in range(len(BACKOFF) + 1):
        pace(est)
        try:
            return litellm.completion(num_retries=0, **{k: v for k, v in kw.items() if k != "num_retries"})
        except Exception as ex:
            if not is_rate_limit(ex) or attempt == len(BACKOFF):
                raise
            wait = _retry_after(ex, attempt)
            print(f"  rate limit: waiting {wait:.0f} s, then trying again ({attempt + 1} of {len(BACKOFF)})", flush=True)
            _time.sleep(wait)


def with_backoff(fn, *args, what: str = "run", **kwargs):
    """Run a whole agent run again after a wait when the model's rate limit stops it."""
    for attempt in range(len(BACKOFF) + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as ex:
            if not is_rate_limit(ex) or attempt == len(BACKOFF):
                raise
            wait = _retry_after(ex, attempt)
            print(f"  rate limit during the {what}: waiting {wait:.0f} s, then running it again ({attempt + 1} of {len(BACKOFF)})", flush=True)
            _time.sleep(wait)
