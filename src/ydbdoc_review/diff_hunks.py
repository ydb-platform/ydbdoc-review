"""Split unified git diffs into batches for incremental translation."""

from __future__ import annotations

import os
import re

_HUNK_HEADER_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def diff_batch_max_chars() -> int:
    raw = os.environ.get("YDBDOC_TRANSLATE_DIFF_BATCH_CHARS", "").strip()
    if raw.isdigit() and int(raw) >= 512:
        return int(raw)
    return 3500


def batch_unified_diff(diff: str, *, max_chars: int | None = None) -> list[str]:
    """
    Split a unified diff into several smaller unified diffs.

    Each batch keeps a shared file header (``---`` / ``+++`` / ``diff --git``)
    when present. Hunks are never split in the middle.
    """
    if not diff or not diff.strip():
        return []
    cap = max_chars if max_chars is not None else diff_batch_max_chars()
    lines = diff.splitlines()
    header: list[str] = []
    hunks: list[list[str]] = []
    cur: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _HUNK_HEADER_RE.match(line):
            if cur:
                hunks.append(cur)
            cur = [line]
        elif cur:
            cur.append(line)
        else:
            header.append(line)
        i += 1
    if cur:
        hunks.append(cur)

    if not hunks:
        return [diff]

    batches: list[list[str]] = []
    batch_hunks: list[list[str]] = []
    batch_size = sum(len(x) + 1 for x in header)

    def flush() -> None:
        nonlocal batch_hunks, batch_size
        if not batch_hunks:
            return
        body: list[str] = []
        body.extend(header)
        for h in batch_hunks:
            if body and body[-1] != "":
                body.append("")
            body.extend(h)
        batches.append(body)
        batch_hunks = []
        batch_size = sum(len(x) + 1 for x in header)

    for h in hunks:
        h_size = sum(len(x) + 1 for x in h)
        if batch_hunks and batch_size + h_size > cap:
            flush()
        batch_hunks.append(h)
        batch_size += h_size
    flush()

    if not batches:
        return [diff]
    return ["\n".join(b) for b in batches]


def count_diff_additions(diff: str) -> int:
    return sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
