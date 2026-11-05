"""Ask real questions through the API and check route, versions, facts and citations.

Each case says what a correct answer must contain. On top of that every answer must:
- cite only versions the route chose (latest route: each doc's latest version),
- cite nothing the model invented (no unknown citations),
- quote text that really appears in the cited chunk (groundedness).
"""

import re

import pytest

pytestmark = pytest.mark.e2e

# id, question, intent, doc, versions, facts (regexes the answer must match), extra checks
CASES = [
    # latest
    ("latest-refund", "How many days do customers have to request a refund?",
     "latest", "refund-policy", None, [r"\b21\b"], {}),
    ("latest-audit-logs", "How long are security audit logs kept?",
     "latest", "data-retention", None, [r"\b2 years\b|two years"], {}),
    ("latest-semver", "When must I increment the major version in semantic versioning?",
     "latest", "semver/semver", None, [r"incompatible"], {}),
    ("latest-not-covered", "What is the company's dress code?",
     "latest", None, None, [], {"insufficient": True}),
    # specific version
    ("version-label", "What was the refund window in version 2 of the refund policy?",
     "version", "refund-policy", ["v2"], [r"\b14\b"], {}),
    ("version-date", "What was the refund window in March 2024?",
     "version", "refund-policy", ["v1"], [r"\b30\b"], {}),
    ("version-old", "What did the old refund policy say about restocking fees?",
     "version", "refund-policy", ["v2"], [r"15\s?%"], {}),
    ("version-previous", "What did the previous version of the data retention standard say about account data?",
     "version", "data-retention", ["v2"], [r"\b7 years\b|seven years"], {}),
    ("version-leave-v1", "What did the leave policy v1 say about parental leave for secondary caregivers?",
     "version", "leave-policy", ["v1"], [r"\b2 weeks\b|two weeks"], {}),
    ("version-semver", "What did semver 1.0.0 say about pre-release versions?",
     "version", "semver/semver", ["v1.0.0"], [r"dash"], {}),
    # compare
    ("compare-two-labels", "What changed between v1 and v3 of the refund policy?",
     "compare", "refund-policy", ["v1", "v3"], [r"\b30\b", r"\b21\b"], {}),
    ("compare-whats-new", "What is new in the latest leave policy?",
     "compare", "leave-policy", ["v2", "v3"], [r"\b10\b", r"\b5\b", r"remote"], {}),
    ("compare-focused", "How did the restocking fee rules change in v2 of the refund policy?",
     "compare", "refund-policy", ["v1", "v2"], [r"15\s?%", r"defective"], {"only_sections": ["Restocking"]}),
    ("compare-semver", "What changed in semver 2.0.0 compared to 1.0.0?",
     "compare", "semver/semver", ["v1.0.0", "v2.0.0"], [r"build metadata"], {}),
    # unclear
    ("unclear-missing-version", "What did version 7 of the refund policy say?",
     "unclear", "refund-policy", [], [r"v1, v2, v3"], {}),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


@pytest.fixture(scope="module")
def answers(client):
    cache = {}

    def ask(q: str) -> dict:
        if q not in cache:
            r = client.post("/ask", json={"question": q})
            assert r.status_code == 200, r.text
            cache[q] = r.json()
        return cache[q]

    return ask


@pytest.mark.parametrize("cid,q,intent,doc,versions,facts,extra", CASES, ids=[c[0] for c in CASES])
def test_answer(cid, q, intent, doc, versions, facts, extra, answers, catalog, chunk_text):
    res = answers(q)
    route = res["route"]
    cited = [(c["doc_id"], c["version"], c["section"]) for c in res["citations"]]
    shown = f"\nroute={route}\nanswer={res['answer']!r}\ncitations={cited}"

    # route
    assert route["intent"] == intent, shown
    if versions is not None:
        assert route["versions"] == versions, shown
    if intent in ("version", "compare", "unclear") and doc:
        assert route["doc_id"] == doc, shown

    # groundedness check by the app itself
    expected = "not_checked" if intent == "unclear" or extra.get("insufficient") else "grounded"
    assert res["grounding"]["status"] == expected, f"grounding={res['grounding']}{shown}"

    # facts in the answer
    for pattern in facts:
        assert re.search(pattern, res["answer"], re.I), f"answer lacks /{pattern}/{shown}"

    if extra.get("insufficient"):
        assert res["insufficient_context"], shown
        return
    if intent == "unclear":
        assert not res["citations"], shown
        return

    # citations: present, nothing invented, from the right doc and versions
    assert res["citations"], f"no citations{shown}"
    assert not res["unknown_citations"], shown
    for c in res["citations"]:
        if doc:
            assert c["doc_id"] == doc, shown
        if intent == "latest":
            latest = catalog[c["doc_id"]]["versions"][-1]["version"]
            assert c["version"] == latest, f"cited {c['version']}, latest is {latest}{shown}"
        else:
            assert c["version"] in route["versions"], shown
        if "only_sections" in extra:
            assert any(s in c["section"] for s in extra["only_sections"]), f"off-topic citation {c['section']}{shown}"

    # compare answers cite both sides
    if intent == "compare":
        assert {c["version"] for c in res["citations"]} == set(route["versions"]), shown

    # groundedness: every quote is really in the cited chunk
    for c in res["citations"]:
        quote = _norm(c["quote"]).removesuffix("...").strip()
        assert quote and quote in _norm(chunk_text(c["chunk_id"])), (
            f"quote not found in {c['chunk_id']}: {c['quote']!r}{shown}"
        )
