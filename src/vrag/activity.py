"""Repo activity: "what was done recently, and in which files".

Separate from the document versions. A repo's commit history goes into its own ES index
(ACTIVITY_INDEX, default vrag-activity): one document per commit (sha, date, author, message, files with
+/- counts, embedding of message + file paths) and one small "repo" document per repo (what was loaded).

Answering: the time window comes from the question (or the caller), the commits come from ES, the file and
directory counts are computed here in code, and the LLM only summarises the themes from commit messages.
File lists and numbers never come from the LLM. Cited commit shas are checked against the commits it was given.

Clones: data/repos/_activity/<key>, made with `--no-checkout --shallow-since=<window start - 7 days>`.
Why: only the commits of the window are needed, and with a shallow clone every blob is local, so `git log
--numstat` is one fast local call (a blobless clone would fetch blobs one by one). No checkout = no working
tree on disk. The 7 extra days plus `fetch --deepen=1` give the oldest commit of the window a real parent, so its
numstat is a real diff. Commits listed in .git/shallow (no parent) are dropped anyway.
"""

import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from elasticsearch import helpers
from pydantic import BaseModel, Field

from vrag import store
from vrag.config import get_settings
from vrag.sources.remote import GITHUB, parse_link

DEFAULT_DAYS = 90  # how much history `sync` loads
MAX_COMMITS = 500  # commits read per sync
RECENT_DAYS = 14  # window when the question gives none ("recently")
MAX_FILES_PER_COMMIT = 500  # larger commits (vendoring, mass renames) keep their first files only
LLM_COMMITS = 100  # commit messages sent to the LLM at most
MAX_CITED_PER_THEME = 5  # the model tends to list dozens; a few clear examples read better
TOPIC_COMMITS = 25  # semantic search hits when the question names a topic
CLONE_ROOT = Path("data/repos/_activity")  # data/repos is gitignored


class ActivityError(ValueError):
    """The request can't be answered as asked (repo not loaded, bad link). Message is for the user."""


def index_name() -> str:
    return os.getenv("ACTIVITY_INDEX", "vrag-activity")


# -- repo identity ---------------------------------------------------------------------


@dataclass(frozen=True)
class Repo:
    key: str  # "owner/name" for GitHub, else the folder name
    clone_url: str
    github: str | None  # "https://github.com/owner/name" when on GitHub

    @property
    def path(self) -> Path:
        return CLONE_ROOT / self.key.replace("/", "__")

    def commit_url(self, sha: str) -> str | None:
        return f"{self.github}/commit/{sha}" if self.github else None


def repo_from_link(url: str) -> Repo:
    try:
        link = parse_link(url)
    except ValueError as e:
        raise ActivityError(str(e)) from e
    m = GITHUB.match(url.strip())
    if m:
        key = f"{m['owner']}/{m['repo']}"
        return Repo(key, link.clone_url, f"https://github.com/{key}")
    return Repo(link.name, link.clone_url, None)


# -- reading git -----------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None) -> str:
    cmd = ["git", "-c", "core.quotepath=off", *(["-C", str(cwd)] if cwd else []), *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def _deepen(path: Path) -> None:
    """One more level of parents: the oldest commit of the shallow clone then has a parent, so its numstat is
    a real diff and not 'the whole tree was added'. (No-op when the repo is already complete.)"""
    if (path / ".git" / "shallow").exists():
        _git("fetch", "--no-tags", "--deepen=1", "origin", cwd=path)


def fetch_repo(repo: Repo, since: date) -> str:
    """Clone (shallow, no checkout) or update the repo. Returns 'cloned' | 'updated'."""
    since_arg = f"--shallow-since={(since - timedelta(days=7)).isoformat()}"
    if (repo.path / ".git").exists():
        origin = _git("remote", "get-url", "origin", cwd=repo.path).strip()
        if re.sub(r"(\.git)?/?$", "", origin).lower() != re.sub(r"(\.git)?/?$", "", repo.clone_url).lower():
            raise ActivityError(f"{repo.path} is a clone of {origin}, not {repo.clone_url}. Remove that folder.")
        _git("fetch", "--no-tags", "--force", "--prune", since_arg, "origin", cwd=repo.path)
        _git("update-ref", "refs/heads/activity", "FETCH_HEAD", cwd=repo.path)
        _deepen(repo.path)
        return "updated"
    if repo.path.exists() and any(repo.path.iterdir()):
        raise ActivityError(f"{repo.path} exists and is not a git clone")
    repo.path.parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "--quiet", "--no-checkout", "--no-tags", since_arg, repo.clone_url, str(repo.path))
    _git("update-ref", "refs/heads/activity", "HEAD", cwd=repo.path)
    _deepen(repo.path)
    return "cloned"


