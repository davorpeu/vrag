from vrag.answer import mentions_change
from vrag.diff import ClauseChange


def mod(title, old, new):
    chunk = {"section_number": "x", "section_title": title}
    return ClauseChange("modified", chunk, chunk, old, new)


RETENTION = mod("Customer data", "2.1 Customer account data is retained for 7 years after account closure.",
                "2.1 Customer account data is retained for 5 years after account closure.")
AUDIT = mod("Logs", "3.2 Security audit logs are retained for 1 year.", "3.2 Security audit logs are retained for 2 years.")


def test_overlapping_words_do_not_cover_other_change():
    said = "Customer account data retention went from 7 years to 5 years."
    assert mentions_change(said, RETENTION)
    assert not mentions_change(said, AUDIT)


def test_second_change_covered_when_its_numbers_are_said():
    said = "Customer data: 7 to 5 years. Security audit logs: 1 year to 2 years."
    assert mentions_change(said, AUDIT)


def test_non_numeric_change_still_uses_words():
    c = mod("Access", "1.1 Staff must use a password.", "1.1 Staff must use a passkey.")
    assert mentions_change("Staff now need a passkey.", c)
    assert not mentions_change("Retention is 5 years.", c)


def test_section_and_change_numbers_do_not_count_as_values():
    said = "Change 1: In section 2 Customer Data, account data is kept 5 years instead of 7 years."
    assert mentions_change(said, RETENTION)
    assert not mentions_change(said, AUDIT)  # "2" is a section number here, not the new value
