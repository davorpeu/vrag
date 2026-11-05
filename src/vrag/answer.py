"""Question answering: route the question, then run one fixed pipeline.

- latest:  hybrid search over the latest version of every doc.
- version: hybrid search restricted to one doc version.
- compare: clause-level diff of two versions (code). The LLM only picks the relevant changes and
           summarises them; citations come straight from the diff, so they are always verbatim.
- unclear: no LLM answer; return the router's reason and the versions that exist.
"""

import re

from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from langfuse import get_client, observe, propagate_attributes
from langfuse.langchain import CallbackHandler

from vrag import router, store
from vrag.config import get_settings
from vrag.diff import ClauseChange, clause_changes, cut, diff_versions, word_changes
from vrag.models import AskResponse, CompareSummary, LLMAnswer, ResolvedCitation, RouteInfo

PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You answer questions about versioned documents (policies, contracts, specifications) "
            "using ONLY the sources below.\n"
            "{scope}\n"
            "Each source is labelled [S1], [S2], ... and shows document, version, effective date and section.\n"
            "Rules:\n"
            "- Cite every fact with the source label and a short verbatim quote.\n"
            "- Only cite labels that appear below.\n"
            "- If the sources do not contain the answer, say so and set insufficient_context to true.\n\n"
            "Sources:\n{context}",
        ),
        ("human", "{question}"),
    ]
)

COMPARE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You explain changes between two versions of {doc_title}: {old} (effective {old_date}) "
            "and {new} (effective {new_date}).\n"
            "Below is the complete, numbered list of changed clauses, computed exactly. "
            "Nothing outside this list changed.\n"
            "1. Put in relevant_changes the numbers of the changes that answer the question. "
            "General questions ('what changed?', 'what is new?', 'what are the differences?') "
            "need ALL numbers: modified clauses count as new too, not only added ones. "
            "Pick a subset only when the question names a topic (e.g. 'refund window').\n"
            "2. In answer, describe each of those changes: what it said in {old} and what it says in {new}. "
            "Use only the wording below. Mention every relevant change; do not say a change is missing.\n\n"
            "Changes:\n{changes}",
        ),
        ("human", "{question}"),
    ]
)

MAX_CHANGES = 12

# Words that do not name a topic in a comparison question. If nothing else is left after
# removing these (and the doc's own name), the question is general: every change is relevant.
GENERAL_WORDS = set(
    "what whats is are was were has have had been did do does the a an any there anything all "
    "new newest latest current previous prior old older earlier last recent changed change changes "
    "changing different difference differences differ compare compared comparison between vs versus "
    "from to in of and since than version versions release releases doc document policy standard "
    "spec specification updated update updates modified added removed how which tell me list show "
    "please since come".split()
)


def is_general_question(question: str, doc_id: str, doc_title: str) -> bool:
    doc_words = set(re.findall(r"[a-z]+", f"{doc_id} {doc_title}".lower()))
    words = re.findall(r"[a-z]+", re.sub(r"\bv?\d[\w.-]*", " ", question.lower()))
    return not [w for w in words if w not in GENERAL_WORDS and w not in doc_words]


def _llm(prompt: ChatPromptTemplate, schema):
    s = get_settings()
    llm = ChatOllama(model=s.llm_model, base_url=s.ollama_base_url, temperature=0)
    return prompt | llm.with_structured_output(schema)


def _source_block(label: str, h: dict) -> str:
    return (
        f"[{label}] {h['doc_title']} | {h['version']} | effective {h['effective_date']} | "
        f"section {h['section_number']}\n{h['text']}"
    )


def format_context(hits: list[dict]) -> tuple[str, dict[str, dict]]:
    labelled = {f"S{i}": h for i, h in enumerate(hits, start=1)}
    return "\n\n".join(_source_block(label, h) for label, h in labelled.items()), labelled


