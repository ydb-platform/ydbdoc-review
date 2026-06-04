"""Detect and fix known RU source typos before translation (fix in RU, not invent in EN)."""

from __future__ import annotations

import re

# Slit RU typo copied into EN when fences are preserved: --config-dir/opt
_CONFIG_DIR_GLUED_OPT = re.compile(r"--config-dir/opt")


def detect_ru_source_bugs(text: str) -> list[str]:
    """Human-readable issues to fix in RU SOURCE before merge."""
    issues: list[str] = []
    if _CONFIG_DIR_GLUED_OPT.search(text):
        issues.append(
            "ru_source: use `--config-dir /opt/...` not `--config-dir/opt/...` (missing space)"
        )
    return issues


def normalize_ru_source_for_translation(text: str) -> str:
    """Apply safe deterministic fixes to RU text in the workdir before translate."""
    text = _CONFIG_DIR_GLUED_OPT.sub("--config-dir /opt", text)
    return text


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
