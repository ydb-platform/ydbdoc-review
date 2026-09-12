"""F-046 contracts for structural and completeness QA."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.qa import gate_round_trip
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.validation.heuristics import (
    check_fence_parity,
    check_heading_parity,
    check_list_tab_parity,
    check_md_link_parity,
)
from ydbdoc_review.validation.include_targets import check_include_parity


def _complex_markdown() -> str:
    return (
        "# Section one\n\n"
        "Text with a [link](other.md).\n\n"
        "- First item\n"
        "- Second item\n\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| row | 42 |\n\n"
        "{% note tip \"Hint\" %}\n\n"
        "Note body.\n\n"
        "{% endnote %}\n\n"
        "{% list tabs %}\n\n"
        "- Example\n\n"
        "  Tab body.\n\n"
        "{% endlist %}\n\n"
        "{% include [details](_includes/details.md) %}\n\n"
        "```yaml\nkey: value\n```\n"
    )


def test_F046_roundtrip() -> None:
    source = _complex_markdown()
    rendered = render_markdown(parse_markdown(source))

    source_segments = extract_segments(parse_markdown(source))
    rendered_segments = extract_segments(parse_markdown(rendered))
    assert [segment.kind for segment in rendered_segments] == [
        segment.kind for segment in source_segments
    ]
    assert rendered.count("{% note tip") == 1
    assert rendered.count("{% endnote %}") == 1
    assert rendered.count("{% list tabs %}") == 1
    assert rendered.count("{% endlist %}") == 1
    assert rendered.count("{% include") == 1
    assert rendered.count("```yaml") == 1
    assert check_heading_parity(source, rendered) == []
    assert check_list_tab_parity(source, rendered) == []
    assert check_fence_parity(source, rendered) == []
    assert check_include_parity(
        source,
        rendered,
        source_file="ydb/docs/ru/core/page.md",
    ) == []


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        (
            "# Section\n\nBody\n\n| A |\n| --- |\n| B |\n",
            "# Section\n\n| A |\n| --- |\n| B |\n",
            "alignment",
        ),
        (
            "# Section\n\nBody\n\n| A |\n| --- |\n| B |\n",
            "# Section\n\nBody\n\n| A |\n| --- |\n",
            "alignment",
        ),
        (
            "Text with [link](other.md).\n",
            "Text without a link.\n",
            "md_link_parity",
        ),
        (
            "{% include [details](_includes/details.md) %}\n",
            "",
            "include_parity",
        ),
        (
            "```yaml\nkey: value\n```\n",
            "",
            "fence_parity",
        ),
        (
            "{% list tabs %}\n\n- Example\n\n  Body\n\n{% endlist %}\n",
            "Body\n",
            "list_tab_parity",
        ),
    ],
)
def test_F046_missing_parts(source: str, target: str, expected: str) -> None:
    if expected == "alignment":
        _, error = gate_round_trip(extract_segments(parse_markdown(source)), target)
        assert error is not None
    elif expected == "md_link_parity":
        errors = check_md_link_parity(
            source,
            target,
            source_lang="ru",
            target_lang="en",
            source_file="ydb/docs/ru/core/page.md",
        )
        assert any(expected in error for error in errors)
    elif expected == "include_parity":
        errors = check_include_parity(
            source,
            target,
            source_file="ydb/docs/ru/core/page.md",
        )
        assert any(expected in error for error in errors)
    else:
        errors = check_fence_parity(source, target) if expected == "fence_parity" else check_list_tab_parity(source, target)
        assert any(expected in error for error in errors)
