"""Elasticsearch index + hybrid retrieval (BM25 + dense kNN, fused with RRF in Python).

RRF is done client-side so it works on the free Basic license; ES's native `rrf`
retriever can replace `_rrf` later if the license allows it.
"""

from dataclasses import asdict
from functools import lru_cache

from elasticsearch import Elasticsearch, helpers
from langchain_ollama import OllamaEmbeddings

from vrag.config import get_settings
from vrag.models import Chunk

# nomic-embed-text is trained with task prefixes
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


@lru_cache
def es() -> Elasticsearch:
    return Elasticsearch(get_settings().es_url)


@lru_cache
def embedder() -> OllamaEmbeddings:
    s = get_settings()
    return OllamaEmbeddings(model=s.embed_model, base_url=s.ollama_base_url)


def mapping(dims: int) -> dict:
    return {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "doc_id": {"type": "keyword"},
            "doc_title": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
            "version": {"type": "keyword"},
            "version_num": {"type": "integer"},
            "effective_date": {"type": "date"},
            "source": {"type": "keyword"},
            "source_ref": {"type": "keyword"},
            "content_hash": {"type": "keyword"},
            "is_latest": {"type": "boolean"},
            "section_number": {"type": "keyword"},
            "section_title": {"type": "text"},
            "text": {"type": "text", "analyzer": "english"},
            "embedding": {"type": "dense_vector", "dims": dims, "index": True, "similarity": "cosine"},
        }
    }


def ensure_index(recreate: bool = False) -> None:
    s = get_settings()
    if recreate and es().indices.exists(index=s.es_index):
        es().indices.delete(index=s.es_index)
    if not es().indices.exists(index=s.es_index):
        es().indices.create(index=s.es_index, mappings=mapping(s.embed_dims))


def indexed_meta(doc_id: str, version: str) -> tuple[str, int] | None:
    """(content_hash, version_num) currently indexed for this doc version, if any."""
    s = get_settings()
    res = es().search(
        index=s.es_index,
        size=1,
        query={"bool": {"filter": [{"term": {"doc_id": doc_id}}, {"term": {"version": version}}]}},
        source=["content_hash", "version_num"],
    )
    hits = res["hits"]["hits"]
    return (hits[0]["_source"]["content_hash"], hits[0]["_source"]["version_num"]) if hits else None


def indexed_hash(doc_id: str, version: str) -> str | None:
    meta = indexed_meta(doc_id, version)
    return meta[0] if meta else None


def set_version_num(doc_id: str, version: str, version_num: int) -> None:
    """Renumber an indexed version without re-embedding (e.g. a back-dated version was inserted)."""
    s = get_settings()
    es().update_by_query(
        index=s.es_index,
        query={"bool": {"filter": [{"term": {"doc_id": doc_id}}, {"term": {"version": version}}]}},
        script={"source": "ctx._source.version_num = params.n", "params": {"n": version_num}},
        refresh=True,
    )


def replace_version(chunks: list[Chunk]) -> None:
    """Delete old chunks of this doc version, then index the new ones with embeddings."""
    s = get_settings()
    first = chunks[0]
    es().delete_by_query(
        index=s.es_index,
        query={"bool": {"filter": [{"term": {"doc_id": first.doc_id}}, {"term": {"version": first.version}}]}},
        refresh=True,
    )
    vectors = embedder().embed_documents(
        [f"{DOC_PREFIX}{c.doc_title} - {c.section_title}\n{c.text}" for c in chunks]
    )
    actions = [
        {
            "_index": s.es_index,
            "_id": c.chunk_id,
            "_source": {**asdict(c), "effective_date": c.effective_date.isoformat(), "is_latest": False, "embedding": v},
        }
        for c, v in zip(chunks, vectors)
    ]
    helpers.bulk(es(), actions, refresh=True)


def indexed_versions(doc_id: str) -> set[str]:
    """Version labels currently indexed for a document."""
    s = get_settings()
    res = es().search(
        index=s.es_index,
        size=0,
        query={"term": {"doc_id": doc_id}},
        aggs={"v": {"terms": {"field": "version", "size": 10000}}},
    )
    return {b["key"] for b in res["aggregations"]["v"]["buckets"]}


