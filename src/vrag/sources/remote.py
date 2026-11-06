"""Turn a pasted repository link into a local clone that GitSource can read.

Accepted links:
- https://github.com/owner/repo            (also .git, /tree/<branch>, trailing slash)
- https://github.com/owner/repo/blob/<branch>/path/to/file.md   -> also yields the file path
- any other git URL or local path git can clone (GitLab, Bitbucket, file paths)

Clones go to data/repos/<repo name> (gitignored, on D:). A second sync of the same link
pulls instead of cloning, so new tags/commits show up and only new versions get indexed.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

GITHUB = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?"
    r"(?:/(?:blob|tree|raw)/(?P<ref>[^/]+)(?:/(?P<path>.+?))?)?/?(?:[?#].*)?$"
)


@dataclass(frozen=True)
class RepoLink:
    clone_url: str
    name: str  # folder name under data/repos, also the doc_id prefix
    file: str | None = None  # set when the link pointed at one file


def parse_link(url: str) -> RepoLink:
    url = url.strip()
    m = GITHUB.match(url)
    if m:
        clone = f"https://github.com/{m['owner']}/{m['repo']}.git"
        is_file = re.search(r"/(blob|raw)/", url) is not None
        path = m["path"] if is_file and m["path"] else None
        return RepoLink(clone, m["repo"], path)
    name = re.sub(r"\.git$", "", url.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1])
    if not name:
        raise ValueError(f"can't tell the repository name from {url!r}")
    return RepoLink(url, name)


def _norm_remote(url: str) -> str:
    return re.sub(r"(\.git)?/?$", "", url.strip()).lower().replace("\\", "/")


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def ensure_clone(link: RepoLink, root: str | Path = "data/repos") -> tuple[Path, str]:
    """Clone the repo, or pull it if already cloned. Returns (path, 'cloned' | 'updated')."""
    dest = Path(root) / link.name
    if (dest / ".git").exists():
        origin = _git("-C", str(dest), "remote", "get-url", "origin").strip()
        if _norm_remote(origin) != _norm_remote(link.clone_url):
            raise RuntimeError(
                f"{dest} is already a clone of {origin}, not {link.clone_url}. "
                "Remove that folder or use --repo to point at a different clone."
            )
        _git("-C", str(dest), "fetch", "--tags", "--force", "--prune", "origin")
        _git("-C", str(dest), "pull", "--ff-only")
        return dest, "updated"
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(f"{dest} exists and is not a git clone")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "--quiet", link.clone_url, str(dest))
    return dest, "cloned"


DOC_EXT = {".md", ".markdown", ".txt", ".pdf", ".docx"}


def suggest_files(repo: Path, limit: int = 15) -> list[str]:
    """Document-like files in the repo, shallowest first, for the 'which file?' hint."""
    files = _git("-C", str(repo), "ls-files").splitlines()
    docs = [f for f in files if Path(f).suffix.lower() in DOC_EXT]
    return sorted(docs, key=lambda f: (f.count("/"), f.lower()))[:limit]
