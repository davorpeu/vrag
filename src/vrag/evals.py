"""Offline answer evals: golden cases, the checks for one answer, and metrics over many runs.

Pure functions, no services. The runner (scripts/eval_answers.py) and the end-to-end tests
(tests/e2e/test_answers.py) get an answer (AskResponse as a dict) and the text of the cited
chunks, then call check_answer().

A case (data/golden/answer_cases.json) says what a correct answer looks like:
  id, question    unique id; the question as a user would ask it
  doc_id          optional focus: the document the user picked in the UI (passed to ask())
  intent          expected route: latest | version | compare | unclear
  doc             expected document: the route's doc for version/compare/unclear, the doc of every
                  citation for latest; null = not checked (off-topic and two-document questions)
  versions        expected route versions, oldest first; null = not checked (latest)
  facts           regexes the answer must match (case-insensitive). A fact that starts with the regex
                  comment (?#answer) is about the answer's wording ("unchanged"), not the source text,
                  so verify_case() does not look for it in the corpus
  forbidden       regexes the answer must not match, e.g. the value an older version had
  grounding       expected grounding status; default not_checked for unclear/insufficient, else grounded
  insufficient    true if the right answer is "the documents don't cover this" (off-topic)
  only_sections   every citation's section must contain one of these (focused comparisons)
  expected        the right answer in words, for reports
  tags            free labels, used by --only and the per-tag metrics

On top of that every answer must (the rules of the e2e tests): cite something (unless the case is
unclear or insufficient), cite no unknown source, cite only the routed versions (latest route: each
doc's latest version), cite both sides of a comparison, and quote text that is really in the cited
chunk. A focus case must also keep the focus document in the route.
"""

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

INTENTS = ("latest", "version", "compare", "unclear")
STATUSES = ("grounded", "partial", "ungrounded", "not_checked")
NO_DOC = "(no doc)"
ANSWER_ONLY = "(?#answer)"  # fact about the answer's wording; not looked for in the source text


# --- cases --------------------------------------------------------------------


@dataclass
class Case:
    id: str
    question: str
    intent: str
    doc: str | None = None
    versions: list[str] | None = None
    facts: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    grounding: str | None = None
    insufficient: bool = False
    doc_id: str | None = None
    only_sections: list[str] | None = None
    expected: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def expected_grounding(self) -> str:
        if self.grounding:
            return self.grounding
        return "not_checked" if self.intent == "unclear" or self.insufficient else "grounded"

    def problems(self) -> list[str]:
        """Structural problems (no corpus needed)."""
        out = []
        if not self.id or not self.question:
            out.append("id and question are required")
        if self.intent not in INTENTS:
            out.append(f"intent {self.intent!r} is not one of {', '.join(INTENTS)}")
        if self.grounding is not None and self.grounding not in STATUSES:
            out.append(f"grounding {self.grounding!r} is not one of {', '.join(STATUSES)}")
        if self.insufficient and self.intent == "unclear":
            out.append("insufficient is for answered (not unclear) questions")
        for p in [*self.facts, *self.forbidden]:
            try:
                re.compile(p)
            except re.error as e:
                out.append(f"bad regex /{p}/: {e}")
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Case":
        unknown = set(d) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"case {d.get('id')!r}: unknown keys {sorted(unknown)}")
        case = cls(**d)
        problems = case.problems()
        if problems:
            raise ValueError(f"case {case.id!r}: " + "; ".join(problems))
        return case


def load_cases(path: str | Path) -> list[Case]:
    cases = [Case.from_dict(d) for d in json.loads(Path(path).read_text(encoding="utf-8"))]
    dupes = sorted(i for i, n in Counter(c.id for c in cases).items() if n > 1)
    if dupes:
        raise ValueError(f"duplicate case ids: {dupes}")
    return cases


def select(cases: list[Case], only: Iterable[str]) -> list[Case]:
    """Cases whose id, or one of whose tags, is in `only` (all cases if `only` is empty)."""
    keys = {k.strip() for k in only if k and k.strip()}
    if not keys:
        return list(cases)
    return [c for c in cases if c.id in keys or keys & set(c.tags)]


# --- checks for one answer -------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""  # why it failed (empty when ok)
    citation: int | None = None  # index of the citation this check is about


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in(quote: str, text: str) -> bool:
    """The quote is in the text, ignoring case and whitespace; a trailing '...' is dropped."""
    q = _norm(quote).removesuffix("...").strip()
    return bool(q) and q in _norm(text)


