"""/sources/git/* and /jobs endpoints with a local upstream repo and a fake indexer."""

import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from vrag import api
from vrag.sources import remote
from vrag.sync import SyncReport


def git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def upstream(tmp_path):
    up = tmp_path / "up"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    (up / "policy.md").write_bytes(b"# P\none\n")
    (up / "main.py").write_bytes(b"x")
    git(up, "add", "-A")
    git(up, "commit", "-q", "-m", "c1")
    git(up, "tag", "v1")
    (up / "policy.md").write_bytes(b"# P\ntwo\n")
    git(up, "commit", "-q", "-am", "c2")
    git(up, "tag", "v1.0.1")
    return up


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "ensure_clone", lambda link: remote.ensure_clone(link, tmp_path / "repos"))
    api.JOBS.clear()
    return TestClient(api.app)


def wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_inspect_lists_doc_files_and_tags(client, upstream):
    r = client.post("/sources/git/inspect", json={"url": str(upstream)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "up" and body["action"] == "cloned" and body["tags"] == 2
    assert body["candidates"] == ["policy.md"] and body["file"] is None
    assert client.post("/sources/git/inspect", json={"url": str(upstream)}).json()["action"] == "updated"


def test_inspect_bad_link_is_400(client, tmp_path):
    r = client.post("/sources/git/inspect", json={"url": str(tmp_path / "missing")})
    assert r.status_code == 400 and "git clone" in r.json()["detail"]


def test_sync_runs_in_background_and_reports(client, upstream, monkeypatch):
    seen = {}

    def fake_sync(src, on_progress=None):
        versions = list(src.iter_versions())
        seen["versions"] = [(v.doc_id, v.version) for v in versions]
        for v in versions:
            on_progress(f"{v.doc_id}@{v.version}")
        return SyncReport(indexed=[f"{d}@{v}" for d, v in seen["versions"]])

    monkeypatch.setattr(api, "run_sync", fake_sync)
    r = client.post("/sources/git/sync", json={"url": str(upstream), "files": ["policy.md"]})
    assert r.status_code == 202, r.text
    job = wait(client, r.json()["id"])
    assert job["status"] == "done" and job["indexed"] == ["up/policy@v1", "up/policy@v1.0.1"]
    assert job["done_count"] == 2 and job["finished"]
    # lock released: a second sync can start
    r2 = client.post("/sources/git/sync", json={"url": str(upstream), "files": ["policy.md"]})
    assert r2.status_code == 202
    wait(client, r2.json()["id"])


def test_sync_rejects_unknown_file_and_releases_lock(client, upstream):
    r = client.post("/sources/git/sync", json={"url": str(upstream), "files": ["main.py"]})
    assert r.status_code == 400 and "main.py" in r.json()["detail"]
    assert api._sync_lock.acquire(blocking=False)
    api._sync_lock.release()


def test_sync_error_is_reported(client, upstream, monkeypatch):
    def boom(src, on_progress=None):
        raise ConnectionError("elasticsearch down")

    monkeypatch.setattr(api, "run_sync", boom)
    r = client.post("/sources/git/sync", json={"url": str(upstream), "files": ["policy.md"]})
    job = wait(client, r.json()["id"])
    assert job["status"] == "error" and "elasticsearch down" in job["error"]


def test_second_sync_while_running_is_409(client, upstream, monkeypatch):
    gate = threading.Event()

    def slow(src, on_progress=None):
        gate.wait(5)
        return SyncReport()

    monkeypatch.setattr(api, "run_sync", slow)
    first = client.post("/sources/git/sync", json={"url": str(upstream), "files": ["policy.md"]})
    second = client.post("/sources/git/sync", json={"url": str(upstream), "files": ["policy.md"]})
    gate.set()
    assert first.status_code == 202 and second.status_code == 409
    wait(client, first.json()["id"])


def test_unknown_job_is_404(client):
    assert client.get("/jobs/nope").status_code == 404
