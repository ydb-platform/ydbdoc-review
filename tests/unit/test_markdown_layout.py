"""Tests for MD031 blanks-around-fences fixes."""

from __future__ import annotations

import re

from ydbdoc_review.harness.render import finalize_en_target
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.validation.markdown_layout import (
    fix_blanks_around_fences,
    fix_image_bang_spacing,
    repair_generated_markdown_layout,
)


def _md031_after_close_violations(text: str) -> list[int]:
    lines = text.splitlines()
    bad: list[int] = []
    for i, line in enumerate(lines):
        if re.fullmatch(r"\s*[`~]{3,}\s*", line.strip()) and line.strip():
            if i + 1 < len(lines) and lines[i + 1].strip():
                bad.append(i + 1)
    return bad


def test_fix_blanks_after_indented_close_fence():
    text = "  ```yaml\n  key: value\n  ```\n- Next section\n"
    fixed = fix_blanks_around_fences(text)
    assert fixed.splitlines()[3] == ""
    assert "- Next section" in fixed


def test_render_tight_list_with_fence_keeps_blank_before_next_item():
    """Regression: RU docs use a blank line between list items after a fence."""
    text = (
        "- Intro\n"
        "\n"
        "  ```yaml\n"
        "  host: x\n"
        "  ```\n"
        "\n"
        "- Section `blob_storage_config`:\n"
        "\n"
        "  ```yaml\n"
        "  fail_domains: []\n"
        "  ```\n"
    )
    out = render_markdown(parse_markdown(text))
    assert _md031_after_close_violations(out) == []


def test_postprocess_fixes_existing_en_pattern():
    bad = "  ```\n- Section `blob_storage_config`:\n"
    fixed = fix_blanks_around_fences(bad)
    assert _md031_after_close_violations(fixed) == []


def test_fix_blanks_before_opening_fence():
    text = "Intro line.\n```yaml\nkey: x\n```\n"
    fixed = fix_blanks_around_fences(text)
    lines = fixed.splitlines()
    assert lines[0] == "Intro line."
    assert lines[1] == ""
    assert lines[2].startswith("```yaml")


def test_render_fence_inside_list_item_before_paragraph():
    """Fence then prose inside one list item (paragraph after fenced_code)."""
    text = "- Item\n\n  ```yaml\n  x: 1\n  ```\n\n  Continuation text.\n"
    out = render_markdown(parse_markdown(text))
    assert _md031_after_close_violations(out) == []


def test_regression_numbered_list_after_indented_fence():
    """PR #42404 deployment-configuration-v1.md ~810: ``` then '4. Set account'."""
    bad = "   ydb yql -s 'CREATE USER user1'\n   ```\n4. Set account permissions:\n"
    fixed = fix_blanks_around_fences(bad)
    assert _md031_after_close_violations(fixed) == []
    idx = fixed.splitlines().index("   ```")
    assert fixed.splitlines()[idx + 1] == ""


def test_regression_systemd_item_after_fence():
    """PR #42404 deployment-configuration-v1.md ~611: ``` then '- Using systemd'."""
    bad = "      --node static &\n  ```\n- Using systemd\n"
    fixed = fix_blanks_around_fences(bad)
    assert _md031_after_close_violations(fixed) == []


def test_postprocess_en_pipeline_clears_multiple_violations():
    from ydbdoc_review.validation.homoglyphs import postprocess_en_target_markdown

    bad = "  ```\n- Section `blob_storage_config`:\n\n  ```yaml\n  x: 1\n  ```\n- Using systemd\n"
    fixed = postprocess_en_target_markdown(bad)
    assert _md031_after_close_violations(fixed) == []


def test_fix_image_bang_spacing():
    assert fix_image_bang_spacing("! [alt](img.png)") == "![alt](img.png)"
    assert fix_image_bang_spacing("![ok](img.png)") == "![ok](img.png)"


def test_repair_drops_only_renderer_inserted_fence_markers():
    source = (
        "- Python\n\n"
        '    {% cut "asyncio" %}\n\n'
        "    ```python\n    async_code()\n"
        "      ```python\n      import ydb\n"
        "    {% endcut %}\n\n"
        "    ```python\n    sync_code()\n      ```\n\n"
        "    {% endlist %}\n"
    )
    rendered = (
        "- Python\n\n"
        '  {% cut "asyncio" %}\n\n'
        "  ```python\n  async_code()\n"
        "    ```python\n    import ydb\n  ```\n"
        "  {% endcut %}\n\n"
        "  ```python\n  sync_code()\n    ```\n  ```\n\n"
        "  {% endlist %}\n"
    )
    fixed = repair_generated_markdown_layout(source, rendered)
    fence = re.compile(r"^\s*(`{3,}|~{3,})(.*)$", re.MULTILINE)
    assert [m.group(1) + m.group(2).strip() for m in fence.finditer(fixed)] == [
        m.group(1) + m.group(2).strip() for m in fence.finditer(source)
    ]
    assert "    {% endcut %}" in fixed
    assert "    {% endlist %}" in fixed


def test_verify_finalize_uses_ru_layout_with_en_fence_body_authority():
    source = "    ```python\n    source()\n      ```\n"
    existing_en = "  ```python\n  translated()\n    ```\n  ```\n"
    fixed = finalize_en_target(
        existing_en,
        existing_en,
        layout_source_text=source,
    )
    assert fixed == "    ```python\n  translated()\n      ```\n\n"


def test_repair_generated_layout_fixes_md009_and_md022():
    source = "### Source heading\n\nSource text.\n"
    target = "### Heading\nText.\n \n- \nNext.\n"
    fixed = repair_generated_markdown_layout(source, target)
    assert "### Heading\n\nText." in fixed
    assert all(not line or line == line.rstrip() for line in fixed.splitlines())
    assert "\n- \n" not in fixed


def test_repair_preserves_intentional_nonblank_hard_break():
    text = "Line with hard break.  \nNext.\n"
    assert repair_generated_markdown_layout(text, text) == text


def test_repair_removes_four_space_trailing_whitespace():
    target = "Text.    \nNext.\n"
    assert repair_generated_markdown_layout(target, target) == "Text.\nNext.\n"


def test_repair_restores_source_fence_indentation_when_markers_match():
    source = "    ```python\n    source()\n    ```\n"
    target = "  ```python\n  translated()\n  ```\n"
    fixed = repair_generated_markdown_layout(source, target)
    assert fixed == "    ```python\n  translated()\n    ```\n"


def test_repair_syncs_equal_yfm_directive_sequence_indentation():
    source = (
        "    {% list tabs group=tool %}\n"
        '    {% cut "Source title" %}\n'
        "    {% endcut %}\n"
        "    {% if oss == true %}\n"
        "    {% endif %}\n"
        "    {% endlist %}\n"
    )
    target = (
        "  {% list tabs group=tool %}\n"
        '  {% cut "Translated title" %}\n'
        "{% endcut %}\n"
        "  {% if oss == true %}\n"
        "{% endif %}\n"
        "{% endlist %}\n"
    )
    fixed = repair_generated_markdown_layout(source, target)
    assert fixed == target.replace("  {%", "    {%").replace("\n{%", "\n    {%")


def test_repair_restores_indentation_of_unchanged_technical_lines():
    source = "Intro RU.\n\n    code()\n        nested()\n"
    target = "Translated intro.\n\n  code()\n  nested()\n"
    fixed = repair_generated_markdown_layout(source, target)
    assert "    code()\n        nested()" in fixed