def check_answer(
    case: Case,
    res: Mapping,
    latest_of: Mapping[str, str],
    chunk_text: Callable[[str], str | None],
) -> list[Check]:
    """Every check for one answer. The case passes when all of them are ok.

    res: AskResponse as a dict (API JSON or model_dump(mode="json")).
    latest_of: doc_id -> latest version label. chunk_text: chunk_id -> indexed text (None if missing).
    """
    checks: list[Check] = []

    def add(name: str, ok, detail: str = "", citation: int | None = None) -> None:
        checks.append(Check(name, bool(ok), "" if ok else detail, citation))

    route = res.get("route") or {}
    answer = res.get("answer") or ""
    cits = res.get("citations") or []
    routed = route.get("versions") or []

    # route
    add("intent", route.get("intent") == case.intent, f"routed {route.get('intent')}, expected {case.intent}")
    if case.versions is not None:
        add("versions", routed == case.versions, f"routed versions {routed}, expected {case.versions}")
    if case.intent in ("version", "compare", "unclear") and case.doc:
        add("doc", route.get("doc_id") == case.doc, f"routed doc {route.get('doc_id')}, expected {case.doc}")
    if case.doc_id:
        add("focus", route.get("doc_id") == case.doc_id, f"focus {case.doc_id} lost: routed doc {route.get('doc_id')}")

    # the app's own groundedness verdict
    status = (res.get("grounding") or {}).get("status")
    add("grounding", status == case.expected_grounding, f"grounding {status}, expected {case.expected_grounding}")

    # facts in the answer
    for p in case.facts:
        add("fact", re.search(p, answer, re.I), f"answer lacks /{p}/")
    for p in case.forbidden:
        m = re.search(p, answer, re.I)
        add("forbidden", m is None, f"answer has /{p}/ ({m.group(0)!r})" if m else "")

    if case.insufficient:
        add("insufficient", res.get("insufficient_context") is True, "answered, but the documents do not cover this")
        return checks
    if case.intent == "unclear":
        add("no_citations", not cits, f"unclear answer cites {len(cits)} sources")
        return checks

    # citations: present, nothing invented, from the right doc and versions, quotes verbatim
    add("has_citations", bool(cits), "no citations")
    unknown = res.get("unknown_citations") or []
    add("unknown_citations", not unknown, f"cited unknown sources {unknown}")
    for i, c in enumerate(cits):
        where = f"[{c.get('source')}] {c.get('doc_id')} {c.get('version')} '{c.get('section')}'"
        if case.doc:
            add("citation_doc", c.get("doc_id") == case.doc, f"{where} is not from {case.doc}", i)
        if case.intent == "latest":
            latest = latest_of.get(c.get("doc_id"))
            add("citation_version", c.get("version") == latest, f"{where}: the latest version is {latest}", i)
        else:
            add("citation_version", c.get("version") in routed, f"{where}: not a routed version {routed}", i)
        if case.only_sections:
            section = c.get("section") or ""
            add("citation_section", any(s in section for s in case.only_sections), f"{where}: off-topic section", i)
        try:
            text = chunk_text(c.get("chunk_id"))
        except Exception:  # noqa: BLE001  (a missing chunk is a failed check, not a crash)
            text = None
        add(
            "quote",
            text is not None and quote_in(c.get("quote") or "", text),
            f"{where}: quote not in chunk {c.get('chunk_id')}: {c.get('quote')!r}",
            i,
        )
    if case.intent == "compare":
        cited = {c.get("version") for c in cits}
        add("both_sides", cited == set(routed), f"cited versions {sorted(cited, key=str)}, routed {routed}")
    return checks


def failures(checks: Iterable[Check]) -> list[str]:
    return [f"{c.name}: {c.detail}" for c in checks if not c.ok]


# --- metrics ----------------------------------------------------------------------


