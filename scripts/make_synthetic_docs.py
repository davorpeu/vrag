"""Generate a small versioned corpus with known, deliberate changes between versions.

Output:
  data/corpus/<doc_id>/v<N>.(pdf|docx)   -- what the folder source ingests
  data/golden/changes.json               -- ground truth of what changed (used for evals later)

Run: python scripts/make_synthetic_docs.py
"""

import json
from copy import deepcopy
from pathlib import Path

import docx
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus"
GOLDEN = ROOT / "data" / "golden"

# Each doc: title, base sections (v1), then per-version patches {"section.clause": new text}.
# A patch value of None removes the clause; a new key adds it.
DOCS = {
    "refund-policy": {
        "title": "Acme Retail Refund Policy",
        "formats": ["pdf", "pdf", "docx"],
        "dates": ["2024-01-15", "2024-09-01", "2025-04-01"],
        "sections": {
            "1": ("Scope", {
                "1.1": "This policy applies to all purchases made in Acme Retail stores and on acme-retail.example.",
                "1.2": "Gift cards and digital downloads are excluded from this policy.",
            }),
            "2": ("Refund Window", {
                "2.1": "Customers may request a refund within 30 days of the purchase date.",
                "2.2": "Items must be returned unused and in their original packaging.",
            }),
            "3": ("Refund Method", {
                "3.1": "Refunds are issued to the original payment method within 10 business days.",
                "3.2": "Cash purchases are refunded in cash at the store of purchase.",
            }),
            "4": ("Restocking Fees", {
                "4.1": "No restocking fee is charged on returned items.",
            }),
        },
        "patches": [
            {},
            {
                "2.1": "Customers may request a refund within 14 days of the purchase date.",
                "4.1": "A restocking fee of 15% applies to opened electronics.",
                "4.2": "The restocking fee is waived for defective items.",
            },
            {
                "2.1": "Customers may request a refund within 21 days of the purchase date.",
                "3.1": "Refunds are issued to the original payment method within 5 business days.",
                "1.2": None,
                "1.3": "Gift cards are non-refundable. Digital downloads may be refunded within 48 hours if not downloaded.",
            },
        ],
    },
    "leave-policy": {
        "title": "Northwind Employee Leave Policy",
        "formats": ["docx", "docx", "pdf"],
        "dates": ["2023-07-01", "2024-07-01", "2025-07-01"],
        "sections": {
            "1": ("Annual Leave", {
                "1.1": "Full-time employees receive 20 days of paid annual leave per calendar year.",
                "1.2": "Up to 5 unused days may be carried over to the next year.",
            }),
            "2": ("Sick Leave", {
                "2.1": "Employees receive 10 days of paid sick leave per year.",
                "2.2": "A medical certificate is required for absences longer than 3 consecutive days.",
            }),
            "3": ("Parental Leave", {
                "3.1": "Primary caregivers receive 12 weeks of paid parental leave.",
                "3.2": "Secondary caregivers receive 2 weeks of paid parental leave.",
            }),
            "4": ("Notice", {
                "4.1": "Annual leave requests must be submitted at least 2 weeks in advance.",
            }),
        },
        "patches": [
            {},
            {
                "1.1": "Full-time employees receive 25 days of paid annual leave per calendar year.",
                "3.2": "Secondary caregivers receive 4 weeks of paid parental leave.",
            },
            {
                "1.2": "Up to 10 unused days may be carried over to the next year and must be used by March 31.",
                "2.2": "A medical certificate is required for absences longer than 5 consecutive days.",
                "5.1": "Employees may work remotely from abroad for up to 20 working days per year.",
            },
        ],
    },
    "data-retention": {
        "title": "Contoso Data Retention Standard",
        "formats": ["docx", "pdf", "pdf"],
        "dates": ["2024-03-01", "2024-11-15", "2025-06-01"],
        "sections": {
            "1": ("Purpose", {
                "1.1": "This standard defines how long Contoso retains customer and operational data.",
            }),
            "2": ("Customer Data", {
                "2.1": "Customer account data is retained for 7 years after account closure.",
                "2.2": "Payment card data is never stored; only payment tokens are retained.",
            }),
            "3": ("Logs", {
                "3.1": "Application logs are retained for 90 days.",
                "3.2": "Security audit logs are retained for 1 year.",
            }),
            "4": ("Deletion Requests", {
                "4.1": "Verified deletion requests are completed within 45 days.",
            }),
        },
        "patches": [
            {},
            {
                "3.1": "Application logs are retained for 30 days.",
                "4.1": "Verified deletion requests are completed within 30 days.",
            },
            {
                "2.1": "Customer account data is retained for 5 years after account closure.",
                "3.2": "Security audit logs are retained for 2 years.",
            },
        ],
    },
}

SECTION_TITLES_FOR_NEW = {"5": "Remote Work Abroad"}


def apply_patches(sections: dict, patches: list[dict]) -> list[dict]:
    """Return the full section tree for each version (cumulative patches)."""
    versions, cur = [], deepcopy(sections)
    for patch in patches:
        for key, text in patch.items():
            sec = key.split(".")[0]
            if sec not in cur:
                cur[sec] = (SECTION_TITLES_FOR_NEW.get(sec, f"Section {sec}"), {})
            if text is None:
                cur[sec][1].pop(key, None)
            else:
                cur[sec][1][key] = text
        versions.append(deepcopy(cur))
    return versions


def lines_for(title: str, version: int, eff: str, tree: dict) -> list[tuple[str, str]]:
    out = [("title", title), ("meta", f"Version: v{version}"), ("meta", f"Effective date: {eff}")]
    for sec in sorted(tree, key=int):
        sec_title, clauses = tree[sec]
        out.append(("heading", f"{sec}. {sec_title}"))
        for key in sorted(clauses, key=lambda k: [int(p) for p in k.split(".")]):
            out.append(("clause", f"{key} {clauses[key]}"))
    return out


def write_docx(path: Path, lines):
    d = docx.Document()
    for kind, text in lines:
        if kind == "title":
            d.add_heading(text, level=0)
        elif kind == "heading":
            d.add_heading(text, level=1)
        else:
            d.add_paragraph(text)
    d.save(path)


def write_pdf(path: Path, lines):
    styles = getSampleStyleSheet()
    style = {"title": styles["Title"], "heading": styles["Heading2"], "meta": styles["Normal"], "clause": styles["BodyText"]}
    story = []
    for kind, text in lines:
        story.append(Paragraph(text, style[kind]))
        if kind == "meta":
            story.append(Spacer(1, 4))
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


def main():
    changes = {}
    for doc_id, spec in DOCS.items():
        out_dir = CORPUS / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        trees = apply_patches(spec["sections"], spec["patches"])
        for i, (tree, fmt, eff) in enumerate(zip(trees, spec["formats"], spec["dates"]), start=1):
            lines = lines_for(spec["title"], i, eff, tree)
            path = out_dir / f"v{i}.{fmt}"
            (write_pdf if fmt == "pdf" else write_docx)(path, lines)
            print(f"wrote {path.relative_to(ROOT)}")
        changes[doc_id] = {
            "title": spec["title"],
            "versions": [
                {"version": f"v{i}", "effective_date": eff, "changed": patch}
                for i, (eff, patch) in enumerate(zip(spec["dates"], spec["patches"]), start=1)
            ],
        }
    GOLDEN.mkdir(parents=True, exist_ok=True)
    (GOLDEN / "changes.json").write_text(json.dumps(changes, indent=2))
    print(f"wrote {(GOLDEN / 'changes.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
