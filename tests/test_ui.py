"""Streamlit UI with a fake API: the route banner, answer and citations render."""

from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "ui" / "streamlit_app.py"


def _cite(source, version, date, days):
    return {
        "source": source,
        "doc_id": "refund-policy",
        "doc_title": "Acme Retail Refund Policy",
        "version": version,
        "effective_date": date,
        "section": "2 Refund Window",
        "quote": f"2.1 Customers may request a refund within {days} days of the purchase date.",
        "chunk_id": f"refund-policy:{version}:2",
    }


COMPARE = {
    "question": "What changed between v1 and v3 of the refund policy?",
    "route": {"intent": "compare", "doc_id": "refund-policy", "versions": ["v1", "v3"], "note": ""},
    "answer": "The refund window went from 30 to 21 days.",
    "insufficient_context": False,
    "citations": [_cite("1-v1", "v1", "2024-01-15", 30), _cite("1-v3", "v3", "2025-04-01", 21)],
    "unknown_citations": [],
    "trace_id": "abc123",
    "grounding": {"status": "grounded", "issues": [], "repaired_quotes": 0, "dropped_quotes": 0, "retried": False},
}

UNCLEAR = {
    "question": "What did version 7 of the refund policy say?",
    "route": {
        "intent": "unclear",
        "doc_id": "refund-policy",
        "versions": [],
        "note": "Version 7 not found. Acme Retail Refund Policy has versions v1, v2, v3.",
    },
    "answer": "I'm not sure which document or version you mean. Version 7 not found.",
    "insufficient_context": True,
    "citations": [],
    "unknown_citations": [],
    "trace_id": None,
}


DOCS = [
    {"doc_id": "refund-policy", "doc_title": "Acme Retail Refund Policy",
     "versions": [{"version": "v1"}, {"version": "v2"}, {"version": "v3"}]},
    {"doc_id": "semver/semver", "doc_title": "Semantic Versioning 2.0.0",
     "versions": [{"version": "v1.0.0"}, {"version": "v2.0.0"}]},
]


def fake_documents(monkeypatch, docs=DOCS):
    def get(url, timeout):
        return httpx.Response(200, json=docs, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)


def run_ui(monkeypatch, payload, sent=None):
    def fake_post(url, json, timeout):
        if sent is not None:
            sent.append(json)
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    fake_documents(monkeypatch)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.text_input[0].input(payload["question"])
    at.button[0].click().run()
    assert not at.exception
    return at


def test_compare_shows_route_answer_and_both_versions(monkeypatch):
    at = run_ui(monkeypatch, COMPARE)
    banner = at.info[0].value
    assert "Comparison" in banner and "refund-policy" in banner and "v1 -> v3" in banner
    assert any("30 to 21 days" in m.value for m in at.markdown)
    labels = [e.label for e in at.expander]
    assert any("v1" in label for label in labels) and any("v3" in label for label in labels)
    assert any("Langfuse trace" in c.value for c in at.caption)


def test_unclear_shows_clarification_and_no_sources(monkeypatch):
    at = run_ui(monkeypatch, UNCLEAR)
    assert "Needs clarification" in at.info[0].value
    assert "v1, v2, v3" in at.info[0].value
    assert any("Version 7 not found" in w.value for w in at.warning)
    assert not at.expander  # st.stop() before the sources section


def test_api_error_is_shown(monkeypatch):
    def failing_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", failing_post)
    fake_documents(monkeypatch)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.text_input[0].input("anything")
    at.button[0].click().run()
    assert any("API error" in e.value for e in at.error)


def test_grounded_badge(monkeypatch):
    at = run_ui(monkeypatch, COMPARE)
    assert any("every quote and number was found" in m.value for m in at.success)
    assert not at.error


def test_ungrounded_warning_lists_issues(monkeypatch):
    bad = {
        **COMPARE,
        "grounding": {
            "status": "ungrounded",
            "issues": ["numbers not in the cited sources: 30"],
            "repaired_quotes": 0,
            "dropped_quotes": 0,
            "retried": True,
        },
    }
    at = run_ui(monkeypatch, bad)
    err = at.error[0].value
    assert "could not be verified" in err and "after one retry" in err and "30" in err
    assert not at.success


# -- sidebar: add a git repository ------------------------------------------------

INSPECT = {
    "repo": "data/repos/semver", "name": "semver", "action": "cloned",
    "file": "semver.md", "candidates": ["README.md", "semver.md"], "tags": 4,
}


