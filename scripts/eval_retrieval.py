"""Retrieval eval: does search rank the chunks that answer a question at the top?

Runs every case in data/golden/retrieval_cases.json with the filter the app uses for it and scores
the ranking against the chunks that hold the answer:
  recall@k  share of the relevant chunks in the top k (k = 1, 3, 6; the LLM sees the top 6)
  MRR       mean of 1 / rank of the first relevant chunk (0 if it is not in the top 20)
  doc@1     only for questions over every document's latest version: is the top hit from the
            right document? (the router's document check uses the top hit)
Overall, per document, per filter kind and per tag, with per-query latency, and the misses.

Modes:
  bm25    keyword search only
  knn     vector search only (nomic-embed-text)
  hybrid  both, fused with RRF (the search without reranking)
  rerank  hybrid, then the cross-encoder reorders the top rerank_candidates

Case format (data/golden/retrieval_cases.json, a list):
  {"id": ..., "q": "question",
   "filter": {"kind": "latest"}                              latest version of every document
           | {"kind": "latest", "doc_id": D}                 latest version of D (document picked in the UI)
           | {"kind": "version", "doc_id": D, "version": V}  one version
           | {"kind": "compare", "doc_id": D, "versions": [old, new]},
   "relevant": [item, ...],   every item is needed for a full answer (recall counts items)
   "tags": [...]}             keyword, paraphrase, older-value, short, short-query, look-alike, e2e
  item = {"doc_id", "version", "section"[, "quote"]}   one chunk: its section_number, plus a quote
                                                        when a section is split into several chunks
       | {"any": [item, ...]}                           chunks that each answer it on their own
Before running, every item is checked against the index: it must match exactly one chunk, and that
chunk must be inside the case's filter.

Usage: .venv/Scripts/python scripts/eval_retrieval.py [--modes bm25,knn,hybrid,rerank]
           [--model NAME] [--candidates N] [--label TEXT] [--check] [--verbose]
Writes data/evals/retrieval-<time>[-label].json. Uses the eval index (vrag-eval) unless ES_INDEX is
set. Only embedding calls (no LLM), so it needs no LLM lock. Run one reranker model per process.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("ES_INDEX", "vrag-eval")  # eval index, not the user's library
load_dotenv(".env")

from vrag import rerank as reranking  # noqa: E402
from vrag import store  # noqa: E402
from vrag.config import get_settings  # noqa: E402

CASES_FILE = Path("data/golden/retrieval_cases.json")
OUT_DIR = Path("data/evals")
DEPTH = 20  # ranks fetched per question; MRR counts a hit only within these
KS = (1, 3, 6)
MODES = ("bm25", "knn", "hybrid", "rerank")

Search = Callable[[str, list[dict]], list[dict]]  # (question, filters) -> chunks, best first


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def label(chunk: dict) -> str:
    return f"{chunk['doc_id']}@{chunk['version']}#{chunk['section_number']}"


def _item_label(alt: dict) -> str:
    quote = f" \"{alt['quote'][:30]}...\"" if "quote" in alt else ""
    return f"{alt['doc_id']}@{alt['version']}#{alt['section']}{quote}"


def item_text(item: dict) -> str:
    return " or ".join(_item_label(a) for a in alternatives(item))


def alternatives(item: dict) -> list[dict]:
    return item["any"] if "any" in item else [item]


def filters_for(f: dict) -> list[dict]:
    """The search filter the app builds for this kind of question (answer.py uses the same helpers)."""
    if f["kind"] == "latest":
        return store.latest_filters(f.get("doc_id"))
    if f["kind"] == "version":
        return store.version_filters(f["doc_id"], f["version"])
    if f["kind"] == "compare":
        return store.compare_filters(f["doc_id"], f["versions"])
    raise ValueError(f"unknown filter kind {f['kind']!r}")


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_cases(cases: list[dict]) -> tuple[list[list[set[str]]], list[str]]:
    """Resolve every relevant item to its chunk ids in the index.
    Returns (per case: one set of chunk ids per item, errors)."""
    latest = {d["doc_id"]: d["versions"][-1]["version"] for d in store.catalog()}
    chunks: dict[tuple[str, str], list[dict]] = {}
    resolved, errors, seen = [], [], set()
    for c in cases:
        if c["id"] in seen:
            errors.append(f"{c['id']}: duplicate id")
        seen.add(c["id"])
        f = c["filter"]
        items = []
        for item in c["relevant"]:
            ids = set()
            for alt in alternatives(item):
                key = (alt["doc_id"], alt["version"])
                if key not in chunks:
                    chunks[key] = store.version_chunks(*key)
                found = [
                    ch for ch in chunks[key]
                    if ch["section_number"] == alt["section"]
                    and ("quote" not in alt or _norm(alt["quote"]) in _norm(ch["text"]))
                ]
                where = f"{c['id']}: {_item_label(alt)}"
                if len(found) != 1:
                    errors.append(f"{where} matches {len(found)} chunks (needs exactly 1; add a quote if the section is split)")
                    continue
                # the chunk must be reachable with the case's filter
                if f["kind"] == "latest" and (alt["version"] != latest.get(alt["doc_id"]) or f.get("doc_id", alt["doc_id"]) != alt["doc_id"]):
                    errors.append(f"{where} is not the latest version of the filtered document(s)")
                if f["kind"] == "version" and (alt["doc_id"], alt["version"]) != (f["doc_id"], f["version"]):
                    errors.append(f"{where} is outside the filter {f['doc_id']}@{f['version']}")
                if f["kind"] == "compare" and (alt["doc_id"] != f["doc_id"] or alt["version"] not in f["versions"]):
                    errors.append(f"{where} is outside the compared versions {f['versions']}")
                ids.add(found[0]["chunk_id"])
            items.append(ids)
        resolved.append(items)
    return resolved, errors


# --- running and scoring ---------------------------------------------------


def score_case(case: dict, items: list[set[str]], ranking: list[dict]) -> dict:
    ids = [h["chunk_id"] for h in ranking]
    ranks = [next((r for r, i in enumerate(ids, start=1) if i in item), None) for item in items]
    first = min((r for r in ranks if r), default=None)
    out = {f"recall@{k}": sum(1 for r in ranks if r and r <= k) / len(items) for k in KS}
    out["rr"] = 1 / first if first else 0.0
    out["first"] = first
    out["ranks"] = ranks
    docs = {a["doc_id"] for item in case["relevant"] for a in alternatives(item)}
    all_docs = case["filter"]["kind"] == "latest" and not case["filter"].get("doc_id")
    out["doc@1"] = (bool(ranking) and ranking[0]["doc_id"] in docs) if all_docs else None
    out["top"] = [label(h) for h in ranking[:6]]
    return out


def run_mode(search: Search, cases: list[dict], resolved: list[list[set[str]]]) -> dict:
    """Run every case once (after one untimed warm-up query) and score it."""
    t = time.perf_counter()
    search(cases[0]["q"], filters_for(cases[0]["filter"]))  # loads models (embedder, reranker)
    warmup_ms = (time.perf_counter() - t) * 1000
    results = []
    for case, items in zip(cases, resolved):
        filters = filters_for(case["filter"])
        t = time.perf_counter()
        ranking = search(case["q"], filters)[:DEPTH]
        ms = (time.perf_counter() - t) * 1000
        results.append({"id": case["id"], **score_case(case, items, ranking), "ms": round(ms, 1)})
    return {"warmup_ms": round(warmup_ms), "rss_mb": rss_mb(), "cases": results}


def _metrics(rows: list[dict]) -> dict:
    out = {"n": len(rows)}
    for k in KS:
        out[f"recall@{k}"] = round(statistics.mean(r[f"recall@{k}"] for r in rows), 3)
    out["mrr"] = round(statistics.mean(r["rr"] for r in rows), 3)
    doc1 = [r["doc@1"] for r in rows if r["doc@1"] is not None]
    out["doc@1"] = round(statistics.mean(doc1), 3) if doc1 else None
    out["doc@1_n"] = len(doc1)
    return out


def summarize(run: dict, cases: list[dict]) -> dict:
    rows = run["cases"]
    groups: dict[str, dict[str, list[dict]]] = {"doc": defaultdict(list), "kind": defaultdict(list), "tag": defaultdict(list)}
    for case, r in zip(cases, rows):
        groups["doc"][alternatives(case["relevant"][0])[0]["doc_id"]].append(r)
        kind = case["filter"]["kind"]
        groups["kind"]["latest+doc" if kind == "latest" and case["filter"].get("doc_id") else kind].append(r)
        for tag in case.get("tags", []):
            groups["tag"][tag].append(r)
    ms = sorted(r["ms"] for r in rows)
    return {
        "overall": _metrics(rows),
        "groups": {g: {k: _metrics(v) for k, v in sorted(by.items())} for g, by in groups.items()},
        "latency_ms": {
            "mean": round(statistics.mean(ms), 1),
            "p50": round(ms[len(ms) // 2], 1),
            "p95": round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1),
            "max": round(ms[-1], 1),
        },
    }


def rss_mb() -> dict | None:
    """Memory of this process in MB (working set now and peak); Windows only, else None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (n, ctypes.c_size_t)
            for n in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")
        ]

    k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    c = Counters(cb=ctypes.sizeof(Counters))
    if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
        return None
    return {"now": round(c.ws / 2**20), "peak": round(c.peak_ws / 2**20)}