def format_changes(changes: list[ClauseChange], old_v: str, new_v: str) -> str:
    blocks = []
    for n, c in enumerate(changes, start=1):
        lines = [f"Change {n} ({c.kind}, section {c.chunk['section_number']} {c.chunk['section_title']}):"]
        if c.old_text:
            lines.append(f"  {old_v}: {cut(c.old_text)}")
        if c.new_text:
            lines.append(f"  {new_v}: {cut(c.new_text)}")
        if c.kind == "added":
            lines.append(f"  {old_v}: (not present)")
        if c.kind == "removed":
            lines.append(f"  {new_v}: (removed)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _first_sentence(text: str, limit: int = 180) -> str:
    text = re.sub(r"^\d+\.\s+", "", text)  # list marker "1. "
    # a sentence ends after a word, not after a number like "1." or "2.0.0."
    s = re.split(r"(?<=[A-Za-z\)\]\"'][.!?])\s", text, maxsplit=1)[0]
    return s if len(s) <= limit else s[: limit - 3].rstrip() + "..."


def _change_line(c: ClauseChange, old_v: str, new_v: str) -> str:
    """Short before/after line; the full clauses are in the citations."""
    head = f"- {c.chunk['section_title']}"
    if c.kind == "added":
        return f"{head}: new in {new_v}: \"{_first_sentence(c.new_text)}\""
    if c.kind == "removed":
        return f"{head}: removed in {new_v}: \"{_first_sentence(c.old_text)}\""
    if len(c.old_text) <= 160 and len(c.new_text) <= 160:
        return f"{head}: {old_v} said \"{c.old_text}\"; {new_v} says \"{c.new_text}\""
    pairs = word_changes(c.old_text, c.new_text, max_items=3)
    edits = "; ".join(
        f"\"{a}\" -> \"{b}\"" if a and b else (f"added \"{b}\"" if b else f"dropped \"{a}\"") for a, b in pairs
    )
    return f"{head} (\"{_first_sentence(c.new_text, 60)}\"): {edits}"


STOP = set("the a an and or of to in for on at by is are be was were with from this that it its may must".split())


def _key_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9%]+", text.lower()) if w not in STOP and (len(w) > 3 or w.isdigit())}


def mentions_change(answer: str, c: ClauseChange) -> bool:
    """Does the answer talk about this change? Checks the words that actually changed
    ("14" -> "21") for modifications, and the clause's own key words for added/removed ones."""
    said = _key_words(answer)
    if c.kind == "modified":
        changed = set()
        for a, b in word_changes(c.old_text, c.new_text):
            changed |= _key_words(b) or _key_words(a)
        return not changed or bool(changed & said)
    words = _key_words(c.new_text or c.old_text)
    return len(words & said) >= min(3, len(words))


def _cite(label: str, chunk: dict, quote: str) -> ResolvedCitation:
    return ResolvedCitation(
        source=label,
        doc_id=chunk["doc_id"],
        doc_title=chunk["doc_title"],
        version=chunk["version"],
        effective_date=chunk["effective_date"],
        section=f"{chunk['section_number']} {chunk['section_title']}",
        quote=quote,
        chunk_id=chunk["chunk_id"],
    )


def _response(question: str, route: router.Route, result: LLMAnswer, labelled: dict[str, dict]) -> AskResponse:
    resolved, unknown = [], []
    for c in result.citations:
        label = c.source.strip("[] ")
        h = labelled.get(label)
        if h is None:
            unknown.append(c.source)
            continue
        resolved.append(_cite(label, h, c.quote))
    return AskResponse(
        question=question,
        route=_route_info(route),
        answer=result.answer,
        insufficient_context=result.insufficient_context,
        citations=resolved,
        unknown_citations=unknown,
        trace_id=get_client().get_current_trace_id(),
    )


def _route_info(route: router.Route) -> RouteInfo:
    return RouteInfo(intent=route.intent, doc_id=route.doc_id, versions=route.versions, note=route.note)


def _plain(question: str, route: router.Route, text: str, insufficient: bool = True) -> AskResponse:
    return AskResponse(
        question=question,
        route=_route_info(route),
        answer=text,
        insufficient_context=insufficient,
        citations=[],
        trace_id=get_client().get_current_trace_id(),
    )


# --- entry point -------------------------------------------------------------


@observe(name="ask")
def ask(question: str) -> AskResponse:
    route = route_question(question)
    # Sets the tag on the root span and everything after it.
    with propagate_attributes(tags=[f"pipeline:{route.intent}"]):
        if route.intent == "latest":
            return answer_latest(question, route)
        if route.intent == "version":
            return answer_version(question, route)
        if route.intent == "compare":
            return answer_compare(question, route)
        return _plain(question, route, f"I'm not sure which document or version you mean. {route.note}")


@observe(name="route")
def route_question(question: str) -> router.Route:
    catalog = store.catalog()
    decision = router.classify(question, catalog, callbacks=[CallbackHandler()])

    def top_doc(doc_ids: list[str] | None) -> str | None:
        filters = [{"term": {"is_latest": True}}]
        if doc_ids:
            filters.append({"terms": {"doc_id": doc_ids}})
        hits = store.hybrid_search(question, filters=filters, top_k=1)
        return hits[0]["doc_id"] if hits else None

    route = router.resolve(question, decision, catalog, top_doc)
    get_client().update_current_span(output={"decision": decision.model_dump(), "route": vars(route)})
    return route


@observe(name="answer-latest")
def answer_latest(question: str, route: router.Route) -> AskResponse:
    hits = store.hybrid_search(question)
    context, labelled = format_context(hits)
    result = _llm(PROMPT, LLMAnswer).invoke(
        {"question": question, "context": context, "scope": "Sources are from the latest version of each document."},
        config={"callbacks": [CallbackHandler()], "run_name": "llm-latest"},
    )
    return _response(question, route, result, labelled)