def fake_git_api(monkeypatch, job_states, inspect=INSPECT, inspect_code=200, preview=None):
    calls = []

    def post(url, json, timeout):
        if url.endswith("/sources/git/preview"):
            rows = preview or [{"file": f, "versions": 4, "size_kb": 12} for f in json["files"]]
            return httpx.Response(200, json=rows, request=httpx.Request("POST", url))
        calls.append((url, json))
        if url.endswith("/sources/git/inspect"):
            return httpx.Response(inspect_code, json=inspect, request=httpx.Request("POST", url))
        return httpx.Response(202, json={"id": "job1"}, request=httpx.Request("POST", url))

    states = iter(job_states)

    def get(url, timeout):
        if url.endswith("/documents"):
            return httpx.Response(200, json=DOCS, request=httpx.Request("GET", url))
        return httpx.Response(200, json=next(states), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    return calls


def job(status, **kw):
    base = {"id": "job1", "status": status, "current": None, "done_count": 0, "started": 0, "finished": None,
            "error": None, "indexed": [], "skipped": [], "renumbered": [], "empty": []}
    return {**base, **kw}


def sidebar_check(at, link):
    at.sidebar.text_input[0].input(link)
    at.sidebar.button[0].click().run()
    assert not at.exception
    return at


def test_sidebar_file_link_preselects_file_and_loads(monkeypatch):
    calls = fake_git_api(monkeypatch, [
        job("running", current="semver/semver@v1.0.0"),
        job("done", indexed=["semver/semver@v1.0.0"], skipped=["semver/semver@v2.0.0"], done_count=2),
    ])
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    sidebar_check(at, "https://github.com/semver/semver/blob/master/semver.md")
    assert any("semver: cloned, 4 tag(s)" in s.value for s in at.sidebar.success)
    assert at.sidebar.multiselect[0].value == ["semver.md"]
    at.sidebar.button[1].click().run()  # Load versions -> first poll: running
    assert calls[-1][1] == {"url": "https://github.com/semver/semver/blob/master/semver.md", "files": ["semver.md"], "by": "tags"}
    assert any("Now: semver/semver@v1.0.0" in i.value for i in at.sidebar.info)
    at.run()  # next poll: done
    msg = " ".join(s.value for s in at.sidebar.success)
    assert "Loaded 1 new version(s), 1 already up to date" in msg and "now selected" in msg
    assert at.selectbox[0].value == "semver/semver"  # picker switched to the loaded document
    at.run()  # result stays visible after the job is gone
    assert any("Loaded 1 new version(s)" in s.value for s in at.sidebar.success)


def test_sidebar_repo_link_needs_a_file_choice(monkeypatch):
    fake_git_api(monkeypatch, [], inspect={**INSPECT, "file": None, "tags": 0})
    at = sidebar_check(AppTest.from_file(str(APP), default_timeout=30).run(), "https://github.com/semver/semver")
    assert at.sidebar.multiselect[0].value == [] and at.sidebar.multiselect[0].options == ["README.md", "semver.md"]
    assert at.sidebar.button[1].disabled  # nothing chosen yet
    assert any("no tags" in w.value for w in at.sidebar.warning)


def test_sidebar_bad_link_shows_error_and_page_still_works(monkeypatch):
    fake_git_api(monkeypatch, [], inspect={"detail": "git clone failed: repository not found"}, inspect_code=400)
    at = sidebar_check(AppTest.from_file(str(APP), default_timeout=30).run(), "https://github.com/x/nope")
    assert any("repository not found" in e.value for e in at.sidebar.error)
    assert at.text_input[0].label == "Question"  # main page not stopped


def test_sidebar_job_error_is_shown(monkeypatch):
    fake_git_api(monkeypatch, [job("error", error="ConnectionError: elasticsearch down")])
    at = sidebar_check(AppTest.from_file(str(APP), default_timeout=30).run(), "https://github.com/semver/semver/blob/master/semver.md")
    at.sidebar.button[1].click().run()
    assert any("elasticsearch down" in e.value for e in at.sidebar.error)


# -- document picker -----------------------------------------------------------------


def test_picker_defaults_to_all_and_sends_no_doc(monkeypatch):
    sent = []
    at = run_ui(monkeypatch, COMPARE, sent)
    assert at.selectbox[0].value == "__all__" and sent[-1]["doc_id"] is None
    assert at.selectbox[0].options[1:] == [
        "Acme Retail Refund Policy (refund-policy) - 3 versions, latest v3",
        "Semantic Versioning 2.0.0 (semver/semver) - 2 versions, latest v2.0.0",
    ]


def test_picked_document_is_sent_with_the_question(monkeypatch):
    sent = []

    def fake_post(url, json, timeout):
        sent.append(json)
        return httpx.Response(200, json=COMPARE, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    fake_documents(monkeypatch)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.selectbox[0].select("semver/semver").run()
    at.text_input[0].input("whats in this document")
    at.button[0].click().run()
    assert sent[-1] == {"question": "whats in this document", "doc_id": "semver/semver"}


def test_page_works_when_document_list_unavailable(monkeypatch):
    def down(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception and at.selectbox[0].options == ["All documents (name the document in your question)"]


def test_sidebar_shows_version_count(monkeypatch):
    fake_git_api(monkeypatch, [])
    at = sidebar_check(AppTest.from_file(str(APP), default_timeout=30).run(), "https://github.com/semver/semver/blob/master/semver.md")
    assert any("semver.md: 4 version(s), newest 12 KB" in c.value for c in at.sidebar.caption)
    assert not at.sidebar.button[1].disabled and not at.sidebar.checkbox


def test_big_load_needs_confirmation(monkeypatch):
    calls = fake_git_api(
        monkeypatch, [job("running")], preview=[{"file": "semver.md", "versions": 226, "size_kb": 287}]
    )
    at = sidebar_check(AppTest.from_file(str(APP), default_timeout=30).run(), "https://github.com/semver/semver/blob/master/semver.md")
    assert any("big load: 226 versions" in w.value for w in at.sidebar.warning)
    assert at.sidebar.button[1].disabled
    at.sidebar.checkbox[0].check().run()
    assert not at.sidebar.button[1].disabled
    at.sidebar.button[1].click().run()
    assert calls[-1][0].endswith("/sources/git/sync")
