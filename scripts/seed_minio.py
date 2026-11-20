"""Seed the docs-minio object store with a versioned policy that exists only in MinIO.

Creates the bucket DOCS_S3_BUCKET (vrag-docs) with versioning on, then stores each policy version
as a new object version of one key (it-security-policy.md), oldest first, with the user metadata
"version" and "effective-date" set to the same values as the text. Ground truth of what changed
per version is in data/golden/changes.json under "it-security-policy".

Idempotent: it reads the versions the s3 source sees and compares their label, date and content
with the seed. Same -> nothing is uploaded. Only newer versions missing -> only those are uploaded.
Anything else (text edited here, extra versions) -> every version of the key is deleted and the
seed is uploaded again, so the key always ends up holding exactly these versions.

Usage: .venv/Scripts/python scripts/seed_minio.py
"""

import io

from dotenv import load_dotenv

load_dotenv(".env")

from minio.commonconfig import ENABLED  # noqa: E402
from minio.versioningconfig import VersioningConfig  # noqa: E402

from vrag.config import get_settings  # noqa: E402
from vrag.sources.s3 import S3Source, make_client  # noqa: E402

KEY = "it-security-policy.md"
DOC_ID = "it-security-policy"
TITLE = "Fabrikam IT Security Policy"

BASE = {
    "1.1": "Passwords must have at least 10 characters.",
    "1.2": "Passwords must be changed every 90 days.",
    "2.1": "Multi-factor authentication is required for remote access to the company network.",
    "3.1": "Company laptops must use full-disk encryption.",
    "3.2": "Laptops must lock the screen after 15 minutes of inactivity.",
    "4.1": "Security incidents must be reported to the IT service desk within 24 hours.",
}
SECTIONS = {
    "1": "Passwords",
    "2": "Multi-Factor Authentication",
    "3": "Laptops",
    "4": "Incident Reporting",
    "5": "Phishing Training",
}

# label, effective date, changes on top of the previous version (None = clause removed)
VERSIONS = [
    ("v1.0", "2024-04-15", {}),
    ("v1.1", "2024-11-01", {
        "1.1": "Passwords must have at least 14 characters.",
        "2.1": "Multi-factor authentication is required for remote access and for all cloud applications.",
    }),
    ("v2.0", "2025-06-15", {
        "1.2": None,
        "1.3": "Passwords must be changed at once if they may have been exposed.",
        "3.2": "Laptops must lock the screen after 5 minutes of inactivity.",
        "4.1": "Security incidents must be reported to the IT service desk within 4 hours.",
        "5.1": "All employees must complete phishing awareness training once a year.",
    }),
]


def render(label: str, effective: str, clauses: dict[str, str]) -> str:
    lines = [f"# {TITLE}", "", f"Version: {label}", f"Effective date: {effective}", ""]
    for sec, name in SECTIONS.items():
        body = [f"{k} {v}" for k, v in sorted(clauses.items()) if k.split(".")[0] == sec]
        if body:
            lines += [f"## {sec}. {name}", "", *body, ""]
    return "\n".join(lines)


def seed_versions() -> list[tuple[str, str, bytes]]:
    """(label, effective date, content) of every version, oldest first."""
    clauses, out = dict(BASE), []
    for label, effective, changes in VERSIONS:
        for k, v in changes.items():
            if v is None:
                clauses.pop(k, None)
            else:
                clauses[k] = v
        out.append((label, effective, render(label, effective, clauses).encode("utf-8")))
    return out


def stored(src: S3Source) -> list[tuple[str, str, bytes]]:
    """What the s3 source sees for the document, in the same shape as seed_versions()."""
    return [(v.version, v.source_date.isoformat(), v.content) for v in src.iter_versions() if v.doc_id == DOC_ID]


def main() -> None:
    s = get_settings()
    client = make_client(s.docs_s3_url, s.docs_s3_access_key, s.docs_s3_secret_key)
    bucket = s.docs_s3_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        print(f"created bucket {bucket}")
    if client.get_bucket_versioning(bucket).status != ENABLED:
        client.set_bucket_versioning(bucket, VersioningConfig(ENABLED))
        print(f"turned on versioning for bucket {bucket}")

    src = S3Source(s.docs_s3_url, bucket, client=client)
    want, have = seed_versions(), stored(src)
    if have != want[: len(have)]:
        print(f"{KEY}: stored versions differ from the seed; deleting every version of the key, uploading again")
        for o in client.list_objects(bucket, prefix=KEY, include_version=True):
            if o.object_name == KEY:
                client.remove_object(bucket, KEY, version_id=o.version_id)
        have = []
    for i, (label, effective, data) in enumerate(want):
        if i < len(have):
            print(f"{KEY} {label} ({effective}): unchanged")
            continue
        client.put_object(
            bucket, KEY, io.BytesIO(data), len(data), content_type="text/markdown; charset=utf-8",
            metadata={"version": label, "effective-date": effective},
        )
        print(f"{KEY} {label} ({effective}): uploaded")
    if stored(src) != want:
        raise SystemExit(f"{KEY}: the s3 source does not read back the seeded versions")


if __name__ == "__main__":
    main()