@dataclass
class FileStat:
    path: str
    additions: int
    deletions: int


@dataclass
class Commit:
    sha: str
    date: str  # ISO 8601 with the author's offset
    author: str
    message: str  # subject + start of the body
    files: list[FileStat]
    files_total: int  # may be larger than len(files) for huge commits

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)


_REC, _FLD = "\x1e", "\x1f"


def read_commits(path: Path, since: date, max_commits: int = MAX_COMMITS) -> list[Commit]:
    """Newest first, from `refs/heads/activity`. Merges are skipped (their files show in the merged commits).
    Shallow-boundary commits are dropped: git has no parent for them, so they'd show the whole tree as added."""
    out = _git(
        "log", "refs/heads/activity", f"--since={since.isoformat()}", f"--max-count={max_commits}",
        "--no-merges", "--no-renames", "--numstat",
        f"--format={_REC}%H{_FLD}%aI{_FLD}%an{_FLD}%s{_FLD}%b",
        cwd=path,
    )
    shallow_file = path / ".git" / "shallow"
    boundary = set(shallow_file.read_text().split()) if shallow_file.exists() else set()
    commits = []
    for rec in out.split(_REC)[1:]:
        fields = rec.split(_FLD, 4)
        if len(fields) < 5 or fields[0].strip() in boundary:
            continue
        sha, when, author, subject, rest = fields
        # `rest` = body, then a blank line, then the numstat lines
        body_lines, files = [], []
        for line in rest.split("\n"):
            m = re.fullmatch(r"(\d+|-)\t(\d+|-)\t(.+)", line)
            if m:
                a, d, p = m.groups()
                files.append(FileStat(p, 0 if a == "-" else int(a), 0 if d == "-" else int(d)))
            elif line.strip():
                body_lines.append(line.strip())
        body = " ".join(body_lines)[:300]
        commits.append(
            Commit(sha.strip(), when, author, f"{subject}\n{body}".strip(), files[:MAX_FILES_PER_COMMIT], len(files))
        )
    return commits


# -- the index -------------------------------------------------------------------------


def _mapping(dims: int) -> dict:
    return {
        "properties": {
            "kind": {"type": "keyword"},  # "commit" | "repo"
            "repo": {"type": "keyword"},
            "sha": {"type": "keyword"},
            "date": {"type": "date"},
            "author": {"type": "keyword"},
            "message": {"type": "text"},
            "files": {"type": "object", "enabled": False},  # [{path, additions, deletions}], source only
            "files_total": {"type": "integer"},
            "additions": {"type": "integer"},
            "deletions": {"type": "integer"},
            "text": {"type": "text"},
            "embedding": {"type": "dense_vector", "dims": dims, "index": True, "similarity": "cosine"},
            # repo documents
            "clone_url": {"type": "keyword"},
            "github": {"type": "keyword"},
            "since": {"type": "date"},
            "synced_at": {"type": "date"},
            "commits": {"type": "integer"},
        }
    }


def ensure_index() -> None:
    es = store.es()
    if not es.indices.exists(index=index_name()):
        es.indices.create(index=index_name(), mappings=_mapping(get_settings().embed_dims))


def embed_text(c: Commit) -> str:
    paths = "\n".join(f.path for f in c.files[:40])
    return f"{store.DOC_PREFIX}{c.message}\nFiles:\n{paths}"[:1500]


@dataclass
class SyncResult:
    repo: str
    action: str = ""  # cloned | updated
    since: str = ""
    found: int = 0  # commits in the window
    indexed: int = 0  # new
    skipped: int = 0  # already indexed


