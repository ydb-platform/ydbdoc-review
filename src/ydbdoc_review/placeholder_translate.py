"""Placeholder pipeline: COPY bytes from RU; TRANSLATE isolated lines via JSON batches."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Literal

from ydbdoc_review.config import Settings
from ydbdoc_review.document_structure import StructureRegion, analyze_document_structure
from ydbdoc_review.annotated_translate import refine_tab_regions
from ydbdoc_review.fm_progress import fm_log
from ydbdoc_review.fence_comments import (
    comment_body_on_line,
    inline_hash_comment_tail,
    inline_sql_comment_tail,
)
from ydbdoc_review.llm import (
    _strip_code_fence,
    call_yandex_responses,
    translation_model_fallbacks,
    clamp_max_output_tokens,
    load_placeholder_instructions,
    parse_json_object,
)

_COPY_ACTIONS = frozenset({"copy_verbatim"})
_TRANSLATE_ACTIONS = frozenset(
    {"translate", "translate_diplodoc", "translate_table", "translate_tabs"}
)

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")
_DIPLODOC_LINE_RE = re.compile(
    r"^\s*\{%\s*(?:endlist|endnote|endcut|list\s+tabs|note|cut)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LineUnit:
    """One translatable source line."""

    unit_id: str
    line_no: int
    source_line: str


@dataclass(frozen=True)
class CopySegment:
    kind: Literal["copy"] = "copy"
    start_line: int = 0
    end_line: int = 0
    text: str = ""


@dataclass(frozen=True)
class TranslateSegment:
    kind: Literal["translate"] = "translate"
    start_line: int = 0
    end_line: int = 0
    units: tuple[LineUnit, ...] = ()


PlaceholderSegment = CopySegment | TranslateSegment


def _slice_lines(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    s = max(1, start_line) - 1
    e = min(len(lines), end_line)
    return "\n".join(lines[s:e])


def _unit_id(line_no: int) -> str:
    return f"L{line_no:05d}"


def _fence_comment_line_numbers(
    lines: list[str], start_line: int, end_line: int, *, source_is_russian: bool
) -> set[int]:
    out: set[int] = set()
    for ln in range(start_line, end_line + 1):
        line = lines[ln - 1]
        body = comment_body_on_line(line)
        if body and _needs_script(body[1], source_is_russian=source_is_russian):
            out.add(ln)
            continue
        for tail_fn in (inline_sql_comment_tail, inline_hash_comment_tail):
            tail = tail_fn(line)
            if tail and _needs_script(tail[1], source_is_russian=source_is_russian):
                out.add(ln)
                break
    return out


def _needs_script(text: str, *, source_is_russian: bool) -> bool:
    has_cyr = bool(_CYRILLIC_RE.search(text))
    if source_is_russian:
        return has_cyr
    return bool(text.strip()) and not has_cyr


def line_needs_translation(
    line: str,
    *,
    region: StructureRegion,
    line_no: int,
    source_is_russian: bool,
    fence_comment_lines: set[int] | None = None,
) -> bool:
    if region.action in _COPY_ACTIONS:
        return False
    if region.action == "fence_comments":
        return line_no in (fence_comment_lines or set())
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("```"):
        return False
    if _DIPLODOC_LINE_RE.match(stripped):
        return False
    if not source_is_russian:
        return bool(_CYRILLIC_RE.search(line))
    if _CYRILLIC_RE.search(line):
        return True
    if re.search(r"\[[^\]]*[\u0400-\u04FF]", line):
        return True
    return False


def build_placeholder_segments(
    text: str,
    regions: list[StructureRegion],
    *,
    source_is_russian: bool = True,
) -> list[PlaceholderSegment]:
    """Ordered COPY / TRANSLATE segments from structure regions."""
    if not regions:
        n = len(text.splitlines()) or 1
        regions = [
            StructureRegion(1, n, "prose", "translate", "Translate entire file.")
        ]
    lines = text.splitlines()
    segments: list[PlaceholderSegment] = []
    for region in regions:
        body = _slice_lines(text, region.start_line, region.end_line)
        if region.action in _COPY_ACTIONS:
            segments.append(
                CopySegment(
                    start_line=region.start_line,
                    end_line=region.end_line,
                    text=body,
                )
            )
            continue
        if region.action not in _TRANSLATE_ACTIONS and region.action != "fence_comments":
            segments.append(
                CopySegment(
                    start_line=region.start_line,
                    end_line=region.end_line,
                    text=body,
                )
            )
            continue

        comment_lines: set[int] | None = None
        if region.action == "fence_comments":
            comment_lines = _fence_comment_line_numbers(
                lines,
                region.start_line,
                region.end_line,
                source_is_russian=source_is_russian,
            )

        units: list[LineUnit] = []
        for ln in range(region.start_line, region.end_line + 1):
            line = lines[ln - 1]
            if line_needs_translation(
                line,
                region=region,
                line_no=ln,
                source_is_russian=source_is_russian,
                fence_comment_lines=comment_lines,
            ):
                units.append(
                    LineUnit(
                        unit_id=_unit_id(ln),
                        line_no=ln,
                        source_line=line,
                    )
                )
        segments.append(
            TranslateSegment(
                start_line=region.start_line,
                end_line=region.end_line,
                units=tuple(units),
            )
        )
    return segments


def assemble_translate_segment(
    source_text: str,
    segment: TranslateSegment,
    translations: dict[str, str],
) -> str:
    lines = source_text.splitlines()
    out: list[str] = []
    for ln in range(segment.start_line, segment.end_line + 1):
        line = lines[ln - 1]
        uid = _unit_id(ln)
        if uid in translations:
            line = _merge_line_indent(line, translations[uid])
        out.append(line)
    return "\n".join(out)


def _merge_line_indent(source_line: str, translated: str) -> str:
    m = re.match(r"^(\s*)", source_line)
    indent = m.group(1) if m else ""
    body = translated.strip("\n")
    if not body and not source_line.strip():
        return source_line
    return indent + body.lstrip()


def _batch_units(
    units: list[LineUnit], *, max_chars: int
) -> list[list[LineUnit]]:
    batches: list[list[LineUnit]] = []
    current: list[LineUnit] = []
    size = 0
    for u in units:
        ulen = len(u.source_line) + len(u.unit_id) + 32
        if current and size + ulen > max_chars:
            batches.append(current)
            current = []
            size = 0
        current.append(u)
        size += ulen
    if current:
        batches.append(current)
    return batches


def _max_batch_chars() -> int:
    raw = os.environ.get("YDBDOC_PLACEHOLDER_BATCH_CHARS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 10_000


def _build_batch_user_input(
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    batch: list[LineUnit],
    batch_index: int,
    batch_total: int,
) -> str:
    payload = {
        "source_lang": source_lang,
        "target_lang": target_lang,
        "file": source_path,
        "batch": f"{batch_index}/{batch_total}",
        "lines": [{"id": u.unit_id, "text": u.source_line} for u in batch],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_batch_response(raw: str, batch: list[LineUnit]) -> dict[str, str]:
    fallback = {u.unit_id: u.source_line for u in batch}
    try:
        data = parse_json_object(_strip_code_fence(raw))
    except json.JSONDecodeError:
        return fallback
    out: dict[str, str] = {}
    items = data.get("lines")
    if not isinstance(items, list):
        return fallback
    for item in items:
        if not isinstance(item, dict):
            continue
        uid = item.get("id")
        text = item.get("text")
        if isinstance(uid, str) and isinstance(text, str):
            out[uid] = text
    for u in batch:
        out.setdefault(u.unit_id, u.source_line)
    return out


def translate_line_units(
    settings: Settings,
    units: list[LineUnit],
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
) -> dict[str, str]:
    """Translate line units in one or more JSON batches."""
    if not units:
        return {}
    instructions = load_placeholder_instructions(
        settings, source_lang=source_lang, target_lang=target_lang
    ).strip()
    batches = _batch_units(units, max_chars=_max_batch_chars())
    merged: dict[str, str] = {}
    model = settings.model_translate
    total = len(batches)
    for i, batch in enumerate(batches, start=1):
        user_input = _build_batch_user_input(
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            batch=batch,
            batch_index=i,
            batch_total=total,
        )
        cap = clamp_max_output_tokens(
            max(2048, min(len(user_input) * 2 + 512, 32_768)),
            model,
        )
        fm_log(
            f"placeholder-translate batch {i}/{total} | {source_path} | "
            f"{len(batch)} line(s)"
        )
        raw = call_yandex_responses(
            settings,
            model,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=cap,
            model_fallbacks=translation_model_fallbacks(),
            operation="translate:placeholder-lines",
            detail=f"{source_path}:batch-{i}-of-{total}",
        )
        merged.update(_parse_batch_response(raw, batch))
    return merged


def translate_with_placeholders(
    settings: Settings,
    *,
    source_path: str,
    source_text: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, int]:
    """
    Translate *source_text* via COPY segments + JSON line batches.

    Returns ``(english_markdown, num_llm_calls)``.
    """
    source_is_russian = source_lang.lower().startswith("rus")
    regions = refine_tab_regions(
        source_text,
        analyze_document_structure(source_text, source_is_russian=source_is_russian),
    )
    segments = build_placeholder_segments(
        source_text, regions, source_is_russian=source_is_russian
    )
    all_units: list[LineUnit] = []
    for seg in segments:
        if isinstance(seg, TranslateSegment):
            all_units.extend(seg.units)

    llm_calls = 0
    translations: dict[str, str] = {}
    if all_units:
        unit_batches = _batch_units(all_units, max_chars=_max_batch_chars())
        llm_calls = len(unit_batches)
        translations = translate_line_units(
            settings,
            all_units,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
        )

    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, CopySegment):
            fm_log(
                f"placeholder-translate copy | {source_path} | "
                f"lines {seg.start_line}-{seg.end_line}"
            )
            parts.append(seg.text)
            continue
        fm_log(
            f"placeholder-translate lines | {source_path} | "
            f"lines {seg.start_line}-{seg.end_line} | {len(seg.units)} unit(s)"
        )
        parts.append(assemble_translate_segment(source_text, seg, translations))

    merged = _join_segments(parts)
    if source_text.endswith("\n") and merged and not merged.endswith("\n"):
        merged += "\n"
    return merged, llm_calls


def _join_segments(parts: list[str]) -> str:
    if not parts:
        return ""
    merged = parts[0]
    for part in parts[1:]:
        if not part:
            continue
        if not merged:
            merged = part
            continue
        if merged.endswith("\n\n") or (merged.endswith("\n") and part.startswith("\n")):
            merged += part
            continue
        if merged.rstrip().endswith("}") and part.lstrip().startswith("{%"):
            merged = merged.rstrip() + "\n\n" + part.lstrip("\n")
        elif not merged.endswith("\n"):
            merged += "\n\n" + part.lstrip("\n")
        else:
            merged += "\n" + part.lstrip("\n")
    return merged


def count_placeholder_stats(segments: list[PlaceholderSegment]) -> tuple[int, int, int]:
    """Return ``(copy_segments, translate_segments, line_units)``."""
    copy_n = sum(1 for s in segments if isinstance(s, CopySegment))
    tr_n = sum(1 for s in segments if isinstance(s, TranslateSegment))
    units = sum(len(s.units) for s in segments if isinstance(s, TranslateSegment))
    return copy_n, tr_n, units
