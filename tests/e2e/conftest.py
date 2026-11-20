"""End-to-end tests: real Elasticsearch, real Ollama model, real API.

Run with:  .venv/Scripts/python -m pytest -m e2e
They are skipped by default (plain `pytest` runs only the fast unit tests).
They use the eval index (ES_INDEX=vrag-eval unless set), not the user's library (vrag-chunks).
Build it with:  .venv/Scripts/python scripts/build_eval_index.py
"""

import os

import httpx
import pytest
from dotenv import load_dotenv

os.environ.setdefault("ES_INDEX", "vrag-eval")  # before .env, which names the library index
load_dotenv(".env")


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


# With Langfuse stopped, every /ask waits ~6 s for the trace export to give up: switch tracing off.
if not _up(f"{os.environ.get('LANGFUSE_HOST', 'http://localhost:3000')}/api/public/health"):
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")


@pytest.fixture(scope="session")
def services():
    from vrag.config import get_settings

    s = get_settings()
    checks = [("Elasticsearch", s.es_url), ("Ollama", f"{s.ollama_base_url}/api/tags")]
    missing = [name for name, url in checks if not _up(url)]
    if missing:
        pytest.skip(f"not running: {', '.join(missing)}")


@pytest.fixture(scope="session")
def client(services):
    from fastapi.testclient import TestClient

    from vrag.api import app

    return TestClient(app)


@pytest.fixture(scope="session")
def catalog(services):
    from vrag import store

    return {d["doc_id"]: d for d in store.catalog()}


@pytest.fixture(scope="session")
def chunk_text(services):
    from vrag import store
    from vrag.config import get_settings

    cache: dict[str, str] = {}

    def get(chunk_id: str) -> str:
        if chunk_id not in cache:
            doc = store.es().get(index=get_settings().es_index, id=chunk_id, source_excludes=["embedding"])
            cache[chunk_id] = doc["_source"]["text"]
        return cache[chunk_id]

    return get
