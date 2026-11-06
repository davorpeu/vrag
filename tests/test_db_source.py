from datetime import date, datetime

import pytest

from vrag.sources.db import DbSource


def rows(*items):
    return lambda query: [dict(r) for r in items]


def row(doc, version, day, ref, content=b"# T\nx", filename="p.md"):
    return {"doc_id": doc, "version": version, "filename": filename, "content": content, "source_date": day, "ref": ref}


def test_versions_numbered_per_doc_by_date():
    src = DbSource("unused", connect=rows(
        row("travel", "v2.0", date(2025, 3, 1), 3),
        row("travel", "v1.0", date(2024, 2, 1), 1),
        row("other", "A", date(2020, 1, 1), 9),
        row("travel", "v1.1", date(2024, 8, 1), 2),
    ))
    out = [(v.doc_id, v.version, v.version_num) for v in src.iter_versions()]
    assert out == [("other", "A", 1), ("travel", "v1.0", 1), ("travel", "v1.1", 2), ("travel", "v2.0", 3)]


def test_backdated_version_takes_its_place():
    # v0.9 inserted later (higher row id) but effective earlier -> becomes version 1
    src = DbSource("unused", connect=rows(
        row("travel", "v1.0", date(2024, 2, 1), 1),
        row("travel", "v0.9", date(2023, 6, 1), 7),
    ))
    assert [(v.version, v.version_num) for v in src.iter_versions()] == [("v0.9", 1), ("v1.0", 2)]


def test_text_content_datetime_and_ref():
    src = DbSource("unused", connect=rows(row("d", "v1", datetime(2024, 5, 6, 13, 0), 42, content="# Title\nhé")))
    v = next(src.iter_versions())
    assert v.content == "# Title\nhé".encode() and v.source_date == date(2024, 5, 6)
    assert v.source == "db" and v.source_ref == "row:42" and v.filename == "p.md"


def test_memoryview_content_from_bytea():
    src = DbSource("unused", connect=rows(row("d", "v1", date(2024, 1, 1), 1, content=memoryview(b"abc"))))
    assert next(src.iter_versions()).content == b"abc"


def test_missing_columns_is_a_clear_error():
    src = DbSource("unused", connect=lambda q: [{"doc_id": "d", "version": "v1"}])
    with pytest.raises(ValueError, match="missing"):
        list(src.iter_versions())


def test_sync_renumbers_without_reembedding(monkeypatch):
    from vrag import sync as sync_mod

    content = b"# T\nVersion: v1\n\n## 1. A\n\n1.1 text"
    import hashlib

    h = hashlib.sha256(content).hexdigest()
    calls = {"renumber": [], "replace": 0, "latest": []}
    monkeypatch.setattr(sync_mod.store, "ensure_index", lambda: None)
    monkeypatch.setattr(sync_mod.store, "indexed_meta", lambda d, v: (h, 1))  # indexed as version 1
    monkeypatch.setattr(sync_mod.store, "set_version_num", lambda d, v, n: calls["renumber"].append((d, v, n)))
    monkeypatch.setattr(sync_mod.store, "replace_version", lambda chunks: calls.__setitem__("replace", calls["replace"] + 1))
    monkeypatch.setattr(sync_mod.store, "refresh_latest", lambda d: calls["latest"].append(d))
    monkeypatch.setattr(sync_mod.store, "indexed_versions", lambda d: {"v1", "v0.9"})

    src = DbSource("unused", connect=rows(
        row("t", "v0.9", date(2023, 1, 1), 5, content=b"# T\nolder"),  # new, back-dated
        row("t", "v1", date(2024, 1, 1), 1, content=content),          # unchanged text, now version 2
    ))
    monkeypatch.setattr(sync_mod.store, "indexed_meta", lambda d, v: (h, 1) if v == "v1" else None)
    report = sync_mod.sync(src)
    assert report.renumbered == ["t@v1"] and calls["renumber"] == [("t", "v1", 2)]
    assert report.indexed == ["t@v0.9"] and calls["replace"] == 1
    assert calls["latest"] == ["t"]


def test_sync_removes_versions_the_source_no_longer_has(monkeypatch):
    # file loaded by commits first (sha labels), then by tags: the sha versions must go
    from vrag import sync as sync_mod

    calls = {"deleted": [], "latest": []}
    monkeypatch.setattr(sync_mod.store, "ensure_index", lambda: None)
    monkeypatch.setattr(sync_mod.store, "indexed_meta", lambda d, v: None)
    monkeypatch.setattr(sync_mod.store, "replace_version", lambda chunks: None)
    monkeypatch.setattr(sync_mod.store, "refresh_latest", lambda d: calls["latest"].append(d))
    monkeypatch.setattr(sync_mod.store, "indexed_versions", lambda d: {"a0566250", "03e77555", "v1.0"} if d == "t" else set())
    monkeypatch.setattr(sync_mod.store, "delete_version", lambda d, v: calls["deleted"].append((d, v)))
    src = DbSource("unused", connect=rows(row("t", "v1.0", date(2024, 1, 1), 1), row("t", "v1.1", date(2024, 2, 1), 2)))
    report = sync_mod.sync(src)
    assert calls["deleted"] == [("t", "03e77555"), ("t", "a0566250")]
    assert report.removed == ["t@03e77555", "t@a0566250"] and calls["latest"] == ["t"]


def test_interrupted_sync_deletes_nothing(monkeypatch):
    from vrag import sync as sync_mod

    deleted = []
    monkeypatch.setattr(sync_mod.store, "ensure_index", lambda: None)
    monkeypatch.setattr(sync_mod.store, "indexed_meta", lambda d, v: None)
    monkeypatch.setattr(sync_mod.store, "indexed_versions", lambda d: {"old"})
    monkeypatch.setattr(sync_mod.store, "delete_version", lambda d, v: deleted.append(v))

    def fail(chunks):
        raise ConnectionError("es down")

    monkeypatch.setattr(sync_mod.store, "replace_version", fail)
    with pytest.raises(ConnectionError):
        sync_mod.sync(DbSource("unused", connect=rows(row("t", "v1", date(2024, 1, 1), 1))))
    assert deleted == []
