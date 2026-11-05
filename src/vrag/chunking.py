"""Structure-aware chunking for policies/contracts.

Splits on top-level numbered sections ("3. Refunds"), keeps the section number so
citations can point at a clause, and only splits a section further (at sub-clause
boundaries like "3.2") when it is longer than max_chars.
"""

import re
from dataclasses import dataclass

SECTION = re.compile(r"^(?P<num>\d+)\.\s+(?P<title>\S.*)$")
CLAUSE = re.compile(r"^(?P<num>\d+\.\d+(?:\.\d+)*)\s+\S")


@dataclass
class Section:
    number: str
    title: str
    text: str


def split_sections(text: str, max_chars: int = 1500) -> list[Section]:
    lines = [ln.strip() for ln in text.splitlines()]
    sections: list[Section] = []
    num, title, buf = "0", "Preamble", []

    def flush():
        body = "\n".join(ln for ln in buf if ln)
        if body:
            sections.extend(_split_long(num, title, body, max_chars))

    for ln in lines:
        m = SECTION.match(ln)
        if m:
            flush()
            num, title, buf = m["num"], m["title"], [ln]
        else:
            buf.append(ln)
    flush()
    return sections


def _split_long(num: str, title: str, body: str, max_chars: int) -> list[Section]:
    if len(body) <= max_chars:
        return [Section(num, title, body)]
    header, *rest = body.split("\n")
    parts: list[list[str]] = [[]]
    for ln in rest:
        if CLAUSE.match(ln) and parts[-1] and len("\n".join(parts[-1])) + len(ln) > max_chars:
            parts.append([])
        parts[-1].append(ln)
    out = []
    for p in parts:
        first = CLAUSE.match(p[0])
        out.append(Section(first["num"] if first else num, title, "\n".join([header, *p])))
    return out
