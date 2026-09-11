"""F-068: excluded material remains unchanged and outside canonical scope."""

# ruff: noqa: RUF001

from __future__ import annotations

from ydbdoc_review.pipeline.skip_paths import (
    filter_translate_changes,
    matches_translate_skip,
    toc_entry_is_skipped,
)
from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown


def test_F068_excluded_usage() -> None:
    excluded = "## Старый раздел {#старый-раздел}\n\nСодержимое не меняется.\n"
    assert fragment_declared_in_markdown(excluded, "старый-раздел")
    assert matches_translate_skip(
        "ydb/docs/ru/core/public-materials/legacy.md",
        ["public-materials/**"],
    )
    assert toc_entry_is_skipped(
        {"href": "public-materials/legacy.md", "include_path": ""},
        ["public-materials/**"],
    )


def test_F068_honest_report() -> None:
    changes = [
        ("ydb/docs/ru/core/public-materials/legacy.md", "modified"),
        ("ydb/docs/ru/core/allowed.md", "modified"),
    ]

    filtered = filter_translate_changes(changes, ["public-materials/**"])

    assert filtered == [("ydb/docs/ru/core/allowed.md", "modified")]
    assert "public-materials" not in {path for path, _ in filtered}
