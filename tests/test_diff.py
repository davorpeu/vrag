from vrag.diff import diff_versions, word_changes


def c(cid, text):
    return {"chunk_id": cid, "text": text, "section_number": cid}


def test_unchanged_modified_added_removed():
    old = [c("o0", "Title v1"), c("o1", "Refunds within 14 days of purchase."), c("o2", "Gift cards excluded entirely from the policy here.")]
    new = [c("n0", "Title v1"), c("n1", "Refunds within 21 days of purchase."), c("n2", "Brand new section about shipping costs and fees.")]
    kinds = [(ch.kind, (ch.old or {}).get("chunk_id"), (ch.new or {}).get("chunk_id")) for ch in diff_versions(old, new)]
    assert ("modified", "o1", "n1") in kinds
    assert ("added", None, "n2") in kinds
    assert ("removed", "o2", None) in kinds
    assert not any(k[1] == "o0" for k in kinds)  # unchanged title not reported


def test_pairs_by_text_not_position():
    rule = "Major version X MUST be incremented if any backwards incompatible changes are introduced."
    old = [c("a", rule)]
    new = [c("x", "A completely new first rule about build metadata."), c("y", rule.replace("any", "some"))]
    ch = diff_versions(old, new)
    assert [(k.kind, (k.old or {}).get("chunk_id"), (k.new or {}).get("chunk_id")) for k in ch] == [
        ("added", None, "x"),
        ("modified", "a", "y"),
    ]


def test_word_changes():
    assert word_changes("within 14 days of purchase", "within 21 days of purchase") == [("14", "21")]


def test_mentions_change():
    from vrag.answer import mentions_change
    from vrag.diff import ClauseChange

    mod = ClauseChange("modified", {}, {}, "1.1 Employees receive 20 days of leave.", "1.1 Employees receive 25 days of leave.")
    assert mentions_change("Annual leave went from 20 to 25 days.", mod)
    assert not mentions_change("Remote work abroad was added.", mod)
    add = ClauseChange("added", None, {}, "", "5.1 Employees may work remotely from abroad for up to 20 working days per year.")
    assert mentions_change("v3 lets employees work remotely from abroad.", add)
    assert not mentions_change("Annual leave went from 20 to 25 days.", add)
