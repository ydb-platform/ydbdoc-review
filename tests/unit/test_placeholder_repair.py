"""Tests for post-translation placeholder repair."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.validation.markers import placeholders_match
from ydbdoc_review.validation.placeholder_repair import repair_translation_placeholders


def _segment_s0003():
    path = (
        Path(__file__).parent.parent
        / "fixtures/markdown_files/ru/core/reference/ydb-cli/parameterized-query-execution.md"
    )
    segs = extract_segments(parse_markdown(path.read_text(encoding="utf-8")))
    return next(s for s in segs if s.id == "s0003")


def test_repair_exposed_yfm_variable_and_link_atoms():
    seg = _segment_s0003()
    # Typical LLM output: literals instead of placeholders.
    translated = (
        "{{ ydb-short-name }} CLI can execute parameterized queries. "
        "To use parameters, declare them using "
        "[the YQL `DECLARE` command](../../yql/reference/syntax/declare.md) "
        "in your query text."
    )
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)
    assert "⟦V1⟧" in fixed
    assert "⟦C1⟧" in fixed
    assert "⟦U1⟧" in fixed
    assert "{{ ydb-short-name }}" not in fixed


def test_repair_absolute_url_in_link():
    seg = _segment_s0003()
    translated = (
        "⟦V1⟧ CLI can execute parameterized queries. "
        "To use parameters, declare them using "
        "[the YQL `DECLARE` command](https://ydb.tech/docs/en/yql/reference/syntax/declare.md) "
        "in your query text."
    )
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)
    assert "⟦U1⟧" in fixed


def test_repair_s0124_duplicate_code_atoms():
    path = (
        Path(__file__).parent.parent
        / "fixtures/markdown_files/ru/core/reference/ydb-cli/parameterized-query-execution.md"
    )
    seg = next(
        s
        for s in extract_segments(parse_markdown(path.read_text(encoding="utf-8")))
        if s.id == "s0124"
    )
    translated = seg.text.replace("⟦C1⟧", "`stdin`", 1).replace("⟦C2⟧", "`--input-file`", 1)
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)


def test_repair_realigns_renumbered_placeholders():
    seg = _segment_s0003()
    translated = (
        "⟦V2⟧ CLI supports parameterized queries. "
        "Parameters must be declared using [the YQL ⟦C3⟧ command](⟦U4⟧)."
    )
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)
