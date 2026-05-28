"""Chunking must not split inside fenced blocks."""

from ydbdoc_review.masked_chunking import chunk_masked_text, find_chunk_end


def test_chunk_keeps_fence_block_intact_under_limit():
    fence = "```bash\necho hello world\nsecond line\n```\n\n"
    text = "Before.\n\n" + fence + "After.\n"
    chunks = chunk_masked_text(text, max_chars=40)
    assert len(chunks) >= 2
    assert any("echo hello" in ch and "```" in ch for ch in chunks)
    assert not any(ch.strip() == "echo hello world" for ch in chunks)


def test_find_chunk_end_extends_past_open_fence():
    text = "pre\n```bash\nline1\nline2\nline3\n```\npost\n"
    # Cut lands inside fence body
    end = find_chunk_end(text, 0, 18)
    assert text[:end].count("```") % 2 == 0 or end >= text.index("```", 10)
