"""Query router: decide which fixed pipeline answers a question.

Two steps:
1. classify(): the LLM reads the question plus the catalog of indexed docs/versions and
   returns a RouteDecision (intent, document, version mentions, as-of date).
2. resolve(): plain code checks that decision against the catalog. Versions the LLM
   names must exist; documents are inferred when not named; dates pick the version in
   effect. Anything that cannot be pinned down becomes "unclear" with a reason, so a
   hallucinated version never reaches retrieval.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

from vrag.config import get_settings

Intent = Literal["latest", "version", "compare", "unclear"]


class RouteDecision(BaseModel):
    """What the LLM must return."""

    intent: Intent = Field(
        description="latest = current rules; version = what a specific/older version or date said; "
        "compare = what changed/differs between versions; unclear = cannot tell what is asked"
    )
    document: str | None = Field(
        description="doc_id from the catalog the question is about, or null if not clear"
    )
    versions: list[str] = Field(
        description="Version labels mentioned, copied from the catalog (e.g. v2, v1.0.0). Empty if none."
    )
    as_of: str | None = Field(
        description="YYYY-MM-DD if the question asks about a point in time instead of a version "
        "(use the last day of a year/month when only that is given), else null"
    )


ROUTER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You route questions about versioned documents. Do not answer the question.\n"
            "Indexed documents (doc_id: title: versions oldest -> newest, with effective dates):\n"
            "{catalog}\n\n"
            "Rules:\n"
            "- intent=latest when the question asks about current rules and names no version or date.\n"
            "- intent=version when it asks what a specific version, an older/previous version, or a date said.\n"
            "- intent=compare when it asks what changed, what is new, or how versions differ.\n"
            "- intent=unclear only when it is impossible to tell what is being asked (e.g. 'the other one'). "
            "A clear question about a topic the documents may not cover is intent=latest, not unclear.\n"
            "- versions: copy labels exactly as in the catalog. 'version 2' of a policy is v2; "
            "'2.0' or '2.0.0' of semver is v2.0.0.\n"
            "- 'previous'/'old' version without a label: leave versions empty.\n\n"
            "Examples:\n"
            "Q: How many days do I have to return an item? -> latest, refund-policy, [], null\n"
            "Q: What did v1 of the leave policy say about carry-over? -> version, leave-policy, [v1], null\n"
            "Q: What was the refund window in March 2024? -> version, refund-policy, [], 2024-03-31\n"
            "Q: What changed between v1 and v3 of the refund policy? -> compare, refund-policy, [v1, v3], null\n"
            "Q: What is new in the latest data retention standard? -> compare, data-retention, [], null\n"
            "Q: What is the company's travel expense limit? -> latest, null, [], null\n"
            "Q: And what about the other one? -> unclear, null, [], null",
        ),
        ("human", "{question}"),
    ]
)


@dataclass
class Route:
    intent: Intent
    doc_id: str | None = None
    versions: list[str] = field(default_factory=list)  # resolved catalog labels, oldest first
    note: str = ""  # why this route / what was assumed / what is unclear


def format_catalog(catalog: list[dict]) -> str:
    lines = []
    for d in catalog:
        vs = ", ".join(f"{v['version']} ({v['effective_date']})" for v in d["versions"])
        lines.append(f"- {d['doc_id']}: {d['doc_title']}: {vs}")
    return "\n".join(lines)


def classify(question: str, catalog: list[dict], callbacks: list | None = None) -> RouteDecision:
    s = get_settings()
    llm = ChatOllama(model=s.llm_model, base_url=s.ollama_base_url, temperature=0)
    chain = ROUTER_PROMPT | llm.with_structured_output(RouteDecision)
    return chain.invoke(
        {"question": question, "catalog": format_catalog(catalog)},
        config={"callbacks": callbacks or [], "run_name": "route"},
    )


# --- deterministic resolution ---------------------------------------------

# Version-looking tokens in the question itself, as a backstop for the LLM:
# "v2", "v1.0.0-rc.1", "version 2", "2.0.0". Bare numbers ("21 days") are not versions.
VERSION_TOKEN = re.compile(
    r"\bv(\d+(?:\.\d+)*(?:-[0-9a-z.]+)?)\b|\bversion\s+(\d+(?:\.\d+)*(?:-[0-9a-z.]+)?)\b|\b(\d+\.\d+(?:\.\d+)?(?:-[0-9a-z.]+)?)\b",
    re.I,
)


def norm_version(v: str) -> str:
    v = v.strip().lower()
    v = re.sub(r"^(version\s+|v)", "", v)
    core, _, pre = v.partition("-")
    core = re.sub(r"(\.0)+$", "", core)  # 2.0.0 == 2.0 == 2
    return f"{core}-{pre}" if pre else core


def versions_in_text(question: str) -> list[str]:
    return [v for v, _ in _version_tokens(question)]


def _version_tokens(question: str) -> list[tuple[str, bool]]:
    """(token, explicit): explicit = written as "v2" / "version 2", not a bare "2.1" that may be a section."""
    out = []
    for m in VERSION_TOKEN.finditer(question):
        v_pref, word_pref, bare = m.groups()
        out.append(((v_pref or word_pref or bare).rstrip("."), bare is None))
    return out


def _find_doc(name: str | None, catalog: list[dict]) -> dict | None:
    if not name:
        return None
    n = name.strip().lower()
    for d in catalog:
        if n in (d["doc_id"].lower(), d["doc_title"].lower()):
            return d
    for d in catalog:  # partial: "refund policy" -> refund-policy, "semver" -> semver/semver
        key = d["doc_id"].lower().replace("-", " ").replace("/", " ")
        if n.replace("-", " ") in key or n in d["doc_title"].lower():
            return d
    return None


def _match_versions(mentions: list[str], doc: dict) -> tuple[list[dict], list[str]]:
    by_norm = {norm_version(v["version"]): v for v in doc["versions"]}
    found, missing = [], []
    for m in mentions:
        v = by_norm.get(norm_version(m))
        if v is None:
            missing.append(m)
        elif v not in found:
            found.append(v)
    return sorted(found, key=lambda v: v["version_num"]), missing


# Words in doc ids/titles too generic to identify a document on their own.
GENERIC = {"policy", "standard", "document", "documents", "the", "and", "for", "version", "versions", "versioning", "docs", "readme"}


def _name_words(doc: dict) -> set[str]:
    text = f"{doc['doc_id']} {doc['doc_title']}".lower()
    return {w for w in re.findall(r"[a-z]{3,}", text) if w not in GENERIC}


def named_in(question: str, doc: dict) -> bool:
    """True if the question uses a distinctive word from the doc's id or title ('refund', 'react', 'claude').
    Prefix match either way covers plurals and stems ('refunds', 'versioning' / 'version...')."""
    q_words = re.findall(r"[a-z]{3,}", question.lower())
    for name in _name_words(doc):
        for w in q_words:
            if w == name or (min(len(w), len(name)) >= 4 and (w.startswith(name) or name.startswith(w))):
                return True
    return False


def _which_doc(catalog: list[dict]) -> str:
    names = "; ".join(f"{d['doc_title']} ({d['doc_id']})" for d in catalog)
    return f"Name the document in your question. Indexed: {names}."


def _available(doc: dict) -> str:
    return f"{doc['doc_title']} has versions " + ", ".join(v["version"] for v in doc["versions"])


def resolve(
    question: str,
    decision: RouteDecision,
    catalog: list[dict],
    top_doc: Callable[[list[str] | None], str | None],
    focus: str | None = None,
) -> Route:
    """top_doc(doc_ids) returns the doc_id best matching the question (search), limited to doc_ids if given.

    focus = doc_id the user picked in the UI: every question is about that document, so
    "what's in this document?" needs no name, and a 'latest' answer searches only that document."""
    if focus:
        doc = _find_doc(focus, catalog)
        if doc is None:
            return Route("unclear", note=f"Document {focus!r} is not indexed.")
        if decision.intent == "unclear":  # "what's in this document?" -> read its latest version
            decision = decision.model_copy(update={"intent": "latest"})
        route = _resolve(question, decision.model_copy(update={"document": doc["doc_id"]}), [doc], top_doc, doc)
        if route.intent == "latest":
            route.doc_id = doc["doc_id"]
            route.note = f"Latest version of {doc['doc_title']} ({doc['versions'][-1]['version']})."
        return route
    return _resolve(question, decision, catalog, top_doc)


def _resolve(
    question: str,
    decision: RouteDecision,
    catalog: list[dict],
    top_doc: Callable[[list[str] | None], str | None],
    forced: dict | None = None,
) -> Route:
    if not catalog:
        return Route("unclear", note="Nothing is indexed yet.")

    # Version mentions must be grounded in the question text:
    # - LLM mentions only count if the question contains them (the LLM may copy "2.0.0" from a doc
    #   title like "Semantic Versioning 2.0.0", or drop a version it cannot find in the catalog).
    # - Bare tokens like "2.1" only count when some doc has that version (else it is a section number).
    # - Explicit tokens ("v7", "version 7") always count, so an unknown one ends up as "not found".
    known = {norm_version(v["version"]) for d in catalog for v in d["versions"]}
    tokens = _version_tokens(question)
    in_text = {norm_version(t) for t, _ in tokens}
    llm_mentions = [v for v in decision.versions if re.search(r"\d", v) and norm_version(v) in in_text]
    text_mentions = [t for t, explicit in tokens if explicit or norm_version(t) in known]
    mentions = list({norm_version(m): m for m in reversed(llm_mentions + text_mentions)}.values())[::-1]
    intent = decision.intent

    # Which document? Only accepted with evidence from the question:
    # named in it, the only doc having the mentioned versions, or the LLM and the search agree.
    # A guess with no evidence (the LLM picking a doc for "what changed in X?") is not accepted.
    llm_doc = _find_doc(decision.document, catalog)
    doc = forced or (llm_doc if llm_doc and named_in(question, llm_doc) else None)
    if doc is None and mentions:
        # only docs that actually have every mentioned version
        candidates = [d for d in catalog if not _match_versions(mentions, d)[1]]
        if len(candidates) == 1:
            doc = candidates[0]
        elif candidates:
            doc = _find_doc(top_doc([d["doc_id"] for d in candidates]), catalog)
    if doc is None and intent != "latest":
        searched = _find_doc(top_doc(None), catalog)
        if searched and (searched is llm_doc or named_in(question, searched)):
            doc = searched

    if intent == "unclear":
        hint = f" {_available(doc)}." if doc else " " + _which_doc(catalog)
        return Route("unclear", doc["doc_id"] if doc else None, note="Could not tell what is being asked." + hint)

    if intent == "latest":
        if mentions and doc:  # LLM said latest but the question names a version
            intent = "version"
        else:
            return Route("latest", note="Latest version of each document.")

    if doc is None:
        return Route("unclear", note="Which document do you mean? " + _which_doc(catalog))
    found, missing = _match_versions(mentions, doc)
    if missing:
        return Route(
            "unclear", doc["doc_id"], note=f"Version {', '.join(missing)} not found. {_available(doc)}."
        )

    if intent == "version":
        if len(found) > 1:
            intent = "compare"  # "what did v1 and v3 say" is a comparison
        elif found:
            return Route("version", doc["doc_id"], [found[0]["version"]])
        elif decision.as_of:
            try:
                when = date.fromisoformat(decision.as_of)
            except ValueError:
                return Route("unclear", doc["doc_id"], note=f"Could not read the date '{decision.as_of}'.")
            in_effect = [v for v in doc["versions"] if date.fromisoformat(v["effective_date"]) <= when]
            if not in_effect:
                first = doc["versions"][0]
                return Route(
                    "unclear",
                    doc["doc_id"],
                    note=f"No version was in effect on {when}; the first is {first['version']} from {first['effective_date']}.",
                )
            v = in_effect[-1]
            return Route("version", doc["doc_id"], [v["version"]], note=f"{v['version']} was in effect on {when}.")
        elif len(doc["versions"]) > 1 and re.search(r"\b(previous|prior|old|older|earlier|before)\b", question, re.I):
            v = doc["versions"][-2]
            return Route("version", doc["doc_id"], [v["version"]], note=f"Previous version taken as {v['version']}.")
        else:
            return Route("latest", note="No version named; using the latest.")

    # compare
    versions = doc["versions"]
    if len(versions) < 2:
        return Route("unclear", doc["doc_id"], note=f"{doc['doc_title']} has only one version.")
    if len(found) >= 2:
        pair, note = [found[0], found[-1]], ""
    elif len(found) == 1:
        v = found[0]
        idx = versions.index(v)
        if idx == 0:
            pair, note = [v, versions[-1]], f"Compared {v['version']} with the latest."
        else:
            pair, note = [versions[idx - 1], v], f"Compared {v['version']} with the version before it."
    else:
        pair, note = versions[-2:], "Compared the latest version with the one before it."
    return Route("compare", doc["doc_id"], [p["version"] for p in pair], note=note)
