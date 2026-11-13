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

Document search (propel/baseline/library.py) ranks passages with embeddings when the provider has an embedding
model (OpenAI's text-embedding-3-small, or AZURE_OPENAI_EMBEDDING_DEPLOYMENT on Azure); without one it uses BM25
keyword ranking.
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
    if AZURE:
        dep = (os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or "").strip()
        return f"azure/{dep}" if dep else None
    return "text-embedding-3-small" if os.environ.get("OPENAI_API_KEY") else None


def describe() -> str:
    return f"{MODEL} ({PROVIDER})"
