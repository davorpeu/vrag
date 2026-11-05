import subprocess

import pytest

from vrag.sources.git import GitSource


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    doc = tmp_path / "docs" / "policy.md"
    doc.parent.mkdir()
    for i, (text, tag, ts) in enumerate(
        [("# P\nv one\n", "v1.0", "2020-01-01T00:00:00"), ("# P\nv two\n", None, "2021-01-01T00:00:00"), ("# P\nv three\n", "v2.0", "2022-01-01T00:00:00")]
    ):
        doc.write_bytes(text.encode())
        git(tmp_path, "add", "-A")
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"c{i}"],
            check=True, capture_output=True, env={**__import__("os").environ, "GIT_COMMITTER_DATE": ts, "GIT_AUTHOR_DATE": ts},
        )
        if tag:
            git(tmp_path, "tag", "-a", tag, "-m", tag)  # annotated: must peel to the commit
    (tmp_path / "other.md").write_bytes(b"x")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "unrelated")
    git(tmp_path, "tag", "v2.1")  # lightweight tag; policy.md unchanged since v2.0
    return tmp_path


def test_tags_mode(repo):
    vs = list(GitSource(repo, ["docs/policy.md"], by="tags", doc_prefix="r").iter_versions())
    assert [(v.version, v.version_num) for v in vs] == [("v1.0", 1), ("v2.0", 2), ("v2.1", 3)]
    assert {v.doc_id for v in vs} == {"r/docs/policy"}
    assert vs[0].content == b"# P\nv one\n"
    assert str(vs[1].source_date) == "2022-01-01"
    assert vs[0].filename == "policy.md" and vs[0].source_ref.endswith(":docs/policy.md")


def test_commits_mode_labels_tagged_commits(repo):
    vs = list(GitSource(repo, ["docs/policy.md"], by="commits", doc_prefix="r").iter_versions())
    assert [v.version_num for v in vs] == [1, 2, 3]
    assert vs[0].version == "v1.0" and vs[2].version == "v2.0"
    assert len(vs[1].version) == 8  # untagged commit -> short sha
    assert vs[1].content == b"# P\nv two\n"


def test_commits_mode_follows_renames(repo):
    git(repo, "mv", "docs/policy.md", "docs/renamed.md")
    git(repo, "commit", "-q", "-m", "rename")
    vs = list(GitSource(repo, ["docs/renamed.md"], by="commits", doc_prefix="r").iter_versions())
    assert [v.content for v in vs][:3] == [b"# P\nv one\n", b"# P\nv two\n", b"# P\nv three\n"]
    assert vs[0].source_ref.endswith(":docs/policy.md")
