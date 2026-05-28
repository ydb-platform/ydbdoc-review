"""Tests for boundary-safe masked chunk splitting."""

from ydbdoc_review.document_mask import MaskRegistry, mask_translatable_text
from ydbdoc_review.masked_chunking import chunk_masked_text, find_chunk_end


def _assert_chunks_rejoin(text: str, chunks: list[str]) -> None:
    assert "".join(chunks) == text


def _assert_no_split_placeholders(chunks: list[str]) -> None:
    for ch in chunks:
        assert ch.count("⟦") == ch.count("⟧")
        assert "⟦" not in ch or "⟧" in ch


def _assert_table_rows_intact(chunks: list[str]) -> None:
    """No chunk ends or starts mid-table-row (between two ``|`` on same line)."""
    for ch in chunks:
        for line in ch.splitlines():
            if not line.strip().startswith("|"):
                continue
            assert line.count("|") >= 2, f"broken table row: {line[:80]!r}"


def _is_safe_cut(text: str, cut: int) -> bool:
    if cut <= 0 or cut >= len(text):
        return True
    if text[cut : cut + 1] == "|":
        return True
    if text.startswith(("#", "{%", "```", "- "), cut):
        return True
    if cut >= 2 and text[cut - 2 : cut] == "\n\n":
        return True
    if cut >= 1 and text[cut - 1] == "\n":
        return True
    return cut >= 1 and text[cut - 1] == " "


def _assert_cuts_on_safe_boundaries(text: str, chunks: list[str]) -> None:
    pos = 0
    for ch in chunks[:-1]:
        pos += len(ch)
        assert _is_safe_cut(text, pos), f"unsafe cut at {pos}: {text[max(0,pos-5):pos+5]!r}"


def test_chunk_preserves_table_rows():
    header = "| Name | Description |\n| --- | --- |\n"
    rows = "".join(f"| `--opt-{i}` | {'x' * 80} |\n" for i in range(40))
    text = "Intro prose.\n\n" + header + rows + "\nOutro.\n"
    chunks = chunk_masked_text(text, max_chars=500)
    assert len(chunks) > 1
    _assert_chunks_rejoin(text, chunks)
    _assert_table_rows_intact(chunks)
    for ch in chunks:
        if "| --- |" in ch:
            assert ch.strip().startswith("|") or ch.lstrip().startswith("Intro")


def test_chunk_preserves_paragraphs_and_headings():
    parts = [f"Paragraph {i}.\n\n" + "word " * 200 for i in range(8)]
    parts.append("## Section {#s}\n\n")
    parts.append("| A | B |\n| - | - |\n| 1 | 2 |\n")
    text = "".join(parts)
    chunks = chunk_masked_text(text, max_chars=600)
    assert len(chunks) > 1
    _assert_chunks_rejoin(text, chunks)
    _assert_cuts_on_safe_boundaries(text, chunks)
    _assert_table_rows_intact(chunks)


def test_chunk_preserves_diplodoc_and_list_tabs_labels():
    text = (
        "Before.\n\n"
        "{% note info %}\n\n"
        "Note body.\n\n"
        "{% endnote %}\n\n"
        "{% list tabs %}\n\n"
        "- JSON\n\n"
        "Step one.\n\n"
        "- CSV\n\n"
        "Step two.\n\n"
        "{% endlist %}\n\n"
        "After.\n"
    )
    chunks = chunk_masked_text(text, max_chars=80)
    assert len(chunks) > 1
    _assert_chunks_rejoin(text, chunks)
    _assert_cuts_on_safe_boundaries(text, chunks)


def test_chunk_does_not_split_placeholder():
    reg = MaskRegistry()
    body = "Text " + mask_translatable_text("[узел](a.md)", reg) + " tail.\n"
    text = (body + "\n") * 30
    chunks = chunk_masked_text(text, max_chars=120)
    assert len(chunks) > 1
    _assert_chunks_rejoin(text, chunks)
    _assert_no_split_placeholders(chunks)


def test_find_chunk_end_prefers_table_boundary_over_mid_row():
    row = "| " + "a" * 200 + " | " + "b" * 200 + " |\n"
    text = "| H1 | H2 |\n| -- | -- |\n" + row * 3
    end = find_chunk_end(text, 0, 250)
    cut = text[:end]
    assert not cut.endswith("| a")  # not mid-cell
    assert cut.endswith("\n") or end == len(text)


def test_single_oversized_row_stays_one_piece_if_under_hard_limit():
    """One very long row may exceed budget but must not split inside ``| … |``."""
    row = "| " + "z" * 400 + " | " + "w" * 400 + " |\n"
    text = "| H | D |\n| - | - |\n" + row
    chunks = chunk_masked_text(text, max_chars=300)
    _assert_chunks_rejoin(text, chunks)
    _assert_table_rows_intact(chunks)
    assert any(row.strip() in ch for ch in chunks)
