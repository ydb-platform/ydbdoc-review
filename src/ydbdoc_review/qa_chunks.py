"""Split large RU↔EN pairs into overlapping chunks for critic QA.

Chunks are built from aligned :func:`parse_document_units` slices so SOURCE and
TRANSLATION stay in sync. Consecutive chunks share one trailing/leading unit
(overlap) so blockers on section boundaries are not missed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ydbdoc_review.document_segments import DocumentUnit, assemble_document_units, parse_document_units


@dataclass(frozen=True)
class QaChunk:
    """One aligned SOURCE↔TRANSLATION slice for a critic call."""

    index: int
    total: int
    label: str
    source_text: str
    translation_text: str
    overlap_units: int


def _qa_chunk_char_budget() -> int:
    raw = os.environ.get("YDBDOC_QA_CHUNK_MAX_CHARS", "").strip()
    if raw.isdigit():
        return max(4000, int(raw))
    return 18_000


def _qa_chunk_overlap_units() -> int:
    raw = os.environ.get("YDBDOC_QA_CHUNK_OVERLAP_UNITS", "").strip()
    if raw.isdigit():
        return max(0, int(raw))
    return 1


def needs_qa_chunking(source_text: str, translation_text: str) -> bool:
    """True when a single critic call would likely exceed the model input window."""
    raw = os.environ.get("YDBDOC_QA_CHUNK_THRESHOLD_CHARS", "").strip()
    threshold = int(raw) if raw.isdigit() else 42_000
    return len(source_text) + len(translation_text) > threshold


def _unit_size(ru: DocumentUnit, en: DocumentUnit) -> int:
    return len(ru.text) + len(en.text)


def build_qa_chunks(
    source_text: str,
    translation_text: str,
    *,
    doc_label: str = "doc",
) -> list[QaChunk]:
    """Group aligned units into overlapping chunks under the char budget."""
    ru_units = parse_document_units(source_text, doc_label=doc_label)
    en_units = parse_document_units(translation_text, doc_label=doc_label)

    if not ru_units:
        return [
            QaChunk(
                index=1,
                total=1,
                label=f"{doc_label}/all",
                source_text=source_text,
                translation_text=translation_text,
                overlap_units=0,
            )
        ]

    if len(en_units) != len(ru_units):
        return _line_window_chunks(source_text, translation_text, doc_label=doc_label)

    budget = _qa_chunk_char_budget()
    overlap = _qa_chunk_overlap_units()
    paired = list(zip(ru_units, en_units, strict=True))

    groups: list[list[tuple[DocumentUnit, DocumentUnit]]] = []
    current: list[tuple[DocumentUnit, DocumentUnit]] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            groups.append(current)
            current = []
            size = 0

    for pair in paired:
        add = _unit_size(pair[0], pair[1])
        if current and size + add > budget:
            flush()
            if overlap > 0 and groups:
                tail = groups[-1][-overlap:]
                current = list(tail)
                size = sum(_unit_size(a, b) for a, b in current)
        current.append(pair)
        size += add
    flush()

    if not groups:
        groups = [paired]

    total = len(groups)
    chunks: list[QaChunk] = []
    for i, group in enumerate(groups, start=1):
        ru_part = [u for u, _ in group]
        en_part = [u for _, u in group]
        labels = [u.label for u in ru_part]
        chunk_label = labels[0] if len(labels) == 1 else f"{labels[0]}…{labels[-1]}"
        chunks.append(
            QaChunk(
                index=i,
                total=total,
                label=chunk_label,
                source_text=assemble_document_units(ru_part),
                translation_text=assemble_document_units(en_part),
                overlap_units=overlap if i > 1 else 0,
            )
        )
    return chunks


def _line_window_chunks(
    source_text: str,
    translation_text: str,
    *,
    doc_label: str,
) -> list[QaChunk]:
    """Fallback when unit counts diverge: sliding line windows with line overlap."""
    ru_lines = source_text.splitlines()
    en_lines = translation_text.splitlines()
    budget_lines = max(80, _qa_chunk_char_budget() // 120)
    overlap_lines = max(5, budget_lines // 8)
    max_lines = max(len(ru_lines), len(en_lines), 1)
    step = max(1, budget_lines - overlap_lines)
    starts = list(range(0, max_lines, step))
    if starts and starts[-1] + budget_lines < max_lines:
        starts.append(max(0, max_lines - budget_lines))

    total = len(starts)
    chunks: list[QaChunk] = []
    for i, start in enumerate(starts, start=1):
        end = start + budget_lines
        chunks.append(
            QaChunk(
                index=i,
                total=total,
                label=f"{doc_label}/lines-{start + 1}-{min(end, max_lines)}",
                source_text="\n".join(ru_lines[start:end]),
                translation_text="\n".join(en_lines[start:end]),
                overlap_units=overlap_lines if i > 1 else 0,
            )
        )
    return chunks


_VERDICT_LINE_RE = re.compile(
    r"^###\s*Вердикт\s*\n+\s*\*?\*?\s*(НЕ\s+ПРИНИМАТЬ|ПРИНИМАТЬ\s+С\s+ОГОВОРКАМИ|ПРИНИМАТЬ)\b",
    re.IGNORECASE | re.MULTILINE,
)


def merge_chunk_reports(
    reports: list[str],
    *,
    file_label: str,
    chunk_labels: list[str],
) -> str:
    """Combine per-chunk critic markdown into one file-level report."""
    if not reports:
        return (
            "### Вердикт\n**ПРИНИМАТЬ С ОГОВОРКАМИ**\n\n"
            "### Блокеры\n_Нет._\n\n"
            "### Оговорки\n- Chunked QA не вернул отчётов.\n\n"
            "### Кратко\nПустой результат chunked QA.\n"
        )
    if len(reports) == 1:
        return reports[0]

    verdict_rank = {"accept": 0, "accept_with_notes": 1, "reject": 2}

    def _local_verdict(report: str) -> str:
        m = _VERDICT_LINE_RE.search(report)
        if not m:
            low = report.lower()
            if re.search(r"не\s+принимать", low):
                return "reject"
            if re.search(r"принимать\s+с\s+оговорками", low):
                return "accept_with_notes"
            return "accept"
        word = " ".join(m.group(1).upper().split())
        if word == "НЕ ПРИНИМАТЬ":
            return "reject"
        if word == "ПРИНИМАТЬ С ОГОВОРКАМИ":
            return "accept_with_notes"
        return "accept"

    verdicts = [_local_verdict(r) for r in reports]
    worst = max(verdicts, key=lambda v: verdict_rank.get(v, 1))
    label_ru = {
        "accept": "ПРИНИМАТЬ",
        "accept_with_notes": "ПРИНИМАТЬ С ОГОВОРКАМИ",
        "reject": "НЕ ПРИНИМАТЬ",
    }[worst]

    blockers: list[str] = []
    notes: list[str] = []
    briefs: list[str] = []

    for rep, clabel, v in zip(reports, chunk_labels, verdicts, strict=False):
        briefs.append(f"чанк `{clabel}`: {label_ru if v in label_ru else v}")
        sec = _extract_section(rep, "Блокеры")
        if sec and "_Нет._" not in sec and "_нет._" not in sec.lower():
            blockers.append(f"**Чанк `{clabel}`:**\n{sec.strip()}")
        sec = _extract_section(rep, "Оговорки")
        if sec and "_Нет._" not in sec and "_нет._" not in sec.lower():
            notes.append(f"**Чанк `{clabel}`:** {sec.strip()}")

    blockers_text = "\n\n".join(blockers) if blockers else "_Нет._"
    notes_text = "\n\n".join(notes[:8]) if notes else "_Нет._"
    brief = (
        f"Файл `{file_label}` проверен по {len(reports)} пересекающимся чанкам "
        f"(overlap units/lines). Худший вердикт: **{label_ru}**. "
        + " ".join(briefs[:6])
    )
    if len(briefs) > 6:
        brief += f" … (+{len(briefs) - 6} чанков)"

    return (
        f"### Вердикт\n**{label_ru}**\n\n"
        f"### Блокеры\n{blockers_text}\n\n"
        f"### Оговорки\n{notes_text}\n\n"
        f"### Кратко\n{brief}\n"
    )


def _extract_section(report: str, name: str) -> str | None:
    pattern = rf"###\s*{re.escape(name)}\s*\n([\s\S]*?)(?=###\s|\Z)"
    m = re.search(pattern, report, re.IGNORECASE)
    return m.group(1).strip() if m else None


def chunk_context_header(chunk: QaChunk) -> str:
    overlap_note = (
        f"Перекрытие с соседним чанком: {chunk.overlap_units} unit(s)/lines.\n"
        if chunk.overlap_units
        else ""
    )
    return (
        f"QA chunk {chunk.index}/{chunk.total} (`{chunk.label}`).\n"
        f"{overlap_note}"
        "Сравнивайте только фрагменты SOURCE и TRANSLATION ниже; "
        "блокер в этом чанке = блокер для всего файла.\n\n"
    )
