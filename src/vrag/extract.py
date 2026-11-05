import io
import re
from datetime import date

import docx
from pypdf import PdfReader

EFFECTIVE_DATE = re.compile(r"effective\s+date\s*:\s*(\d{4}-\d{2}-\d{2})", re.I)


def extract_text(filename: str, content: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if name.endswith(".docx"):
        d = docx.Document(io.BytesIO(content))
        return "\n".join(p.text for p in d.paragraphs)
    raise ValueError(f"Unsupported file type: {filename}")


def find_effective_date(text: str) -> date | None:
    m = EFFECTIVE_DATE.search(text)
    return date.fromisoformat(m.group(1)) if m else None


def find_title(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""
