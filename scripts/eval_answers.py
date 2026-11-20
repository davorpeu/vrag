"""Answer eval: run data/golden/answer_cases.json through vrag.answer.ask in-process and score it.

Checks per answer (vrag.evals, the same rules as tests/e2e/test_answers.py): route, versions,
facts, forbidden facts, citations only from the routed versions, no unknown citations, quotes
verbatim in the cited chunk, both sides cited for a comparison, grounding status. Metrics overall,
per intent, per document and per tag. Each run also records evidence for the failure report: the
router LLM's decision, every search, the sources the answer LLM saw and its raw output.

Usage (.venv/Scripts/python scripts/eval_answers.py ...):
  (no options)            every case once
  --only ID_OR_TAG        cases with this id or tag (repeatable, or comma-separated)
  --failed-from FILE      the cases that failed in an earlier output file
  --repeat N              each case N times: pass / flaky / fail (the local LLM is not fully deterministic)
  --out FILE              output JSON (default data/evals/answers-<time>.json; data/evals/ is gitignored)
  --check-cases           no LLM: check every case against the indexed text and exit
  --task ID               task id written into the LLM lock (default W3)
  --lock FILE             LLM lock file (default agents/runs/2026-11-20/llm.lock)

Output: the JSON (every run with its evidence) and a short text summary next to it (same name, .txt).
The run holds the LLM lock (a file holding task id and start time) and refuses to start if a lock
younger than 45 minutes exists.

Needs Elasticsearch with the eval index (ES_INDEX=vrag-eval unless set; build it with
scripts/build_eval_index.py) and Ollama. LLM-heavy: about 10 s per question with qwen2.5:7b.
"""

import argparse
import functools
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

os.environ.setdefault("ES_INDEX", "vrag-eval")  # eval index, not the user's library
load_dotenv(".env")


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


# With Langfuse stopped, every trace flush waits ~6.5 s and logs an export error: switch tracing off.
if not _up(f"{os.environ.get('LANGFUSE_HOST', 'http://localhost:3000')}/api/public/health"):
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")

from vrag import evals, store  # noqa: E402
from vrag.config import get_settings  # noqa: E402

CASES = Path("data/golden/answer_cases.json")
OUT_DIR = Path("data/evals")
LOCK = Path("agents/runs/2026-11-20/llm.lock")
LOCK_MAX_AGE_S = 45 * 60


# --- evidence for the failure report ----------------------------------------------


def _hit(h: dict, label: str | None = None) -> dict:
    out = {
        "chunk_id": h.get("chunk_id"),
        "version": h.get("version"),
        "section": f"{h.get('section_number')} {h.get('section_title')}",
        "text": re.sub(r"\s+", " ", h.get("text") or "")[:220],
    }
    return {"label": label, **out} if label else out


class _LLMProxy:
    """Stands in for the LangChain chain returned by answer._llm and records each output."""

    def __init__(self, chain, schema, events: list):
        self._chain, self._schema, self._events = chain, schema, events

    def invoke(self, inputs, *args, **kwargs):
        out = self._chain.invoke(inputs, *args, **kwargs)
        try:
            self._events.append(
                {
                    "event": "answer_llm",
                    "schema": getattr(self._schema, "__name__", str(self._schema)),
                    "question": inputs.get("question") if isinstance(inputs, dict) else None,
                    "output": out.model_dump() if hasattr(out, "model_dump") else repr(out),
                }
            )
        except Exception:  # noqa: BLE001  (tracing never breaks a run)
            pass
        return out

    def __getattr__(self, name):
        return getattr(self._chain, name)


class Tracer:
    """Wraps a few internals to record what happened inside one ask(); results are unchanged.
    Anything it cannot find (another worker may rename it) is skipped and listed in `skipped`."""

    def __init__(self):
        self.events: list[dict] = []
        self.skipped: list[str] = []

    def take(self) -> list[dict]:
        events, self.events = self.events, []
        return events

    def install(self) -> None:
        from vrag import answer, router

        self._wrap(router, "classify", self._decision)
        self._wrap(store, "hybrid_search", self._search)
        self._wrap(answer, "format_context", self._sources)
        orig = getattr(answer, "_llm", None)
        if orig is None:
            self.skipped.append("answer._llm")
        else:
            answer._llm = lambda prompt, schema: _LLMProxy(orig(prompt, schema), schema, self.events)

    def _wrap(self, module, name: str, record) -> None:
        orig = getattr(module, name, None)
        if orig is None:
            self.skipped.append(f"{module.__name__}.{name}")
            return

        @functools.wraps(orig)
        def wrapper(*args, **kwargs):
            out = orig(*args, **kwargs)
            try:
                record(out, *args, **kwargs)
            except Exception as e:  # noqa: BLE001
                self.events.append({"event": "trace_error", "where": name, "error": repr(e)})
            return out

        setattr(module, name, wrapper)

    def _decision(self, decision, *args, **kwargs):
        self.events.append({"event": "router_llm", "decision": decision.model_dump()})

    def _search(self, hits, *args, **kwargs):
        filters = kwargs.get("filters", args[1] if len(args) > 1 else None)
        top_k = kwargs.get("top_k", args[2] if len(args) > 2 else None)
        self.events.append({"event": "search", "filters": filters, "top_k": top_k, "hits": [_hit(h) for h in hits]})

    def _sources(self, out, hits, *args, **kwargs):
        self.events.append({"event": "llm_sources", "hits": [_hit(h, f"S{i}") for i, h in enumerate(hits, 1)]})


