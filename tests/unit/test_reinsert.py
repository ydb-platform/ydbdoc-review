"""Tests for re-inserting translations back into AST."""

from __future__ import annotations

from pathlib import Path

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments


def identity_pipeline(text: str) -> str:
    """parse → extract → reinsert with no changes → render."""
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    # Identity: each segment's translation is its own text.
    translations = {s.id: s.text for s in segments}
    new_doc = reinsert_segments(doc, segments, translations)
    return render_markdown(new_doc)


def assert_identity(text: str) -> None:
    """The identity pipeline must produce stable, equivalent markdown."""
    direct = render_markdown(parse_markdown(text))
    via_segments = identity_pipeline(text)
    assert direct == via_segments, (
        f"\nDirect render:\n{direct!r}\n"
        f"Via segments:\n{via_segments!r}"
    )


# --- Basic cases ---


def test_identity_simple_paragraph():
    assert_identity("Hello world.\n")


def test_identity_with_inline_code():
    assert_identity("Use `--yaml` flag.\n")


def test_identity_with_link():
    assert_identity("See [docs](http://x).\n")


def test_identity_with_variable():
    assert_identity("Use {{ ydb-short-name }} CLI.\n")


def test_identity_heading():
    assert_identity("# Title with `code`\n\nText.\n")


def test_identity_heading_with_anchor():
    assert_identity("## Section {#section-id}\n\nText.\n")


def test_identity_list():
    assert_identity("- one with `code`\n- two with [link](http://x)\n")


def test_identity_ordered_list():
    assert_identity("1. first\n2. second\n3. third\n")


def test_identity_table():
    assert_identity(
        "| Flag | Description |\n"
        "| --- | --- |\n"
        "| `--yaml` | YAML output |\n"
        "| `--json` | JSON output |\n"
    )


def test_identity_blockquote():
    assert_identity("> A quoted line with `code`.\n")


def test_identity_note():
    assert_identity(
        "{% note warning %}\n\n"
        "Be careful with `--force`.\n\n"
        "{% endnote %}\n"
    )


def test_identity_tabs():
    assert_identity(
        "{% list tabs %}\n\n"
        "- Python\n\n"
        "  Use the {{ py-sdk }} library.\n\n"
        "- Go\n\n"
        "  Use the Go SDK.\n\n"
        "{% endlist %}\n"
    )


def test_identity_cut():
    assert_identity(
        '{% cut "Show example" %}\n\n'
        "Hidden text with `code`.\n\n"
        "{% endcut %}\n"
    )


def test_identity_if():
    assert_identity(
        "{% if oss %}\n\n"
        "OSS text with {{ var }}.\n\n"
        "{% endif %}\n"
    )


def test_identity_include():
    """Include is not translated, but must round-trip via segmentation."""
    assert_identity("{% include [text](path.md) %}\n")


def test_identity_code_block():
    """Code blocks are not segmented; must pass through unchanged."""
    text = "Intro.\n\n```bash\necho hi\n```\n\nOutro.\n"
    assert_identity(text)


def test_identity_term_definition_and_ref():
    assert_identity(
        "A YDB [*cluster] is a system.\n\n"
        "[*cluster]: A set of nodes.\n"
    )


def test_identity_image_with_size():
    assert_identity("![alt](image.png =100x200)\n")


def test_identity_complex_mixed():
    text = (
        "# Title {#title}\n\n"
        "Intro with `code` and [link](http://x).\n\n"
        "## Section\n\n"
        "- item with {{ var }}\n"
        "- item with **bold**\n\n"
        "{% note tip %}\n\n"
        "Use `--yaml` for [output]({{ url-var }}).\n\n"
        "{% endnote %}\n\n"
        "```python\n"
        "print('hello')\n"
        "```\n\n"
        "Final.\n"
    )
    assert_identity(text)


# --- Real-world identity ---


def test_identity_on_real_fixtures():
    """All fixture files must round-trip through extract+reinsert identically."""
    fixtures = Path(__file__).parent.parent / "fixtures" / "markdown_files"
    files = list(fixtures.rglob("*.md"))
    assert files, "no fixtures found"

    failures: list[tuple[Path, str]] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        try:
            direct = render_markdown(parse_markdown(text))
            via = identity_pipeline(text)
            if direct != via:
                from difflib import unified_diff
                diff = "\n".join(
                    list(
                        unified_diff(
                            direct.splitlines(),
                            via.splitlines(),
                            lineterm="",
                            n=2,
                        )
                    )[:40]
                )
                failures.append((f, diff))
        except Exception as e:  # noqa: BLE001
            failures.append((f, f"exception: {e!r}"))

    if failures:
        details = "\n\n".join(
            f"=== {f.relative_to(fixtures)} ===\n{d}" for f, d in failures[:5]
        )
        pytest.fail(
            f"{len(failures)} of {len(files)} fixtures failed identity round-trip:\n{details}"
        )


# --- Translation simulation (not identity) ---


def test_translate_paragraph_simple():
    text = "Hello world.\n"
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    translations = {segments[0].id: "Привет мир."}
    new_doc = reinsert_segments(doc, segments, translations)
    out = render_markdown(new_doc)
    assert "Привет мир." in out
    assert "Hello world." not in out


def test_translate_preserves_placeholders():
    text = "Use `--yaml` flag.\n"
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    seg = segments[0]
    # Simulate translation that keeps the placeholder.
    translated = "Используйте ⟦C1⟧ флаг."
    translations = {seg.id: translated}
    new_doc = reinsert_segments(doc, segments, translations)
    out = render_markdown(new_doc)
    assert "Используйте `--yaml` флаг." in out


def test_translate_with_link_placeholder():
    text = "See [docs](http://x) for details.\n"
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    seg = segments[0]
    translated = "See [documentation](⟦U1⟧) for details."
    translations = {seg.id: translated}
    new_doc = reinsert_segments(doc, segments, translations)
    out = render_markdown(new_doc)
    assert "See [documentation](http://x) for details." in out


def test_translate_with_multiple_placeholders():
    text = "Run `cmd` then see [docs](http://x) with {{ var }}.\n"
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    seg = segments[0]
    translated = "Run ⟦C1⟧ then see [docs](⟦U1⟧) with ⟦V1⟧."
    translations = {seg.id: translated}
    new_doc = reinsert_segments(doc, segments, translations)
    out = render_markdown(new_doc)
    assert "`cmd`" in out
    assert "[docs](http://x)" in out
    assert "{{ var }}" in out


def test_translate_table_cell():
    text = (
        "| Flag | Desc |\n"
        "| --- | --- |\n"
        "| `--yaml` | YAML out |\n"
    )
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    # Find the "YAML out" cell.
    body_segs = [s for s in segments if s.kind.value == "table_body_cell"]
    assert body_segs
    target = next(s for s in body_segs if "YAML" in s.text)
    translations = {target.id: "Вывод YAML"}
    new_doc = reinsert_segments(doc, segments, translations)
    out = render_markdown(new_doc)
    assert "Вывод YAML" in out


def test_translate_heading_keeps_anchor():
    text = "## Section {#sec-id}\n\nText.\n"
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    heading_seg = next(s for s in segments if s.kind.value == "heading")
    translations = {heading_seg.id: "Раздел"}
    new_doc = reinsert_segments(doc, segments, translations)
    out = render_markdown(new_doc)
    assert "## Раздел {#sec-id}\n" in out

