from ydbdoc_review.list_tabs_blocks import (
    list_tabs_block_copy_verbatim,
    split_preserving_list_tabs,
)


def test_list_tabs_block_copy_verbatim_manual_english_not_config():
    manual_en = (
        "{% list tabs group=manual-systemd %}\n\n"
        "- Manually\n\n"
        "Run the service.\n\n"
        "{% endlist %}\n"
    )
    assert not list_tabs_block_copy_verbatim(manual_en)


def test_list_tabs_block_copy_verbatim_config_yes_manual_no():
    config = (
        "{% list tabs %}\n\n- mirror-3-dc-3nodes\n\n"
        "  ```yaml\n  services_enabled:\n  - legacy\n  ```\n\n{% endlist %}\n"
    )
    manual = (
        "{% list tabs group=manual-systemd %}\n\n"
        "- Вручную\n\nЗапустите сервис.\n\n"
        "- С использованием systemd\n\n{% endlist %}\n"
    )
    assert list_tabs_block_copy_verbatim(config)
    assert not list_tabs_block_copy_verbatim(manual)


def test_split_preserving_list_tabs_two_blocks():
    ru = (
        "Intro.\n\n"
        "{% list tabs %}\n\n- mirror-3-dc-3nodes\n\n  ```yaml\n  - legacy\n  ```\n\n{% endlist %}\n\n"
        "Middle.\n\n"
        "{% list tabs %}\n\n- Manually\n\n{% endlist %}\n\n"
        "Outro.\n"
    )
    segs = split_preserving_list_tabs(ru)
    assert [s.kind for s in segs] == [
        "prose",
        "list_tabs_verbatim",
        "prose",
        "list_tabs_translate",
        "prose",
    ]
    assert "- legacy" in segs[1].text
    assert "Intro." in segs[0].text
    assert "".join(s.text for s in segs) == ru


def test_split_no_tabs_single_prose():
    segs = split_preserving_list_tabs("Hello only.\n")
    assert len(segs) == 1
    assert segs[0].kind == "prose"
