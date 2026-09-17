"""YFM note/cut/tab titles translate; markers stay protected (§5 / §9 / §14)."""

from __future__ import annotations

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.types import SegmentKind


def test_note_and_cut_titles_are_extracted_and_reinserted():
    text = (
        '{% note warning "Осторожно" %}\n'
        "\n"
        "Тело заметки.\n"
        "\n"
        "{% endnote %}\n"
        "\n"
        '{% cut "Подробности" %}\n'
        "\n"
        "Внутри.\n"
        "\n"
        "{% endcut %}\n"
    )
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    by_kind = {s.kind: s for s in segments}
    assert SegmentKind.NOTE_TITLE in by_kind
    assert SegmentKind.CUT_TITLE in by_kind
    assert by_kind[SegmentKind.NOTE_TITLE].text == "Осторожно"
    assert by_kind[SegmentKind.CUT_TITLE].text == "Подробности"

    # Translate only titles; leave body segments in RU to prove markers survive.
    translations = {
        by_kind[SegmentKind.NOTE_TITLE].id: "Be careful",
        by_kind[SegmentKind.CUT_TITLE].id: "Details",
    }
    out = render_markdown(reinsert_segments(doc, segments, translations))
    assert '{% note warning "Be careful" %}' in out
    assert "{% endnote %}" in out
    assert '{% cut "Details" %}' in out
    assert "{% endcut %}" in out
    assert "Тело заметки." in out
    assert "Внутри." in out
    # Markers / boundaries unchanged aside from title prose.
    assert out.count("{% note warning") == 1
    assert out.count("{% endnote %}") == 1
    assert out.count("{% cut ") == 1
    assert out.count("{% endcut %}") == 1


def test_note_without_title_emits_no_note_title_segment():
    text = "{% note info %}\n\nHello.\n\n{% endnote %}\n"
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    assert all(s.kind != SegmentKind.NOTE_TITLE for s in segments)
    assert any(s.kind == SegmentKind.PARAGRAPH for s in segments)


def test_tab_title_translates_while_list_markers_stay():
    text = (
        "{% list tabs %}\n"
        "\n"
        "- Из консоли\n"
        "\n"
        "  Текст.\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    title = next(s for s in segments if s.kind == SegmentKind.TAB_TITLE)
    assert title.text == "Из консоли"
    out = render_markdown(
        reinsert_segments(doc, segments, {title.id: "From console"})
    )
    assert "{% list tabs %}" in out
    assert "{% endlist %}" in out
    assert "- From console" in out
    assert "Текст." in out


def test_whitelisted_sdk_tab_title_is_not_a_segment():
    text = (
        "{% list tabs %}\n"
        "\n"
        "- Python\n"
        "\n"
        "  Code.\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    assert all(s.kind != SegmentKind.TAB_TITLE for s in segments)


def test_combined_fm_and_yfm_title_matrix_round_trip():
    text = (
        "---\n"
        "title: 'Русский заголовок'\n"
        "# keep\n"
        "vcsPath: docs/ru/a.md\n"
        "description: |\n"
        "  Описание\n"
        "  страницы\n"
        "---\n"
        "\n"
        '{% note tip "Подсказка" %}\n'
        "\n"
        "Проза.\n"
        "\n"
        "{% endnote %}\n"
        "\n"
        '{% cut "Развернуть" %}\n'
        "\n"
        "Скрыто.\n"
        "\n"
        "{% endcut %}\n"
        "\n"
        "{% list tabs %}\n"
        "\n"
        "- Вручную\n"
        "\n"
        "  Шаг.\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    kinds = {s.kind for s in segments}
    assert SegmentKind.FRONT_MATTER in kinds
    assert SegmentKind.NOTE_TITLE in kinds
    assert SegmentKind.CUT_TITLE in kinds
    assert SegmentKind.TAB_TITLE in kinds

    translations = {}
    for seg in segments:
        if seg.kind == SegmentKind.FRONT_MATTER and "title" in seg.path[0]:
            translations[seg.id] = "English title"
        elif seg.kind == SegmentKind.FRONT_MATTER and "description" in seg.path[0]:
            translations[seg.id] = "Page description\n"
        elif seg.kind == SegmentKind.NOTE_TITLE:
            translations[seg.id] = "Hint"
        elif seg.kind == SegmentKind.CUT_TITLE:
            translations[seg.id] = "Expand"
        elif seg.kind == SegmentKind.TAB_TITLE:
            translations[seg.id] = "Manually"
        elif seg.kind == SegmentKind.PARAGRAPH:
            translations[seg.id] = {
                "Проза.": "Prose.",
                "Скрыто.": "Hidden.",
                "Шаг.": "Step.",
            }[seg.text]

    out = render_markdown(reinsert_segments(doc, segments, translations))
    assert "title: 'English title'" in out
    assert "# keep" in out
    assert "vcsPath: docs/ru/a.md" in out
    assert "description: |" in out
    assert "Page description" in out
    assert '{% note tip "Hint" %}' in out
    assert "{% endnote %}" in out
    assert '{% cut "Expand" %}' in out
    assert "{% endcut %}" in out
    assert "{% list tabs %}" in out
    assert "- Manually" in out
    assert "{% endlist %}" in out
    assert "Prose." in out
