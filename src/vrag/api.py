from dotenv import load_dotenv

load_dotenv()  # Langfuse SDK reads LANGFUSE_* from os.environ

import threading  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from typing import Literal  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from vrag import answer, store  # noqa: E402
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
