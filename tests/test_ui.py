"""Streamlit UI with a fake API: document list, conversation, answer rendering, adding from GitHub."""

from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "ui" / "streamlit_app.py"

DOCS = [
    {"doc_id": "refund-policy", "doc_title": "Acme Retail Refund Policy",
     "versions": [{"version": "v1"}, {"version": "v2"}, {"version": "v3"}]},
    {"doc_id": "semver/semver", "doc_title": "Semantic Versioning 2.0.0",
     "versions": [{"version": "v1.0.0"}, {"version": "v2.0.0"}]},
]


def _cite(version, date, days):
    return {
        "source": f"x-{version}",
        "doc_id": "refund-policy",
        "doc_title": "Acme Retail Refund Policy",
        "version": version,
        "effective_date": date,
        "section": "2 Refund Window",
        "quote": f"2.1 Customers may request a refund within {days} days of the purchase date.",
        "chunk_id": f"refund-policy:{version}:2",
    }


GROUNDED = {"status": "grounded", "issues": [], "repaired_quotes": 0, "dropped_quotes": 0, "retried": False}

COMPARE = {
    "question": "What changed between v1 and v3 of the refund policy?",
    "route": {"intent": "compare", "doc_id": "refund-policy", "versions": ["v1", "v3"], "note": ""},
    "answer": "The refund window went from 30 to 21 days.",
    "insufficient_context": False,
    "citations": [_cite("v1", "2024-01-15", 30), _cite("v3", "2025-04-01", 21)],
    "unknown_citations": [],
    "trace_id": "abc123",
    "grounding": GROUNDED,
}

UNCLEAR_VERSION = {
    "question": "What did version 7 of the refund policy say?",
    "route": {"intent": "unclear", "doc_id": "refund-policy", "versions": [],
              "note": "Version 7 not found. Acme Retail Refund Policy has versions v1, v2, v3."},
    "answer": "I'm not sure which document or version you mean.",
    "insufficient_context": True, "citations": [], "unknown_citations": [], "trace_id": None,
}

UNCLEAR_DOC = {**UNCLEAR_VERSION, "route": {"intent": "unclear", "doc_id": None, "versions": [], "note": "x"}}


class FakeApi:
    """Records calls; answers /ask with `answer`, git endpoints from the given states."""

    def __init__(self, monkeypatch, answer=COMPARE, inspect=None, inspect_code=200, preview=None, jobs=(), docs=DOCS):
        self.calls, self.answer, self.inspect, self.inspect_code = [], answer, inspect, inspect_code
        self.preview, self.jobs, self.docs = preview, iter(jobs), docs
        monkeypatch.setattr(httpx, "post", self.post)
        monkeypatch.setattr(httpx, "get", self.get)

    def _r(self, code, body, url):
        return httpx.Response(code, json=body, request=httpx.Request("POST", url))

    def post(self, url, json, timeout):
        self.calls.append((url.rsplit("8000", 1)[-1], json))
        if url.endswith("/ask"):
            return self._r(200, self.answer, url)
        if url.endswith("/inspect"):
            return self._r(self.inspect_code, self.inspect, url)
        if url.endswith("/preview"):
            rows = self.preview(json) if callable(self.preview) else self.preview
            return self._r(200, rows or [{"file": json["files"][0], "versions": 4, "size_kb": 12}], url)
        if url.endswith("/sync"):
            return self._r(202, {"id": "job1"}, url)
        raise AssertionError(url)

    def get(self, url, timeout):
        if url.endswith("/documents"):
            return self._r(200, self.docs, url)
        return self._r(200, next(self.jobs), url)

    def sent(self, path):
        return [body for p, body in self.calls if p == path]


def app():
    return AppTest.from_file(str(APP), default_timeout=30).run()


def ask(at, q):
    at.chat_input[0].set_value(q).run()
    assert not at.exception
    return at


def html(at):
    return " ".join(m.value for m in at.markdown)


# -- conversation ------------------------------------------------------------------


def test_empty_state_shows_examples_and_document_list(monkeypatch):
    FakeApi(monkeypatch)
    at = app()
    assert "Every answer says which version it comes from" in html(at)
    radio = at.sidebar.radio[0]
    assert radio.value == "__all__" and radio.options[0] == "All documents"
    assert "Acme Retail Refund Policy" in radio.options[1] and "3 versions" in radio.options[1]
    assert radio.options[2].startswith("semver/semver") and "from git" in radio.options[2]  # git: path, not heading


def test_compare_answer_context_timeline_check_and_sources(monkeypatch):
    FakeApi(monkeypatch)
    at = ask(app(), COMPARE["question"])
    page = html(at)
    assert "Comparing <b>Acme Retail Refund Policy</b> v1 and v3" in page
    assert '<span class="vr-v then">v1</span>' in page and '<span class="vr-v now">v3</span>' in page
    assert '<span class="vr-v ">v2</span>' in page  # the version in between, not used
    assert "30 to 21 days" in page and "Every quote and number was found" in page
    assert at.main.expander[-1].label == "Sources (2)"
    assert "within 30 days" in page and "Langfuse" in " ".join(c.value for c in at.caption)
    assert "Every answer says" not in page  # intro gone once asking


def test_history_is_kept_across_questions(monkeypatch):
    FakeApi(monkeypatch)
    at = ask(app(), "first question")
    at = ask(at, "second question")
    assert len([m for m in at.chat_message if m.name == "user"]) == 2


