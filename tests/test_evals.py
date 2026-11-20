"""Offline tests for vrag.evals (scoring logic) and the golden answer cases. No services."""

from pathlib import Path

import pytest

from vrag import evals
from vrag.evals import Case, check_answer, load_cases

CASES_FILE = Path(__file__).parent.parent / "data" / "golden" / "answer_cases.json"
LATEST = {"refund-policy": "v3"}
CHUNKS = {"c1": "Customers may request a refund within 21 days.", "c2": "A fee of 15%\napplies."}


def chunk_text(cid):
    return CHUNKS.get(cid)


def cit(chunk="c1", version="v3", doc="refund-policy", quote="within 21 days", section="2 Refund Window"):
    return {"source": "S1", "doc_id": doc, "version": version, "section": section, "quote": quote, "chunk_id": chunk}


def res(answer="21 days", intent="latest", versions=(), doc="refund-policy", cits=None, status="grounded", **kw):
    return {
        "answer": answer,
        "route": {"intent": intent, "doc_id": doc, "versions": list(versions)},
        "citations": [cit()] if cits is None else cits,
        "unknown_citations": [],
        "grounding": {"status": status},
        "insufficient_context": False,
        **kw,
    }


def failed(case, r):
    return {c.name for c in check_answer(case, r, LATEST, chunk_text) if not c.ok}


def test_good_latest_answer_passes():
    case = Case("a", "q", "latest", doc="refund-policy", facts=[r"\b21\b"], forbidden=[r"\b14\b"])
    assert failed(case, res()) == set()


def test_wrong_intent_and_versions():
    case = Case("a", "q", "compare", doc="refund-policy", versions=["v1", "v3"])
    r = res(intent="version", versions=["v1"], cits=[cit(version="v1")])
    assert {"intent", "versions"} <= failed(case, r)


def test_missing_fact_and_forbidden_fact():
    case = Case("a", "q", "latest", facts=[r"\b30\b"], forbidden=[r"\b21\b"])
    assert {"fact", "forbidden"} <= failed(case, res())


def test_answer_only_fact_is_matched_against_the_answer():
    case = Case("a", "q", "latest", facts=[evals.ANSWER_ONLY + "unchanged"])
    assert failed(case, res("it is unchanged")) == set()
    assert failed(case, res("21 days")) == {"fact"}


def test_latest_citation_must_be_latest_version():
    case = Case("a", "q", "latest")
    assert failed(case, res(cits=[cit(version="v2")])) == {"citation_version"}


def test_version_citation_must_be_routed_version():
    case = Case("a", "q", "version", doc="refund-policy", versions=["v2"])
    r = res(intent="version", versions=["v2"], cits=[cit(version="v3")])
    assert failed(case, r) == {"citation_version"}


def test_compare_needs_both_sides():
    case = Case("a", "q", "compare", doc="refund-policy", versions=["v1", "v3"])
    r = res(intent="compare", versions=["v1", "v3"], cits=[cit(version="v3")])
    assert failed(case, r) == {"both_sides"}
    both = [cit(version="v1"), cit(version="v3")]
    assert "both_sides" not in failed(case, res(intent="compare", versions=["v1", "v3"], cits=both))


def test_quote_must_be_in_chunk_ignoring_case_whitespace_and_ellipsis():
    case = Case("a", "q", "latest")
    assert failed(case, res(cits=[cit(chunk="c2", quote="A FEE of 15% applies...")])) == set()
    assert failed(case, res(cits=[cit(quote="within 99 days")])) == {"quote"}
    assert failed(case, res(cits=[cit(chunk="missing")])) == {"quote"}


def test_chunk_lookup_error_is_a_failed_quote_not_a_crash():
    def boom(_):
        raise RuntimeError("es down")

    case = Case("a", "q", "latest")
    names = {c.name for c in check_answer(case, res(), LATEST, boom) if not c.ok}
    assert names == {"quote"}


def test_unknown_citations_and_no_citations():
    case = Case("a", "q", "latest")
    assert failed(case, res(unknown_citations=["S9"])) == {"unknown_citations"}
    assert {"has_citations"} <= failed(case, res(cits=[]))


def test_only_sections():
    case = Case("a", "q", "latest", only_sections=["Restocking"])
    assert failed(case, res()) == {"citation_section"}
    assert failed(case, res(cits=[cit(section="4 Restocking Fees")])) == set()


