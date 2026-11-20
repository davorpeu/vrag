"""S3Source with a fake object-store client (no MinIO needed)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from vrag.sources.s3 import S3Source, endpoint

T0 = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    """objects: key -> list of entries OLDEST first. Entry = dict(id, data, meta, marker, when)."""

    def __init__(self):
        self.objects: dict[str, list[dict]] = {}
        self.n = 0

    def put(self, key, data=b"# T\nx", meta=None, marker=False, when=None):
        self.n += 1
        self.objects.setdefault(key, []).append(
            {"id": f"id{self.n:04d}", "data": data, "meta": meta or {}, "marker": marker,
             "when": when or T0 + timedelta(days=self.n)}
        )

    def list_objects(self, bucket, prefix="", recursive=False, include_version=False):
        for key in sorted(self.objects):
            if not key.startswith(prefix):
                continue
            chain = self.objects[key][::-1]  # S3 lists newest first
            if not include_version:
                if not chain[0]["marker"]:
                    yield self._obj(key, chain[0], True)
                continue
            # the minio SDK lists a page's delete markers after its versions
            for e in [e for e in chain if not e["marker"]] + [e for e in chain if e["marker"]]:
                yield self._obj(key, e, e is chain[0])

    @staticmethod
    def _obj(key, e, latest):
        return SimpleNamespace(
            object_name=key, version_id=e["id"], is_delete_marker=e["marker"],
            is_latest="true" if latest else "false", last_modified=e["when"],
        )

    def _entry(self, key, version_id):
        chain = self.objects[key]
        return next(e for e in chain if e["id"] == version_id) if version_id else chain[-1]

    def stat_object(self, bucket, key, version_id=None):
        return SimpleNamespace(metadata={f"x-amz-meta-{k}": v for k, v in self._entry(key, version_id)["meta"].items()})

    def get_object(self, bucket, key, version_id=None):
        data = self._entry(key, version_id)["data"]
        return SimpleNamespace(read=lambda: data, close=lambda: None, release_conn=lambda: None)


def source(fake, **kw):
    return S3Source("http://x:1", "b", client=fake, **kw)


def meta(label=None, eff=None):
    m = {}
    if label:
        m["version"] = label
    if eff:
        m["effective-date"] = eff
    return m


def test_versions_in_order_with_labels_and_dates():
    f = FakeClient()
    f.put("policy.md", b"one", meta("v1.0", "2024-02-01"))
    f.put("policy.md", b"two", meta("v1.1", "2024-08-01"))
    f.put("policy.md", b"three", meta("v2.0", "2025-03-01"))
    out = list(source(f).iter_versions())
    assert [(v.doc_id, v.version, v.version_num, str(v.source_date)) for v in out] == [
        ("policy", "v1.0", 1, "2024-02-01"), ("policy", "v1.1", 2, "2024-08-01"), ("policy", "v2.0", 3, "2025-03-01"),
    ]
    assert [v.content for v in out] == [b"one", b"two", b"three"]
    assert all(v.source == "s3" and v.filename == "policy.md" for v in out)
    assert out[0].source_ref == "policy.md@id0001"


def test_labels_and_date_fall_back_to_position_and_last_modified_utc():
    f = FakeClient()
    f.put("a.txt", when=datetime(2024, 3, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5))))  # = 2024-03-02 UTC
    f.put("a.txt", meta=meta(eff="not-a-date"), when=datetime(2024, 4, 1, tzinfo=timezone.utc))
    out = list(source(f).iter_versions())
    assert [(v.version, str(v.source_date)) for v in out] == [("v1", "2024-03-02"), ("v2", "2024-04-01")]


def test_same_label_newest_wins():
    f = FakeClient()
    f.put("p.md", b"old", meta("v1"))
    f.put("p.md", b"typo", meta("v2"))
    f.put("p.md", b"fixed", meta("v2"))
    out = list(source(f).iter_versions())
    assert [(v.version, v.version_num, v.content) for v in out] == [("v1", 1, b"old"), ("v2", 2, b"fixed")]


def test_positional_label_does_not_clash_with_a_named_one():
    f = FakeClient()
    f.put("p.md", meta=meta("v2"))
    f.put("p.md")  # position 2 -> "v2" is taken
    labels = [v.version for v in source(f).iter_versions()]
    assert labels[0] == "v2" and labels[1].startswith("v2-") and len(set(labels)) == 2


def test_stable_ids_prefix_and_unreadable_keys_ignored():
    f = FakeClient()
    f.put("policies/hr/leave.docx")
    f.put("policies/readme.png")
    f.put("other/x.md")
    assert [v.doc_id for v in source(f, prefix="policies").iter_versions()] == ["hr/leave"]
    assert [v.doc_id for v in source(f, prefix="/policies/").iter_versions()] == ["hr/leave"]
    assert sorted(v.doc_id for v in source(f).iter_versions()) == ["other/x", "policies/hr/leave"]


def test_deleted_document_is_reported_not_produced():
    f = FakeClient()
    f.put("gone.md")
    f.put("gone.md", marker=True)
    f.put("kept.md")
    s = source(f)
    assert [v.doc_id for v in s.iter_versions()] == ["kept"]
    assert s.deleted_docs == ["gone"]


def test_undelete_brings_history_back_and_marker_in_the_middle_is_skipped():
    f = FakeClient()
    f.put("p.md", b"1", meta("v1"))
    f.put("p.md", marker=True)
    f.put("p.md", b"2", meta("v2"))
    s = source(f)
    assert [(v.version, v.version_num) for v in s.iter_versions()] == [("v1", 1), ("v2", 2)]
    assert s.deleted_docs == []


def test_format_change_is_not_a_clash_when_the_old_key_is_deleted():
    f = FakeClient()
    f.put("policy.md")
    f.put("policy.md", marker=True)
    f.put("policy.pdf")
    s = source(f)
    assert [(v.doc_id, v.filename) for v in s.iter_versions()] == [("policy", "policy.pdf")]
    assert s.deleted_docs == []


def test_two_live_keys_with_one_doc_id_is_an_error():
    f = FakeClient()
    f.put("policy.md")
    f.put("policy.pdf")
    with pytest.raises(ValueError, match="same doc_id"):
        list(source(f).iter_versions())


def test_iteration_is_repeatable():
    f = FakeClient()
    f.put("p.md", meta=meta("v1"))
    s = source(f)
    assert [v.source_ref for v in s.iter_versions()] == [v.source_ref for v in s.iter_versions()]


def test_folders_layout():
    f = FakeClient()
    f.put("handbook/v1_2024-01-10.md", b"a")
    f.put("handbook/v2.md", b"b", when=datetime(2024, 6, 5, tzinfo=timezone.utc))
    f.put("handbook/notes.md")  # not a version file
    f.put("top.md")  # no folder
    out = list(source(f, layout="folders").iter_versions())
    assert [(v.doc_id, v.version, v.version_num, str(v.source_date)) for v in out] == [
        ("handbook", "v1", 1, "2024-01-10"), ("handbook", "v2", 2, "2024-06-05"),
    ]
    assert out[0].source_ref == "handbook/v1_2024-01-10.md"


def test_folders_layout_duplicate_number_is_an_error():
    f = FakeClient()
    f.put("h/v1.md")
    f.put("h/v1_2024-01-01.txt")
    with pytest.raises(ValueError, match="version 1"):
        list(source(f, layout="folders").iter_versions())


def test_bad_layout_and_bad_url():
    with pytest.raises(ValueError, match="layout"):
        source(FakeClient(), layout="tree")
    assert endpoint("https://s3.example.com:9000") == ("s3.example.com:9000", True)
    assert endpoint("http://127.0.0.1:9100/") == ("127.0.0.1:9100", False)
    with pytest.raises(ValueError):
        endpoint("127.0.0.1:9100")
    with pytest.raises(ValueError):
        endpoint("http://host/bucket")


def test_seed_text_matches_golden_truth():
    """The seeded texts differ exactly in the clauses data/golden/changes.json lists."""
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("scripts").resolve()))
    try:
        import seed_minio
    finally:
        sys.path.pop(0)
    truth = json.loads(Path("data/golden/changes.json").read_text(encoding="utf-8"))["it-security-policy"]["versions"]
    seeded = seed_minio.seed_versions()
    assert [(label, eff) for label, eff, _ in seeded] == [(t["version"], t["effective_date"]) for t in truth]

    def clauses(data: bytes) -> dict[str, str]:
        out = {}
        for line in data.decode().splitlines():
            head, _, _ = line.partition(" ")
            if "." in head and head.replace(".", "").isdigit():
                out[head] = line
        return out

    for (_, _, a), (_, _, b), t in zip(seeded, seeded[1:], truth[1:]):
        ca, cb = clauses(a), clauses(b)
        changed = {k for k in ca.keys() | cb.keys() if ca.get(k) != cb.get(k)}
        assert changed == set(t["changed"])
        for k, text in t["changed"].items():
            assert (cb.get(k) is None) if text is None else cb[k] == f"{k} {text}"
