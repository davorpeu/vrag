"""Groundedness check: is the answer backed by the sources it cites?

Two deterministic checks, no extra LLM call:
1. Quotes: every citation quote must appear in the cited chunk. Near-misses (a changed word,
   different spacing) are repaired to the exact source text; quotes that are not really in the
   source are dropped.
2. Numbers: every number in the answer ("21 days", "15%", "two weeks") must appear in the cited
   sources (text, version label, effective date, section) or in the question. Wrong numbers are the
   costliest hallucination for policies and specs, and this check is exact.

The pipelines use the result to retry once with feedback, and report it on the response.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

REPAIR_MIN_COVERAGE = 0.85  # share of the quote's characters that must match the source

NUMBER_WORDS = {
    w: str(i)
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen twenty".split()
    )
}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i >= 2}
TENS_WORDS = re.compile(
    r"\b(" + "|".join(TENS) + r")(?:[-\s](one|two|three|four|five|six|seven|eight|nine))?\b", re.I
)
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
LIST_MARKER = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*])\s+")


@dataclass
class QuoteCheck:
    status: str  # "exact" | "repaired" | "not_found"
    quote: str  # the quote to show (exact source text when repaired)


@dataclass
class Grounding:
    status: str  # "grounded" | "partial" | "ungrounded" | "not_checked"
    issues: list[str] = field(default_factory=list)
    repaired_quotes: int = 0
    dropped_quotes: int = 0

    @property
    def ok(self) -> bool:
        return self.status in ("grounded", "not_checked")


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_quote(quote: str, source: str) -> QuoteCheck:
    q = _ws(quote).strip("\"'“”‘’ ").removesuffix("...").rstrip()
    src = _ws(source)
    if not q:
        return QuoteCheck("not_found", quote)
    if q.lower() in src.lower():
        return QuoteCheck("exact", quote)

    # Fuzzy: find the source span that covers most of the quote.
    sm = SequenceMatcher(None, src.lower(), q.lower(), autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 3]
    matched = sum(b.size for b in blocks)
    if blocks and matched / len(q) >= REPAIR_MIN_COVERAGE:
        start, end = blocks[0].a, blocks[-1].a + blocks[-1].size
        # widen to word boundaries so the repaired quote does not start mid-word
        while start > 0 and src[start - 1].isalnum():
            start -= 1
        while end < len(src) and src[end].isalnum():
            end += 1
        span = src[start:end].strip()
        if len(span) <= 2 * len(q):
            return QuoteCheck("repaired", span)
    return QuoteCheck("not_found", quote)


def numbers_in(text: str) -> set[str]:
    text = LIST_MARKER.sub(" ", text)  # "1. Section ..." list numbering is not a claim
    # "twenty-one" -> "21", "thirty" -> "30"
    text = TENS_WORDS.sub(
        lambda m: str(TENS[m[1].lower()] + (int(NUMBER_WORDS[m[2].lower()]) if m[2] else 0)), text
    )
    out = {n.replace(",", "") for n in NUMBER.findall(text)}
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in NUMBER_WORDS:
            out.add(NUMBER_WORDS[word])
    return out


def unsupported_numbers(answer: str, allowed_text: str, extra_allowed: set[str] = frozenset()) -> list[str]:
    allowed = numbers_in(allowed_text) | set(extra_allowed)
    # Version numbers can be shortened: "2.0.0" in the source allows "2" or "2.0" in the answer.
    # Section numbers ("2.1") are not split, or every small number would pass.
    for n in list(allowed):
        if n.count(".") >= 2:
            parts = n.split(".")
            allowed.update(".".join(parts[:k]) for k in range(1, len(parts)))
    return sorted(numbers_in(answer) - allowed)


def source_text(chunk: dict) -> str:
    """Everything a citation shows: header (title, version, date, section) and text."""
    return (
        f"{chunk['doc_title']} {chunk['version']} {chunk['effective_date']} "
        f"{chunk['section_number']} {chunk['section_title']}\n{chunk['text']}"
    )


def summarize(issues: list[str], kept: int, dropped: int, repaired: int, numbers_bad: bool) -> Grounding:
    if kept == 0 or numbers_bad:
        status = "ungrounded"
    elif dropped:
        status = "partial"
    else:
        status = "grounded"
    return Grounding(status, issues, repaired, dropped)
