"""Line numbers and GitHub links for reviewer reports."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.schemas import CriticIssueOut

_SEGMENT_PATH_PART = re.compile(
    r"^(tab|table|note|term):(.+)$"
)
_TABLE_CELL = re.compile(r"^row(\d+):col(\d+)$")
_TABLE_HEADER_COL = re.compile(r"^header:col(\d+)$")

_PLACEHOLDER = re.compile(r"⟦[^⟧]+⟧")
_CYRILLIC_LINE = re.compile(r"строка ~(\d+)")
_CLI_TOKEN = re.compile(r"`[^`]{3,}`")


@dataclass(frozen=True)
class ReportLinkContext:
    """Optional GitHub coordinates for file line links in PR comments."""

    github_repo: str | None = None  # owner/repo
    ref: str | None = None  # branch or commit


def humanize_path_part(part: str) -> str:
    """Turn internal segment path tokens into reviewer-facing Russian labels."""
    if part == "(начало документа)":
        return "начало документа"
    if part == "blockquote":
        return "цитата"
    if part == "cut":
        return "cut-блок"
    if part == "list_item":
        return "пункт списка"
    m = _SEGMENT_PATH_PART.match(part)
    if not m:
        return part
    kind, rest = m.group(1), m.group(2)
    if kind == "tab":
        return f"вкладка «{rest}»"
    if kind == "note":
        return f"примечание «{rest}»"
    if kind == "term":
        return f"термин «{rest}»"
    if kind == "table":
        hm = _TABLE_HEADER_COL.match(rest)
        if hm:
            return f"таблица, заголовок, столбец {hm.group(1)}"
        rm = _TABLE_CELL.match(rest)
        if rm:
            return f"таблица, строка {rm.group(1)}, столбец {rm.group(2)}"
        return f"таблица ({rest})"
    return part


def humanize_path_label(path_label: str) -> str:
    """Humanize a joined ``segment.path`` label for PR reports."""
    return " › ".join(humanize_path_part(p) for p in path_label.split(" › "))


def rendered_segment_text(
    seg: Segment,
    translations: dict[str, str],
    *,
    placeholder_seg: Segment | None = None,
) -> str:
    """Best-effort markdown preview of a segment translation for search/excerpt."""
    translated = translations.get(seg.id, seg.text)
    ph_seg = placeholder_seg or seg
    if not ph_seg.placeholders:
        return translated
    from ydbdoc_review.segmentation.inline_protector import restore_inline_text

    return restore_inline_text(translated, ph_seg.placeholders)


def excerpt_for_line_search(text: str) -> str | None:
    """Pick a stable substring to locate segment text in rendered markdown."""
    plain = _PLACEHOLDER.sub("", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) >= 20:
        return plain[:120]
    cli = _CLI_TOKEN.search(text)
    if cli:
        return cli.group(0)
    if len(plain) >= 12:
        return plain
    return plain or None


def _offset_for_line(haystack: str, line: int) -> int:
    if line <= 1:
        return 0
    pos = 0
    for _ in range(line - 1):
        nxt = haystack.find("\n", pos)
        if nxt < 0:
            return len(haystack)
        pos = nxt + 1
    return pos


def line_range_for_needle(
    haystack: str,
    needle: str,
    *,
    min_line: int = 1,
) -> tuple[int, int] | None:
    """Return 1-based inclusive line range for an occurrence of ``needle``."""
    if not needle:
        return None
    start_at = _offset_for_line(haystack, min_line)
    idx = haystack.find(needle, start_at)
    if idx < 0:
        collapsed_hay = re.sub(r"\s+", " ", haystack)
        collapsed_needle = re.sub(r"\s+", " ", needle).strip()
        collapsed_start = _offset_for_line(collapsed_hay, min_line)
        idx = collapsed_hay.find(collapsed_needle, collapsed_start)
        if idx < 0:
            return None
        haystack = collapsed_hay
        needle = collapsed_needle
    start = haystack.count("\n", 0, idx) + 1
    end = haystack.count("\n", 0, idx + len(needle)) + 1
    return start, max(start, end)


def _truncate_excerpt(text: str, *, max_len: int = 100) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1] + "…"


def segment_display_excerpt(
    seg: Segment,
    translations: dict[str, str],
    *,
    final_text: str = "",
    line_range: tuple[int, int] | None = None,
    max_len: int = 100,
    placeholder_seg: Segment | None = None,
) -> str | None:
    """Short EN/RU snippet reviewers can search for in the file."""
    if line_range and final_text:
        lines = final_text.splitlines()
        start, end = line_range
        if 1 <= start <= len(lines):
            chunk = " ".join(lines[start - 1 : end]).strip()
            if chunk:
                return _truncate_excerpt(chunk, max_len=max_len)
    rendered = rendered_segment_text(
        seg, translations, placeholder_seg=placeholder_seg
    )
    needle = excerpt_for_line_search(rendered) or excerpt_for_line_search(seg.text)
    if needle:
        return _truncate_excerpt(needle, max_len=max_len)
    if rendered.strip():
        return _truncate_excerpt(rendered, max_len=max_len)
    return None


def excerpt_found_in_file(excerpt: str, final_text: str) -> bool:
    """True when a report search excerpt plausibly occurs in the rendered file."""
    if not excerpt or not final_text:
        return True
    if excerpt in final_text:
        return True
    collapsed_hay = re.sub(r"\s+", " ", final_text)
    collapsed_excerpt = re.sub(r"\s+", " ", excerpt).strip()
    if collapsed_excerpt and collapsed_excerpt in collapsed_hay:
        return True
    # Require a substantive backtick span from the excerpt itself.
    cli = _CLI_TOKEN.search(excerpt)
    if cli:
        return cli.group(0) in final_text
    # Broken previews from wrong placeholder restore (missing inline code).
    if "(e.g.," in excerpt and "`" not in excerpt:
        return False
    if re.search(r"\(\s*\)|\[\w*\]\(\s*\)", excerpt):
        return False
    return len(collapsed_excerpt) < 12


def build_segment_line_map(
    final_text: str,
    segments: list[Segment],
    translations: dict[str, str],
    *,
    placeholder_segments: list[Segment] | None = None,
) -> dict[str, tuple[int, int]]:
    """Map segment id → (start_line, end_line) in rendered markdown."""
    ph_segs = placeholder_segments or segments
    lines: dict[str, tuple[int, int]] = {}
    search_from = 1
    for seg, ph_seg in zip(segments, ph_segs, strict=False):
        rendered = rendered_segment_text(seg, translations, placeholder_seg=ph_seg)
        needle = excerpt_for_line_search(rendered) or excerpt_for_line_search(seg.text)
        if needle is None:
            continue
        found = line_range_for_needle(final_text, needle, min_line=search_from)
        if found is not None:
            lines[seg.id] = found
            search_from = found[1] + 1
    return lines


def build_segment_source_excerpts(
    segments: list[Segment],
    *,
    max_len: int = 240,
) -> dict[str, str]:
    """Map segment id → readable source-language preview for PR reports."""
    excerpts: dict[str, str] = {}
    for seg in segments:
        display = rendered_segment_text(seg, {}, placeholder_seg=seg)
        excerpt = _truncate_excerpt(re.sub(r"\s+", " ", display).strip(), max_len=max_len)
        if excerpt:
            excerpts[seg.id] = excerpt
    return excerpts


def build_segment_excerpts(
    final_text: str,
    segments: list[Segment],
    translations: dict[str, str],
    segment_lines: dict[str, tuple[int, int]],
    *,
    max_len: int = 100,
    placeholder_segments: list[Segment] | None = None,
) -> dict[str, str]:
    """Map segment id → short searchable preview for PR reports."""
    ph_segs = placeholder_segments or segments
    excerpts: dict[str, str] = {}
    for seg, ph_seg in zip(segments, ph_segs, strict=False):
        excerpt = segment_display_excerpt(
            seg,
            translations,
            final_text=final_text,
            line_range=segment_lines.get(seg.id),
            max_len=max_len,
            placeholder_seg=ph_seg,
        )
        if excerpt and excerpt_found_in_file(excerpt, final_text):
            excerpts[seg.id] = excerpt
    return excerpts


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
        parts.append(humanize_path_label(path_label))
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