def score(
    case: Case,
    res: Mapping | None,
    checks: list[Check],
    latency_s: float | None = None,
    error: str | None = None,
) -> dict:
    """One run of one case, flattened for the metrics. error = exception text if ask() crashed."""
    res = res or {}

    def all_ok(name: str) -> bool | None:
        xs = [c.ok for c in checks if c.name == name]
        return all(xs) if xs else None

    intent_ok = bool(all_ok("intent")) and error is None
    route_parts = [all_ok(n) for n in ("doc", "versions", "focus")]
    n_cits = len(res.get("citations") or [])
    bad_cits = {c.citation for c in checks if c.citation is not None and not c.ok}
    status = (res.get("grounding") or {}).get("status")
    return {
        "id": case.id,
        "intent": case.intent,
        "doc": case.doc or NO_DOC,
        "tags": list(case.tags),
        "passed": error is None and bool(checks) and all(c.ok for c in checks),
        "error": error,
        "intent_ok": intent_ok,
        "doc_ok": all_ok("doc"),
        "versions_ok": all_ok("versions"),
        "route_ok": intent_ok and all(p is not False for p in route_parts),
        "facts_total": len(case.facts),
        "facts_hit": sum(c.ok for c in checks if c.name == "fact"),
        "forbidden_hits": sum(not c.ok for c in checks if c.name == "forbidden"),
        "citations_total": n_cits,
        "citations_valid": n_cits - len(bad_cits),
        "unknown_citations": len(res.get("unknown_citations") or []),
        "grounding_expected": case.expected_grounding,
        "grounding_got": status,
        "grounding_ok": status == case.expected_grounding,
        "insufficient_expected": case.insufficient,
        "insufficient_got": res.get("insufficient_context") if res else None,
        "latency_s": latency_s,
        "failed": failures(checks),
    }


