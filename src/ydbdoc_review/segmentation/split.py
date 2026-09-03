"""Structure-aware segment subdivision for translate batching."""

from __future__ import annotations

from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.validation.markers import PLACEHOLDER_SPLIT_RE

_SPLITTABLE_KINDS = frozenset(
    {
        SegmentKind.PARAGRAPH,
        SegmentKind.BLOCKQUOTE_PARAGRAPH,
        SegmentKind.LIST_ITEM,
    }
)


def _split_on_blank_lines_outside_placeholders(text: str) -> list[str]:
    if not text:
        return [""]
    tokens = PLACEHOLDER_SPLIT_RE.split(text)
    parts: list[str] = []
    current: list[str] = []

    def flush_para() -> None:
        nonlocal current
        if current:
            parts.append("".join(current))
            current = []

    def append_text_chunk(chunk: str) -> None:
        if not chunk:
            return
        subparts = chunk.split("\n\n")
        for idx, subpart in enumerate(subparts):
            if idx > 0:
                flush_para()
            if subpart:
                current.append(subpart)

    for idx, token in enumerate(tokens):
        if idx % 2 == 1:
            current.append(token)
        else:
            append_text_chunk(token)
    flush_para()
    return parts if parts else [""]


def _greedy_pack_parts(parts: list[str], max_chars: int) -> list[str]:
    if not parts:
        return [""]
    packed: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}\n\n{part}" if current else part
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            packed.append(current)
            current = ""
        if len(part) > max_chars:
            packed.append(part)
        else:
            current = part
    if current:
        packed.append(current)
    return packed


def split_segment_for_batching(
    segment: Segment,
    *,
    max_chars: int,
) -> list[Segment]:
    """Split one segment on structural boundaries when it exceeds ``max_chars``."""
    if len(segment.text) <= max_chars:
        return [segment]
    if segment.kind not in _SPLITTABLE_KINDS:
        return [segment]

    parts = _greedy_pack_parts(
        _split_on_blank_lines_outside_placeholders(segment.text),
        max_chars,
    )
    if len(parts) <= 1:
        return [segment]

    out: list[Segment] = []
    for idx, part_text in enumerate(parts, start=1):
        part_placeholders = [
            ph for ph in segment.placeholders if ph.placeholder in part_text
        ]
        out.append(
            segment.model_copy(
                update={
                    "id": f"{segment.id}__p{idx}",
                    "text": part_text,
                    "placeholders": part_placeholders,
                    "ast_path": [*segment.ast_path, "split", idx],
                }
            )
        )
    return out
