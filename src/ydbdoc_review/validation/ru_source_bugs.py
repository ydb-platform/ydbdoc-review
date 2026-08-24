"""Detect and fix known RU source typos before translation (fix in RU, not invent in EN)."""

from __future__ import annotations

import re

# Slit RU typo copied into EN when fences are preserved: --config-dir/opt
_CONFIG_DIR_GLUED_OPT = re.compile(r"--config-dir/opt")
_FENCE_LINE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
_YFM_CONTAINER_LINE = re.compile(
    r"^\s*{%\s*(list\b[^%]*|endlist|cut\b[^%]*|endcut|if\b[^%]*|endif)\s*%}\s*$"
)
_OVERLAY_INCLUDE_LINE = re.compile(r"^(\s+)(?=\{%\s*include\s+\[overlay])")
_MARKDOWN_CONTAINER_BOUNDARY = re.compile(r"^(\s*)[-+*]\s+")
_YFM_OPEN = {"list": "endlist", "cut": "endcut", "if": "endif"}
_YFM_CLOSE = {closer: opener for opener, closer in _YFM_OPEN.items()}
_ORDERED_LIST_LINE = re.compile(r"^\s*\d+[.)]\s+")
_LEGACY_INDENTED_BULLET = re.compile(r"^ {4}([-+*]\s+.*)$")


def _normalize_pr_23186_indented_bullet_run(text: str) -> str:
    """Recover the four nested bullets from source PR #23186 / translation #50980.

    The source of #23186 has a run of four-space bullet lines immediately after
    continuation paragraphs in an ordered list.  Markdown-it classifies that
    run as ``IndentedCode``; after AST rendering it becomes a nested list and
    changes the segment count from 28 to 32.  Only normalize the unambiguous
    exact source actions seen there, outside fences and directly before a
    heading.  This is intentionally a named compatibility repair, not a generic
    guess: generic four-space bullet examples are valid indented code and must
    stay byte-for-byte unchanged.
    """
    lines = text.splitlines()
    in_fence: tuple[str, int] | None = None
    candidates: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        fence = _FENCE_LINE.match(lines[i])
        if fence:
            marker = fence.group(2)
            if in_fence is None:
                in_fence = (marker[0], len(marker))
            elif marker[0] == in_fence[0] and len(marker) >= in_fence[1]:
                in_fence = None
            i += 1
            continue
        if in_fence is not None or _LEGACY_INDENTED_BULLET.match(lines[i]) is None:
            i += 1
            continue

        start = i
        while i < len(lines) and _LEGACY_INDENTED_BULLET.match(lines[i]):
            i += 1
        end = i
        prev = start - 1
        while prev >= 0 and not lines[prev].strip():
            prev -= 1
        nxt = end
        while nxt < len(lines) and not lines[nxt].strip():
            nxt += 1
        ordered_before = any(
            _ORDERED_LIST_LINE.match(lines[j])
            for j in range(max(0, prev - 12), prev + 1)
        )
        prev_indent = len(lines[prev]) - len(lines[prev].lstrip()) if prev >= 0 else 0
        heading_after = nxt < len(lines) and lines[nxt].startswith("#")
        actions = [lines[idx][4:].lower() for idx in range(start, end)]
        pr_23186_signature = end - start == 4 and all(
            needle in action
            for needle, action in zip(
                ("добавляем новую группу колец", "снимаем флаг", "выставляем флаг", "удаляем старую группу колец"),
                actions,
                strict=True,
            )
        )
        if ordered_before and prev_indent >= 5 and heading_after and pr_23186_signature:
            candidates.append((start, end))

    for start, end in candidates:
        for idx in range(start, end):
            # Three spaces keep the bullets inside the preceding ordered item,
            # matching the structure produced by the Markdown renderer.
            lines[idx] = lines[idx][1:]
    result = "\n".join(lines)
    return result + "\n" if text.endswith("\n") else result


