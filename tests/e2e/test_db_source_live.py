"""DbSource against the real docs-db container (seeded by scripts/seed_db.py)."""

import pytest

pytestmark = pytest.mark.e2e


def test_reads_seeded_versions_in_order(services):
    import psycopg

    from vrag.config import get_settings
    from vrag.sources.db import DbSource

    try:
        versions = [v for v in DbSource(get_settings().docs_db_url).iter_versions() if v.doc_id == "travel-expense-policy"]
    except psycopg.OperationalError as e:
        pytest.skip(f"docs-db not reachable: {e}")
    assert [(v.version, v.version_num, str(v.source_date)) for v in versions] == [
        ("v1.0", 1, "2024-02-01"),
        ("v1.1", 2, "2024-08-01"),
        ("v2.0", 3, "2025-03-01"),
    ]
    assert b"The daily meal allowance is 60 EUR." in versions[1].content
    assert all(v.source == "db" and v.source_ref.startswith("row:") for v in versions)


def test_indexed_with_latest_flag(catalog):
    doc = catalog["travel-expense-policy"]
    assert doc["doc_title"] == "Contoso Travel and Expense Policy"
    assert [(v["version"], v["is_latest"]) for v in doc["versions"]] == [
        ("v1.0", False),
        ("v1.1", False),
        ("v2.0", True),
    ]
