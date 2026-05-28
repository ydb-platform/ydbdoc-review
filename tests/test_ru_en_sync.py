from ydbdoc_review.heuristics import _check_tab_labels_parity
from ydbdoc_review.ru_en_sync import (
    finalize_en_document_from_ru,
    restore_fence_openers_from_source,
    sync_fenced_blocks_from_source,
    sync_verbatim_list_tabs_from_source,
)
from ydbdoc_review.tabs_repair import is_tab_label_line, repair_tab_labels_from_source


def test_yaml_legacy_is_not_tab_label():
    assert is_tab_label_line("- mirror-3-dc-3nodes")
    assert not is_tab_label_line("      - legacy")
    assert not is_tab_label_line("  - legacy")  # indented YAML list item


def test_repair_does_not_inject_legacy_between_config_tabs():
    ru = (
        "{% list tabs %}\n\n"
        "- mirror-3-dc-3nodes\n\n"
        "  ```yaml\n"
        "  services_enabled:\n"
        "  - legacy\n"
        "  ```\n\n"
        "- mirror-3-dc-9nodes\n\n"
        "  ```yaml\n"
        "  x: 1\n"
        "  ```\n\n"
        "{% endlist %}\n"
    )
    broken_en = (
        "{% list tabs %}\n\n"
        "- mirror-3-dc-3nodes\n\n"
        "      - legacy\n"
        "- mirror-3-dc-9nodes\n\n"
        "  ```yaml\n"
        "  x: 1\n"
        "  ```\n\n"
        "{% endlist %}\n"
    )
    fixed, _ = repair_tab_labels_from_source(ru, broken_en)
    assert "- mirror-3-dc-3nodes\n\n      - legacy\n" not in fixed
    assert _check_tab_labels_parity(source=ru, translation=fixed) is None


def test_sync_verbatim_list_tabs_replaces_broken_en_block():
    ru = (
        "Intro.\n\n"
        "{% list tabs %}\n\n"
        "- mirror-3-dc-3nodes\n\n"
        "  ```yaml\n"
        "  services_enabled:\n"
        "  - legacy\n"
        "  ```\n\n"
        "{% endlist %}\n"
    )
    broken = ru.replace("Intro.", "Intro EN.").replace(
        "- mirror-3-dc-3nodes\n\n  ```yaml",
        "- mirror-3-dc-3nodes\n\n      - legacy\n  ```yaml",
    )
    out, changed = sync_verbatim_list_tabs_from_source(ru, broken)
    assert changed
    assert "      - legacy\n" not in out.split("```yaml")[0]
    assert "services_enabled:" in out


def test_sync_fenced_blocks_from_source():
    ru = "Text\n\n```yaml\nkey: 1\n```\n\nMore\n"
    en = "Text EN\n\n```yaml\nkey: 999\n```\n\nMore EN\n"
    out, changed = sync_fenced_blocks_from_source(ru, en)
    assert changed
    assert "key: 1" in out
    assert "key: 999" not in out


def test_restore_fence_openers_from_source():
    ru = "```bash\necho 1\n```\n\n```text\nok\n```\n"
    en = "```\necho 1\n```\n\n```\nok\n```\n"
    out, changed = restore_fence_openers_from_source(ru, en)
    assert changed
    assert "```bash" in out
    assert "```text" in out


def test_finalize_restores_markdown_link():
    ru = "See [{{ ydb-short-name }} CLI](../../../reference/ydb-cli/profile/index.md).\n"
    en = "See {{ ydb-short-name }} CLI documentation (../../../reference/ydb-cli/profile/index.md).\n"
    out = finalize_en_document_from_ru(ru, en)
    assert "](../../../reference/ydb-cli/profile/index.md)" in out