# --- output -------------------------------------------------------------------


def _fmt(m: dict) -> str:
    d1 = f"{m['doc@1']:.3f}" if m["doc@1"] is not None else "  -  "
    return f"{m['n']:4} {m['recall@1']:6.3f} {m['recall@3']:6.3f} {m['recall@6']:6.3f} {m['mrr']:6.3f} {d1:>6}"


HEAD = "   n    R@1    R@3    R@6    MRR  doc@1"


def print_report(runs: dict[str, dict], cases: list[dict], verbose: bool = False) -> None:
    by_id = {c["id"]: c for c in cases}
    print(f"\n{'mode':8}{HEAD}   p50 ms  p95 ms  warm-up ms  RAM MB (now/peak)")
    for mode, run in runs.items():
        s, lat, rss = run["summary"], run["summary"]["latency_ms"], run.get("rss_mb")
        mem = f"{rss['now']}/{rss['peak']}" if rss else "-"
        print(f"{mode:8}{_fmt(s['overall'])}  {lat['p50']:7.1f} {lat['p95']:7.1f}  {run['warmup_ms']:10}  {mem}")
    doc1_n = next(iter(runs.values()))["summary"]["overall"]["doc@1_n"]
    print(f"(doc@1 over the {doc1_n} questions searched across all documents)")

    for group in ("doc", "kind", "tag"):
        print(f"\nper {group}: R@1 / R@6 / MRR")
        keys = next(iter(runs.values()))["summary"]["groups"][group].keys()
        print(f"{'':24}{'n':>4}  " + "  ".join(f"{m:>17}" for m in runs))
        for k in keys:
            cells = []
            for run in runs.values():
                m = run["summary"]["groups"][group][k]
                cells.append(f"{m['recall@1']:.2f}/{m['recall@6']:.2f}/{m['mrr']:.2f}")
            n = next(iter(runs.values()))["summary"]["groups"][group][k]["n"]
            print(f"{k:24}{n:>4}  " + "  ".join(f"{c:>17}" for c in cells))

    for mode, run in runs.items():
        missed = [r for r in run["cases"] if r["recall@6"] < 1]
        print(f"\n[{mode}] misses: a relevant chunk not in the top 6 ({len(missed)})")
        for r in missed:
            c = by_id[r["id"]]
            print(f"  {r['id']}: {c['q']}")
            for item, rank in zip(c["relevant"], r["ranks"]):
                print(f"      expected {item_text(item)}  -> rank {rank or f'>{DEPTH}'}")
            print(f"      got {', '.join(r['top'][:3])}")
        not_first = [r for r in run["cases"] if r["first"] != 1 and r["recall@6"] == 1]
        print(f"[{mode}] found in the top 6 but not first ({len(not_first)}):")
        for r in not_first:
            line = f"  {r['id']}: first relevant at rank {r['first']}, top 1 {r['top'][0]}"
            print(line if verbose else line[:150])

    if "hybrid" in runs and "rerank" in runs:
        a = {r["id"]: r for r in runs["hybrid"]["cases"]}
        better, worse = [], []
        for r in runs["rerank"]["cases"]:
            old, new = a[r["id"]]["first"] or DEPTH + 1, r["first"] or DEPTH + 1
            if new < old:
                better.append(f"{r['id']} ({old}->{new})")
            elif new > old:
                worse.append(f"{r['id']} ({old}->{new})")
        print(f"\nrerank vs hybrid, rank of the first relevant chunk: better in {len(better)}, worse in {len(worse)}")
        print("  better: " + (", ".join(better) or "-"))
        print("  worse:  " + (", ".join(worse) or "-"))