@observe(name="answer-version")
def answer_version(question: str, route: router.Route) -> AskResponse:
    version = route.versions[0]
    filters = [{"term": {"doc_id": route.doc_id}}, {"term": {"version": version}}]
    hits = store.hybrid_search(question, filters=filters)
    context, labelled = format_context(hits)
    title = hits[0]["doc_title"] if hits else route.doc_id
    scope = (
        f"The user asks about {title}, version {version} specifically. All sources are from that version; "
        f"answer as that version states it, even if newer versions differ. "
        f"Words in the question like 'previous', 'old', 'earlier' or a date all refer to {version}: "
        f"the sources below ARE that version."
    )
    if route.note:
        scope += f" ({route.note})"
    # Small models read "the previous version" as "a version I don't have"; name it explicitly.
    llm_question = re.sub(
        r"\b(?:the\s+)?(?:previous|prior|old|older|earlier|last)\s+(?:version|release|edition)\b",
        f"version {version}",
        question,
        flags=re.I,
    )
    llm_question = re.sub(r"\b(?:the\s+)?(?:previous|prior|old|older|earlier)\s+", "the ", llm_question, flags=re.I)
    if llm_question != question or version not in question:
        llm_question = f"{llm_question} (Answer from {title} {version}.)"
    result = _llm(PROMPT, LLMAnswer).invoke(
        {"question": llm_question, "context": context, "scope": scope},
        config={"callbacks": [CallbackHandler()], "run_name": "llm-version"},
    )
    return _response(question, route, result, labelled)


@observe(name="answer-compare")
def answer_compare(question: str, route: router.Route) -> AskResponse:
    old_v, new_v = route.versions
    old = store.version_chunks(route.doc_id, old_v)
    new = store.version_chunks(route.doc_id, new_v)
    changes = clause_changes(diff_versions(old, new))
    if not changes:
        return _plain(
            question, route, f"No changes to the content between {old_v} and {new_v}.", insufficient=False
        )

    if len(changes) > MAX_CHANGES:
        # keep the changes in the sections most relevant to the question, in document order
        filters = [{"term": {"doc_id": route.doc_id}}, {"terms": {"version": [old_v, new_v]}}]
        ranked = [h["chunk_id"] for h in store.hybrid_search(question, filters=filters, top_k=40)]
        rank = {cid: i for i, cid in enumerate(ranked)}

        def score(c: ClauseChange) -> int:
            ids = [x["chunk_id"] for x in (c.old_chunk, c.new_chunk) if x]
            return min(rank.get(i, 10_000) for i in ids)

        keep = sorted(range(len(changes)), key=lambda i: score(changes[i]))[:MAX_CHANGES]
        changes = [changes[i] for i in sorted(keep)]

    title = new[0]["doc_title"] if new else route.doc_id
    general = is_general_question(question, route.doc_id, title)
    llm_question = question
    if general:
        llm_question += "\n(General question: describe ALL the changes listed, modified ones included.)"
    summary: CompareSummary = _llm(COMPARE_PROMPT, CompareSummary).invoke(
        {
            "question": llm_question,
            "changes": format_changes(changes, old_v, new_v),
            "doc_title": title,
            "old": old_v,
            "new": new_v,
            "old_date": old[0]["effective_date"],
            "new_date": new[0]["effective_date"],
        },
        config={"callbacks": [CallbackHandler()], "run_name": "llm-compare"},
    )

    every = list(range(1, len(changes) + 1))
    if general:
        picked = every
    else:
        picked = [n for n in dict.fromkeys(summary.relevant_changes) if 1 <= n <= len(changes)] or every
    citations = []
    for n in picked:
        c = changes[n - 1]
        if c.old_text:
            citations.append(_cite(f"{n}-{old_v}", c.old_chunk, cut(c.old_text)))
        if c.new_text:
            citations.append(_cite(f"{n}-{new_v}", c.new_chunk, cut(c.new_text)))

    text = summary.answer.strip()
    # The 7B model sometimes writes only an intro ("...the following changes:") and stops.
    # Then the plain before/after list from the diff becomes the body.
    if len(text) < 20 or text.endswith(":"):
        listing = "\n".join(_change_line(changes[n - 1], old_v, new_v) for n in picked)
        text = f"{text}\n{listing}" if text.endswith(":") else listing
    else:
        skipped = [c for c in (changes[n - 1] for n in picked) if not mentions_change(text, c)]
        if skipped:
            text += "\n\nAlso changed:\n" + "\n".join(_change_line(c, old_v, new_v) for c in skipped)
    return AskResponse(
        question=question,
        route=_route_info(route),
        answer=text,
        insufficient_context=False,
        citations=citations,
        trace_id=get_client().get_current_trace_id(),
    )


def flush() -> None:
    get_client().flush()
