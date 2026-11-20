from dotenv import load_dotenv

load_dotenv()  # Langfuse SDK reads LANGFUSE_* from os.environ

import threading  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from typing import Literal  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from vrag import activity, answer, store  # noqa: E402
from vrag.models import AskResponse  # noqa: E402
from vrag.sources.git import GitSource  # noqa: E402
from vrag.sources.remote import clone_path, ensure_clone, parse_link, suggest_files  # noqa: E402
from vrag.sync import sync as run_sync  # noqa: E402

app = FastAPI(title="versioned-rag")


class AskRequest(BaseModel):
    question: str
    doc_id: str | None = None  # restrict the question to one document


@app.get("/health")
def health() -> dict:
    return {"elasticsearch": store.es().ping()}


@app.get("/documents")
def documents() -> list[dict]:
    """Indexed documents with their versions, oldest first."""
    return store.catalog()


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    try:
        return answer.ask(req.question, req.doc_id)
    finally:
        answer.flush()


# -- adding a git repo from a pasted link -------------------------------------


class GitLinkRequest(BaseModel):
    url: str


class GitInspectResponse(BaseModel):
    repo: str  # local clone path
    name: str
    action: Literal["cloned", "updated"]
    file: str | None  # file the link pointed at, if any
    candidates: list[str]  # document-like files in the repo
    tags: int  # number of tags in the repo (= versions per file in "tags" mode, at most)


class GitSyncRequest(BaseModel):
    url: str
    files: list[str] = Field(min_length=1)
    by: Literal["tags", "commits"] = "tags"


class FilePreview(BaseModel):
    file: str
    versions: int
    size_kb: int  # newest version


# Above this a load takes long with the local model (rough: 10-60 s per version, more for big files).
BIG_VERSIONS = 30
BIG_KB = 100


