from ydbdoc_review.list_tabs_blocks import split_preserving_list_tabs


def test_split_preserving_list_tabs_two_blocks():
    ru = (
        "Intro.\n\n"
        "{% list tabs %}\n\n- tab-a\n\n  ```yaml\n  - legacy\n  ```\n\n{% endlist %}\n\n"
        "Middle.\n\n"
        "{% list tabs %}\n\n- tab-b\n\n{% endlist %}\n\n"
        "Outro.\n"
    )
    segs = split_preserving_list_tabs(ru)
    assert [s.kind for s in segs] == ["prose", "list_tabs", "prose", "list_tabs", "prose"]
    assert "- legacy" in segs[1].text
    assert "Intro." in segs[0].text
    assert "".join(s.text for s in segs) == ru


def test_split_no_tabs_single_prose():
    segs = split_preserving_list_tabs("Hello only.\n")
    assert len(segs) == 1
    assert segs[0].kind == "prose"
