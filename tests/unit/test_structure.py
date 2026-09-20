# ruff: noqa: RUF001

from __future__ import annotations

from ydbdoc_review.structure import assemble_document, plan_document


def test_identity_assembly_preserves_source_bytes_and_newlines() -> None:
    source = "# Заголовок\r\n\r\nТекст.\r\n"

    plan = plan_document(source, path="docs/example.md")
    result = assemble_document(plan, source)

    assert plan.path == "docs/example.md"
    assert result.text == source
    assert result.diagnostics == ()
    assert result.red is False


def test_candidate_text_is_used_but_technical_parts_are_source_owned() -> None:
    source = (
        "---\n"
        "title: Исходный\n"
        "config: /ru/docs\n"
        "---\n\n"
        "# Заголовок {#stable-id}\n\n"
        "Абзац [ссылка](https://example.test/ru) и ![альт](images/a.png).\n"
        "\n"
        "```python\n"
        "print('source')\n"
        "```\n"
    )
    candidate = (
        "---\n"
        "title: Перевод\n"
        "config: /en/docs\n"
        "---\n\n"
        "# Heading {#changed-id}\n\n"
        "Paragraph [link](https://example.test/en) and ![alt](images/b.png).\n"
        "\n"
        "```python\n"
        "print('candidate')\n"
        "```\n"
    )

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == (
        "---\n"
        "title: Исходный\n"
        "config: /ru/docs\n"
        "---\n\n"
        "# Heading {#stable-id}\n\n"
        "Paragraph [link](https://example.test/ru) and ![alt](images/a.png).\n"
        "\n"
        "```python\n"
        "print('source')\n"
        "```\n"
    )


def test_lists_tables_and_images_are_editable_but_table_syntax_is_not() -> None:
    source = (
        "- Первый пункт\n"
        "- Второй пункт\n\n"
        "| Заголовок | Значение |\n"
        "| --- | --- |\n"
        "| один | два |\n"
    )
    candidate = (
        "- First item\n"
        "- Second item\n\n"
        "| Header | Value |\n"
        "| --- | --- |\n"
        "| one | two |\n"
    )

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == candidate

    broken_candidate = candidate.replace("| --- | --- |", "| === | === |")
    broken = assemble_document(plan_document(source, path="docs/example.md"), broken_candidate)
    assert broken.text == source
    assert broken.red is True
    assert any("structure" in diagnostic.lower() for diagnostic in broken.diagnostics)


def test_link_labels_and_image_alt_are_editable_but_url_and_path_are_source_owned() -> None:
    source = (
        "See [the guide](../guide.md) at https://example.test/ru and "
        "![diagram](images/ru.png).\n"
    )
    candidate = (
        "Read [the handbook](../other.md) at https://example.test/en and "
        "![schema](images/en.png).\n"
    )

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == (
        "Read [the handbook](../guide.md) at https://example.test/ru and "
        "![schema](images/ru.png).\n"
    )


def test_code_include_yaml_and_mermaid_are_source_owned() -> None:
    source = (
        "{% include [подсказка](../_includes/tip.md) %}\n\n"
        "```mermaid\n"
        "graph TD\n"
        "  A-->B\n"
        "```\n\n"
        "```yaml\n"
        "path: /ru/docs\n"
        "```\n"
    )
    candidate = (
        "{% include [hint](../_includes/other.md) %}\n\n"
        "```mermaid\n"
        "graph LR\n"
        "  X-->Y\n"
        "```\n\n"
        "```yaml\n"
        "path: /en/docs\n"
        "```\n"
    )

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == source


def test_yfm_directive_is_source_owned_but_note_body_is_editable() -> None:
    source = '{% note info "Исходная заметка" %}\nТекст заметки.\n{% endnote %}\n'
    candidate = '{% note warning "Translated note" %}\nNote text.\n{% endnote %}\n'

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == '{% note info "Исходная заметка" %}\nNote text.\n{% endnote %}\n'


def test_inline_code_identifiers_templates_urls_and_paths_are_preserved() -> None:
    source = (
        "Use `SELECT` with `--profile=ru` at /docs/ru and "
        "{{ config.path }} or https://example.test/ru.\n"
    )
    candidate = (
        "Run `DROP` with `--profile=en` at /docs/en and "
        "{{ config.other }} or https://example.test/en.\n"
    )

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == (
        "Run `SELECT` with `--profile=ru` at /docs/ru and "
        "{{ config.path }} or https://example.test/ru.\n"
    )


def test_structural_candidate_mismatch_retains_coherent_source_chunk() -> None:
    source = "# One\n\nText.\n\nSecond paragraph.\n"
    candidate = "# One\n\nInserted paragraph.\n\nText.\n\nSecond paragraph.\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source
    assert result.red is True
    assert any("structure" in diagnostic.lower() for diagnostic in result.diagnostics)


def test_unsafe_link_field_retains_source_and_reports_diagnostic() -> None:
    source = "See [the guide](guide.md) for details.\n"
    candidate = "See [the guide [broken](other.md) for details.\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source
    assert result.red is True
    assert result.diagnostics


def test_unknown_construct_is_retained_with_a_local_diagnostic() -> None:
    source = "{% custom-directive value %}\nText.\n"
    candidate = source

    plan = plan_document(source, path="docs/example.md")
    result = assemble_document(plan, candidate)

    assert result.text == source
    assert result.red is False
    assert any("unknown" in diagnostic.lower() for diagnostic in result.diagnostics)


def test_nested_fenced_code_inside_list_is_source_owned_and_red_on_change() -> None:
    source = "- Перед кодом\n  ```sql\n  SELECT 1\n  ```\n- После кода\n"
    candidate = "- Before code\n  ```sql\n  DROP 1\n  ```\n- After code\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source
    assert result.red is True
    assert result.diagnostics


def test_inline_code_inside_link_label_is_source_owned() -> None:
    source = "См. [используйте `SELECT`](guide.md) для чтения.\n"
    candidate = "See [use `DROP`](other.md) for reading.\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source
    assert result.red is True
    assert result.diagnostics


def test_yaml_configuration_path_is_opaque_and_red_on_changed_href() -> None:
    source = "title: Документация\nhref: docs/source.md\nitems:\n  - source.md\n"
    candidate = "title: Documentation\nhref: docs/other.md\nitems:\n  - other.md\n"

    result = assemble_document(plan_document(source, path="docs/toc.yaml"), candidate)

    assert result.text == source
    assert result.red is True
    assert result.diagnostics


def test_emphasis_markers_are_source_syntax_but_inner_text_is_editable() -> None:
    source = "Это *важно* и **критично**.\n"
    candidate = "This is *important* and **critical**.\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == candidate


def test_setext_underline_is_structural_and_heading_level_cannot_change() -> None:
    source = "Заголовок\n=======\n\nТекст.\n"
    candidate = "Heading\n--------\n\nText.\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.text == source
    assert result.red is True
    assert result.diagnostics


def test_bare_relative_path_in_prose_is_source_owned() -> None:
    source = "Откройте файл docs/source.md для примера.\n"
    candidate = "Open file docs/translated.md for an example.\n"

    result = assemble_document(plan_document(source, path="docs/example.md"), candidate)

    assert result.red is False
    assert result.text == "Open file docs/source.md for an example.\n"
