"""Versions of documents stored in an S3-compatible object store (MinIO here, or AWS S3).

Two layouts.

"versions" (default): native S3 bucket versioning. Every object key is a document and every
object version is a document version.
- doc_id = the key without its extension, and without the --prefix folder when one is given:
  "policies/it-security-policy.md" with prefix "policies" -> "it-security-policy".
- Order = the version chain S3 keeps per key. ListObjectVersions lists each key's versions newest
  first, in the order they were stored (checked on MinIO, also for the "null" version of a bucket
  whose versioning was turned on or suspended later). That order is reversed: oldest = version 1.
  LastModified is not used to order: listings give it in milliseconds and quick uploads can share
  a timestamp.
- Label = the object's user metadata "version" (x-amz-meta-version) when set, else "v<n>" by
  position. When several versions of a key carry the same label, the newest one wins (a re-upload
  of that version, e.g. a fixed typo) and the older ones are skipped. A "v<n>" label that another
  version already has in its metadata gets the version id added: "v2-1a2b3c4d".
- source_date = user metadata "effective-date" (YYYY-MM-DD) when set and valid, else the
  LastModified date in UTC. UTC because S3 stores only UTC, not the uploader's time zone, and
  the syncing machine's zone would give the same object different dates on different machines.
  sync prefers an "Effective date:" line in the text over both.
- source_ref = "<key>@<version id>". S3 gives the id "null" to an object written while
  versioning was off.
- Only keys that extract.py can read: .md, .markdown, .txt, .pdf, .docx. Other keys are ignored.
- Delete markers:
  - Newest entry of a key is a delete marker: the document was deleted. None of its versions are
    produced and its doc_id goes into `deleted_docs`, so sync can drop it from the index.
    Removing the marker (S3's undelete) brings the document back with its history.
  - A delete marker in the middle (deleted, then uploaded again) is skipped: the versions before
    and after it are all versions of the same document.
  - Deleting one object version (DELETE with a version id) removes it for good, with no marker.
    sync then drops that version and renumbers the rest. "v<n>" labels shift with the positions,
    so set the "version" metadata for labels that never change.
- Buckets without versioning: each key has exactly one version (id "null"), so each document has
  one version. An overwrite replaces it; without "version" metadata the label stays v1 and the
  new content is re-indexed under it.
- Two live keys with the same doc_id (policy.md and policy.pdf) are an error: nothing is synced
  until one is renamed or deleted. Deleted keys don't count, so a document can change format by
  uploading policy.pdf and deleting policy.md.

"folders": for buckets without versioning. Keys "<doc_id>/v<N>[_YYYY-MM-DD].<ext>", the naming of
sources/folder.py, plus .md/.markdown/.txt. doc_id = the folder path without --prefix, label
"v<N>", version_num N, source_date from the name, else the LastModified date (UTC). Only current
objects are read. Two files with the same N in one folder are an error.
"""

import re
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from vrag.models import DocVersion
from vrag.sources.base import VersionSource

DOC_EXT = {".md", ".markdown", ".txt", ".pdf", ".docx"}  # what extract.py reads
LAYOUTS = ("versions", "folders")
VERSION_FILE = re.compile(
    r"^v(?P<num>\d+)(?:_(?P<date>\d{4}-\d{2}-\d{2}))?\.(?:md|markdown|txt|pdf|docx)$", re.I
)
META_VERSION = "x-amz-meta-version"
META_DATE = "x-amz-meta-effective-date"


