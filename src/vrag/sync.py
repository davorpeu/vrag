import hashlib
from dataclasses import dataclass, field

from vrag import store
from vrag.chunking import split_markdown, split_sections
from vrag.extract import extract_text, find_effective_date, find_title
from vrag.models import Chunk, DocVersion
from vrag.sources.base import VersionSource


@dataclass
class SyncReport:
    indexed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)


def build_chunks(v: DocVersion) -> list[Chunk]:
    text = extract_text(v.filename, v.content)
    content_hash = hashlib.sha256(v.content).hexdigest()
    title = find_title(text) or v.doc_id
    effective = find_effective_date(text) or v.source_date
    is_md = v.filename.lower().endswith((".md", ".markdown"))
    sections = split_markdown(text) if is_md else split_sections(text)
    return [
        Chunk(
            chunk_id=f"{v.doc_id}:{v.version}:{i}",
            doc_id=v.doc_id,
            doc_title=title,
            version=v.version,
            version_num=v.version_num,
            effective_date=effective,
            source=v.source,
            source_ref=v.source_ref,
            content_hash=content_hash,
            section_number=sec.number,
            section_title=sec.title,
            text=sec.text,
        )
        for i, sec in enumerate(sections)
    ]


def sync(source: VersionSource) -> SyncReport:
    """Incremental + idempotent: a version is only re-indexed if its content hash changed."""
    store.ensure_index()
    report = SyncReport()
    touched: set[str] = set()
    for v in source.iter_versions():
        label = f"{v.doc_id}@{v.version}"
        if store.indexed_hash(v.doc_id, v.version) == hashlib.sha256(v.content).hexdigest():
            report.skipped.append(label)
            continue
        chunks = build_chunks(v)
        if not chunks:
            report.empty.append(label)
            continue
        store.replace_version(chunks)
        touched.add(v.doc_id)
        report.indexed.append(label)
    for doc_id in touched:
        store.refresh_latest(doc_id)
    return report
