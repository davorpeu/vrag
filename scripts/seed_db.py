"""Seed the docs-db Postgres with a versioned policy that exists only in the database.

Idempotent: re-running updates rows in place. Ground truth of what changed per version is in
data/golden/changes.json under "travel-expense-policy".

Usage: .venv/Scripts/python scripts/seed_db.py
"""

import psycopg
from dotenv import load_dotenv

load_dotenv(".env")

from vrag.config import get_settings  # noqa: E402

SLUG = "travel-expense-policy"
TITLE = "Contoso Travel and Expense Policy"

BASE = {
    "1.1": "Flights must be booked at least 14 days before travel.",
    "1.2": "Economy class is required for flights under 6 hours.",
    "2.1": "The daily meal allowance is 50 EUR.",
    "2.2": "Alcohol is not reimbursable.",
    "3.1": "Hotel stays are reimbursed up to 150 EUR per night.",
    "4.1": "Expense claims must be submitted within 30 days of the trip.",
}
SECTIONS = {"1": "Booking", "2": "Meals", "3": "Hotels", "4": "Claims", "5": "Rail Travel"}

# label, effective date, changes on top of the previous version (None = clause removed)
VERSIONS = [
    ("v1.0", "2024-02-01", {}),
    ("v1.1", "2024-08-01", {
        "2.1": "The daily meal allowance is 60 EUR.",
        "4.1": "Expense claims must be submitted within 60 days of the trip.",
    }),
    ("v2.0", "2025-03-01", {
        "1.2": "Economy class is required for flights under 8 hours.",
        "2.2": "Alcohol is reimbursable only for client dinners, up to 30 EUR per person.",
        "3.1": "Hotel stays are reimbursed up to 180 EUR per night.",
        "5.1": "Rail travel is required for trips under 4 hours where a direct train exists.",
    }),
]


def render(label: str, effective: str, clauses: dict[str, str]) -> str:
    lines = [f"# {TITLE}", "", f"Version: {label}", f"Effective date: {effective}", ""]
    for sec, name in SECTIONS.items():
        body = [f"{k} {v}" for k, v in sorted(clauses.items()) if k.split(".")[0] == sec]
        if body:
            lines += [f"## {sec}. {name}", "", *body, ""]
    return "\n".join(lines)


def main() -> None:
    clauses = dict(BASE)
    with psycopg.connect(get_settings().docs_db_url, connect_timeout=10) as conn:
        doc_id = conn.execute(
            "INSERT INTO documents (slug, title) VALUES (%s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET title = EXCLUDED.title RETURNING id",
            (SLUG, TITLE),
        ).fetchone()[0]
        for label, effective, changes in VERSIONS:
            for k, v in changes.items():
                if v is None:
                    clauses.pop(k, None)
                else:
                    clauses[k] = v
            text = render(label, effective, clauses)
            conn.execute(
                "INSERT INTO document_versions (document_id, version_label, effective_from, filename, content) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (document_id, version_label) DO UPDATE SET "
                "effective_from = EXCLUDED.effective_from, filename = EXCLUDED.filename, content = EXCLUDED.content",
                (doc_id, label, effective, "policy.md", text.encode("utf-8")),
            )
            print(f"{SLUG} {label} ({effective}): {len(clauses)} clauses")


if __name__ == "__main__":
    main()
