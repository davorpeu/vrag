"""/activity/* endpoints with a fake indexer: background job, 409, errors, repos, ask."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from vrag import activity, api


@pytest.fixture
def client():
    api.ACTIVITY_JOBS.clear()
    return TestClient(api.app)


def wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/activity/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_sync_runs_in_background_and_reports_progress(client, monkeypatch):
    seen = {}

    def fake(url, days, max_commits, on_progress):
        seen.update(url=url, days=days, max_commits=max_commits)
        on_progress("downloading", 0, 0)
        on_progress("embedding", 3, 5)
        return activity.SyncResult("o/r", "cloned", "2026-08-01", found=7, indexed=5, skipped=2)

    monkeypatch.setattr(activity, "sync_repo", fake)
    r = client.post("/activity/sync", json={"url": "https://github.com/o/r", "days": 30})
    assert r.status_code == 202, r.text
    job = wait(client, r.json()["id"])
    assert seen == {"url": "https://github.com/o/r", "days": 30, "max_commits": 500}
    assert (job["status"], job["repo"], job["action"], job["found"], job["indexed"], job["skipped"]) == (
        "done", "o/r", "cloned", 7, 5, 2)
    assert job["finished"] and (job["phase"], job["done"], job["total"]) == ("embedding", 3, 5)
    second = client.post("/activity/sync", json={"url": "https://github.com/o/r"})
    assert second.status_code == 202  # lock released
    wait(client, second.json()["id"])


def test_default_period_is_90_days(client, monkeypatch):
    got = {}

    def fake(url, days, max_commits, on_progress):
        got["days"] = days
        return activity.SyncResult("o/r")

    monkeypatch.setattr(activity, "sync_repo", fake)
    wait(client, client.post("/activity/sync", json={"url": "https://github.com/o/r"}).json()["id"])
    assert got["days"] == 90


def test_sync_error_is_reported_and_releases_the_lock(client, monkeypatch):
    def boom(*a, **k):
        raise activity.ActivityError("git clone failed: repository not found")

    monkeypatch.setattr(activity, "sync_repo", boom)
    job = wait(client, client.post("/activity/sync", json={"url": "https://github.com/o/nope"}).json()["id"])
    assert job["status"] == "error" and "repository not found" in job["error"]
    assert api._sync_lock.acquire(blocking=False)
    api._sync_lock.release()


def test_second_sync_while_running_is_409(client, monkeypatch):
    gate = threading.Event()

    def slow(*a, **k):
        gate.wait(5)
        return activity.SyncResult("o/r")

    monkeypatch.setattr(activity, "sync_repo", slow)
    first = client.post("/activity/sync", json={"url": "https://github.com/o/r"})
    second = client.post("/activity/sync", json={"url": "https://github.com/o/r"})
    gate.set()
    assert first.status_code == 202 and second.status_code == 409
    wait(client, first.json()["id"])


def test_sync_rejects_an_unreadable_link_and_bad_days(client):
    assert client.post("/activity/sync", json={"url": "   "}).status_code == 400
    assert client.post("/activity/sync", json={"url": "https://github.com/o/r", "days": 0}).status_code == 422
    assert api._sync_lock.acquire(blocking=False)  # a rejected request leaves the lock free
    api._sync_lock.release()


def test_unknown_job_is_404(client):
    assert client.get("/activity/jobs/nope").status_code == 404


def test_repos_lists_what_is_loaded(client, monkeypatch):
    info = activity.RepoInfo(
        repo="o/r", url="https://github.com/o/r", since="2026-08-01", synced_at="2026-11-20T10:00:00Z", commits=42
    )
    monkeypatch.setattr(activity, "list_repos", lambda: [info])
    assert client.get("/activity/repos").json() == [info.model_dump()]


def test_ask_passes_the_question_and_returns_the_schema(client, monkeypatch):
    got = {}
    sha = "a" * 40
    answer = activity.ActivityAnswer(
        repo="o/r", question="q", answer="Work on x.",
        window=activity.WindowInfo(since="2026-11-06", until="2026-11-20", last_n=None, label="last 14 days"),
        topic="", commits_in_window=1, commits_used=1, authors=1, first_commit="2026-11-10", last_commit="2026-11-10",
        files=[activity.FileChange(path="a.py", commits=1, additions=2, deletions=1)], files_total=1, directories=[],
        commits=[activity.CommitInfo(
            sha=sha, short="aaaaaaa", date="2026-11-10T00:00:00Z", author="x", message="m",
            url=f"https://github.com/o/r/commit/{sha}", files_changed=1, additions=2, deletions=1)],
        citations=[], insufficient_context=False,
    )

    def fake(question, repo, days=None, last_n=None):
        got.update(question=question, repo=repo, days=days, last_n=last_n)
        return answer

    monkeypatch.setattr(activity, "ask", fake)
    r = client.post("/activity/ask", json={"question": "what was done?", "repo": "o/r", "days": 7})
    assert r.status_code == 200, r.text
    assert got == {"question": "what was done?", "repo": "o/r", "days": 7, "last_n": None}
    body = r.json()
    assert body["files"] == [{"path": "a.py", "commits": 1, "additions": 2, "deletions": 1}]
    assert body["commits"][0]["url"].endswith(sha) and body["window"]["label"] == "last 14 days"


def test_ask_for_a_repo_that_is_not_loaded_is_400(client, monkeypatch):
    def fake(*a, **k):
        raise activity.ActivityError("'x' is not loaded yet.")

    monkeypatch.setattr(activity, "ask", fake)
    r = client.post("/activity/ask", json={"question": "q", "repo": "x"})
    assert r.status_code == 400 and "not loaded" in r.json()["detail"]