def normalize_legacy_markdown_structure(text: str) -> str:
    """Canonicalize unambiguous legacy syntax that Diplodoc rejects."""
    text = _normalize_pr_23186_indented_bullet_run(text)
    had_final_newline = text.endswith("\n")
    out: list[str] = []
    open_fence: tuple[str, int, str] | None = None
    yfm_stack: list[str] = []

    for line in text.splitlines():
        fence = _FENCE_LINE.match(line)
        if fence:
            marker = fence.group(2)
            if open_fence is None:
                open_fence = (marker[0], len(marker), fence.group(1))
            elif marker[0] == open_fence[0] and len(marker) >= open_fence[1]:
                # A closing fence cannot have an info string. Legacy RU has
                # several `````python`` closers which Diplodoc treats as opens.
                line = open_fence[2] + marker
                open_fence = None
                out.append(line)
                if fence.group(3).strip():
                    out.append("")
                continue
            out.append(line)
            continue
        if open_fence is not None:
            directive_in_fence = _YFM_CONTAINER_LINE.match(line)
            directive_in_fence_kind = (
                directive_in_fence.group(1).split(maxsplit=1)[0]
                if directive_in_fence is not None
                else None
            )
            boundary = _MARKDOWN_CONTAINER_BOUNDARY.match(line)
            if directive_in_fence and re.match(r"^\s*{%\s*endcut\s*%}\s*$", line) and (
                not yfm_stack or yfm_stack[-1] != "cut"
            ):
                # Some legacy pages contain ``endcut`` where the closing fence
                # was intended. Keeping it inside code leaves the fence open;
                # treating it as YFM would create an unmatched closer.
                out.append(open_fence[2] + open_fence[0] * open_fence[1])
                open_fence = None
                if boundary is not None:
                    out.append("")
                continue
            opener_indent = len(open_fence[2].expandtabs(4))
            boundary_indent = (
                len(boundary.group(1).expandtabs(4)) if boundary is not None else None
            )
            if directive_in_fence_kind in _YFM_CLOSE or (
                opener_indent > 0
                and boundary_indent is not None
                and boundary_indent <= opener_indent
            ):
                # A peer list item/heading or YFM control cannot belong to the
                # fenced body at the opener's indentation. Insert the missing
                # closer before processing that structural line.
                out.append(open_fence[2] + open_fence[0] * open_fence[1])
                open_fence = None
                if boundary is not None:
                    out.append("")
            else:
                out.append(line)
                continue

        directive = _YFM_CONTAINER_LINE.match(line)
        if directive:
            kind = directive.group(1).split(maxsplit=1)[0]
            if kind in _YFM_OPEN:
                yfm_stack.append(kind)
            elif kind in _YFM_CLOSE:
                expected = _YFM_CLOSE[kind]
                if yfm_stack and yfm_stack[-1] == expected:
                    yfm_stack.pop()
                elif kind == "endlist":
                    previous = next((item for item in reversed(out) if item.strip()), "")
                    if previous.strip() == line.strip() and (
                        len(previous) - len(previous.lstrip())
                        == len(line) - len(line.lstrip())
                    ):
                        while out and not out[-1].strip():
                            out.pop()
                        continue
                    continue

        overlay = _OVERLAY_INCLUDE_LINE.match(line)
        if overlay and len(overlay.group(1).expandtabs(4)) > 2:
            # Empty public overlay includes expand to indentation-only lines.
            # Two spaces are a valid hard break; four trigger MD009.
            line = "  " + line.lstrip()
        out.append(line)

    while out and not out[-1].strip():
        out.pop()
    result = "\n".join(out)
    return result + "\n" if had_final_newline else result


def detect_ru_source_bugs(text: str) -> list[str]:
    """Human-readable issues to fix in RU SOURCE before merge."""
    issues: list[str] = []
    if _CONFIG_DIR_GLUED_OPT.search(text):
        issues.append(
            "ru_source (исправьте в RU PR, не в EN): "
            "опечатка `--config-dir/opt/...` — нужен пробел `--config-dir /opt/...`"
        )
    return issues


def normalize_ru_source_for_translation(text: str) -> str:
    """Apply safe deterministic fixes to RU text in the workdir before translate."""
    text = _CONFIG_DIR_GLUED_OPT.sub("--config-dir /opt", text)
    return normalize_legacy_markdown_structure(text)


def check_required_anchor_lines(source_text: str, target_text: str) -> list[str]:
    """Prose/CLI anchors present in RU must appear in EN (catches dropped paragraphs)."""
    anchors = [
        "test -r /opt/ydb/certs/web.pem",
        "sudo -u ydb test -r",
    ]
    warnings: list[str] = []
    for anchor in anchors:
        if anchor in source_text and anchor not in target_text:
            warnings.append(
                f"missing_anchor: RU contains «{anchor}» but EN does not "
                "(paragraph likely dropped by translation)"
            )
    return warnings
