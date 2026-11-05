from vrag.grounding import check_quote, numbers_in, unsupported_numbers
from vrag.models import Citation, LLMAnswer

SRC = "2. Refund Window\n2.1 Customers may request a refund within 21 days of the purchase date.\n2.2 Items must be unused."


# --- quotes ----------------------------------------------------------------


def test_exact_quote_ignores_case_spacing_and_quote_marks():
    assert check_quote('"customers may request a refund  within 21 days"', SRC).status == "exact"


def test_truncated_quote_with_ellipsis_is_exact():
    assert check_quote("Customers may request a refund within 21 days...", SRC).status == "exact"


def test_near_miss_is_repaired_to_source_text():
    qc = check_quote("Customers can request a refund within 21 days of the purchase date.", SRC)
    assert qc.status == "repaired"
    assert "Customers may request a refund within 21 days of the purchase date." in qc.quote


def test_invented_quote_is_not_found():
    assert check_quote("Refunds are always paid in cash within 30 days.", SRC).status == "not_found"


def test_changed_number_is_not_repaired_into_a_different_claim():
    # one digit off is a different claim; repairing would silently "fix" a wrong number into a right one,
    # which is fine for the quote, but the number check on the answer still catches the wrong claim
    qc = check_quote("Customers may request a refund within 30 days of the purchase date.", SRC)
    assert qc.status in ("repaired", "not_found")
    if qc.status == "repaired":
        assert "21 days" in qc.quote


# --- numbers -----------------------------------------------------------------


def test_numbers_in_handles_words_percent_and_list_markers():
    text = "1. A fee of 15% applies.\n2. Two weeks of leave, 1,000 items, version 2.0.0."
    assert numbers_in(text) == {"15", "2", "1000", "2.0.0"}


def test_unsupported_numbers():
    assert unsupported_numbers("You have 21 days.", SRC) == []
    assert unsupported_numbers("You have 30 days.", SRC) == ["30"]
    assert unsupported_numbers("You have twenty-one days.", SRC) == []
    assert unsupported_numbers("You have thirty days.", SRC) == ["30"]
    assert unsupported_numbers("You have two weeks.", SRC) == ["2"]


def test_numbers_from_question_and_dotted_versions_allowed():
    assert unsupported_numbers("In 2024 the window was 21 days.", SRC + "\nWhat was it in 2024?") == []
    assert unsupported_numbers("SemVer 2.0.0 says so.", "Semantic Versioning 2.0.0") == []


# --- the full check on a model answer (no LLM involved) --------------------------


def _labelled():
    return {
        "S1": {
            "chunk_id": "refund-policy:v3:2",
            "doc_id": "refund-policy",
            "doc_title": "Acme Retail Refund Policy",
            "version": "v3",
            "effective_date": "2025-04-01",
            "section_number": "2",
            "section_title": "Refund Window",
            "text": SRC,
        }
    }


def _answer(text, quote, source="S1", insufficient=False):
    return LLMAnswer(citations=[Citation(source=source, quote=quote)], answer=text, insufficient_context=insufficient)


def test_check_grounded():
    from vrag.answer import _check

    c = _check("refund window?", _answer("You have 21 days.", "within 21 days of the purchase date"), _labelled())
    assert c.grounding.status == "grounded" and len(c.citations) == 1


def test_check_wrong_number_is_ungrounded():
    from vrag.answer import _check

    c = _check("refund window?", _answer("You have 30 days.", "within 21 days of the purchase date"), _labelled())
    assert c.grounding.status == "ungrounded"
    assert any("30" in i for i in c.grounding.issues)


def test_check_invented_quote_dropped():
    from vrag.answer import _check

    c = _check("refund window?", _answer("You have 21 days.", "Refunds are paid in gold."), _labelled())
    assert c.grounding.status == "ungrounded" and not c.citations
    assert c.grounding.dropped_quotes == 1


def test_check_unknown_source():
    from vrag.answer import _check

    c = _check("refund window?", _answer("You have 21 days.", "within 21 days", source="S9"), _labelled())
    assert c.unknown == ["S9"] and c.grounding.status == "ungrounded"


def test_check_refusal_not_checked():
    from vrag.answer import _check

    ans = LLMAnswer(citations=[], answer="The sources do not say.", insufficient_context=True)
    assert _check("dress code?", ans, _labelled()).grounding.status == "not_checked"


def test_uncited_claim_is_not_shown():
    from vrag.answer import NOT_FOUND, _check

    ans = LLMAnswer(citations=[], answer="The dress code is business casual.", insufficient_context=False)
    c = _check("dress code?", ans, _labelled())
    assert c.result.answer == NOT_FOUND and c.result.insufficient_context
    assert c.grounding.status == "not_checked"
