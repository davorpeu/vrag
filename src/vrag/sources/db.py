"""Versions of documents stored as rows in a SQL database (Postgres).

The source runs one query that returns one row per version with these columns:

    doc_id       text   stable identity of the document
    version      text   version label as the business names it (v1.0, 2025-Q1, ...)
    filename     text   picks the extractor: .md / .txt / .pdf / .docx
    content      bytea or text
    source_date  date   effective date (orders versions; fallback when the text has no date)
    ref          any    row id, kept as source_ref

DEFAULT_QUERY fits db/init/001_schema.sql. Any other schema works by passing a query that returns
those columns, e.g. a history/audit table or a valid_from/valid_to table.

Version numbers are assigned per document by (source_date, ref), oldest = 1, so a back-dated
version inserted later lands in the right place.
"""

from collections.abc import Callable, Iterator
from datetime import date, datetime
from itertools import groupby

from vrag.models import DocVersion
from vrag.sources.base import VersionSource

DEFAULT_QUERY = """
SELECT d.slug           AS doc_id,
       v.version_label  AS version,
       v.filename       AS filename,
       v.content        AS content,
       v.effective_from AS source_date,
       v.id             AS ref
FROM document_versions v
JOIN documents d ON d.id = v.document_id
"""

REQUIRED = ("doc_id", "version", "filename", "content", "source_date", "ref")


class DbSource(VersionSource):
    name = "db"

    def __init__(self, url: str, query: str = DEFAULT_QUERY, connect: Callable | None = None):
        self.url = url
        self.query = query
        self._connect = connect  # injectable for tests

    def _rows(self) -> list[dict]:
        if self._connect is None:
            import psycopg
            from psycopg.rows import dict_row

            # connect_timeout: "localhost" may try IPv6 first and hang where only IPv4 is published
            with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=10) as conn:
                return list(conn.execute(self.query))
        return list(self._connect(self.query))

    def iter_versions(self) -> Iterator[DocVersion]:
        rows = self._rows()
        if rows:
            missing = [c for c in REQUIRED if c not in rows[0]]
            if missing:
                raise ValueError(f"query must return columns {REQUIRED}; missing {missing}")

        def day(r: dict) -> date:
            d = r["source_date"]
            return d.date() if isinstance(d, datetime) else d if isinstance(d, date) else date.fromisoformat(str(d))

        rows.sort(key=lambda r: (r["doc_id"], day(r), str(r["ref"])))
        for doc_id, group in groupby(rows, key=lambda r: r["doc_id"]):
            for num, r in enumerate(group, start=1):
                content = r["content"]
                if isinstance(content, str):
                    content = content.encode("utf-8")
                yield DocVersion(
                    doc_id=doc_id,
                    version=r["version"],
                    version_num=num,
                    source=self.name,
                    source_ref=f"row:{r['ref']}",
                    filename=r["filename"],
                    content=bytes(content),
                    source_date=day(r),
                )
