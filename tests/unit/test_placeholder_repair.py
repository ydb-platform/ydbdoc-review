"""Tests for post-translation placeholder repair."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.validation.markers import placeholders_match
from ydbdoc_review.validation.placeholder_repair import (
    repair_translation_placeholders,
    _is_url_placeholder_template,
)


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


def test_repair_legacy_whole_link_marker():
    seg = _segment_s0003()
    translated = (
        "⟦V1⟧ CLI can execute parameterized queries. "
        "To use parameters, you need to declare them using ⟦L1⟧ in your query text."
    )
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)


def test_repair_missing_leading_variable():
    seg = _segment_s0003()
    translated = (
        "YDB CLI can execute parameterized queries. "
        "To use parameters, declare them using [the YQL DECLARE command](⟦U1⟧)."
    )
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)


def test_repair_wrong_marker_order():
    seg = _segment_s0003()
    translated = (
        "⟦C1⟧ ⟦V1⟧ CLI can execute parameterized queries. "
        "To use parameters, declare them using [the YQL ⟦C2⟧ command](⟦U3⟧)."
    )
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)


def _segment_s0010():
    path = (
        Path(__file__).parent.parent
        / "fixtures/markdown_files/ru/core/reference/ydb-cli/parameterized-query-execution.md"
    )
    return next(
        s
        for s in extract_segments(parse_markdown(path.read_text(encoding="utf-8")))
        if s.id == "s0010"
    )


def test_repair_s0010_literals_with_link_variable():
    seg = _segment_s0010()
    translated = (
        "This command supports passing parameters via command-line options, a file, "
        "or through `stdin`. When passing parameters via `stdin` or a file, "
        "multiple streaming executions are supported. For this, the following "
        "parameters are provided in the [`{{ ydb-cli }}` sql](sql.md) command:"
    )
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)
    assert fixed.count("⟦V1⟧") == 1


def test_repair_swapped_variable_and_url_vscode_s0077():
    url = (
        "https://raw.githubusercontent.com/ydb-platform/ydb/main/"
        "ydb/docs/ru/core/integrations/gui/vscode-plugin.md"
    )
    text = urllib.request.urlopen(url, timeout=30).read().decode()
    seg = next(
        s for s in extract_segments(parse_markdown(text)) if s.id == "s0077"
    )
    broken = (
        "Authentication by login and password. Specify the username in the "
        "**Username** field and the password in the **Password** field. "
        "Used if [login and password authentication](⟦V1⟧) is enabled on the "
        "[](../../security/authentication.md#static-credentials) server."
    )
    fixed = repair_translation_placeholders(seg, broken)
    assert placeholders_match(seg.text, fixed)
    assert "[login and password authentication](⟦U1⟧)" in fixed
    assert "on the ⟦V1⟧ server" in fixed


def test_repair_s0124_all_literals():
    path = (
        Path(__file__).parent.parent
        / "fixtures/markdown_files/ru/core/reference/ydb-cli/parameterized-query-execution.md"
    )
    seg = next(
        s
        for s in extract_segments(parse_markdown(path.read_text(encoding="utf-8")))
        if s.id == "s0124"
    )
    from ydbdoc_review.rendering.markdown_renderer import _render_inline_node

    translated = seg.text
    for protected in seg.placeholders:
        node = protected.node
        if _is_url_placeholder_template(node):
            translated = translated.replace(protected.placeholder, node.href, 1)
        else:
            translated = translated.replace(
                protected.placeholder, _render_inline_node(node), 1
            )
    fixed = repair_translation_placeholders(seg, translated)
    assert placeholders_match(seg.text, fixed)
