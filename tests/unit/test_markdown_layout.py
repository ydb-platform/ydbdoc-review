"""Tests for MD031 blanks-around-fences fixes."""

from __future__ import annotations

import re

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.validation.markdown_layout import fix_blanks_around_fences


def _md031_after_close_violations(text: str) -> list[int]:
    lines = text.splitlines()
    bad: list[int] = []
    for i, line in enumerate(lines):
        if re.fullmatch(r"\s*[`~]{3,}\s*", line.strip()) and line.strip():
            if i + 1 < len(lines) and lines[i + 1].strip():
                bad.append(i + 1)
    return bad


def test_fix_blanks_after_indented_close_fence():
    text = (
        "  ```yaml\n"
        "  key: value\n"
        "  ```\n"
        "- Next section\n"
    )
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
    bad = (
        "  ```\n"
        "- Section `blob_storage_config`:\n"
    )
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
    text = (
        "- Item\n"
        "\n"
        "  ```yaml\n"
        "  x: 1\n"
        "  ```\n"
        "\n"
        "  Continuation text.\n"
    )
    out = render_markdown(parse_markdown(text))
    assert _md031_after_close_violations(out) == []


def test_regression_numbered_list_after_indented_fence():
    """PR #42404 deployment-configuration-v1.md ~810: ``` then '4. Set account'."""
    bad = (
        "   ydb yql -s 'CREATE USER user1'\n"
        "   ```\n"
        "4. Set account permissions:\n"
    )
    fixed = fix_blanks_around_fences(bad)
    assert _md031_after_close_violations(fixed) == []
    idx = fixed.splitlines().index("   ```")
    assert fixed.splitlines()[idx + 1] == ""


def test_regression_systemd_item_after_fence():
    """PR #42404 deployment-configuration-v1.md ~611: ``` then '- Using systemd'."""
    bad = (
        "      --node static &\n"
        "  ```\n"
        "- Using systemd\n"
    )
    fixed = fix_blanks_around_fences(bad)
    assert _md031_after_close_violations(fixed) == []


def test_postprocess_en_pipeline_clears_multiple_violations():
    from ydbdoc_review.validation.homoglyphs import postprocess_en_target_markdown

    bad = (
        "  ```\n"
        "- Section `blob_storage_config`:\n"
        "\n"
        "  ```yaml\n"
        "  x: 1\n"
        "  ```\n"
        "- Using systemd\n"
    )
    fixed = postprocess_en_target_markdown(bad)
    assert _md031_after_close_violations(fixed) == []
