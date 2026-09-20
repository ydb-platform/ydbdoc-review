# ruff: noqa: RUF001

from __future__ import annotations

import pytest

from ydbdoc_review.structure import assemble_document, plan_document


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