def sync_repo(
    url: str,
    days: int = DEFAULT_DAYS,
    max_commits: int = MAX_COMMITS,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> SyncResult:
    """Load the last `days` days (at most `max_commits` commits). Idempotent by sha: known commits are not
    embedded again. on_progress(phase, done, total) with phase downloading | reading | embedding."""
    progress = on_progress or (lambda *a: None)
    repo = repo_from_link(url)
    since = date.today() - timedelta(days=days)
    progress("downloading", 0, 0)
    try:
        action = fetch_repo(repo, since)
        progress("reading", 0, 0)
        commits = read_commits(repo.path, since, max_commits)
    except RuntimeError as e:
        raise ActivityError(str(e)) from e
    ensure_index()
    es = store.es()
    ids = {c.sha: f"{repo.key}:{c.sha}" for c in commits}
    known: set[str] = set()
    if ids:
        res = es.search(
            index=index_name(), size=len(ids), source=False,
            query={"bool": {"filter": [{"term": {"kind": "commit"}}, {"ids": {"values": list(ids.values())}}]}},
        )
        known = {h["_id"] for h in res["hits"]["hits"]}
    new = [c for c in commits if ids[c.sha] not in known]
    result = SyncResult(repo.key, action, since.isoformat(), len(commits), len(new), len(commits) - len(new))
    batch = 32
    for i in range(0, len(new), batch):
        progress("embedding", i, len(new))
        part = new[i : i + batch]
        vectors = store.embedder().embed_documents([embed_text(c) for c in part])
        helpers.bulk(
            es,
            (
                {
                    "_index": index_name(), "_id": ids[c.sha],
                    "_source": {
                        "kind": "commit", "repo": repo.key, "sha": c.sha, "date": c.date, "author": c.author,
                        "message": c.message, "files": [vars(f) for f in c.files], "files_total": c.files_total,
                        "additions": c.additions, "deletions": c.deletions, "text": embed_text(c)[len(store.DOC_PREFIX):],
                        "embedding": v,
                    },
                }
                for c, v in zip(part, vectors)
            ),
            refresh=True,
        )
    progress("embedding", len(new), len(new))
    # repo document: keep the earliest `since` ever loaded, so a later short sync doesn't shrink the coverage
    try:
        old = es.get(index=index_name(), id=f"repo:{repo.key}")["_source"]
    except Exception:  # noqa: BLE001 - not found
        old = None
    covered = min(since.isoformat(), old["since"][:10]) if old else since.isoformat()
    total = es.count(index=index_name(), query={"bool": {"filter": [{"term": {"kind": "commit"}}, {"term": {"repo": repo.key}}]}})["count"]
    es.index(
        index=index_name(), id=f"repo:{repo.key}", refresh=True,
        document={
            "kind": "repo", "repo": repo.key, "clone_url": repo.clone_url, "github": repo.github,
            "since": covered, "synced_at": datetime.now(timezone.utc).isoformat(), "commits": total,
        },
    )
    return result


class RepoInfo(BaseModel):
    repo: str
    url: str | None  # GitHub address, if on GitHub
    since: str  # history is loaded from this day on
    synced_at: str
    commits: int


def list_repos() -> list[RepoInfo]:
    if not store.es().indices.exists(index=index_name()):
        return []
    res = store.es().search(index=index_name(), size=200, query={"term": {"kind": "repo"}}, sort=[{"repo": "asc"}])
    return [
        RepoInfo(
            repo=s["repo"], url=s.get("github") or s.get("clone_url"), since=s["since"][:10],
            synced_at=s["synced_at"], commits=s["commits"],
        )
        for s in (h["_source"] for h in res["hits"]["hits"])
    ]


def _find_repo(repo: str) -> tuple[Repo, dict]:
    """Resolve a repo key, name or link to its loaded repo document."""
    candidates = [repo.strip()]
    try:
        candidates.append(repo_from_link(repo).key)
    except ActivityError:
        pass
    infos = {i.repo: i for i in list_repos()}
    for c in candidates:
        if c in infos:
            i = infos[c]
            return Repo(i.repo, i.url or "", i.url if i.url and "github.com" in i.url else None), i.model_dump()
    short = [i for k, i in infos.items() if k.rsplit("/", 1)[-1] == candidates[0]]
    if len(short) == 1:
        i = short[0]
        return Repo(i.repo, i.url or "", i.url if i.url and "github.com" in i.url else None), i.model_dump()
    raise ActivityError(f"{repo!r} is not loaded yet. Load it first (vrag activity sync --url ...).")


# -- the time window -------------------------------------------------------------------

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
)}
MONTHS.update({m[:3]: n for m, n in list(MONTHS.items())})
MONTHS["sept"] = 9
UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "twelve": 12}


