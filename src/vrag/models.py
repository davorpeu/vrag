from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class DocVersion:
    """One version of one document, as emitted by a VersionSource."""

    doc_id: str  # stable identity across versions, decided by the source
    version: str  # label as the source names it, e.g. "v2"
    version_num: int  # ordering key; higher = newer
    source: str  # "folder", "git", ...
    source_ref: str  # path, commit SHA, row id...
    filename: str  # used to pick the extractor (.pdf / .docx)
    content: bytes
    source_date: date  # commit / row / mtime date, fallback for effective_date


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    version: str
    version_num: int
    effective_date: date
    source: str
    source_ref: str
    content_hash: str
    section_number: str
    section_title: str
    text: str


class Citation(BaseModel):
    source: str = Field(description="Source label from the context, e.g. S2")
    quote: str = Field(description="Short verbatim quote from that source supporting the answer")


class LLMAnswer(BaseModel):
    """What the LLM must return (validated by Pydantic)."""

    # citations is required and comes first: small local models skip optional fields,
    # and picking quotes before answering keeps the answer grounded.
    citations: list[Citation] = Field(
        description="One entry per fact in the answer: the source label and a verbatim quote. "
        "Empty only when insufficient_context is true."
    )
    answer: str = Field(description="Answer to the question, using only the quoted sources")
    insufficient_context: bool = Field(
        default=False, description="True if the sources do not contain the answer"
    )


class CompareSummary(BaseModel):
    """What the LLM returns for a comparison. Citations are built from the diff, not by the LLM."""

    relevant_changes: list[int] = Field(
        description="Numbers of the changes that answer the question (all of them for 'what changed?')"
    )
    answer: str = Field(description="Answer describing those changes: what it was before and what it is now")


class ResolvedCitation(BaseModel):
    source: str
    doc_id: str
    doc_title: str
    version: str
    effective_date: date
    section: str
    quote: str
    chunk_id: str


class RouteInfo(BaseModel):
    intent: str = Field(description="latest | version | compare | unclear")
    doc_id: str | None = None
    versions: list[str] = Field(default_factory=list, description="Versions used, oldest first")
    note: str = Field("", description="What the router assumed, or why it is unclear")


class GroundingInfo(BaseModel):
    status: str = Field(description="grounded | partial | ungrounded | not_checked")
    issues: list[str] = Field(default_factory=list, description="What failed or was corrected")
    repaired_quotes: int = 0
    dropped_quotes: int = 0
    retried: bool = False


class AskResponse(BaseModel):
    question: str
    route: RouteInfo | None = None
    grounding: GroundingInfo | None = None
    answer: str
    insufficient_context: bool
    citations: list[ResolvedCitation]
    unknown_citations: list[str] = Field(
        default_factory=list, description="Labels the LLM cited that were not in the context"
    )
    trace_id: str | None = None
