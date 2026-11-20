import logging

from vrag import rerank


def hit(i, text="body"):
    return {"chunk_id": f"c{i}", "doc_title": "Doc", "section_title": f"S{i}", "text": text}


def loader(scores, calls=None):
    def load(model, cache):
        if calls is not None:
            calls.append((model, cache))
        return lambda q, passages: scores[: len(passages)]

    return load


def make(load):
    return rerank.Reranker("m", "cache", load=load)


def test_orders_by_score_best_first():
    hits = [hit(0), hit(1), hit(2)]
    out = make(loader([0.1, 0.9, 0.5])).rerank("q", hits)
    assert [h["chunk_id"] for h in out] == ["c1", "c2", "c0"]


def test_equal_scores_keep_search_order():
    hits = [hit(0), hit(1), hit(2)]
    out = make(loader([1.0, 1.0, 1.0])).rerank("q", hits)
    assert [h["chunk_id"] for h in out] == ["c0", "c1", "c2"]


def test_fewer_than_two_hits_never_load_the_model():
    calls = []
    r = make(loader([1.0], calls))
    assert r.rerank("q", []) == []
    assert r.rerank("q", [hit(0)]) == [hit(0)]
    assert calls == []


def test_model_loads_once():
    calls = []
    r = make(loader([0.2, 0.1], calls))
    r.rerank("q", [hit(0), hit(1)])
    r.rerank("q", [hit(0), hit(1)])
    assert calls == [("m", "cache")]


def test_load_failure_keeps_order_and_warns_once(caplog):
    def broken(model, cache):
        raise OSError("no network")

    r = make(broken)
    hits = [hit(0), hit(1)]
    with caplog.at_level(logging.WARNING):
        assert r.rerank("q", hits) == hits
        assert r.rerank("q", hits) == hits
    assert len([m for m in caplog.records if m.levelno == logging.WARNING]) == 1


def test_load_failure_is_retried_after_a_pause(monkeypatch):
    attempts = []

    def flaky(model, cache):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("down")
        return lambda q, p: [0.0, 1.0]

    r = make(flaky)
    hits = [hit(0), hit(1)]
    assert r.rerank("q", hits) == hits
    assert r.rerank("q", hits) == hits  # still inside the pause
    assert len(attempts) == 1
    r._failed_at -= rerank.RETRY_AFTER_S + 1
    assert [h["chunk_id"] for h in r.rerank("q", hits)] == ["c1", "c0"]


def test_scoring_failure_or_bad_scores_keep_order():
    hits = [hit(0), hit(1)]

    def raises(model, cache):
        def score(q, p):
            raise RuntimeError("boom")

        return score

    assert make(raises).rerank("q", hits) == hits
    assert make(loader([float("nan"), 1.0])).rerank("q", hits) == hits
    assert make(loader([1.0])).rerank("q", hits) == hits  # wrong number of scores


def test_passage_joins_title_section_text_and_cuts_long_text():
    p = rerank.passage(hit(1, "line one\n\n  line   two"))
    assert p == "Doc - S1 line one line two"
    long = rerank.passage(hit(1, "word " * 1000), max_chars=100)
    assert len(long) <= 100 and not long.endswith(" ")


def test_get_keeps_one_model():
    a = rerank.get("m1", "c")
    assert rerank.get("m1", "c") is a
    assert rerank.get("m2", "c") is not a
