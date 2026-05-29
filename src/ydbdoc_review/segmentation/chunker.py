"""Group segments into batches for LLM translation.

Goals:
- A batch never exceeds the character budget (best-effort).
- A single segment is never split across batches.
- Adjacent segments stay together to give the model local context.
- A segment larger than the budget becomes its own batch.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ydbdoc_review.segmentation.types import Segment


class Batch(BaseModel):
    """A group of segments sent to the LLM as a single request."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    index: int               # 0-based index of this batch
    segments: list[Segment]

    @property
    def total_chars(self) -> int:
        return sum(len(s.text) for s in self.segments)


def chunk_segments(
    segments: list[Segment],
    *,
    max_chars: int = 4000,
) -> list[Batch]:
    """Greedy packing of segments into batches.

    A segment longer than ``max_chars`` becomes its own batch (one segment per
    batch). All other segments are packed greedily up to the budget.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")

    batches: list[Batch] = []
    current: list[Segment] = []
    current_size = 0

    def flush() -> None:
        nonlocal current, current_size
        if current:
            batches.append(Batch(index=len(batches), segments=current))
            current = []
            current_size = 0

    for seg in segments:
        seg_size = len(seg.text)

        # Oversized segment → its own batch.
        if seg_size > max_chars:
            flush()
            batches.append(Batch(index=len(batches), segments=[seg]))
            continue

        # Doesn't fit into current batch → start a new one.
        if current and current_size + seg_size > max_chars:
            flush()

        current.append(seg)
        current_size += seg_size

    flush()
    return batches

