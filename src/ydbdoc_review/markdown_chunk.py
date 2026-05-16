"""Split long markdown into translation-sized chunks without breaking code fences."""

from __future__ import annotations


def _is_fence_toggle(line: str) -> bool:
    s = line.strip()
    return s.startswith("```") and len(s) >= 3


def _h2_heading(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("##") and not s.startswith("###")


def split_markdown_for_translate(
    text: str,
    *,
    target_chars: int,
    min_chunk_chars: int = 4000,
) -> list[str]:
    """
    Split ``text`` into chunks each roughly ``target_chars`` (UTF-8 length).

    Prefers boundaries at ``##`` headings (outside fences) or blank-line runs.
    Never splits inside a ``` … ``` fence unless a fence block alone exceeds
    ``target_chars * 3`` (then splits at a line boundary with best effort).
    """
    if target_chars < 2048:
        target_chars = 2048
    if min_chunk_chars < 500:
        min_chunk_chars = 500
    if len(text) <= target_chars:
        return [text]

    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    in_fence = False

    def emit_buf() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        chunks.append("\n".join(buf))
        buf = []
        buf_len = 0

    for line in lines:
        # Avoid unbounded buffers inside a single code fence (may split mid-fence; rare).
        est = buf_len + len(line) + 1
        if in_fence and est > target_chars * 3 and buf:
            emit_buf()

        if _is_fence_toggle(line):
            in_fence = not in_fence

        # Prefer new chunk at H2 when we already have enough material (outside fence).
        if (
            not in_fence
            and buf
            and buf_len >= min_chunk_chars
            and _h2_heading(line)
        ):
            emit_buf()

        buf.append(line)
        buf_len += len(line) + 1

        # Hard flush when outside fence and over target — split at last good break in buffer.
        if not in_fence and buf_len >= target_chars:
            joined = "\n".join(buf)
            cut = _last_good_break(joined, min_keep=min_chunk_chars)
            if 0 < cut < len(joined):
                head, tail = joined[:cut], joined[cut:]
                chunks.append(head.rstrip("\n"))
                tail_lines = tail.lstrip("\n").split("\n") if tail.strip() else []
                buf = tail_lines
                buf_len = sum(len(x) + 1 for x in buf) if buf else 0
            else:
                cut2 = _force_line_split(joined, near=target_chars, min_keep=min_chunk_chars)
                if 0 < cut2 < len(joined):
                    head, tail = joined[:cut2], joined[cut2:]
                    chunks.append(head.rstrip("\n"))
                    tail_lines = tail.lstrip("\n").split("\n") if tail.strip() else []
                    buf = tail_lines
                    buf_len = sum(len(x) + 1 for x in buf) if buf else 0
                else:
                    emit_buf()

    emit_buf()

    return _merge_tiny_tail_chunks(chunks, min_chunk_chars, target_chars)


def _force_line_split(s: str, *, near: int, min_keep: int) -> int:
    """Index to split after (exclusive), on a line boundary near ``near`` chars."""
    if len(s) <= min_keep + 1:
        return 0
    take = min(max(near, min_keep + 1), len(s) - 1)
    pos = s.rfind("\n", min_keep, take + 1)
    if pos >= min_keep:
        return pos + 1
    pos = s.find("\n", min_keep)
    if pos != -1:
        return pos + 1
    return 0


def _last_good_break(s: str, *, min_keep: int) -> int:
    """Largest index < len(s) to split after, preferring \\n\\n near end of s."""
    if len(s) <= min_keep + 10:
        return 0
    search_from = max(min_keep, len(s) * 2 // 3)
    pos = s.rfind("\n\n", 0, len(s))
    if pos >= search_from:
        return pos + 2
    pos = s.rfind("\n", min_keep, len(s) - 1)
    if pos >= min_keep:
        return pos + 1
    return 0


def _merge_tiny_tail_chunks(
    chunks: list[str], min_chars: int, target_chars: int
) -> list[str]:
    """Attach only very small trailing fragments to the previous chunk."""
    if len(chunks) <= 1:
        return chunks
    out: list[str] = [chunks[0]]
    tiny = max(120, min(min_chars // 6, target_chars // 8))
    for ch in chunks[1:]:
        if len(ch) <= tiny and out and len(out[-1]) + len(ch) + 2 <= int(target_chars * 1.1):
            out[-1] = out[-1] + "\n\n" + ch
        else:
            out.append(ch)
    return out


def translate_chunk_target_chars() -> int:
    import os

    raw = os.environ.get("YDBDOC_TRANSLATE_CHUNK_CHARS", "").strip()
    if raw.isdigit() and int(raw) >= 2048:
        return int(raw)
    return 14_000
