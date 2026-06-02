"""Line numbers and GitHub links for reviewer reports."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.schemas import CriticIssueOut

_PLACEHOLDER = re.compile(r"⟦[^⟧]+⟧")
_CYRILLIC_LINE = re.compile(r"строка ~(\d+)")
_CLI_TOKEN = re.compile(r"`[^`]{3,}`")


@dataclass(frozen=True)
class ReportLinkContext:
    """Optional GitHub coordinates for file line links in PR comments."""

    github_repo: str | None = None  # owner/repo
    ref: str | None = None  # branch or commit


def excerpt_for_line_search(text: str) -> str | None:
    """Pick a stable substring to locate segment text in rendered markdown."""
    cli = _CLI_TOKEN.search(text)
    if cli:
        return cli.group(0)
    plain = _PLACEHOLDER.sub("", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) >= 12:
        return plain[:100]
    return plain or None


def line_range_for_needle(haystack: str, needle: str) -> tuple[int, int] | None:
    """Return 1-based inclusive line range for the first occurrence of ``needle``."""
    if not needle:
        return None
    idx = haystack.find(needle)
    if idx < 0:
        collapsed_hay = re.sub(r"\s+", " ", haystack)
        collapsed_needle = re.sub(r"\s+", " ", needle).strip()
        idx = collapsed_hay.find(collapsed_needle)
        if idx < 0:
            return None
        haystack = collapsed_hay
    start = haystack.count("\n", 0, idx) + 1
    end = haystack.count("\n", 0, idx + len(needle)) + 1
    return start, max(start, end)


def build_segment_line_map(
    final_text: str,
    segments: list[Segment],
    translations: dict[str, str],
) -> dict[str, tuple[int, int]]:
    """Map segment id → (start_line, end_line) in rendered markdown."""
    lines: dict[str, tuple[int, int]] = {}
    for seg in segments:
        rendered = translations.get(seg.id, seg.text)
        needle = excerpt_for_line_search(rendered) or excerpt_for_line_search(seg.text)
        if needle is None:
            continue
        found = line_range_for_needle(final_text, needle)
        if found is not None:
            lines[seg.id] = found
    return lines


def format_line_ref(
    line_range: tuple[int, int] | None,
    *,
    file_path: str,
    link: ReportLinkContext | None = None,
) -> str:
    """Human-readable line reference, optionally linked on GitHub."""
    if line_range is None:
        return ""
    start, end = line_range
    if start == end:
        label = f":{start}"
    else:
        label = f":{start}-{end}"
    if link and link.github_repo and link.ref:
        url = (
            f"https://github.com/{link.github_repo}/blob/{link.ref}/"
            f"{file_path}{label}"
        )
        return f" ([строки {start}]({url}))"
    return f" (строки {start}" + (f"–{end}" if end != start else "") + ")"


def format_location_label(
    *,
    file_path: str,
    segment_id: str | None,
    path_label: str | None,
    line_range: tuple[int, int] | None,
    link: ReportLinkContext | None = None,
) -> str:
    parts: list[str] = []
    if path_label:
        parts.append(path_label)
    if segment_id:
        parts.append(f"`{segment_id}`")
    loc = " › ".join(parts) if parts else "файл"
    line_suffix = format_line_ref(line_range, file_path=file_path, link=link)
    return loc + line_suffix


def manual_action_segment_ids(actions: list[ManualAction]) -> set[str]:
    return {a.segment_id for a in actions}


def filter_critic_for_report(
    issues: list[CriticIssueOut],
    manual_ids: set[str],
) -> list[CriticIssueOut]:
    """Drop critic issues redundant with manual-actions (untranslated segment)."""
    out: list[CriticIssueOut] = []
    for issue in issues:
        if issue.segment_id and issue.segment_id in manual_ids:
            cat = issue.category.lower().replace("_", " ")
            if "missing" in cat and "translation" in cat:
                continue
            if "not translated" in issue.comment.lower():
                continue
        out.append(issue)
    return out


def consolidate_heuristic_warnings(
    warnings: list[str],
    *,
    manual_ids: set[str],
    manual_line_ranges: list[tuple[int, int]],
) -> list[str]:
    """Group Cyrillic noise; tie to manual table cells when possible."""
    cyrillic: list[str] = []
    other: list[str] = []
    for w in warnings:
        if w.startswith("Кириллица в EN-тексте") or w.startswith("… и ещё"):
            cyrillic.append(w)
        else:
            other.append(w)

    if not cyrillic:
        return warnings

    lines: list[int] = []
    total_chars = 0
    for w in cyrillic:
        m = _CYRILLIC_LINE.search(w)
        if m:
            lines.append(int(m.group(1)))
        if "всего" in w and "символ" in w:
            m_total = re.search(r"всего (\d+)", w)
            if m_total:
                total_chars = int(m_total.group(1))

    if manual_ids and lines:
        lo, hi = min(lines), max(lines)
        seg_hint = ""
        if len(manual_ids) == 1:
            seg_hint = f" (сегмент `{next(iter(manual_ids))}`)"
        summary = (
            f"Кириллица в EN-тексте (строки {lo}–{hi}{seg_hint}): "
            "остаток непереведённой ячейки таблицы — см. пункт выше."
        )
        if total_chars:
            summary += f" (~{total_chars} символов кириллицы в файле)."
        return other + [summary]

    grouped = _group_cyrillic_by_line(cyrillic)
    return other + grouped


def _group_cyrillic_by_line(cyrillic: list[str]) -> list[str]:
    by_line: dict[int, list[str]] = {}
    tail: list[str] = []
    for w in cyrillic:
        if w.startswith("… и ещё"):
            tail.append(w)
            continue
        m = _CYRILLIC_LINE.search(w)
        if not m:
            tail.append(w)
            continue
        line = int(m.group(1))
        by_line.setdefault(line, []).append(w)

    out: list[str] = []
    for line in sorted(by_line):
        items = by_line[line]
        if len(items) == 1:
            out.append(items[0])
        else:
            snippet = items[0].split("«", 1)[-1].rstrip("»")
            if len(snippet) > 60:
                snippet = snippet[:57] + "…"
            out.append(
                f"Кириллица в EN-тексте (строка ~{line}, {len(items)} вхождений): «{snippet}»"
            )
    out.extend(tail)
    return out
