"""F-045 contracts for extracting every human-readable prose surface."""

# ruff: noqa: RUF001

from __future__ import annotations

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.types import SegmentKind


def _prose_fixture() -> str:
    return (
        "---\n"
        "title: Заголовок страницы\n"
        "description: Описание страницы\n"
        "vcsPath: docs/ru/page.md\n"
        "---\n\n"
        "# Основной заголовок\n\n"
        "Абзац с [подписью ссылки](target.md) и ![подписью изображения](image.png).\n\n"
        "- Элемент списка\n\n"
        "> Текст цитаты\n\n"
        "| Заголовок | Значение |\n"
        "| --- | --- |\n"
        "| Пояснение | 42 |\n\n"
        '{% note tip "Подсказка" %}\n\n'
        "Текст заметки.\n\n"
        "{% endnote %}\n\n"
        '{% cut "Подробности" %}\n\n'
        "Текст раскрытия.\n\n"
        "{% endcut %}\n\n"
        "{% list tabs %}\n\n"
        "- Вручную\n\n"
        "  Текст вкладки.\n\n"
        "{% endlist %}\n"
    )


def test_F045_prose_kinds() -> None:
    segments = extract_segments(parse_markdown(_prose_fixture()))
    kinds = {segment.kind for segment in segments}

    assert {
        SegmentKind.FRONT_MATTER,
        SegmentKind.HEADING,
        SegmentKind.PARAGRAPH,
        SegmentKind.TABLE_HEADER_CELL,
        SegmentKind.TABLE_BODY_CELL,
        SegmentKind.NOTE_TITLE,
        SegmentKind.CUT_TITLE,
        SegmentKind.TAB_TITLE,
    } <= kinds
    assert any(segment.path == ["list_item"] for segment in segments)
    assert any(segment.path == ["blockquote"] for segment in segments)
    assert any("подписью ссылки" in segment.text for segment in segments)
    assert any("подписью изображения" in segment.text for segment in segments)
    front_matter = [
        segment.text
        for segment in segments
        if segment.kind == SegmentKind.FRONT_MATTER
    ]
    assert front_matter == ["Заголовок страницы", "Описание страницы"]


def test_F045_meaning() -> None:
    source = (
        "Не удаляйте файл, если значение меньше 42, "
        "даже при ошибке.\n"
    )
    doc = parse_markdown(source)
    segments = extract_segments(doc)
    prose = next(segment for segment in segments if segment.kind == SegmentKind.PARAGRAPH)
    translated = {
        prose.id: "Do not delete the file if the value is less than 42, even on error."
    }

    output = render_markdown(reinsert_segments(doc, segments, translated))

    assert output == translated[prose.id] + "\n"
    assert "Do not" in output
    assert "less than 42" in output
    assert "even on error" in output