# --- run info ---------------------------------------------------------------------


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"(git failed: {e})"


def run_info() -> dict:
    s = {k: v for k, v in get_settings().model_dump().items() if not re.search(r"secret|password|_key$", k)}
    s = {k: re.sub(r"//[^/@\s]+@", "//***@", v) if isinstance(v, str) else v for k, v in s.items()}
    return {
        "settings": s,
        "rerank": {
            "settings": {k: v for k, v in s.items() if "rerank" in k} or None,
            "module_vrag_rerank": importlib.util.find_spec("vrag.rerank") is not None,
        },
        "langfuse_tracing": os.environ.get("LANGFUSE_TRACING_ENABLED", "true"),
        "git": {
            "head": _git("rev-parse", "--short", "HEAD"),
            "diff_stat": _git("diff", "--stat"),
            "untracked": _git("ls-files", "--others", "--exclude-standard").splitlines(),
        },
        "python": platform.python_version(),
    }


# --- LLM lock, summary ----------------------------------------------------------------


def take_lock(path: Path, task: str) -> bool:
    """Create the lock file; False if a lock younger than 45 minutes exists."""
    if path.exists() and time.time() - path.stat().st_mtime < LOCK_MAX_AGE_S:
        print(f"LLM lock {path} is held: {path.read_text(encoding='utf-8').strip()!r}. Try later.")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{task} {datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
    return True


def summary_text(result: dict) -> str:
    m = result["metrics"]["overall"]
    lines = [
        f"Answer eval {result['started']} - index {result['index']}, model {result['settings'].get('llm_model')}, "
        f"git {result['git']['head']}, rerank {result['rerank']}",
        f"passed {m['passed']}/{m['runs']} runs in {result['elapsed_s']} s; errors {m['errors']}",
        "",
        evals.metrics_table(result["metrics"]),
        "",
        f"grounding: {m['grounding']}; insufficient: {m['insufficient']}",
    ]
    bad = [r for r in result["runs"] if not r["passed"]]
    lines.append(f"\nfailing runs ({len(bad)}):" if bad else "\nno failing runs")
    for r in bad:
        route = (r["got"] or {}).get("route") or {}
        lines.append(f"- {r['id']} (run {r['repeat']}): routed {route.get('intent')} {route.get('doc_id') or '-'} "
                     f"{route.get('versions') or ''}".rstrip())
        lines += [f"    {f[:300]}" for f in r["failed"]]
    return "\n".join(lines) + "\n"


# --- modes ------------------------------------------------------------------------


def version_text(doc_id: str, version: str) -> str:
    return "\n".join(c["text"] for c in store.version_chunks(doc_id, version))


def check_cases(cases: list[evals.Case]) -> int:
    catalog = store.catalog()
    bad = 0
    for c in cases:
        problems = evals.verify_case(c, catalog, functools.lru_cache(maxsize=None)(version_text))
        if problems:
            bad += 1
            print(f"PROBLEM {c.id}")
            for p in problems:
                print(f"    {p}")
    counts = {i: sum(c.intent == i for c in cases) for i in evals.INTENTS}
    docs = sorted({c.doc or evals.NO_DOC for c in cases})
    print(f"\n{len(cases) - bad}/{len(cases)} cases match the index {get_settings().es_index}")
    print(f"intents: {counts}; docs: {docs}")
    return 1 if bad else 0


def _failed_ids(path: Path) -> list[str]:
    runs = json.loads(path.read_text(encoding="utf-8"))["runs"]
    return list(dict.fromkeys(r["id"] for r in runs if not r["passed"]))


def run(cases: list[evals.Case], repeat: int, out: Path, args: dict) -> int:
    from vrag import answer

    tracer = Tracer()
    tracer.install()
    catalog = store.catalog()
    latest_of = {d["doc_id"]: d["versions"][-1]["version"] for d in catalog}
    texts: dict[str, str | None] = {}

    def chunk_text(chunk_id: str) -> str | None:
        if chunk_id not in texts:
            try:
                doc = store.es().get(index=get_settings().es_index, id=chunk_id, source_excludes=["embedding"])
                texts[chunk_id] = doc["_source"]["text"]
            except Exception:  # noqa: BLE001  (missing chunk -> failed quote check)
                texts[chunk_id] = None
        return texts[chunk_id]

    started = datetime.now()
    result = {
        "started": started.isoformat(timespec="seconds"),
        "args": args,
        "cases_file": str(CASES),
        "index": get_settings().es_index,
        **run_info(),
        "tracer_skipped": tracer.skipped,
        "complete": False,
        "runs": [],
    }
    print(f"index {result['index']}, {len(cases)} cases x {repeat}, model {get_settings().llm_model}, "
          f"rerank {result['rerank']}, tracing {result['langfuse_tracing']} -> {out}")
    if tracer.skipped:
        print(f"(not traced: {', '.join(tracer.skipped)})")

    out.parent.mkdir(parents=True, exist_ok=True)
    total = len(cases) * repeat
    for rep in range(1, repeat + 1):
        for case in cases:
            tracer.take()
            t0 = time.perf_counter()
            try:
                res, error = answer.ask(case.question, case.doc_id).model_dump(mode="json"), None
            except Exception as e:  # noqa: BLE001  (a crash is a failed run, the eval goes on)
                res, error = None, f"{type(e).__name__}: {e}"
            latency = round(time.perf_counter() - t0, 2)
            checks = evals.check_answer(case, res, latest_of, chunk_text) if res else [evals.Check("error", False, error)]
            rec = evals.score(case, res, checks, latency, error)
            route = (res or {}).get("route") or {}
            result["runs"].append(
                {
                    **rec,
                    "repeat": rep,
                    "question": case.question,
                    "focus": case.doc_id,
                    "expected": {
                        "intent": case.intent,
                        "doc": case.doc,
                        "versions": case.versions,
                        "facts": case.facts,
                        "forbidden": case.forbidden,
                        "grounding": case.expected_grounding,
                        "insufficient": case.insufficient,
                        "answer": case.expected,
                    },
                    "got": None
                    if res is None
                    else {
                        "route": route,
                        "answer": res.get("answer"),
                        "insufficient": res.get("insufficient_context"),
                        "grounding": res.get("grounding"),
                        "citations": [
                            {k: c.get(k) for k in ("source", "doc_id", "version", "section", "quote", "chunk_id")}
                            for c in res.get("citations") or []
                        ],
                        "unknown_citations": res.get("unknown_citations"),
                    },
                    "trace": tracer.take(),
                }
            )
            n = len(result["runs"])
            got = f"{route.get('intent')} {route.get('doc_id') or '-'} {route.get('versions') or ''}".rstrip()
            print(f"[{n}/{total}] {'PASS' if rec['passed'] else 'FAIL'} {case.id} ({latency:.1f}s) route: {got}")
            for f in rec["failed"]:
                print(f"      {f[:300]}")
            out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")  # partial results

    records = [{k: r[k] for k in r if k not in ("expected", "got", "trace")} for r in result["runs"]]
    result["metrics"] = evals.aggregate(records)
    result["stability"] = evals.stability(records)
    result["finished"] = datetime.now().isoformat(timespec="seconds")
    result["elapsed_s"] = round((datetime.now() - started).total_seconds(), 1)
    result["complete"] = True
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")

    print("\n" + evals.metrics_table(result["metrics"]))
    m = result["metrics"]["overall"]
    print(f"\ngrounding: {m['grounding']}; insufficient: {m['insufficient']}; errors: {m['errors']}")
    by_status: dict[str, list[str]] = {"pass": [], "flaky": [], "fail": []}
    for cid, s in result["stability"].items():
        by_status[s["status"]].append(cid if repeat == 1 else f"{cid} ({s['passed']}/{s['runs']})")
    for status in ("fail", "flaky"):
        if by_status[status]:
            print(f"{status} ({len(by_status[status])}): {', '.join(by_status[status])}")
    out.with_suffix(".txt").write_text(summary_text(result), encoding="utf-8")
    print(f"\npassed {m['passed']}/{m['runs']} runs in {result['elapsed_s']} s -> {out} (+ .txt summary)")
    return 0 if m["passed"] == m["runs"] else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # answers may hold non-ASCII text
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", action="append", default=[], help="case id or tag (repeatable, comma-separated)")
    p.add_argument("--failed-from", type=Path, help="re-run the cases that failed in this output file")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--out", type=Path)
    p.add_argument("--check-cases", action="store_true", help="check the cases against the index (no LLM)")
    p.add_argument("--task", default="W3", help="task id for the LLM lock")
    p.add_argument("--lock", type=Path, default=LOCK, help="LLM lock file")
    a = p.parse_args()

    cases = evals.load_cases(CASES)
    only = [k for x in a.only for k in x.split(",")]
    if a.failed_from:
        only += _failed_ids(a.failed_from)
        if not only:
            print(f"no failed cases in {a.failed_from}")
            return 0
    cases = evals.select(cases, only)
    if not cases:
        print(f"no case matches {only}")
        return 2
    if a.check_cases:
        return check_cases(cases)
    out = a.out or OUT_DIR / f"answers-{datetime.now():%Y%m%d-%H%M%S}.json"
    args = {"only": only, "repeat": a.repeat, "failed_from": str(a.failed_from) if a.failed_from else None}
    if not take_lock(a.lock, a.task):
        return 3
    try:
        return run(cases, max(1, a.repeat), out, args)
    finally:
        a.lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
