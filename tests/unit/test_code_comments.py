# ruff: noqa: RUF001

from __future__ import annotations

import pytest

from ydbdoc_review.structure import assemble_document, plan_document
from ydbdoc_review.validation.code_comments import (
    CommentSpan,
    approved_code_skeleton,
    approved_comment_spans,
    replace_comments,
    scan_approved_comments,
)


@pytest.mark.parametrize(
    ("language", "source_comment", "translated_comment"),
    (
        ("python", "# Русский", "# English"),
        ("bash", "# Русский", "# English"),
        ("yaml", "# Русский", "# English"),
        ("cpp", "// Русский", "// English"),
        ("java", "/* Русский */", "/* English */"),
        ("javascript", "// Русский", "// English"),
    ),
)
def test_approved_fenced_comment_is_a_translatable_child(
    language: str, source_comment: str, translated_comment: str
) -> None:
    source = f"```{language}\n{source_comment}\nvalue = 1\n```\n"
    candidate = source.replace(source_comment, translated_comment)

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == source.replace(source_comment, translated_comment)


def test_code_structure_change_with_comment_retains_entire_block() -> None:
    source = "```python\n# Русский\nvalue = 1\n```\n"
    candidate = "```python\n# English\nvalue += 2\n```\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source
    assert result.red is True


def test_yaml_configuration_and_literals_remain_immutable() -> None:
    source = "```yaml\n# Русский\npath: /ru/docs\n```\n"
    candidate = "```yaml\n# English\npath: /en/docs\n```\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == "```yaml\n# English\npath: /ru/docs\n```\n"
    assert result.red is False


def test_comment_like_text_inside_literals_is_immutable() -> None:
    source = '```python\nvalue = "# Не комментарий"\nprint(value)\n```\n'
    candidate = '```python\nvalue = "# Not a comment"\nprint(value)\n```\n'

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source


def test_unsupported_language_keeps_entire_code_block() -> None:
    source = "```sql\n-- Русский\nSELECT 1\n```\n"
    candidate = "```sql\n-- English\nSELECT 2\n```\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source


def test_block_comment_body_is_translatable_but_code_is_immutable() -> None:
    source = "```cpp\n/* Русский\n * комментарий */\nint value = 1;\n```\n"
    candidate = "```cpp\n/* English\n * comment */\nint value = 1;\n```\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == "```cpp\n/* English\n * comment */\nint value = 1;\n```\n"


def test_malformed_block_comment_retains_source_code() -> None:
    source = "```cpp\n/* Русский\nint value = 1;\n```\n"
    candidate = "```cpp\n/* English\nint value = 2;\n```\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source
    assert result.red is True


@pytest.mark.parametrize(
    ("language", "body"),
    (
        ("cpp", '// Русский\n/* unclosed\nint value = 1\n'),
        ("java", '// Русский\n/* unclosed\nint value = 1\n'),
        ("javascript", '// Русский\n/* unclosed\nconst value = 1\n'),
        ("cpp", '// Русский\n*/\nint value = 1\n'),
        ("java", '// Русский\n*/\nint value = 1\n'),
        ("javascript", '// Русский\n*/\nconst value = 1\n'),
        ("python", 'value = "unterminated\n# Русский\n'),
        ("yaml", 'value: "unterminated\n# Русский\n'),
        ("javascript", 'const value = "unterminated\n// Русский\n'),
    ),
)
def test_malformed_supported_code_is_unsafe_and_restored(
    language: str, body: str
) -> None:
    source = f"```{language}\n{body}```\n"
    translated_body = body.replace("Русский", "English").replace("1", "2")
    candidate = f"```{language}\n{translated_body}```\n"

    scan = scan_approved_comments(body, language)
    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert scan.supported is True
    assert scan.safe is False
    assert result.text == source
    assert result.red is True
    assert result.diagnostics


@pytest.mark.parametrize("operator", (">", "+"))
def test_operator_spelling_change_is_unsafe_even_with_same_token_kind(operator: str) -> None:
    source = "```python\n# Русский\nvalue = 1\n```\n"
    candidate = f"```python\n# English\nvalue {operator} 1\n```\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source
    assert result.red is True
    assert result.diagnostics
    assert approved_code_skeleton("value = 1\n", "python") != approved_code_skeleton(
        f"value {operator} 1\n", "python"
    )


def test_approved_spans_are_comment_bodies_and_adjacent_comments_stay_distinct() -> None:
    code = "// first\n// second\n/* third */ /* fourth */\n"

    assert approved_comment_spans(code, "cpp") == [
        CommentSpan(3, 8),
        CommentSpan(12, 18),
        CommentSpan(22, 27),
        CommentSpan(34, 40),
    ]


def test_multiline_span_excludes_comment_delimiters_and_formatting() -> None:
    code = "/*\n * first\n * second\n */\n"

    spans = approved_comment_spans(code, "cpp")

    assert [code[span.start : span.end] for span in spans] == ["first", "second"]


def test_comment_replacement_cannot_change_delimiters() -> None:
    code = "// first\n/* second */\n"
    spans = approved_comment_spans(code, "cpp")

    assert [code[span.start : span.end] for span in spans] == ["first", "second"]
    with pytest.raises(ValueError, match="boundaries"):
        replace_comments(code, "cpp", {(spans[0].start, spans[0].end): "*/"})

    fenced = f"```cpp\n{code}```\n"
    delimiter_changed = "```cpp\n// first\n// second\n```\n"
    result = assemble_document(
        plan_document(fenced, path="docs/example.md"), delimiter_changed
    )
    assert result.text == fenced
    assert result.red is True