def delete_version(doc_id: str, version: str) -> None:
    es().delete_by_query(
        index=get_settings().es_index,
        query={"bool": {"filter": [{"term": {"doc_id": doc_id}}, {"term": {"version": version}}]}},
        refresh=True,
    )


def refresh_latest(doc_id: str) -> None:
    """Mark only the highest version_num of a doc as is_latest."""
    s = get_settings()
    res = es().search(
        index=s.es_index,
        size=0,
        query={"term": {"doc_id": doc_id}},
        aggs={"max_v": {"max": {"field": "version_num"}}},
    )
    max_v = res["aggregations"]["max_v"]["value"]
    if max_v is None:
        return
    es().update_by_query(
        index=s.es_index,
        query={"term": {"doc_id": doc_id}},
        script={"source": "ctx._source.is_latest = (ctx._source.version_num == params.v)", "params": {"v": int(max_v)}},
        refresh=True,
    )


def _rrf(rankings: list[list[str]], k: int) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)


def hybrid_search(question: str, filters: list[dict] | None = None, top_k: int | None = None) -> list[dict]:
    s = get_settings()
    top_k = top_k or s.top_k
    filters = filters or [{"term": {"is_latest": True}}]
    src = {"excludes": ["embedding"]}

    bm25 = es().search(
        index=s.es_index,
        size=s.candidates,
        query={
            "bool": {
                "must": {"multi_match": {"query": question, "fields": ["text", "section_title^2", "doc_title^2"]}},
                "filter": filters,
            }
        },
        source=src,
    )["hits"]["hits"]

    qvec = embedder().embed_query(f"{QUERY_PREFIX}{question}")
    knn = es().search(
        index=s.es_index,
        size=s.candidates,
        knn={"field": "embedding", "query_vector": qvec, "k": s.candidates, "num_candidates": 100, "filter": filters},
        source=src,
    )["hits"]["hits"]

    by_id = {h["_id"]: h["_source"] for h in bm25 + knn}
    fused = _rrf([[h["_id"] for h in bm25], [h["_id"] for h in knn]], s.rrf_k)
    return [by_id[i] for i in fused[:top_k]]


def catalog() -> list[dict]:
    """Every indexed doc with its versions, oldest first:
    [{"doc_id", "doc_title", "versions": [{"version", "version_num", "effective_date", "is_latest"}]}]
    """
    s = get_settings()
    res = es().search(
        index=s.es_index,
        size=0,
        aggs={
            "docs": {
                "terms": {"field": "doc_id", "size": 1000},
                "aggs": {
                    "versions": {
                        "terms": {"field": "version", "size": 1000},
                        "aggs": {
                            "info": {
                                "top_hits": {
                                    "size": 1,
                                    "_source": ["doc_title", "version_num", "effective_date", "is_latest"],
                                }
                            }
                        },
                    }
                },
            }
        },
    )
    out = []
    for d in res["aggregations"]["docs"]["buckets"]:
        versions = []
        for v in d["versions"]["buckets"]:
            src = v["info"]["hits"]["hits"][0]["_source"]
            versions.append(
                {
                    "version": v["key"],
                    "version_num": src["version_num"],
                    "effective_date": src["effective_date"],
                    "is_latest": src["is_latest"],
                    "doc_title": src["doc_title"],
                }
            )
        versions.sort(key=lambda x: x["version_num"])
        out.append({"doc_id": d["key"], "doc_title": versions[-1]["doc_title"], "versions": versions})
    return sorted(out, key=lambda d: d["doc_id"])


def version_chunks(doc_id: str, version: str) -> list[dict]:
    """All chunks of one doc version in document order (chunk_id ends with the chunk index)."""
    s = get_settings()
    res = es().search(
        index=s.es_index,
        size=1000,
        query={"bool": {"filter": [{"term": {"doc_id": doc_id}}, {"term": {"version": version}}]}},
        source={"excludes": ["embedding"]},
    )
    hits = [h["_source"] for h in res["hits"]["hits"]]
    return sorted(hits, key=lambda h: int(h["chunk_id"].rsplit(":", 1)[1]))
