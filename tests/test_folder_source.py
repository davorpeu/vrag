from datetime import date

from vrag.sources.folder import FolderSource


def test_parses_doc_id_version_and_date(tmp_path):
    d = tmp_path / "refund-policy"
    d.mkdir()
    (d / "v1.pdf").write_bytes(b"a")
    (d / "v2_2024-09-01.docx").write_bytes(b"b")
    (d / "notes.txt").write_bytes(b"ignored")

    versions = list(FolderSource(tmp_path).iter_versions())

    assert [(v.doc_id, v.version, v.version_num) for v in versions] == [
        ("refund-policy", "v1", 1),
        ("refund-policy", "v2", 2),
    ]
    assert versions[1].source_date == date(2024, 9, 1)
    assert versions[1].source_ref == "refund-policy/v2_2024-09-01.docx"
