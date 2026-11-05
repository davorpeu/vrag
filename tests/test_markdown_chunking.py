from vrag.chunking import split_markdown

DOC = """Spec 2.0
========

Summary
-------

Short summary.

Rules
-----

Intro to the rules.

1. First rule MUST hold.
   It continues here.

1. Second rule SHOULD hold.

1. Third rule MAY hold.

## FAQ

### Is this a question?

```
# not a heading
```
Yes.
"""


def test_headings_numbering_and_title():
    secs = split_markdown(DOC, max_chars=10_000)
    assert [(s.number, s.title) for s in secs] == [
        ("1", "Summary"),
        ("2", "Rules"),
        ("4", "Is this a question?"),  # "FAQ" (3) has no body and is dropped; title keeps 0
    ]
    assert "# not a heading" in secs[-1].text


def test_long_section_splits_at_rules_numbered_in_order():
    secs = [s for s in split_markdown(DOC, max_chars=40) if s.title == "Rules"]
    assert [s.number for s in secs] == ["2", "2.1", "2.2", "2.3"]
    assert "It continues here." in secs[1].text
    assert all(s.text.startswith("Rules") for s in secs)