def endpoint(url: str) -> tuple[str, bool]:
    """("host:port", secure) from an S3 URL like http://127.0.0.1:9100. The scheme decides TLS."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/"):
        raise ValueError(f"S3 URL must look like http(s)://host:port, got {url!r}")
    return parts.netloc, parts.scheme == "https"


def make_client(url: str, access_key: str, secret_key: str):
    """A minio.Minio client for MinIO or any S3-compatible store."""
    from minio import Minio

    host, secure = endpoint(url)
    return Minio(host, access_key=access_key, secret_key=secret_key, secure=secure)


def _day(text: str | None) -> date | None:
    """YYYY-MM-DD -> date. None when missing or not a valid date."""
    try:
        return date.fromisoformat(text.strip()) if text else None
    except ValueError:
        return None


def _utc_day(ts: datetime) -> date:
    return (ts.astimezone(timezone.utc) if ts.tzinfo else ts).date()


class S3Source(VersionSource):
    """After each pass, `deleted_docs` lists the doc_ids whose key ends in a delete marker."""

    name = "s3"

    def __init__(
        self,
        url: str,
        bucket: str,
        prefix: str = "",
        layout: str = "versions",
        access_key: str = "",
        secret_key: str = "",
        client=None,
    ):
        if layout not in LAYOUTS:
            raise ValueError(f"layout must be 'versions' or 'folders', got {layout!r}")
        self.url = url
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""  # a folder
        self.layout = layout
        self._keys = (access_key, secret_key)
        self._client = client  # injectable for tests
        self.deleted_docs: list[str] = []

    @property
    def client(self):
        if self._client is None:
            self._client = make_client(self.url, *self._keys)
        return self._client

    def iter_versions(self) -> Iterator[DocVersion]:
        self.deleted_docs = []
        if self.layout == "folders":
            yield from self._folder_versions()
        else:
            yield from self._bucket_versions()

    # -- object store -------------------------------------------------------
    def _read(self, key: str, version_id: str | None) -> bytes:
        r = self.client.get_object(self.bucket, key, version_id=version_id)
        try:
            return r.read()
        finally:
            r.close()
            r.release_conn()

    def _meta(self, key: str, version_id: str | None) -> dict[str, str]:
        st = self.client.stat_object(self.bucket, key, version_id=version_id)
        return {k.lower(): v for k, v in (st.metadata or {}).items()}

    # -- layout "versions" --------------------------------------------------
    def _doc_id(self, key: str) -> str | None:
        """Key without --prefix and extension. None for files extract.py can't read."""
        if not key.startswith(self.prefix):
            return None
        path = PurePosixPath(key[len(self.prefix):])
        return str(path.with_suffix("")) if path.suffix.lower() in DOC_EXT else None

    def _bucket_versions(self) -> Iterator[DocVersion]:
        chains: dict[str, list] = {}  # key -> its object versions, newest first (listing order)
        gone: set[str] = set()  # keys whose newest entry is a delete marker
        listing = self.client.list_objects(self.bucket, prefix=self.prefix, recursive=True, include_version=True)
        for o in listing:
            if self._doc_id(o.object_name) is None:
                continue
            if o.is_delete_marker:
                # The minio SDK lists a page's delete markers after its versions, so only
                # IsLatest tells whether a marker is the newest entry of its key.
                if str(o.is_latest).lower() == "true":
                    gone.add(o.object_name)
            else:
                chains.setdefault(o.object_name, []).append(o)

        docs: dict[str, list[str]] = {}
        for key in chains:
            if key not in gone:
                docs.setdefault(self._doc_id(key), []).append(key)
        clashes = sorted((d, keys) for d, keys in docs.items() if len(keys) > 1)
        if clashes:
            detail = "; ".join(f"{', '.join(sorted(keys))} -> {d!r}" for d, keys in clashes)
            raise ValueError(f"keys with the same doc_id ({detail}); rename or delete all but one")
        self.deleted_docs = sorted({self._doc_id(k) for k in gone} - docs.keys())

        for doc_id in sorted(docs):
            key = docs[doc_id][0]
            yield from self._key_versions(doc_id, key, chains[key][::-1])

    def _key_versions(self, doc_id: str, key: str, versions: list) -> Iterator[DocVersion]:
        """versions = the object versions of one key, oldest first."""
        metas = [self._meta(key, o.version_id) for o in versions]
        labels = [(m.get(META_VERSION) or "").strip() or None for m in metas]
        # The same label on several versions: the newest is a re-upload of that version and wins.
        keep = [i for i, label in enumerate(labels) if label is None or label not in labels[i + 1:]]
        named = {labels[i] for i in keep} - {None}
        for num, i in enumerate(keep, start=1):
            o = versions[i]
            vid = o.version_id or "null"
            label = labels[i] or (f"v{num}" if f"v{num}" not in named else f"v{num}-{vid[:8]}")
            yield DocVersion(
                doc_id=doc_id,
                version=label,
                version_num=num,
                source=self.name,
                source_ref=f"{key}@{vid}",
                filename=PurePosixPath(key).name,
                content=self._read(key, o.version_id),
                source_date=_day(metas[i].get(META_DATE)) or _utc_day(o.last_modified),
            )

    # -- layout "folders" ---------------------------------------------------
    def _folder_versions(self) -> Iterator[DocVersion]:
        docs: dict[str, dict[int, object]] = {}
        for o in self.client.list_objects(self.bucket, prefix=self.prefix, recursive=True):
            path = PurePosixPath(o.object_name[len(self.prefix):])
            m = VERSION_FILE.match(path.name)
            if not m or str(path.parent) == ".":
                continue
            versions = docs.setdefault(str(path.parent), {})
            num = int(m["num"])
            if num in versions:
                raise ValueError(
                    f"two files are version {num} of {path.parent}: {versions[num].object_name}, {o.object_name}"
                )
            versions[num] = o
        for doc_id in sorted(docs):
            for num, o in sorted(docs[doc_id].items()):
                name = PurePosixPath(o.object_name).name
                yield DocVersion(
                    doc_id=doc_id,
                    version=f"v{num}",
                    version_num=num,
                    source=self.name,
                    source_ref=o.object_name,
                    filename=name,
                    content=self._read(o.object_name, None),
                    source_date=_day(VERSION_FILE.match(name)["date"]) or _utc_day(o.last_modified),
                )
