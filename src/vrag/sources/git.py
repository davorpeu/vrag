"""Versions of files tracked in a local git repository.

Two ways to cut versions:
- "tags": one version per tag that contains the file, ordered by the tagged commit's date.
  Version label = tag name. Best for released documents (specs, policies with releases).
- "commits": one version per commit that touched the file (renames followed).
  Version label = short SHA, or the tag name when a tag points at that commit.

Doc identity = "<repo dir name>/<path without extension>" of the file as requested,
so it stays stable across renames in commits mode.
"""

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from vrag.models import DocVersion
from vrag.sources.base import VersionSource


@dataclass(frozen=True)
class _Rev:
    label: str
    sha: str
    path: str  # path of the file at that revision
    ts: int  # commit timestamp, ordering key
    day: str  # committer's own calendar date (git %cs), not UTC


class GitSource(VersionSource):
    name = "git"

    def __init__(self, repo: str | Path, files: list[str], by: str = "tags", doc_prefix: str | None = None):
        if by not in ("tags", "commits"):
            raise ValueError("by must be 'tags' or 'commits'")
        self.repo = Path(repo)
        self.files = [f.replace("\\", "/") for f in files]
        self.by = by
        self.doc_prefix = doc_prefix or self.repo.resolve().name

    # -- git plumbing -------------------------------------------------------
    def _git(self, *args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True
        ).stdout

    def _exists(self, sha: str, path: str) -> bool:
        r = subprocess.run(
            ["git", "-C", str(self.repo), "cat-file", "-e", f"{sha}:{path}"], capture_output=True
        )
        return r.returncode == 0

    def _tags_by_commit(self) -> dict[str, str]:
        out = self._git("for-each-ref", "refs/tags", "--format=%(refname:short) %(objectname) %(*objectname)")
        tags: dict[str, str] = {}
        for line in out.decode().splitlines():
            name, obj, peeled = (line.split(" ") + [""])[:3]
            tags.setdefault(peeled or obj, name)  # annotated tags peel to the commit
        return tags

    # -- revisions ----------------------------------------------------------
    def _tag_revs(self, path: str) -> list[_Rev]:
        revs = []
        for sha, tag in self._tags_by_commit().items():
            if self._exists(sha, path):
                ts, day = self._git("log", "-1", "--format=%ct %cs", sha).decode().split()
                revs.append(_Rev(tag, sha, path, int(ts), day))
        return sorted(revs, key=lambda r: (r.ts, r.label))

    def _commit_revs(self, path: str) -> list[_Rev]:
        tags = self._tags_by_commit()
        out = self._git("log", "--follow", "--name-only", "--format=@%H %ct %cs", "--", path).decode()
        revs, sha, ts, day = [], None, 0, ""
        for line in out.splitlines():
            if line.startswith("@"):
                sha, ts_s, day = line[1:].split()
                ts = int(ts_s)
            elif line.strip() and sha:
                p = line.strip()
                if self._exists(sha, p):  # skip the commit that deleted the file
                    revs.append(_Rev(tags.get(sha, sha[:8]), sha, p, ts, day))
                sha = None
        return list(reversed(revs))  # git log is newest-first

    def preview(self, path: str) -> tuple[int, int]:
        """(number of versions, size in bytes of the newest one) for a file, without reading every version."""
        revs = self._tag_revs(path) if self.by == "tags" else self._commit_revs(path)
        if not revs:
            return 0, 0
        size = int(self._git("cat-file", "-s", f"{revs[-1].sha}:{revs[-1].path}").decode().strip())
        return len(revs), size

    def iter_versions(self) -> Iterator[DocVersion]:
        for f in self.files:
            revs = self._tag_revs(f) if self.by == "tags" else self._commit_revs(f)
            doc_id = f"{self.doc_prefix}/{PurePosixPath(f).with_suffix('')}"
            for num, r in enumerate(revs, start=1):
                yield DocVersion(
                    doc_id=doc_id,
                    version=r.label,
                    version_num=num,
                    source=self.name,
                    source_ref=f"{r.sha[:12]}:{r.path}",
                    filename=PurePosixPath(r.path).name,
                    content=self._git("cat-file", "blob", f"{r.sha}:{r.path}"),
                    source_date=date.fromisoformat(r.day),
                )

