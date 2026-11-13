"""Provider selection: Azure OpenAI when its endpoint and key are set, OpenAI otherwise (propel/llm.py)."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AZ = {"AZURE_OPENAI_ENDPOINT": "https://example.cognitiveservices.azure.com/", "AZURE_OPENAI_KEY": "k",
      "AZURE_OPENAI_API_VERSION": "2024-12-01-preview", "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-5-mini"}
NAMES = list(AZ) + ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "OPENAI_API_KEY", "PROPEL_MODEL",
                    "AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"]


def _probe(extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in NAMES}
    env.update(extra)
    code = ("import json, os; from propel import llm; print(json.dumps({'model': llm.MODEL, 'provider': llm.PROVIDER, "
            "'key': llm.has_key(), 'embed': llm.embed_model(), 'base': os.environ.get('AZURE_API_BASE'), "
            "'version': os.environ.get('AZURE_API_VERSION')}))")
    # .env is not read here: dotenv does not override variables already set, so blank them explicitly
    for k in NAMES:
        env.setdefault(k, "")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_azure_settings_route_every_call_to_the_deployment():
    x = _probe({**AZ, "PROPEL_MODEL": "openai/gpt-5-mini"})   # an OpenAI PROPEL_MODEL left in .env does not win
    assert x == {"model": "azure/gpt-5-mini", "provider": "Azure OpenAI", "key": True, "embed": None,
                 "base": "https://example.cognitiveservices.azure.com", "version": "2024-12-01-preview"}
    assert _probe({**AZ, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "emb"})["embed"] == "azure/emb"


def test_openai_when_azure_is_not_set():
    x = _probe({"OPENAI_API_KEY": "sk-test"})
    assert (x["model"], x["provider"], x["key"], x["embed"]) == ("openai/gpt-5-mini", "OpenAI", True, "text-embedding-3-small")
    assert _probe({})["key"] is False