def test_unclear_version_shows_route_note(monkeypatch):
    FakeApi(monkeypatch, answer=UNCLEAR_VERSION)
    at = ask(app(), UNCLEAR_VERSION["question"])
    assert "Version 7 not found" in html(at) and not at.main.expander


def test_unclear_document_points_to_the_list(monkeypatch):
    FakeApi(monkeypatch, answer=UNCLEAR_DOC)
    at = ask(app(), "what changed?")
    assert "Pick one on the left" in html(at)


def test_ungrounded_answer_lists_issues(monkeypatch):
    bad = {**COMPARE, "grounding": {**GROUNDED, "status": "ungrounded", "issues": ["numbers not in the cited sources: 30"]}}
    FakeApi(monkeypatch, answer=bad)
    at = ask(app(), "q")
    assert "could not be checked" in at.error[0].value and "30" in at.error[0].value
    assert "Every quote" not in html(at)


def test_api_down_is_explained(monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", down)
    monkeypatch.setattr(httpx, "get", down)
    at = ask(app(), "anything")
    assert "isn't reachable" in at.error[0].value
    assert "API isn't running" in " ".join(c.value for c in at.sidebar.caption)


def test_picked_document_is_sent_and_named_in_the_input(monkeypatch):
    api = FakeApi(monkeypatch)
    at = app()
    at.sidebar.radio[0].set_value("semver/semver").run()
    assert at.chat_input[0].placeholder == "Ask about Semantic Versioning 2.0.0"
    ask(at, "whats in this document")
    assert api.sent("/ask")[-1] == {"question": "whats in this document", "doc_id": "semver/semver"}


# -- adding from GitHub ----------------------------------------------------------------


def job(status, **kw):
    return {"id": "job1", "status": status, "current": None, "done_count": 0, "error": None,
            "indexed": [], "skipped": [], "renumbered": [], "removed": [], "empty": [], **kw}


INSPECT_FILE = {"repo": "r", "name": "semver", "action": "cloned", "file": "semver.md", "candidates": ["semver.md"], "tags": 4}


def add(at, link):
    at.sidebar.text_input[0].input(link).run()
    at.sidebar.button[0].click().run()
    assert not at.exception
    return at


def test_file_link_adds_in_one_click_and_selects_the_document(monkeypatch):
    api = FakeApi(monkeypatch, inspect=INSPECT_FILE, jobs=[
        job("running", done_count=1),
        job("done", indexed=["semver/semver@v1.0.0"], skipped=["semver/semver@v2.0.0"], done_count=2),
    ])
    at = add(app(), "https://github.com/semver/semver/blob/master/semver.md")
    assert api.sent("/sources/git/sync") == [
        {"url": "https://github.com/semver/semver/blob/master/semver.md", "files": ["semver.md"], "by": "tags"}
    ]
    assert "1 of 4 versions" in at.sidebar.get("progress")[0].proto.text
    at.run()  # job done -> message, and the new document is selected
    assert any("Added semver.md: 1 new version, 1 already here." in s.value for s in at.sidebar.success)
    assert at.sidebar.radio[0].value == "semver/semver"


def test_repo_link_asks_which_file(monkeypatch):
    api = FakeApi(monkeypatch, inspect={**INSPECT_FILE, "file": None, "candidates": ["README.md", "semver.md"]},
                  jobs=[job("running")])
    at = add(app(), "https://github.com/semver/semver")
    sel = at.sidebar.selectbox[0]
    assert sel.options == ["README.md", "semver.md"] and at.sidebar.button[0].disabled
    sel.set_value("semver.md").run()
    at.sidebar.button[0].click().run()
    assert api.sent("/sources/git/sync")[-1]["files"] == ["semver.md"]


def test_falls_back_to_commits_when_file_has_no_tagged_versions(monkeypatch):
    api = FakeApi(
        monkeypatch, inspect=INSPECT_FILE,
        preview=lambda body: [{"file": "semver.md", "versions": 1 if body["by"] == "tags" else 9, "size_kb": 3}],
        jobs=[job("running")],
    )
    add(app(), "https://github.com/semver/semver/blob/master/semver.md")
    assert api.sent("/sources/git/sync")[-1]["by"] == "commits"


def test_big_file_needs_confirmation(monkeypatch):
    api = FakeApi(monkeypatch, inspect=INSPECT_FILE, preview=[{"file": "semver.md", "versions": 226, "size_kb": 287}],
                  jobs=[job("running")])
    at = add(app(), "https://github.com/semver/semver/blob/master/semver.md")
    assert "226 versions" in at.sidebar.warning[0].value and not api.sent("/sources/git/sync")
    [b for b in at.sidebar.button if b.label == "Add anyway"][0].click().run()
    assert api.sent("/sources/git/sync")


def test_bad_link_shows_the_reason(monkeypatch):
    FakeApi(monkeypatch, inspect={"detail": "git clone failed: repository not found"}, inspect_code=400)
    at = add(app(), "https://github.com/x/nope")
    assert "repository not found" in at.sidebar.error[0].value


def test_failed_load_is_reported(monkeypatch):
    FakeApi(monkeypatch, inspect=INSPECT_FILE, jobs=[job("error", error="ConnectionError: elasticsearch down")])
    at = add(app(), "https://github.com/semver/semver/blob/master/semver.md")
    assert any("elasticsearch down" in e.value for e in at.sidebar.error)
