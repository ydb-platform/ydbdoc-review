"""Smoke tests for tabs block translation (fence helpers must be imported)."""

from unittest.mock import MagicMock, patch

from ydbdoc_review.tabs_translate import translate_tabs_block


def test_translate_tabs_block_preserves_fence_without_llm():
    text = (
        "{% list tabs %}\n"
        "- OSS\n"
        "Short intro.\n"
        "```yaml\n"
        "key: value\n"
        "```\n"
        "{% endlist %}\n"
    )
    with patch(
        "ydbdoc_review.tabs_translate._translate_prose_blob",
        side_effect=lambda _settings, *, blob, **_: blob,
    ):
        out = translate_tabs_block(
            MagicMock(),
            text,
            source_path="ydb/docs/ru/core/example.md",
            source_lang="Russian",
            target_lang="English",
            label="example/tabs-1",
        )
    assert "```yaml" in out
    assert "key: value" in out
