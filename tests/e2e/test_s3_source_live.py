"""S3Source against the real docs-minio container (seeded by scripts/seed_minio.py).

Skips when MinIO is down or not seeded. The sync test writes to the index, so it only runs on a
scratch index: ES_INDEX=vrag-dev-<id>. It never writes to vrag-eval or vrag-chunks.
"""

import os

import pytest

pytestmark = pytest.mark.e2e

DOC = "it-security-policy"


@pytest.fixture(scope="module")
def source():
    from vrag.config import get_settings
    from vrag.sources.s3 import S3Source

    s = get_settings()
    src = S3Source(s.docs_s3_url, s.docs_s3_bucket, access_key=s.docs_s3_access_key, secret_key=s.docs_s3_secret_key)
    try:
        src.client.bucket_exists(s.docs_s3_bucket)
    except Exception as e:  # urllib3 / minio errors: MinIO is not running
        pytest.skip(f"docs-minio not reachable: {e}")
    return src


@pytest.fixture(scope="module")
def versions(source):
    out = [v for v in source.iter_versions() if v.doc_id == DOC]
    if not out:
        pytest.skip("docs-minio is not seeded (scripts/seed_minio.py)")
    return out


def test_reads_seeded_versions_in_order(versions):
    assert [(v.version, v.version_num, str(v.source_date)) for v in versions] == [
        ("v1.0", 1, "2024-04-15"),
        ("v1.1", 2, "2024-11-01"),
        ("v2.0", 3, "2025-06-15"),
    ]
    assert b"at least 10 characters" in versions[0].content
    assert b"at least 14 characters" in versions[1].content
    assert b"within 4 hours" in versions[2].content
    assert all(v.source == "s3" and v.source_ref.startswith("it-security-policy.md@") for v in versions)
    assert len({v.source_ref for v in versions}) == 3


def test_sync_into_scratch_index_is_idempotent(services, source):
    if not os.environ.get("ES_INDEX", "").startswith("vrag-dev-"):
        pytest.skip("writes to the index: set ES_INDEX=vrag-dev-<id>")
    from vrag import store
    from vrag.sync import sync

    store.ensure_index(recreate=True)
    first = sync(source)
    assert f"{DOC}@v2.0" in first.indexed
    second = sync(source)
    assert second.indexed == [] and second.renumbered == [] and second.removed == []
    assert f"{DOC}@v1.0" in second.skipped and not second.empty
    doc = {d["doc_id"]: d for d in store.catalog()}[DOC]
    assert [(v["version"], v["is_latest"]) for v in doc["versions"]] == [
        ("v1.0", False), ("v1.1", False), ("v2.0", True),
    ]


def test_indexed_in_eval_index(catalog):
    if DOC not in catalog:
        pytest.skip("it-security-policy is not in the index yet (vrag sync s3)")
    doc = catalog[DOC]
    assert doc["doc_title"] == "Fabrikam IT Security Policy"
    assert [v["version"] for v in doc["versions"]] == ["v1.0", "v1.1", "v2.0"]
