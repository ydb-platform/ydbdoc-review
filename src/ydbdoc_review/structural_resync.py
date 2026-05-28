"""Structural parity checks and RU→EN resync after translation merge."""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.document_segments import _is_fence_toggle
from ydbdoc_review.fence_repair import extract_fence_blocks
from ydbdoc_review.ru_en_sync import finalize_en_document_from_ru


@dataclass(frozen=True)
class StructuralReport:
    ru_fence_blocks: int
    en_fence_blocks: int
    ru_h2: int
    en_h2: int
    ru_lines: int
    en_lines: int

    @property
    def ok(self) -> bool:
        return (
            self.ru_fence_blocks == self.en_fence_blocks
            and self.ru_h2 == self.en_h2
            and self.en_lines >= int(self.ru_lines * 0.85)
        )


def structural_report(ru_source: str, en_text: str) -> StructuralReport:
    ru_lines = ru_source.splitlines()
    en_lines = en_text.splitlines()
    return StructuralReport(
        ru_fence_blocks=len(extract_fence_blocks(ru_source)),
        en_fence_blocks=len(extract_fence_blocks(en_text)),
        ru_h2=sum(1 for ln in ru_lines if ln.startswith("## ") and not ln.startswith("### ")),
        en_h2=sum(1 for ln in en_lines if ln.startswith("## ") and not ln.startswith("### ")),
        ru_lines=len(ru_lines),
        en_lines=len(en_lines),
    )


def resync_en_structure_from_ru(ru_source: str, en_text: str) -> str:
    """Deterministic RU alignment: fences, tabs, tables, links (full finalize pass)."""
    return finalize_en_document_from_ru(ru_source, en_text)


def count_raw_fence_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if _is_fence_toggle(line))
