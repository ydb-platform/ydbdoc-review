"""Group segments into batches for LLM translation.

Goals:
- A batch never exceeds the character budget (best-effort).
- A single segment is never split across batches.
- Adjacent segments stay together to give the model local context.
- A segment larger than the budget becomes its own batch.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ydbdoc_review.segmentation.split import split_segment_for_batching
from ydbdoc_review.segmentation.types import Segment

# Dense table cells: translate alone so the model keeps every ⟦C⟧/⟦U⟧ marker.
_HEAVY_PLACEHOLDER_COUNT = 8

_DEFAULT_OUTPUT_EXPANSION = 1.35
_DEFAULT_JSON_OVERHEAD = 512
_PER_SEGMENT_JSON_OVERHEAD = 40


class Batch(BaseModel):
    """A group of segments sent to the LLM as a single request."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    index: int               # 0-based index of this batch
    segments: list[Segment]

    @property
    def total_chars(self) -> int:
        return sum(len(s.text) for s in self.segments)


def estimate_translate_batch_output_chars(
    segments: list[Segment],
    *,
    expansion_ratio: float = _DEFAULT_OUTPUT_EXPANSION,
    json_overhead: int = _DEFAULT_JSON_OVERHEAD,
) -> int:
    source_chars = sum(len(seg.text) for seg in segments)
    return (
        int(source_chars * expansion_ratio)
        + json_overhead
        + _PER_SEGMENT_JSON_OVERHEAD * len(segments)
    )


def expand_segments_for_batching(
    segments: list[Segment],
    *,
    segment_max_chars: int,
) -> list[Segment]:
    """Apply ``split_segment_for_batching`` to each segment."""
    expanded: list[Segment] = []
    for seg in segments:
        expanded.extend(
            split_segment_for_batching(seg, max_chars=segment_max_chars)
        )
    return expanded


def chunk_segments(
    segments: list[Segment],
    *,
    max_chars: int = 4000,
    max_output_chars: int = 6000,
    expansion_ratio: float = _DEFAULT_OUTPUT_EXPANSION,
    json_overhead: int = _DEFAULT_JSON_OVERHEAD,
    segment_max_chars: int = 1200,
) -> list[Batch]:
    """Greedy packing of segments into batches.

    Segments with ``len(placeholders) >= 8`` always get their own batch (dense
    table cells). A segment longer than ``max_chars`` becomes its own batch.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")

    expanded = expand_segments_for_batching(
        segments, segment_max_chars=segment_max_chars
    )

    batches: list[Batch] = []
    current: list[Segment] = []
    current_size = 0

    def flush() -> None:
        nonlocal current, current_size
        if current:
            batches.append(Batch(index=len(batches), segments=current))
            current = []
            current_size = 0

    def fits(candidate: list[Segment]) -> bool:
        source_chars = sum(len(seg.text) for seg in candidate)
        if source_chars > max_chars:
            return False
        return (
            estimate_translate_batch_output_chars(
                candidate,
                expansion_ratio=expansion_ratio,
                json_overhead=json_overhead,
            )
            <= max_output_chars
        )

    def append_solo_segments(segs: list[Segment]) -> None:
        for seg in segs:
            flush()
            batches.append(Batch(index=len(batches), segments=[seg]))

    for seg in expanded:
        seg_size = len(seg.text)

        if len(seg.placeholders) >= _HEAVY_PLACEHOLDER_COUNT:
            flush()
            if (
                seg_size > max_chars
                or estimate_translate_batch_output_chars(
                    [seg],
                    expansion_ratio=expansion_ratio,
                    json_overhead=json_overhead,
                )
                > max_output_chars
            ):
                subsegments = split_segment_for_batching(
                    seg, max_chars=segment_max_chars
                )
                if len(subsegments) > 1:
                    append_solo_segments(subsegments)
                    continue
            batches.append(Batch(index=len(batches), segments=[seg]))
            continue

        if seg_size > max_chars:
            flush()
            batches.append(Batch(index=len(batches), segments=[seg]))
            continue

        candidate = current + [seg]
        if current and not fits(candidate):
            flush()
            candidate = [seg]

        if not fits(candidate):
            flush()
            batches.append(Batch(index=len(batches), segments=[seg]))
            continue

        current = candidate
        current_size = sum(len(s.text) for s in current)

    flush()
    return batches
