"""Milestone 1: latest-version Q&A with citations, traced in Langfuse.

The query router (latest / specific version / compare) and the groundedness check
come in later milestones; this module is the 'latest' pipeline.
"""

from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from langfuse import get_client, observe, propagate_attributes
from langfuse.langchain import CallbackHandler

from vrag import store
from vrag.config import get_settings
from vrag.models import AskResponse, LLMAnswer, ResolvedCitation

PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You answer questions about company policies and contracts using ONLY the sources below.\n"
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


def format_context(hits: list[dict]) -> tuple[str, dict[str, dict]]:
    labelled = {f"S{i}": h for i, h in enumerate(hits, start=1)}
    blocks = [
        f"[{label}] {h['doc_title']} | {h['version']} | effective {h['effective_date']} | section {h['section_number']}\n{h['text']}"
        for label, h in labelled.items()
    ]
    return "\n\n".join(blocks), labelled


def chain():
    s = get_settings()
    llm = ChatOllama(model=s.llm_model, base_url=s.ollama_base_url, temperature=0)
    return PROMPT | llm.with_structured_output(LLMAnswer)


@observe(name="ask")
def ask(question: str) -> AskResponse:
    # Tags must be set before child spans start, so wrap the whole pipeline.
    with propagate_attributes(tags=["pipeline:latest"]):
        return _ask_latest(question)


def _ask_latest(question: str) -> AskResponse:
    hits = retrieve(question)
    context, labelled = format_context(hits)
    result: LLMAnswer = chain().invoke(
        {"question": question, "context": context},
        config={"callbacks": [CallbackHandler()], "run_name": "answer-latest"},
    )

    resolved, unknown = [], []
    for c in result.citations:
        label = c.source.strip("[] ")
        h = labelled.get(label)
        if h is None:
            unknown.append(c.source)
            continue
        resolved.append(
            ResolvedCitation(
                source=label,
                doc_id=h["doc_id"],
                doc_title=h["doc_title"],
                version=h["version"],
                effective_date=h["effective_date"],
                section=f"{h['section_number']} {h['section_title']}",
                quote=c.quote,
                chunk_id=h["chunk_id"],
            )
        )

    return AskResponse(
        question=question,
        answer=result.answer,
        insufficient_context=result.insufficient_context,
        citations=resolved,
        unknown_citations=unknown,
        trace_id=get_client().get_current_trace_id(),
    )


@observe(name="retrieve")
def retrieve(question: str) -> list[dict]:
    return store.hybrid_search(question)


def flush() -> None:
    get_client().flush()