@dataclass(frozen=True)
class Window:
    since: date | None  # inclusive
    until: date | None  # inclusive
    last_n: int | None  # "last 20 commits": newest N, no dates
    label: str  # for the user: "last 14 days", "since 1 March 2026"
    explicit: bool  # False = the default window was used


def _month_start(month: int, today: date) -> date:
    d = date(today.year, month, 1)
    return d if d <= today else date(today.year - 1, month, 1)


def parse_window(question: str, today: date | None = None) -> Window:
    """Read the period from the question. 'recently' and no period at all = the last 14 days."""
    today = today or date.today()
    q = question.lower()
    num = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve)"

    def n_of(s: str) -> int:
        return int(s) if s.isdigit() else NUMBER_WORDS[s]

    m = re.search(rf"\b(?:last|latest|past|most recent|recent)\s+{num}?\s*commits?\b", q)
    if m:
        n = n_of(m[1]) if m[1] else 10
        return Window(None, None, n, f"last {n} commits", True)
    m = re.search(rf"\b(?:last|past|previous)\s+{num}?\s*(day|week|month|year)s?\b", q)
    if m:
        n = n_of(m[1]) if m[1] else 1
        days = n * UNIT_DAYS[m[2]]
        label = f"last {n} {m[2]}{'s' if n != 1 else ''}" if m[1] else f"last {m[2]}"
        return Window(today - timedelta(days=days), today, None, label, True)
    if re.search(r"\byesterday\b", q):
        d = today - timedelta(days=1)
        return Window(d, d, None, "yesterday", True)
    if re.search(r"\btoday\b", q):
        return Window(today, today, None, "today", True)
    if re.search(r"\bthis week\b", q):
        return Window(today - timedelta(days=today.weekday()), today, None, "this week", True)
    if re.search(r"\bthis month\b", q):
        return Window(today.replace(day=1), today, None, "this month", True)
    if re.search(r"\bthis year\b", q):
        return Window(date(today.year, 1, 1), today, None, "this year", True)
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", q)
    if m:
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return Window(d, today, None, f"since {d.isoformat()}", True)
        except ValueError:
            pass
    names = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b(since|in|during|from)\s+(?:the\s+)?({names})\b(?:\s+(\d{{1,2}})(?:st|nd|rd|th)?\b)?(?:,?\s+(\d{{4}}))?", q)
    if m:
        month = MONTHS[m[2]]
        year = int(m[4]) if m[4] else None
        if m[3] and not m[4] and int(m[3]) > 31:
            year, day = int(m[3]), 1
        else:
            day = int(m[3]) if m[3] and int(m[3]) <= 31 else 1
        try:
            start = date(year, month, day) if year else _month_start(month, today).replace(day=day)
        except ValueError:
            start = None
        if start:
            if m[1] == "since" or m[1] == "from":
                return Window(start, today, None, f"since {start.day} {start.strftime('%B %Y')}", True)
            nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1) - timedelta(days=1)
            return Window(start.replace(day=1), min(nxt, today), None, f"{start.strftime('%B %Y')}", True)
    if re.search(r"\brecent(?:ly)?\b", q):
        return Window(today - timedelta(days=RECENT_DAYS), today, None, f"last {RECENT_DAYS} days (recently)", False)
    return Window(today - timedelta(days=RECENT_DAYS), today, None, f"last {RECENT_DAYS} days (default)", False)


