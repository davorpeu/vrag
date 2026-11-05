from vrag.chunking import split_sections

TEXT = """Acme Retail Refund Policy
Version: v2
Effective date: 2024-09-01
1. Scope
1.1 This policy applies to all purchases.
2. Refund Window
2.1 Customers may request a refund within 14 days.
2.2 Items must be unused.
"""


def test_splits_on_top_level_sections():
    secs = split_sections(TEXT)
    assert [s.number for s in secs] == ["0", "1", "2"]
    assert secs[2].title == "Refund Window"
    assert "14 days" in secs[2].text


def test_long_section_splits_at_clause_and_keeps_header():
    body = "3. Fees\n" + "\n".join(f"3.{i} " + "x" * 100 for i in range(1, 11))
    secs = split_sections(body, max_chars=350)
    assert len(secs) > 1
    assert all(s.text.startswith("3. Fees") for s in secs)
    assert secs[1].number.startswith("3.")
