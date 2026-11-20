"""Offline tests of store.py search helpers: Elasticsearch and the embedder are faked."""

import pytest

from vrag import rerank, store
from vrag.config import get_settings


def doc(i):
    return {"chunk_id": f"c{i}", "doc_id": "d", "doc_title": "D", "section_title": "S", "text": f"t{i}"}


class FakeReranker:
    def __init__(self):
        self.seen = None

    def rerank(self, question, hits):
        self.seen = [h["chunk_id"] for h in hits]
        return list(reversed(hits))


@pytest.fixture
def search(monkeypatch):
    # bm25: c0 c1 c2 c3 ; knn: c3 c2 c1 c0 -> RRF ties broken by first appearance: fused order
    monkeypatch.setattr(store, "bm25_hits", lambda q, f, size=None: [{"_id": f"c{i}", "_source": doc(i)} for i in range(4)])
    monkeypatch.setattr(store, "knn_hits", lambda q, f, size=None: [{"_id": f"c{i}", "_source": doc(i)} for i in (3, 2, 1, 0)])
    fake = FakeReranker()
    monkeypatch.setattr(rerank, "get", lambda model, cache: fake)
    s = get_settings()
    monkeypatch.setattr(s, "rerank", False)
    monkeypatch.setattr(s, "rerank_candidates", 3)
    monkeypatch.setattr(s, "top_k", 4)
    return fake


def ids(hits):
    return [h["chunk_id"] for h in hits]


def test_filters():
    assert store.latest_filters() == [{"term": {"is_latest": True}}]
    assert store.latest_filters("a") == [{"term": {"is_latest": True}}, {"term": {"doc_id": "a"}}]
    assert store.version_filters("a", "v1") == [{"term": {"doc_id": "a"}}, {"term": {"version": "v1"}}]
    assert store.compare_filters("a", ["v1", "v2"]) == [{"term": {"doc_id": "a"}}, {"terms": {"version": ["v1", "v2"]}}]


def test_rrf_orders_by_fused_score():
    # a and c (1st in one list, 3rd in the other) score a little above b (2nd in both)
    assert store._rrf([["a", "b", "c"], ["c", "b", "a"]], 60) == ["a", "c", "b"]
    assert store._rrf([["a", "b"], ["a", "c"]], 60)[0] == "a"
    assert store._rrf([["a"], ["b"]], 60) == ["a", "b"]


def test_no_rerank_by_default(search):
    out = store.hybrid_search("q")
    assert search.seen is None
    assert ids(out) == ids(store.hybrid_search("q", rerank=False))
    assert len(out) == 4


def test_rerank_setting_on_reorders_only_the_candidates(search, monkeypatch):
    monkeypatch.setattr(get_settings(), "rerank", True)
    plain = ids(store.hybrid_search("q", rerank=False))
    out = ids(store.hybrid_search("q"))
    assert search.seen == plain[:3]
    assert out == list(reversed(plain[:3])) + plain[3:]


def test_rerank_argument_overrides_setting(search, monkeypatch):
    monkeypatch.setattr(get_settings(), "rerank", True)
    store.hybrid_search("q", rerank=False)
    assert search.seen is None
    monkeypatch.setattr(get_settings(), "rerank", False)
    store.hybrid_search("q", rerank=True)
    assert search.seen is not None


def test_top_k_cuts_after_rerank(search):
    out = store.hybrid_search("q", top_k=2, rerank=True)
    plain = ids(store.hybrid_search("q", rerank=False))
    assert ids(out) == list(reversed(plain[:3]))[:2]
