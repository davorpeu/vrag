import subprocess

import pytest

from vrag.sources.git import GitSource
from vrag.sources.remote import RepoLink, ensure_clone, parse_link, suggest_files


@pytest.mark.parametrize(
    "url,clone,name,file",
    [
        ("https://github.com/semver/semver", "https://github.com/semver/semver.git", "semver", None),
        ("https://github.com/semver/semver.git", "https://github.com/semver/semver.git", "semver", None),
        ("github.com/semver/semver/", "https://github.com/semver/semver.git", "semver", None),
        ("https://github.com/semver/semver/tree/master", "https://github.com/semver/semver.git", "semver", None),
        ("https://github.com/semver/semver/tree/master/lang", "https://github.com/semver/semver.git", "semver", None),
        ("https://github.com/semver/semver/blob/master/semver.md", "https://github.com/semver/semver.git", "semver", "semver.md"),
        ("https://github.com/o/my.repo/blob/main/docs/a b.md?plain=1#L5", "https://github.com/o/my.repo.git", "my.repo", "docs/a b.md"),
        ("  https://www.github.com/o/r/raw/v1/x/y.txt ", "https://github.com/o/r.git", "r", "x/y.txt"),
        ("https://gitlab.com/group/proj.git", "https://gitlab.com/group/proj.git", "proj", None),
        ("D:\\clones\\thing\\", "D:\\clones\\thing\\", "thing", None),
    ],
)
def test_parse_link(url, clone, name, file):
    assert parse_link(url) == RepoLink(clone, name, file)


def git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def upstream(tmp_path):
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    (up / "policy.md").write_bytes(b"# P\none\n")
    (up / "docs").mkdir()
    (up / "docs" / "deep.md").write_bytes(b"x")
    (up / "code.py").write_bytes(b"x")
    git(up, "add", "-A")
    git(up, "commit", "-q", "-m", "c1")
    git(up, "tag", "v1")
    return up


def test_clone_then_pull_picks_up_new_tag(upstream, tmp_path):
    link = parse_link(str(upstream))
    root = tmp_path / "repos"
    dest, how = ensure_clone(link, root)
    assert how == "cloned" and dest == root / "upstream"
    assert [v.version for v in GitSource(dest, ["policy.md"]).iter_versions()] == ["v1"]

    (upstream / "policy.md").write_bytes(b"# P\ntwo\n")
    git(upstream, "commit", "-q", "-am", "c2")
    git(upstream, "tag", "v2")
    dest2, how = ensure_clone(link, root)
    assert how == "updated" and dest2 == dest
    vs = list(GitSource(dest, ["policy.md"]).iter_versions())
    assert [v.version for v in vs] == ["v1", "v2"] and vs[-1].content == b"# P\ntwo\n"


def test_same_folder_other_origin_is_refused(upstream, tmp_path):
    root = tmp_path / "repos"
    ensure_clone(parse_link(str(upstream)), root)
    with pytest.raises(RuntimeError, match="already a clone of"):
        ensure_clone(RepoLink("https://github.com/someone/upstream.git", "upstream"), root)


def test_bad_url_is_a_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="git clone"):
        ensure_clone(RepoLink(str(tmp_path / "nope"), "nope"), tmp_path / "repos")


def test_suggest_files_lists_docs_shallowest_first(upstream):
    assert suggest_files(upstream) == ["policy.md", "docs/deep.md"]
