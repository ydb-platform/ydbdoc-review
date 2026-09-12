"""F-047 contracts for preserving executable syntax and translating comments."""

from __future__ import annotations

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.validation.cli_tokens import cli_tokens_preserved
from ydbdoc_review.validation.fence_comments import (
    check_cyrillic_in_en_fence_comments,
    trailing_comment_code_prefix,
    translate_cyrillic_fence_comments,
)
from ydbdoc_review.validation.fence_integrity import fence_content_matches_source


def test_F047_technical_tokens() -> None:
    source = (
        "Run `ydb --endpoint $YDB_ENDPOINT --database /local` with "
        "{{ ydb-short-name }}.\n\n"
        "{% if feature_x %}\n\n"
        "```yaml\n"
        "endpoint: grpcs://localhost:2135\n"
        "database: /local\n"
        "api_key: $YDB_API_KEY\n"
        "```\n\n"
        "{% endif %}\n"
    )
    translated = (
        "Run `ydb --endpoint $YDB_ENDPOINT --database /local` with "
        "{{ ydb-short-name }}.\n\n"
        "{% if feature_x %}\n\n"
        "```yaml\n"
        "endpoint: grpcs://localhost:2135\n"
        "database: /local\n"
        "api_key: $YDB_API_KEY\n"
        "```\n\n"
        "{% endif %}\n"
    )

    assert cli_tokens_preserved(source, translated)
    assert "{{ ydb-short-name }}" in render_markdown(parse_markdown(source))
    assert fence_content_matches_source(
        "endpoint: grpcs://localhost:2135\ndatabase: /local\napi_key: $YDB_API_KEY\n",
        "endpoint: grpcs://localhost:2135\ndatabase: /local\napi_key: $YDB_API_KEY\n",
        fence_info="yaml",
    )


def test_F047_comments() -> None:
    source = (
        "```go\n"
        "// обычный комментарий\n"
        "panic(err) // аварийный выход\n"
        "value := 42 // неоднозначный комментарий\n"
        "```\n"
    )

    def translate(body: str) -> str:
        return {
            "обычный комментарий": "ordinary comment",
            "аварийный выход": "abort on error",
        }.get(body, body)

    translated = translate_cyrillic_fence_comments(source, translate)

    assert "// ordinary comment" in translated
    assert "panic(err) // abort on error" in translated
    assert "value := 42 // неоднозначный комментарий" in translated
    assert trailing_comment_code_prefix("panic(err) // abort on error") == "panic(err)"
    warnings = check_cyrillic_in_en_fence_comments(translated, target_lang="en")
    assert warnings
    assert warnings[0].startswith("cyrillic_in_fence:")
