"""Detect and fix known bugs in Russian SOURCE before translate/QA."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CONFIG_DIR_SLUG_RE = re.compile(r"--config-dir/(\S+)")


@dataclass(frozen=True)
class RuSourceBug:
    """A defect in RU documentation that should be fixed in SOURCE, not masked in EN."""

    kind: str
    location: str
    detail: str
    suggested_fix: str


def detect_ru_source_bugs(ru_text: str, *, file_path: str = "") -> list[RuSourceBug]:
    """Find RU-only issues (EN should use the corrected form after RU is fixed)."""
    bugs: list[RuSourceBug] = []
    loc = file_path or "файл"
    for m in _CONFIG_DIR_SLUG_RE.finditer(ru_text):
        path = m.group(1)
        bugs.append(
            RuSourceBug(
                kind="config_dir_spacing",
                location=loc,
                detail=(
                    f"Опечатка `--config-dir/{path}` — в CLI нужен пробел: "
                    f"`--config-dir /{path}`."
                ),
                suggested_fix=f"--config-dir /{path}",
            )
        )
    return bugs


def fix_ru_config_dir_spacing(ru_text: str) -> str:
    """``--config-dir/path`` → ``--config-dir /path`` in RU SOURCE."""
    return _CONFIG_DIR_SLUG_RE.sub(r"--config-dir /\1", ru_text)


def fix_ru_source_bugs_in_text(
    ru_text: str, *, file_path: str = ""
) -> tuple[str, list[RuSourceBug]]:
    """Apply safe RU fixes; return updated text and bugs that were present."""
    bugs = detect_ru_source_bugs(ru_text, file_path=file_path)
    if not bugs:
        return ru_text, []
    return fix_ru_config_dir_spacing(ru_text), bugs


def format_ru_reviewer_suggestions(
    entries: list[tuple[str, list[RuSourceBug]]],
) -> str:
    """Markdown block for the translation PR report."""
    lines: list[str] = []
    for path, bugs in entries:
        if not bugs:
            continue
        lines.append(f"- `{path}`:")
        for b in bugs:
            lines.append(f"  - **{b.kind}:** {b.detail} → исправить в RU: `{b.suggested_fix}`")
    if not lines:
        return ""
    return "\n".join(
        [
            "### Предложения ревьюеру (исправить в RU SOURCE)",
            "",
            "Это баги русской документации; в EN после перевода должно быть "
            "`--config-dir /opt/ydb/cfg` (с пробелом), как в исправленном RU.",
            "",
            *lines,
        ]
    )
