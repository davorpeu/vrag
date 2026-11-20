"""Cross-encoder reranking of search hits (fastembed: ONNX on the CPU, no torch).

A cross-encoder reads the question and one chunk together and scores how well the chunk answers
the question. That is more accurate than BM25 or vector similarity but slower, so it only reorders
the top fused hits (settings.rerank_candidates; see store.hybrid_search).

The model loads on first use; its files are cached in settings.models_dir (downloaded once).
If it cannot be loaded, or scoring fails, a warning is logged once and the hits keep their search
order: the reranker must never break answering.
"""

import logging
import math
import re
import threading
import time
from collections.abc import Callable, Sequence

log = logging.getLogger(__name__)

# The cross-encoder reads what the embedder reads (title - section + text), whitespace collapsed.
# MiniLM cross-encoders read at most 512 tokens (question included), about 2,000 characters of
# English; chunks are at most ~1,500 characters, so the cut only bounds the cost of odd chunks.
MAX_CHARS = 1500
RETRY_AFTER_S = 300.0  # after a failed load, try again at most this often

Scorer = Callable[[str, list[str]], Sequence[float]]  # (question, passages) -> one score each


def passage(hit: dict, max_chars: int = MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", f"{hit['doc_title']} - {hit['section_title']}\n{hit['text']}").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0]


def load_fastembed(model_name: str, cache_dir: str) -> Scorer:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    encoder = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)
    return lambda question, passages: list(encoder.rerank(question, passages))


class Reranker:
    """One cross-encoder model. Thread-safe: it loads once and scores one request at a time
    (scoring already uses every CPU core)."""

    def __init__(self, model_name: str, cache_dir: str, load: Callable[[str, str], Scorer] | None = None):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._load = load  # None = fastembed
        self._scorer: Scorer | None = None
        self._failed_at: float | None = None
        self._warned = False
        self._lock = threading.Lock()

    def rerank(self, question: str, hits: list[dict]) -> list[dict]:
        """The hits ordered by cross-encoder score, best first (equal scores keep their order).
        The hits unchanged if the model is unavailable or fails."""
        if len(hits) < 2:
            return list(hits)
        with self._lock:
            scorer = self._scorer_or_none()
            if scorer is None:
                return list(hits)
            try:
                scores = [float(x) for x in scorer(question, [passage(h) for h in hits])]
                if len(scores) != len(hits) or any(math.isnan(x) for x in scores):
                    raise ValueError(f"bad scores: {len(scores)} for {len(hits)} passages")
            except Exception as e:  # noqa: BLE001 - any failure falls back to the search order
                self._warn(f"reranker {self.model_name} failed ({e!r}); keeping the search order")
                return list(hits)
        order = sorted(range(len(hits)), key=lambda i: -scores[i])  # sorted() is stable
        return [hits[i] for i in order]

    def _scorer_or_none(self) -> Scorer | None:
        """Load the model if needed (call with the lock held). None while it cannot be loaded."""
        if self._scorer is not None:
            return self._scorer
        if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_AFTER_S:
            return None
        try:
            self._scorer = (self._load or load_fastembed)(self.model_name, self.cache_dir)
            self._failed_at = None
        except Exception as e:  # noqa: BLE001 - missing package, no network for the download, bad files...
            self._failed_at = time.monotonic()
            self._warn(f"reranker {self.model_name} could not be loaded ({e!r}); keeping the search order")
        return self._scorer

    def _warn(self, message: str) -> None:
        if self._warned:
            log.debug(message)
        else:
            self._warned = True
            log.warning(message)


_current: Reranker | None = None
_current_lock = threading.Lock()


def get(model_name: str, cache_dir: str) -> Reranker:
    """The reranker for this model. Only one model is kept in memory: asking for another one
    drops the old one."""
    global _current
    with _current_lock:
        if _current is None or (_current.model_name, _current.cache_dir) != (model_name, cache_dir):
            _current = Reranker(model_name, cache_dir)
        return _current