def percentile(values: Iterable[float], p: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default)."""
    xs = sorted(values)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _rate(xs: Iterable) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(bool(x) for x in xs) / len(xs) if xs else None


def metrics(records: list[dict]) -> dict:
    facts_total = sum(r["facts_total"] for r in records)
    facts_hit = sum(r["facts_hit"] for r in records)
    cits = sum(r["citations_total"] for r in records)
    cits_valid = sum(r["citations_valid"] for r in records)
    # "The documents don't cover this": scored on answered routes only (unclear has its own message).
    answered = [r for r in records if r["intent"] != "unclear" and r["error"] is None]
    tp = sum(1 for r in answered if r["insufficient_expected"] and r["insufficient_got"])
    fp = sum(1 for r in answered if not r["insufficient_expected"] and r["insufficient_got"])
    fn = sum(1 for r in answered if r["insufficient_expected"] and not r["insufficient_got"])
    lat = [r["latency_s"] for r in records if r["latency_s"] is not None]
    return {
        "runs": len(records),
        "passed": sum(1 for r in records if r["passed"]),
        "pass_rate": _rate(r["passed"] for r in records),
        "route_accuracy": _rate(r["route_ok"] for r in records),
        "intent_accuracy": _rate(r["intent_ok"] for r in records),
        "version_accuracy": _rate(r["versions_ok"] for r in records),  # only runs that check versions
        "doc_accuracy": _rate(r["doc_ok"] for r in records),  # only runs that check the route's doc
        "facts_hit": facts_hit,
        "facts_total": facts_total,
        "fact_recall": facts_hit / facts_total if facts_total else None,
        "forbidden_hits": sum(r["forbidden_hits"] for r in records),
        "runs_with_forbidden_hit": sum(1 for r in records if r["forbidden_hits"]),
        "citations": cits,
        "citations_valid": cits_valid,
        "citation_validity": cits_valid / cits if cits else None,
        "unknown_citations": sum(r["unknown_citations"] for r in records),
        "grounding": dict(sorted(Counter(r["grounding_got"] or "error" for r in records).items())),
        "grounding_as_expected": _rate(r["grounding_ok"] for r in records),
        "insufficient": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        },
        "latency_s": {"p50": percentile(lat, 50), "p90": percentile(lat, 90), "max": max(lat) if lat else None},
        "errors": sum(1 for r in records if r["error"] is not None),
    }


def aggregate(records: list[dict]) -> dict:
    """Metrics overall, per expected intent, per expected document and per tag."""
    by_intent: dict[str, list] = defaultdict(list)
    by_doc: dict[str, list] = defaultdict(list)
    by_tag: dict[str, list] = defaultdict(list)
    for r in records:
        by_intent[r["intent"]].append(r)
        by_doc[r["doc"]].append(r)
        for t in r["tags"]:
            by_tag[t].append(r)
    order = {k: i for i, k in enumerate(INTENTS)}
    return {
        "overall": metrics(records),
        "by_intent": {k: metrics(by_intent[k]) for k in sorted(by_intent, key=lambda k: order.get(k, 99))},
        "by_doc": {k: metrics(v) for k, v in sorted(by_doc.items())},
        "by_tag": {k: metrics(v) for k, v in sorted(by_tag.items())},
    }


def stability(records: list[dict]) -> dict[str, dict]:
    """case id -> runs, passed, status: pass (every run), flaky (some runs), fail (no run)."""
    out: dict[str, dict] = {}
    for r in records:
        s = out.setdefault(r["id"], {"runs": 0, "passed": 0})
        s["runs"] += 1
        s["passed"] += bool(r["passed"])
    for s in out.values():
        s["status"] = "pass" if s["passed"] == s["runs"] else "fail" if s["passed"] == 0 else "flaky"
    return out


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def _sec(x: float | None) -> str:
    return "-" if x is None else f"{x:.1f}"


def metrics_table(agg: dict) -> str:
    """Markdown table: overall, then per intent, then per document."""
    lines = [
        "| group | runs | pass | route | versions | fact recall | forbidden hits | citations valid "
        "| grounding as expected | insufficient P / R | p50 s | p90 s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    groups = [("overall", agg["overall"])]
    groups += [(f"intent {k}", m) for k, m in agg["by_intent"].items()]
    groups += [(f"doc {k}", m) for k, m in agg["by_doc"].items()]
    for name, m in groups:
        ins = m["insufficient"]
        lines.append(
            f"| {name} | {m['runs']} | {m['passed']} ({_pct(m['pass_rate'])}) | {_pct(m['route_accuracy'])} "
            f"| {_pct(m['version_accuracy'])} | {m['facts_hit']}/{m['facts_total']} ({_pct(m['fact_recall'])}) "
            f"| {m['forbidden_hits']} | {m['citations_valid']}/{m['citations']} ({_pct(m['citation_validity'])}) "
            f"| {_pct(m['grounding_as_expected'])} | {_pct(ins['precision'])} / {_pct(ins['recall'])} "
            f"| {_sec(m['latency_s']['p50'])} | {_sec(m['latency_s']['p90'])} |"
        )
    return "\n".join(lines)


# --- checking the cases against the indexed corpus (no LLM) ---------------------------


def catalog_text(catalog: list[dict]) -> str:
    """What an 'unclear' answer can list: titles, ids, version labels, effective dates."""
    return "\n".join(
        f"{d['doc_title']} ({d['doc_id']}) has versions "
        + ", ".join(v["version"] for v in d["versions"])
        + "; effective "
        + ", ".join(str(v["effective_date"]) for v in d["versions"])
        for d in catalog
    )


def verify_case(case: Case, catalog: list[dict], version_text: Callable[[str, str], str]) -> list[str]:
    """Problems with a case against the indexed corpus: an unknown doc or version, a required fact
    that is not in the text the answer must come from, a forbidden fact that is, or a forbidden fact
    that no other version of the doc has (then it is a guess, not an old value).

    version_text(doc_id, version) -> the indexed text of that version."""
    docs = {d["doc_id"]: d for d in catalog}
    problems = case.problems()
    for name, doc_id in (("doc", case.doc), ("doc_id", case.doc_id)):
        if doc_id and doc_id not in docs:
            problems.append(f"{name} {doc_id!r} is not indexed")
    if problems:
        return problems

    def labels(doc_id: str) -> list[str]:
        return [v["version"] for v in docs[doc_id]["versions"]]

    if case.versions:
        if not case.doc:
            return ["versions given without doc"]
        missing = [v for v in case.versions if v not in labels(case.doc)]
        if missing:
            return [f"versions {missing} are not indexed for {case.doc}"]
        if case.versions != sorted(case.versions, key=labels(case.doc).index):
            return [f"versions {case.versions} must be oldest first"]

    if case.insufficient:
        return ["an insufficient case has nothing to check facts against"] if case.facts else []
    if case.intent == "unclear":
        source = catalog_text(catalog)
        used: list[tuple[str, str]] = []
    elif case.intent == "latest":
        used = [(d, labels(d)[-1]) for d in ([case.doc] if case.doc else list(docs))]
        source = "\n".join(version_text(d, v) for d, v in used)
    else:
        if not case.doc or not case.versions:
            return [f"a {case.intent} case needs doc and versions"]
        used = [(case.doc, v) for v in case.versions]
        source = "\n".join(version_text(case.doc, v) for _, v in used)

    for p in case.facts:
        if not p.startswith(ANSWER_ONLY) and not re.search(p, source, re.I):
            problems.append(f"fact /{p}/ is not in the expected source text")
    for s in case.only_sections or []:
        if s not in source:
            problems.append(f"section {s!r} is not in the expected source text")
    if case.intent != "unclear":
        for p in case.forbidden:
            m = re.search(p, source, re.I)
            if m:
                problems.append(f"forbidden /{p}/ is in the expected source text ({m.group(0)!r})")
            elif case.doc:
                others = [v for v in labels(case.doc) if (case.doc, v) not in used]
                if not any(re.search(p, version_text(case.doc, v), re.I) for v in others):
                    problems.append(f"forbidden /{p}/ is in no other version of {case.doc}")
    return problems