def test_focus_must_stay_in_route():
    case = Case("a", "q", "latest", doc_id="leave-policy")
    assert "focus" in failed(case, res())


def test_unclear_must_not_cite_and_is_not_checked_for_grounding():
    case = Case("a", "q", "unclear", doc="refund-policy", versions=[], facts=[r"v1, v2"])
    ok = res("v1, v2, v3", intent="unclear", cits=[], status="not_checked")
    assert failed(case, ok) == set()
    assert failed(case, res("v1, v2", intent="unclear", status="not_checked")) == {"no_citations"}


def test_insufficient_case():
    case = Case("a", "q", "latest", insufficient=True)
    ok = res("not covered", cits=[], status="not_checked", insufficient_context=True)
    assert failed(case, ok) == set()
    assert failed(case, res(cits=[], status="not_checked")) == {"insufficient"}
    assert case.expected_grounding == "not_checked"


def test_grounding_status_is_checked():
    case = Case("a", "q", "latest")
    assert failed(case, res(status="partial")) == {"grounding"}
    assert Case("b", "q", "latest", grounding="partial").expected_grounding == "partial"


def test_case_validation():
    with pytest.raises(ValueError, match="intent"):
        Case.from_dict({"id": "a", "question": "q", "intent": "nope"})
    with pytest.raises(ValueError, match="unknown keys"):
        Case.from_dict({"id": "a", "question": "q", "intent": "latest", "bogus": 1})
    with pytest.raises(ValueError, match="bad regex"):
        Case.from_dict({"id": "a", "question": "q", "intent": "latest", "facts": ["("]})
    with pytest.raises(ValueError, match="grounding"):
        Case.from_dict({"id": "a", "question": "q", "intent": "latest", "grounding": "x"})


def test_load_cases_rejects_duplicate_ids(tmp_path):
    f = tmp_path / "c.json"
    f.write_text('[{"id":"a","question":"q","intent":"latest"},{"id":"a","question":"q2","intent":"latest"}]')
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(f)


def test_select_by_id_or_tag():
    cs = [Case("a", "q", "latest", tags=["x"]), Case("b", "q", "latest", tags=["y"]), Case("c", "q", "latest")]
    assert [c.id for c in evals.select(cs, [])] == ["a", "b", "c"]
    assert [c.id for c in evals.select(cs, ["x", "c"])] == ["a", "c"]


def test_score_flattens_one_run():
    case = Case("a", "q", "latest", doc="refund-policy", facts=[r"\b21\b", r"\b99\b"])
    r = res()
    rec = evals.score(case, r, check_answer(case, r, LATEST, chunk_text), 2.5)
    assert rec["passed"] is False
    assert (rec["facts_total"], rec["facts_hit"]) == (2, 1)
    assert (rec["citations_total"], rec["citations_valid"]) == (1, 1)
    assert rec["grounding_ok"] and rec["intent_ok"] and rec["route_ok"]
    assert rec["failed"] == [f for f in rec["failed"] if f.startswith("fact")]


def test_score_error_run():
    case = Case("a", "q", "latest")
    rec = evals.score(case, None, [evals.Check("error", False, "boom")], None, "RuntimeError: boom")
    assert not rec["passed"] and not rec["intent_ok"] and not rec["route_ok"]
    assert evals.metrics([rec])["errors"] == 1


def test_percentile():
    assert evals.percentile([], 50) is None
    assert evals.percentile([1, 2, 3, 4], 50) == 2.5
    assert evals.percentile([5], 90) == 5
    assert evals.percentile([1, 2, 3, 4, 5], 100) == 5


def _rec(cid, passed, intent="latest", doc="d", **kw):
    base = {
        "id": cid, "intent": intent, "doc": doc, "tags": [], "passed": passed, "error": None,
        "intent_ok": True, "doc_ok": None, "versions_ok": None, "route_ok": True,
        "facts_total": 2, "facts_hit": 2 if passed else 1, "forbidden_hits": 0,
        "citations_total": 2, "citations_valid": 2, "unknown_citations": 0,
        "grounding_expected": "grounded", "grounding_got": "grounded", "grounding_ok": True,
        "insufficient_expected": False, "insufficient_got": False, "latency_s": 1.0, "failed": [],
    }
    return {**base, **kw}