STOP = set(
    """what whats which who how why when where was were is are has have had been being be did does do done doing
    the and for from with about into over under that this these those there their them they then than its
    can could would should will shall may might must any all some many much more most other another each every
    describe tell show give list summarize summarise summary explain happened happen happening changed change changes
    changing work worked working going recent recently lately latest last past previous since during today yesterday
    week weeks month months year years day days commit commits file files folder folders code repo repository project
    please made make makes new newly updated update updates touched touch modified main changing activity active
    mostly especially mainly just also still now area areas active actively focus focused biggest""".split()
)


def topic_of(question: str, repo_key: str) -> str:
    """Words left after removing time words, filler and the repo's own name; '' = no topic (just 'what happened')."""
    skip = {w for part in re.split(r"[/_.-]", repo_key.lower()) for w in [part]}
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_+#-]{2,}", question):
        lw = w.lower()
        if lw in STOP or lw in MONTHS or lw in skip or lw in NUMBER_WORDS or lw in UNIT_DAYS:
            continue
        words.append(w)
    return " ".join(words)


# -- fetching and counting -------------------------------------------------------------


class FileChange(BaseModel):
    path: str
    commits: int
    additions: int
    deletions: int


class CommitInfo(BaseModel):
    sha: str
    short: str
    date: str
    author: str
    message: str  # first line
    url: str | None
    files_changed: int
    additions: int
    deletions: int


def _window_filter(repo_key: str, w: Window) -> list[dict]:
    f: list[dict] = [{"term": {"kind": "commit"}}, {"term": {"repo": repo_key}}]
    rng = {}
    if w.since:
        rng["gte"] = f"{w.since.isoformat()}T00:00:00Z"
    if w.until:
        rng["lte"] = f"{w.until.isoformat()}T23:59:59Z"
    if rng:
        f.append({"range": {"date": rng}})
    return f


def _to_commit(src: dict) -> Commit:
    return Commit(
        src["sha"], src["date"], src["author"], src["message"],
        [FileStat(**f) for f in src["files"]], src["files_total"],
    )


def window_commits(repo_key: str, w: Window) -> tuple[list[Commit], int]:
    """(commits newest first, total in the window). A last-N window returns N and the number returned."""
    es = store.es()
    size = w.last_n or MAX_COMMITS
    res = es.search(
        index=index_name(), size=min(size, 10000), track_total_hits=True,
        query={"bool": {"filter": _window_filter(repo_key, w)}}, sort=[{"date": "desc"}],
    )
    commits = [_to_commit(h["_source"]) for h in res["hits"]["hits"]]
    total = res["hits"]["total"]["value"]
    return commits, len(commits) if w.last_n else total


def topic_commits(repo_key: str, w: Window, topic: str, k: int = TOPIC_COMMITS) -> list[Commit]:
    vec = store.embedder().embed_query(f"{store.QUERY_PREFIX}{topic}")
    res = store.es().search(
        index=index_name(), size=k, source_excludes=["embedding"],
        knn={"field": "embedding", "query_vector": vec, "k": k, "num_candidates": max(100, k * 4),
             "filter": _window_filter(repo_key, w)},
    )
    commits = [_to_commit(h["_source"]) for h in res["hits"]["hits"]]
    return sorted(commits, key=lambda c: c.date, reverse=True)


def _dir_of(path: str) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:2]) + "/" if parts else "(top level)"


def aggregate(commits: list[Commit]) -> tuple[list[FileChange], list[FileChange]]:
    """Files and directories ranked by number of commits touching them (then lines changed). Plain counting."""
    def rank(stats: dict[str, list[int]]) -> list[FileChange]:
        rows = [FileChange(path=p, commits=v[0], additions=v[1], deletions=v[2]) for p, v in stats.items()]
        return sorted(rows, key=lambda r: (-r.commits, -(r.additions + r.deletions), r.path))

    files: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    dirs: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for c in commits:
        seen_dirs: set[str] = set()
        for f in c.files:
            s = files[f.path]
            s[0] += 1
            s[1] += f.additions
            s[2] += f.deletions
            d = _dir_of(f.path)
            ds = dirs[d]
            ds[1] += f.additions
            ds[2] += f.deletions
            if d not in seen_dirs:  # a commit counts once per directory
                ds[0] += 1
                seen_dirs.add(d)
    return rank(files), rank(dirs)


