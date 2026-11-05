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


def run_ui(monkeypatch, payload):
    def fake_post(url, json, timeout):
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
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
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.text_input[0].input("anything")
    at.button[0].click().run()
    assert any("API error" in e.value for e in at.error)
