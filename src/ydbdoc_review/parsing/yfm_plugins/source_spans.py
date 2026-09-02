"""Parser-owned UTF-8 source coordinates for YFM block tokens."""

from __future__ import annotations


def utf8_source_span(source: str, start_char: int, end_char: int) -> dict[str, int]:
    if not 0 <= start_char <= end_char <= len(source):
        raise ValueError("invalid source span")
    prefix = source[:start_char]
    return {
        "byte_start": len(prefix.encode("utf-8")),
        "byte_end": len(source[:end_char].encode("utf-8")),
        "line": prefix.count("\n") + 1,
        "column": len(prefix.rsplit("\n", 1)[-1]) + 1,
    }