def save(runs: dict[str, dict], cases: list[dict], label_text: str = "") -> Path:
    s = get_settings()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"retrieval-{stamp}{'-' + label_text if label_text else ''}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "index": s.es_index,
        "cases": len(cases),
        "label": label_text,
        "settings": {k: getattr(s, k, None) for k in ("top_k", "candidates", "rrf_k", "rerank", "rerank_model", "rerank_candidates")},
    }
    path.write_text(json.dumps({"run": meta, "modes": runs}, indent=1), encoding="utf-8")
    return path


def time_rerank_calls() -> list[float]:
    """Record the duration (ms) of every cross-encoder call from now on. The first one includes
    loading the model. Query latency also includes the embedding call to the shared Ollama,
    which is noisy when others use it; this isolates the reranker's own cost."""
    times: list[float] = []
    original = reranking.Reranker.rerank

    def timed(self, question, hits):
        t = time.perf_counter()
        try:
            return original(self, question, hits)
        finally:
            times.append((time.perf_counter() - t) * 1000)

    reranking.Reranker.rerank = timed
    return times


def _stats(ms: list[float]) -> dict:
    ms = sorted(ms)
    return {"n": len(ms), "p50": round(ms[len(ms) // 2], 1), "p95": round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1), "max": round(ms[-1], 1)}


def mode_search(mode: str) -> Search:
    if mode == "bm25":
        return lambda q, f: [h["_source"] for h in store.bm25_hits(q, f)]
    if mode == "knn":
        return lambda q, f: [h["_source"] for h in store.knn_hits(q, f)]
    if mode == "hybrid":
        return lambda q, f: store.hybrid_search(q, filters=f, top_k=DEPTH, rerank=False)
    if mode == "rerank":
        return lambda q, f: store.hybrid_search(q, filters=f, top_k=DEPTH, rerank=True)
    raise ValueError(f"unknown mode {mode!r}; pick from {', '.join(MODES)}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--modes", default=",".join(MODES), help="comma-separated: " + ", ".join(MODES))
    p.add_argument("--model", help="cross-encoder for the rerank mode (default: settings.rerank_model)")
    p.add_argument("--candidates", type=int, help="how many fused hits the cross-encoder reorders")
    p.add_argument("--label", default="", help="added to the results file name")
    p.add_argument("--check", action="store_true", help="only check the cases against the index")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    s = get_settings()
    if args.model:
        s.rerank_model = args.model
    if args.candidates:
        s.rerank_candidates = args.candidates
    cases = load_cases()
    resolved, errors = check_cases(cases)
    print(f"{len(cases)} cases, index {s.es_index}: {len(errors)} problems")
    for e in errors:
        print(f"  {e}")
    if errors or args.check:
        return 1 if errors else 0

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    runs = {}
    for mode in modes:
        search = mode_search(mode)
        print(f"running {mode}...", flush=True)
        rerank_ms = time_rerank_calls() if mode == "rerank" else None
        runs[mode] = run_mode(search, cases, resolved)
        runs[mode]["summary"] = summarize(runs[mode], cases)
        if rerank_ms:
            runs[mode]["model_load_ms"] = round(rerank_ms[0])  # first call = warm-up, loads the model
            runs[mode]["rerank_step_ms"] = _stats(rerank_ms[1:])
    print_report(runs, cases, args.verbose)
    if "rerank" in runs:
        r = runs["rerank"]
        print(
            f"\nrerank: model {s.rerank_model}, top {s.rerank_candidates} fused hits reordered; "
            f"first call (loads the model) {r.get('model_load_ms', '-')} ms; "
            f"cross-encoder step alone {r.get('rerank_step_ms')}"
        )
    print(f"\nsaved {save(runs, cases, args.label)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
