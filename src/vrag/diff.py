"""Section-level diff between two versions of one document.

Chunks are paired by text similarity, not by section number: numbering shifts when
rules are inserted (semver 1.0.0 -> 2.0.0), but the text of a rule stays close.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

PAIR_THRESHOLD = 0.5  # below this, two chunks are "removed + added", not "modified"


@dataclass
class Change:
    kind: str  # "modified" | "added" | "removed"
    old: dict | None
    new: dict | None

    @property
    def chunk_ids(self) -> set[str]:
        return {c["chunk_id"] for c in (self.old, self.new) if c}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def diff_versions(old: list[dict], new: list[dict]) -> list[Change]:
    old_t = [_norm(c["text"]) for c in old]
    new_t = [_norm(c["text"]) for c in new]

    # Exact matches first (unchanged sections), then greedy best-similarity pairing.
    used_old: set[int] = set()
    used_new: set[int] = set()
    by_text: dict[str, list[int]] = {}
    for i, t in enumerate(old_t):
        by_text.setdefault(t, []).append(i)
    for j, t in enumerate(new_t):
        if by_text.get(t):
            used_old.add(by_text[t].pop(0))
            used_new.add(j)

    pairs = []
    for i, a in enumerate(old_t):
        if i in used_old:
            continue
        for j, b in enumerate(new_t):
            if j in used_new:
                continue
            sm = SequenceMatcher(None, a, b, autojunk=False)
            if sm.real_quick_ratio() < PAIR_THRESHOLD or sm.quick_ratio() < PAIR_THRESHOLD:
                continue
            r = sm.ratio()
            if r >= PAIR_THRESHOLD:
                pairs.append((r, i, j))

    changes: list[tuple[int, Change]] = []  # (position in new doc for ordering, change)
    for _, i, j in sorted(pairs, reverse=True):
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        changes.append((j, Change("modified", old[i], new[j])))
    for j in range(len(new)):
        if j not in used_new:
            changes.append((j, Change("added", None, new[j])))
    for i in range(len(old)):
        if i not in used_old:
            # place removed sections near where they used to be
            changes.append((min(i, max(len(new) - 1, 0)), Change("removed", old[i], None)))
    return [c for _, c in sorted(changes, key=lambda x: (x[0], x[1].kind))]


def word_changes(old_text: str, new_text: str, max_items: int = 8) -> list[tuple[str, str]]:
    """Removed/added word runs between two texts, e.g. [("14", "21")], for the LLM to anchor on."""
    a, b = old_text.split(), new_text.split()
    out = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            out.append((" ".join(a[i1:i2]), " ".join(b[j1:j2])))
    return out[:max_items]


# --- clause-level changes -------------------------------------------------------
# A section-level Change can hide one changed sentence among ten unchanged ones. For the
# answer we go down to clauses: numbered clauses ("2.1 ..."), list items ("1. ..."), or
# paragraphs (wrapped lines joined), so each change is a short before/after pair.

CLAUSE_START = re.compile(r"^(\d+(?:\.\d+)+\s+\S|\d+\.\s+\S)")
# Only version labels and dates are noise: "v2", "v1.0.0-rc.1", "2.0.0", "2025-04-01".
# Plain numbers ("14 days") and section numbers ("2.1") are real content.
NOISE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\bv\d+(?:\.\d+)*(?:-[0-9a-z.]+)?\b|\b\d+\.\d+\.\d+(?:-[0-9a-z.]+)?\b", re.I
)
HEADING_NUM = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
MAX_CLAUSE_CHARS = 400


@dataclass
class ClauseChange:
    kind: str  # "modified" | "added" | "removed"
    old_chunk: dict | None
    new_chunk: dict | None
    old_text: str = ""
    new_text: str = ""

    @property
    def chunk(self) -> dict:
        return self.new_chunk or self.old_chunk  # type: ignore[return-value]


def clauses(text: str) -> list[str]:
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        cur: list[str] = []
        for ln in para.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if cur and CLAUSE_START.match(ln):
                units.append(" ".join(cur))
                cur = []
            cur.append(ln)
        if cur:
            units.append(" ".join(cur))
    return units


def _only_labels_changed(a: str, b: str) -> bool:
    """True if two clauses differ only in version labels / dates (e.g. 'Version: v2' -> 'Version: v3')."""
    return _norm(NOISE.sub("#", a)) == _norm(NOISE.sub("#", b))


def _body(chunk: dict) -> list[str]:
    """Clauses of a chunk without its heading line (each chunk repeats its section title on top)."""
    units = clauses(chunk["text"])
    title = _norm(chunk.get("section_title", ""))
    if units and title and _norm(HEADING_NUM.sub("", units[0])) == title:
        return units[1:]
    return units


def cut(s: str) -> str:
    """Shorten long clauses for prompts and citations (diffs always use the full text)."""
    return s if len(s) <= MAX_CLAUSE_CHARS else s[: MAX_CLAUSE_CHARS - 3].rstrip() + "..."


def clause_changes(changes: list[Change]) -> list[ClauseChange]:
    out: list[ClauseChange] = []
    for ch in changes:
        if ch.kind == "added":
            out.append(ClauseChange("added", None, ch.new, new_text=" ".join(_body(ch.new))))
            continue
        if ch.kind == "removed":
            out.append(ClauseChange("removed", ch.old, None, old_text=" ".join(_body(ch.old))))
            continue
        a, b = _body(ch.old), _body(ch.new)
        sm = SequenceMatcher(None, [_norm(x) for x in a], [_norm(x) for x in b], autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                continue
            olds, news = a[i1:i2], b[j1:j2]
            for k in range(max(len(olds), len(news))):
                o = olds[k] if k < len(olds) else ""
                n = news[k] if k < len(news) else ""
                if o and n and _only_labels_changed(o, n):
                    continue
                kind = "modified" if o and n else ("added" if n else "removed")
                out.append(ClauseChange(kind, ch.old if o else None, ch.new if n else None, o, n))
    return out