def test_metrics_and_aggregate():
    recs = [
        _rec("a", True),
        _rec("b", False, intent="compare", tags=["t"], versions_ok=False, route_ok=False, forbidden_hits=1),
        _rec("c", True, intent="latest", insufficient_expected=True, insufficient_got=True),
        _rec("d", False, insufficient_expected=False, insufficient_got=True),
    ]
    m = evals.metrics(recs)
    assert (m["runs"], m["passed"], m["pass_rate"]) == (4, 2, 0.5)
    assert m["route_accuracy"] == 0.75 and m["version_accuracy"] == 0.0
    assert m["facts_hit"] == 6 and m["fact_recall"] == 6 / 8
    assert m["forbidden_hits"] == 1 and m["runs_with_forbidden_hit"] == 1
    assert m["insufficient"] == {"tp": 1, "fp": 1, "fn": 0, "precision": 0.5, "recall": 1.0}
    agg = evals.aggregate(recs)
    assert list(agg["by_intent"]) == ["latest", "compare"]
    assert agg["by_tag"]["t"]["runs"] == 1
    assert "| overall |" in evals.metrics_table(agg)


def test_unclear_is_not_counted_in_insufficient_scores():
    m = evals.metrics([_rec("a", True, intent="unclear", insufficient_got=True)])
    assert m["insufficient"]["fp"] == 0


def test_stability():
    recs = [_rec("a", True), _rec("a", False), _rec("b", True), _rec("c", False)]
    st = evals.stability(recs)
    assert (st["a"]["status"], st["b"]["status"], st["c"]["status"]) == ("flaky", "pass", "fail")


# --- verify_case against a fake corpus ---------------------------------------------

CATALOG = [
    {"doc_id": "doc", "doc_title": "The Doc", "versions": [
        {"version": "v1", "effective_date": "2024-01-01"},
        {"version": "v2", "effective_date": "2024-06-01"},
        {"version": "v3", "effective_date": "2025-01-01"},
    ]},
]
TEXT = {"v1": "window is 30 days", "v2": "window is 14 days", "v3": "window is 21 days"}


def vtext(doc, version):
    return TEXT[version]


def test_verify_case_accepts_a_good_case():
    c = Case("a", "q", "latest", doc="doc", facts=[r"\b21\b"], forbidden=[r"\b30\b"])
    assert evals.verify_case(c, CATALOG, vtext) == []


def test_verify_case_finds_bad_cases():
    probs = lambda **kw: evals.verify_case(Case("a", "q", **kw), CATALOG, vtext)  # noqa: E731
    assert "not indexed" in probs(intent="latest", doc="nope")[0]
    assert "not indexed" in probs(intent="version", doc="doc", versions=["v9"])[0]
    assert "oldest first" in probs(intent="compare", doc="doc", versions=["v3", "v1"])[0]
    assert "needs doc and versions" in probs(intent="compare")[0]
    assert "not in the expected source" in probs(intent="latest", doc="doc", facts=[r"\b99\b"])[0]
    assert "is in the expected source" in probs(intent="latest", doc="doc", forbidden=[r"\b21\b"])[0]
    assert "no other version" in probs(intent="latest", doc="doc", forbidden=[r"\b77\b"])[0]
    # a version case reads only the routed version
    assert probs(intent="version", doc="doc", versions=["v2"], facts=[r"\b14\b"], forbidden=[r"\b21\b"]) == []
    assert probs(intent="version", doc="doc", versions=["v2"], facts=[r"\b21\b"])


def test_verify_case_unclear_reads_the_catalog_and_answer_only_facts_are_skipped():
    c = Case("a", "q", "unclear", doc="doc", versions=[], facts=[r"v1, v2, v3"])
    assert evals.verify_case(c, CATALOG, vtext) == []
    c = Case("a", "q", "latest", doc="doc", facts=[evals.ANSWER_ONLY + "unchanged"])
    assert evals.verify_case(c, CATALOG, vtext) == []


# --- the golden file ---------------------------------------------------------------


def test_golden_cases_load_and_cover_the_corpus():
    cases = load_cases(CASES_FILE)
    assert {c.intent for c in cases} == set(evals.INTENTS)
    docs = {c.doc for c in cases if c.doc}
    assert docs == {"refund-policy", "leave-policy", "data-retention", "semver/semver", "travel-expense-policy"}
    assert any(c.insufficient for c in cases)
    assert any(c.doc_id for c in cases)
    for c in cases:
        if c.intent in ("version", "compare"):
            assert c.doc and c.versions, c.id
        if c.intent == "latest" and not c.insufficient:
            assert c.facts, c.id
