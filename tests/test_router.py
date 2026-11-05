import pytest

from vrag.router import RouteDecision, norm_version, resolve, versions_in_text

CATALOG = [
    {
        "doc_id": "refund-policy",
        "doc_title": "Acme Retail Refund Policy",
        "versions": [
            {"version": "v1", "version_num": 1, "effective_date": "2024-01-15"},
            {"version": "v2", "version_num": 2, "effective_date": "2024-09-01"},
            {"version": "v3", "version_num": 3, "effective_date": "2025-04-01"},
        ],
    },
    {
        "doc_id": "leave-policy",
        "doc_title": "Northwind Employee Leave Policy",
        "versions": [
            {"version": "v1", "version_num": 1, "effective_date": "2023-07-01"},
            {"version": "v2", "version_num": 2, "effective_date": "2024-07-01"},
        ],
    },
    {
        "doc_id": "semver/semver",
        "doc_title": "Semantic Versioning 2.0.0",
        "versions": [
            {"version": "v1.0.0-beta", "version_num": 1, "effective_date": "2011-06-08"},
            {"version": "v1.0.0", "version_num": 2, "effective_date": "2011-09-06"},
            {"version": "v2.0.0", "version_num": 3, "effective_date": "2013-06-18"},
        ],
    },
]


def D(intent, document=None, versions=(), as_of=None):
    return RouteDecision(intent=intent, document=document, versions=list(versions), as_of=as_of)


def run(q, decision, top="refund-policy"):
    return resolve(q, decision, CATALOG, lambda ids: top if not ids or top in ids else ids[0])


@pytest.mark.parametrize(
    "a,b", [("v2.0.0", "2.0"), ("2", "v2"), ("version 2", "v2"), ("v1.0.0-beta", "1.0.0-beta")]
)
def test_norm_version_equal(a, b):
    assert norm_version(a) == norm_version(b)


def test_norm_version_keeps_prerelease_distinct():
    assert norm_version("v1.0.0-beta") != norm_version("v1.0.0")


def test_versions_in_text_ignores_plain_numbers():
    assert versions_in_text("Is it 21 days in v2 or version 3? And semver 2.0.0.") == ["2", "3", "2.0.0"]


def test_latest():
    r = run("How many days for a refund?", D("latest", "refund-policy"))
    assert r.intent == "latest"


def test_latest_with_version_in_text_becomes_version():
    r = run("What does v2 of the refund policy say?", D("latest", "refund-policy"))
    assert (r.intent, r.doc_id, r.versions) == ("version", "refund-policy", ["v2"])


def test_version_doc_inferred_from_unique_version():
    r = run("What did 1.0.0-beta say about pre-releases?", D("version", None, ["1.0.0-beta"]))
    assert (r.doc_id, r.versions) == ("semver/semver", ["v1.0.0-beta"])


def test_version_shared_label_uses_search():
    r = run("What did v1 say about carry-over?", D("version", None, ["v1"]), top="leave-policy")
    assert (r.doc_id, r.versions) == ("leave-policy", ["v1"])


def test_missing_version_is_unclear():
    r = run("What did v9 of the refund policy say?", D("version", "refund-policy", ["v9"]))
    assert r.intent == "unclear" and "v9" in r.note and "v1, v2, v3" in r.note


def test_section_number_not_taken_as_version():
    r = run("What does section 2.1 of the refund policy say?", D("latest", "refund-policy"))
    assert r.intent == "latest"


def test_as_of_date_picks_version_in_effect():
    r = run("Refund window in March 2024?", D("version", "refund-policy", [], "2024-03-31"))
    assert (r.intent, r.versions) == ("version", ["v1"])
    assert run("x", D("version", "refund-policy", [], "2024-09-01")).versions == ["v2"]


def test_as_of_before_first_version_is_unclear():
    assert run("x", D("version", "refund-policy", [], "2020-01-01")).intent == "unclear"


def test_previous_version():
    r = run("What did the previous refund policy say?", D("version", "refund-policy"))
    assert r.versions == ["v2"]


def test_compare_two_versions_sorted_old_to_new():
    r = run("Differences between v3 and v1?", D("compare", "refund-policy", ["v3", "v1"]))
    assert (r.intent, r.versions) == ("compare", ["v1", "v3"])


def test_compare_one_version_uses_previous():
    assert run("What changed in v2?", D("compare", "refund-policy", ["v2"])).versions == ["v1", "v2"]


def test_compare_no_version_uses_last_two():
    assert run("What is new?", D("compare", "refund-policy")).versions == ["v2", "v3"]


def test_version_with_two_versions_becomes_compare():
    r = run("What did v1 and v2 say?", D("version", "refund-policy", ["v1", "v2"]))
    assert (r.intent, r.versions) == ("compare", ["v1", "v2"])


def test_llm_non_version_words_ignored():
    r = run("What is the latest refund window?", D("latest", "refund-policy", ["latest"]))
    assert r.intent == "latest"


def test_doc_matched_by_partial_name():
    r = run("semver 1.0.0 vs 2.0.0", D("compare", "semver", ["1.0.0", "2.0.0"]))
    assert (r.doc_id, r.versions) == ("semver/semver", ["v1.0.0", "v2.0.0"])


def test_llm_version_not_in_question_is_ignored():
    # the doc title "Semantic Versioning 2.0.0" tempts the LLM to add v2.0.0
    r = run("When must I increment the major version in semantic versioning?", D("version", "semver/semver", ["v2.0.0"]))
    assert r.intent == "latest"


def test_explicit_unknown_version_is_unclear_even_if_llm_drops_it():
    r = run("What did version 7 of the refund policy say?", D("version", "refund-policy", []))
    assert r.intent == "unclear" and "7 not found" in r.note


def test_same_version_written_twice_counts_once():
    r = run("What did v9 (version 9) say?", D("version", "refund-policy", ["v9"]))
    assert r.note.startswith("Version v9 not found")


@pytest.mark.parametrize(
    "q,general",
    [
        ("What is new in the latest leave policy?", True),
        ("What changed between v1 and v3 of the refund policy?", True),
        ("What are the differences from the previous version?", True),
        ("How did the restocking fee rules change in v2 of the refund policy?", False),
        ("What changed about carry-over?", False),
    ],
)
def test_is_general_question(q, general):
    from vrag.answer import is_general_question

    assert is_general_question(q, "leave-policy" if "leave" in q else "refund-policy", "Acme Retail Refund Policy") is general