@app.post("/sources/git/preview", response_model=list[FilePreview])
def git_preview(req: GitSyncRequest) -> list[FilePreview]:
    """How many versions each file would get, from the clone made by /inspect (no download)."""
    try:
        path = clone_path(parse_link(req.url))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not (path / ".git").exists():
        raise HTTPException(400, "Check the link first (repository not downloaded yet).")
    out = []
    for f in req.files:
        n, size = GitSource(path, [f], by=req.by).preview(f.replace("\\", "/"))
        out.append(FilePreview(file=f, versions=n, size_kb=-(-size // 1024)))
    return out


class Job(BaseModel):
    id: str
    status: Literal["running", "done", "error"] = "running"
    current: str | None = None  # version being handled now
    done_count: int = 0
    started: float = Field(default_factory=time.time)
    finished: float | None = None
    error: str | None = None
    indexed: list[str] = []
    skipped: list[str] = []
    renumbered: list[str] = []
    removed: list[str] = []
    empty: list[str] = []


JOBS: dict[str, Job] = {}
_sync_lock = threading.Lock()  # one sync at a time: shares the local model and the index


def _clone(url: str):
    try:
        link = parse_link(url)
        path, action = ensure_clone(link)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from e
    return link, path, action


@app.post("/sources/git/inspect", response_model=GitInspectResponse)
def git_inspect(req: GitLinkRequest) -> GitInspectResponse:
    """Clone (or pull) the repo and list the files that could be versioned."""
    link, path, action = _clone(req.url)
    tags = len(GitSource(path, [])._tags_by_commit())
    return GitInspectResponse(
        repo=str(path), name=link.name, action=action, file=link.file, candidates=suggest_files(path, 50), tags=tags
    )


def _run_job(job: Job, src: GitSource) -> None:
    def progress(label: str) -> None:
        if job.current:
            job.done_count += 1
        job.current = label

    try:
        r = run_sync(src, on_progress=progress)
        job.indexed, job.skipped, job.renumbered, job.empty = r.indexed, r.skipped, r.renumbered, r.empty
        job.removed = r.removed
        job.done_count = len(r.indexed) + len(r.skipped) + len(r.renumbered) + len(r.empty)
        job.status = "done"
    except Exception as e:  # noqa: BLE001 - reported to the UI
        job.status, job.error = "error", f"{type(e).__name__}: {e}"
    finally:
        job.current, job.finished = None, time.time()
        _sync_lock.release()


@app.post("/sources/git/sync", response_model=Job, status_code=202)
def git_sync(req: GitSyncRequest) -> Job:
    """Start indexing the chosen files in the background. Poll GET /jobs/{id}."""
    if not _sync_lock.acquire(blocking=False):
        raise HTTPException(409, "Another repository is being loaded. Wait for it to finish.")
    try:
        _, path, _ = _clone(req.url)
        files = [f.replace("\\", "/").strip("/") for f in req.files]
        known = set(suggest_files(path, limit=100000))
        missing = [f for f in files if f not in known]
        if missing:
            raise HTTPException(400, f"not a document file in the repo: {', '.join(missing)}")
        job = Job(id=uuid.uuid4().hex[:12])
        JOBS[job.id] = job
        threading.Thread(target=_run_job, args=(job, GitSource(path, files, by=req.by)), daemon=True).start()
        return job
    except BaseException:
        _sync_lock.release()
        raise


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    if job_id not in JOBS:
        raise HTTPException(404, "unknown job")
    return JOBS[job_id]


# -- repo activity: "what was done recently, in which files" ---------------------------------


class ActivitySyncRequest(BaseModel):
    url: str
    days: int = Field(default=activity.DEFAULT_DAYS, ge=1, le=3650)  # how much history to load
    max_commits: int = Field(default=activity.MAX_COMMITS, ge=1, le=5000)


class ActivityAskRequest(BaseModel):
    question: str
    repo: str  # key from GET /activity/repos (owner/name), a short name, or the repo link
    days: int | None = Field(default=None, ge=1, le=3650)  # overrides the period found in the question
    last_n: int | None = Field(default=None, ge=1, le=500)


class ActivityJob(BaseModel):
    id: str
    status: Literal["running", "done", "error"] = "running"
    phase: str = "downloading"  # downloading | reading | embedding
    done: int = 0  # commits embedded so far
    total: int = 0  # new commits to embed (known after reading)
    started: float = Field(default_factory=time.time)
    finished: float | None = None
    error: str | None = None
    repo: str | None = None
    action: str | None = None  # cloned | updated
    found: int = 0  # commits in the period
    indexed: int = 0
    skipped: int = 0


ACTIVITY_JOBS: dict[str, ActivityJob] = {}


def _run_activity_job(job: ActivityJob, req: ActivitySyncRequest) -> None:
    def progress(phase: str, done: int, total: int) -> None:
        job.phase, job.done, job.total = phase, done, total

    try:
        r = activity.sync_repo(req.url, days=req.days, max_commits=req.max_commits, on_progress=progress)
        job.repo, job.action, job.found, job.indexed, job.skipped = r.repo, r.action, r.found, r.indexed, r.skipped
        job.status = "done"
    except Exception as e:  # noqa: BLE001 - reported to the UI
        job.status, job.error = "error", f"{type(e).__name__}: {e}"
    finally:
        job.finished = time.time()
        _sync_lock.release()


@app.post("/activity/sync", response_model=ActivityJob, status_code=202)
def activity_sync(req: ActivitySyncRequest) -> ActivityJob:
    """Load a repo's recent commits in the background. Poll GET /activity/jobs/{id}."""
    try:
        activity.repo_from_link(req.url)
    except activity.ActivityError as e:
        raise HTTPException(400, str(e)) from e
    if not _sync_lock.acquire(blocking=False):
        raise HTTPException(409, "Another repository is being loaded. Wait for it to finish.")
    job = ActivityJob(id=uuid.uuid4().hex[:12])
    ACTIVITY_JOBS[job.id] = job
    try:
        threading.Thread(target=_run_activity_job, args=(job, req), daemon=True).start()
    except BaseException:
        _sync_lock.release()
        raise
    return job


@app.get("/activity/jobs/{job_id}", response_model=ActivityJob)
def activity_job(job_id: str) -> ActivityJob:
    if job_id not in ACTIVITY_JOBS:
        raise HTTPException(404, "unknown job")
    return ACTIVITY_JOBS[job_id]


@app.get("/activity/repos", response_model=list[activity.RepoInfo])
def activity_repos() -> list[activity.RepoInfo]:
    return activity.list_repos()


@app.post("/activity/ask", response_model=activity.ActivityAnswer)
def activity_ask(req: ActivityAskRequest) -> activity.ActivityAnswer:
    try:
        return activity.ask(req.question, req.repo, days=req.days, last_n=req.last_n)
    except activity.ActivityError as e:
        raise HTTPException(400, str(e)) from e
