"""End-to-end tests: real Elasticsearch, real Ollama model, real API.

Run with:  .venv/Scripts/python -m pytest -m e2e
They are skipped by default (plain `pytest` runs only the fast unit tests).
Expects the folder corpus and the semver repo to be synced (see STATE.md).
"""

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv(".env")


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


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