# -- the answer ------------------------------------------------------------------------


class Theme(BaseModel):
    title: str = Field(description="Short name of the theme, 2-6 words")
    description: str = Field(description="One or two sentences about what was done, from the commit messages only")
    commits: list[str] = Field(description="Short shas (as given) of the commits that show this theme")


class ThemeSummary(BaseModel):
    summary: str = Field(description="Two or three sentences: the overall picture of the work")
    themes: list[Theme]


class WindowInfo(BaseModel):
    since: str | None
    until: str | None
    last_n: int | None
    label: str


class Citation(BaseModel):
    sha: str
    short: str
    url: str | None
    message: str


class ActivityAnswer(BaseModel):
    repo: str
    question: str
    answer: str
    window: WindowInfo
    topic: str  # '' when the question names none
    commits_in_window: int  # all commits of the window
    commits_used: int  # commits the numbers below are about (topic search narrows it)
    authors: int
    first_commit: str | None
    last_commit: str | None
    files: list[FileChange]  # top files by commits; counted in code
    files_total: int
    directories: list[FileChange]
    commits: list[CommitInfo]
    citations: list[Citation]  # commits the summary cites
    insufficient_context: bool
    notes: list[str] = []  # coverage, sampling, fallbacks


PROMPT = """You describe what was done in a software repository, using ONLY the commit messages below.
Rules:
- Group the work into 2-6 themes. Each theme cites the short shas of the commits that show it, exactly as written.
- Say only what the messages say. Do not guess about code you cannot see.
- Cite at most 5 commits per theme: the clearest examples. Put each commit in the one theme it fits best.
- The commits are already the ones to describe. Never say that nothing was done or that nothing was found; if a topic is
  given, the commits are the ones most related to it, so describe what they show.
- Do not write any counts or numbers of commits or files, and do not list file names: those come from the data.
- Plain, short English.

Repository: {repo}
Period: {period}
{topic_line}
Question: {question}

Commits (short sha | date | author | message):
{commits}
"""


def _llm_summary(prompt_vars: dict) -> ThemeSummary:
    from langchain_ollama import ChatOllama

    s = get_settings()
    llm = ChatOllama(model=s.llm_model, base_url=s.ollama_base_url, temperature=0, num_ctx=6144)
    return llm.with_structured_output(ThemeSummary).invoke(PROMPT.format(**prompt_vars))


def _commit_line(c: Commit) -> str:
    first = c.message.split("\n", 1)[0][:140]
    return f"{c.sha[:7]} | {c.date[:10]} | {c.author} | {first}"


def _sample(commits: list[Commit], n: int) -> list[Commit]:
    if len(commits) <= n:
        return commits
    step = len(commits) / n
    return [commits[int(i * step)] for i in range(n)]


SHA_LIKE = re.compile(r"\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b")  # needs a digit: "decade", "facade" are words
NOTHING = re.compile(r"\b(no|nothing|none|not any|didn't|did not)\b", re.I)


