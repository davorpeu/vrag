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


# --- Markdown -------------------------------------------------------------
# Sections = headings up to level 3 (ATX "## Title" or setext "Title\n-----"),
# numbered in document order (preamble = 0). Long sections are split at numbered
# list items ("9. ...") so a citation can point at "3.9" = section 3, rule 9,
# otherwise at paragraph boundaries.

ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SETEXT = re.compile(r"^(=+|-+)\s*$")
LIST_ITEM = re.compile(r"^(?P<num>\d+)\.\s+\S")
FENCE = re.compile(r"^(```|~~~)")


def split_markdown(text: str, max_chars: int = 1500, max_level: int = 3) -> list[Section]:
    lines = text.splitlines()
    sections: list[Section] = []
    num, title, buf = 0, "Preamble", []
    in_fence = False

    def flush():
        body = "\n".join(buf).strip()
        if body and body != title:  # skip heading-only sections like "FAQ"
            sections.extend(_split_long_md(str(num), title, body, max_chars))

    i = 0
    while i < len(lines):
        ln = lines[i]
        if FENCE.match(ln.strip()):
            in_fence = not in_fence
        heading = None
        if not in_fence:
            m = ATX.match(ln)
            if m and len(m[1]) <= max_level:
                heading = m[2]
            elif (
                ln.strip()
                and i + 1 < len(lines)
                and SETEXT.match(lines[i + 1])
                and (not buf or not buf[-1].strip())  # setext title is a one-line paragraph
            ):
                heading = ln.strip()
        if heading is not None:
            # A heading before any content is the document title: it keeps number 0.
            first = num == 0 and not "\n".join(buf).strip()
            flush()
            num, title, buf = (0 if first else num + 1), heading, [heading]
            i += 2 if not ATX.match(ln) else 1
            continue
        buf.append(ln)
        i += 1
    flush()
    return sections


def _split_long_md(num: str, title: str, body: str, max_chars: int) -> list[Section]:
    if len(body) <= max_chars:
        return [Section(num, title, body)]
    header, rest = body.split("\n", 1)
    # blocks = paragraphs or numbered list items (with their continuation lines)
    blocks: list[str] = []
    for para in re.split(r"\n\s*\n", rest):
        cur: list[str] = []
        for ln in para.splitlines():
            if LIST_ITEM.match(ln) and cur:
                blocks.append("\n".join(cur))
                cur = []
            cur.append(ln)
        if cur:
            blocks.append("\n".join(cur))
    parts: list[list[str]] = [[]]
    for b in blocks:
        starts_item = LIST_ITEM.match(b)
        size = sum(len(x) for x in parts[-1]) + len(b)
        if parts[-1] and (starts_item or size > max_chars):
            parts.append([])
        parts[-1].append(b)
    # Number items in order rather than trusting the literal number: Markdown renders
    # "1. 1. 1." as 1, 2, 3 and many docs (semver.md included) are written that way.
    out, item_no = [], 0
    for p in parts:
        sub = f"{num}.{item_no + 1}" if LIST_ITEM.match(p[0]) else num
        item_no += sum(1 for b in p if LIST_ITEM.match(b))
        out.append(Section(sub, title, "\n\n".join([header, *p])))
    return out
