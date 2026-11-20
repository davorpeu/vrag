"""Ask the golden questions (data/golden/answer_cases.json) through the real API and check each answer.

The cases and the checks live in vrag.evals (the same ones scripts/eval_answers.py uses): route,
versions, facts, forbidden facts, grounding status, citations only from the routed versions, no
unknown citations, both sides cited in a comparison, and quotes found verbatim in the cited chunk.
"""

from pathlib import Path

import pytest

from vrag import evals

pytestmark = pytest.mark.e2e

CASES = evals.load_cases(Path(__file__).parents[2] / "data" / "golden" / "answer_cases.json")


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_answer(case, client, catalog, chunk_text):
    r = client.post("/ask", json={"question": case.question, "doc_id": case.doc_id})
    assert r.status_code == 200, r.text
    res = r.json()

    latest_of = {d: v["versions"][-1]["version"] for d, v in catalog.items()}
    checks = evals.check_answer(case, res, latest_of, chunk_text)
    problems = evals.failures(checks)
    cited = [(c["doc_id"], c["version"], c["section"]) for c in res["citations"]]
    assert not problems, (
        "\n".join(problems) + f"\nroute={res['route']}\nanswer={res['answer']!r}\ncitations={cited}"
    )
