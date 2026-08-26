"""RU source bug detection and pre-translate normalization."""

from __future__ import annotations

from ydbdoc_review.validation.ru_source_bugs import (
    check_required_anchor_lines,
    detect_ru_source_bugs,
    normalize_legacy_markdown_structure,
    normalize_ru_source_for_translation,
)


def test_detect_config_dir_glued():
    text = "sudo ydb admin node config init --config-dir/opt/ydb/cfg\n"
    issues = detect_ru_source_bugs(text)
    assert any("config-dir" in i for i in issues)


def test_normalize_fixes_config_dir():
    text = "init --config-dir/opt/ydb/cfg\n"
    assert "--config-dir /opt" in normalize_ru_source_for_translation(text)


def test_missing_web_pem_anchor():
    ru = "sudo -u ydb test -r /opt/ydb/certs/web.pem\n"
    en = "sudo cp web.pem\n"
    warnings = check_required_anchor_lines(ru, en)
    assert any("missing_anchor" in w for w in warnings)


def test_normalize_legacy_closing_fence_drops_info_string():
    text = "```python\nprint('ok')\n  ```python\n"
    assert normalize_legacy_markdown_structure(text).endswith("```\n")


def test_normalize_legacy_drops_duplicate_endlist_only():
    text = (
        "{% list tabs %}\n"
        "- Python\n"
        "{% endlist %}\n"
        "{% endlist %}\n"
        "{% endcut %}\n"
    )
    assert normalize_legacy_markdown_structure(text) == (
        "{% list tabs %}\n- Python\n{% endlist %}\n{% endcut %}\n"
    )


def test_normalize_legacy_keeps_nonduplicate_interleaved_closer():
    text = (
        "{% list tabs %}\n"
        "{% cut \"asyncio\" %}\n"
        "{% endlist %}\n"
        "{% endcut %}\n"
        "{% endlist %}\n"
    )
    assert normalize_legacy_markdown_structure(text) == (
        "{% list tabs %}\n"
        "{% cut \"asyncio\" %}\n"
        "{% endcut %}\n"
        "{% endlist %}\n"
    )


def test_normalize_legacy_limits_empty_overlay_include_indent():
    text = "    {% include [overlay](_includes/empty.md) %}\n"
    assert normalize_legacy_markdown_structure(text).startswith("  {% include")


def test_normalize_legacy_replaces_unmatched_endcut_inside_fence():
    text = (
        "{% list tabs %}\n"
        "  ```python\n"
        "  code()\n"
        "{% endcut %}\n"
        "  ```python\n"
        "  next_code()\n"
        "  ```\n"
        "{% endlist %}\n"
    )
    fixed = normalize_legacy_markdown_structure(text)
    assert "{% endcut %}" not in fixed
    assert fixed.count("  ```\n") == 2
    assert fixed.endswith("{% endlist %}\n")


def test_normalize_legacy_closes_fence_before_peer_tab_item():
    text = (
        "{% list tabs %}\n"
        "- Python\n"
        "    ```python\n"
        "    code()\n"
        "    - Native SDK (Asyncio)\n"
        "      ```python\n"
        "      async_code()\n"
        "      ```\n"
        "{% endlist %}\n"
    )
    fixed = normalize_legacy_markdown_structure(text)
    assert "    code()\n    ```\n\n    - Native SDK" in fixed