def clean_text(text: str) -> str:
    """Model text without commit shas or 'Commits: ...' lists (the cited shas are added by the code)."""
    text = re.sub(r"\s*\bCommits?:[\s0-9a-f,]*", " ", text)
    text = SHA_LIKE.sub("", text)
    text = re.sub(r"\(\s*[,\s]*\)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return re.sub(r"\s+([,.;])", r"\1", text).strip()


def _info(repo: Repo, c: Commit) -> CommitInfo:
    return CommitInfo(
        sha=c.sha, short=c.sha[:7], date=c.date, author=c.author, message=c.message.split("\n", 1)[0],
        url=repo.commit_url(c.sha), files_changed=c.files_total, additions=c.additions, deletions=c.deletions,
    )


def _plain_answer(commits: list[Commit]) -> str:
    return "Commit messages: " + "; ".join(f"{c.message.split(chr(10), 1)[0]} ({c.sha[:7]})" for c in commits[:8]) + "."


def ask(
    question: str, repo: str, days: int | None = None, last_n: int | None = None, today: date | None = None
) -> ActivityAnswer:
    """Answer 'what was done recently, in which files' for a loaded repo. days / last_n override the window."""
    repo_obj, info = _find_repo(repo)
    today = today or date.today()
    w = parse_window(question, today)
    if last_n:
        w = Window(None, None, last_n, f"last {last_n} commits", True)
    elif days:
        w = Window(today - timedelta(days=days), today, None, f"last {days} days", True)
    notes: list[str] = []
    if w.since and w.since.isoformat() < info["since"]:
        notes.append(
            f"Only history since {info['since']} is loaded, so the start of the period ({w.since.isoformat()}) is "
            "not covered. Load more with a longer window (vrag activity sync --days N)."
        )
    commits, total = window_commits(repo_obj.key, w)
    topic = topic_of(question, repo_obj.key)
    used = commits
    if topic and commits:
        hits = topic_commits(repo_obj.key, w, topic)
        if hits:
            used = hits
            notes.append(f"The question names a topic ({topic}): showing the {len(hits)} commits most related to it.")
    if not used:
        return ActivityAnswer(
            repo=repo_obj.key, question=question,
            answer=f"No commits found in {repo_obj.key} for {w.label}.",
            window=WindowInfo(since=w.since and w.since.isoformat(), until=w.until and w.until.isoformat(),
                           last_n=w.last_n, label=w.label),
            topic=topic, commits_in_window=0, commits_used=0, authors=0, first_commit=None, last_commit=None,
            files=[], files_total=0, directories=[], commits=[], citations=[], insufficient_context=True,
            notes=notes,
        )
    files, dirs = aggregate(used)
    sent = _sample(used, LLM_COMMITS)
    if len(sent) < len(used):
        notes.append(f"The summary is based on a spread sample of {len(sent)} of the {len(used)} commit messages.")
    by_short = {c.sha[:7]: c for c in sent}
    try:
        summary = _llm_summary({
            "repo": repo_obj.key, "period": w.label, "question": question,
            "topic_line": f"Topic of the question: {topic}" if topic else "",
            "commits": "\n".join(_commit_line(c) for c in sent),
        })
    except Exception as e:  # noqa: BLE001 - model down: still give the data
        summary = None
        notes.append(f"The summary model was not available ({type(e).__name__}); showing the commit messages instead.")
    cited: dict[str, Commit] = {}
    parts = []
    if summary:
        for t in summary.themes:
            shas = [s.strip()[:7] for s in t.commits if s.strip()[:7] in by_short]
            if not shas:
                continue  # a theme with no real commit behind it is dropped
            shas = list(dict.fromkeys(shas))[:MAX_CITED_PER_THEME]
            cited.update({s: by_short[s] for s in shas})
            desc = clean_text(t.description)
            parts.append(f"- **{t.title.strip()}**: {desc + ' ' if desc else ''}({', '.join(shas)})")
    if parts:
        head = clean_text(summary.summary)
        if not head or NOTHING.search(head):  # "no changes were made" above a list of changes: don't show it
            head = f"Work in {w.label}" + (f' most related to "{topic}"' if topic else "") + ":"
        text = f"{head}\n\n" + "\n".join(parts)
    else:
        text = _plain_answer(used)
        cited = {c.sha[:7]: c for c in used[:8]}
    if files:
        top = ", ".join(f"{f.path} ({f.commits})" for f in files[:5])
        text += f"\n\nMost changed files (commits touching them): {top}."
    authors = {c.author for c in used}
    return ActivityAnswer(
        repo=repo_obj.key, question=question, answer=text,
        window=WindowInfo(since=w.since and w.since.isoformat(), until=w.until and w.until.isoformat(),
                       last_n=w.last_n, label=w.label),
        topic=topic, commits_in_window=total, commits_used=len(used), authors=len(authors),
        first_commit=min(c.date for c in used)[:10], last_commit=max(c.date for c in used)[:10],
        files=files[:30], files_total=len(files), directories=dirs[:10],
        commits=[_info(repo_obj, c) for c in used[:60]],
        citations=[Citation(sha=c.sha, short=s, url=repo_obj.commit_url(c.sha), message=c.message.split("\n", 1)[0])
                   for s, c in cited.items()],
        insufficient_context=False, notes=notes,
    )
