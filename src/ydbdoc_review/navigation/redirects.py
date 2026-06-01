"""Diplodoc redirect/preservation YAML — parse, diff-scoped merge, validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_ENTRY_SPLIT = re.compile(r"(?m)^- from: ")
_FROM_LINE = re.compile(r"^- from: (.+)$", re.MULTILINE)
_TO_LINE = re.compile(r"^  to: (.+)$", re.MULTILINE)


def parse_redirect_entries(yaml_text: str) -> list[dict[str, str]]:
    """Return ``[{from_path, to_path, block}, ...]``."""
    text = yaml_text.replace("\r\n", "\n")
    if not text.strip():
        return []
    parts = _ENTRY_SPLIT.split(text)
    entries: list[dict[str, str]] = []
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        block = "- from: " + chunk
        m_from = _FROM_LINE.search(block)
        m_to = _TO_LINE.search(block)
        if not m_from or not m_to:
            continue
        entries.append(
            {
                "from_path": m_from.group(1).strip(),
                "to_path": m_to.group(1).strip(),
                "block": block.rstrip() + "\n",
            }
        )
    return entries


def redirect_translate_scope(ru_base_yaml: str, ru_pr_yaml: str) -> set[str]:
    """``from`` keys whose ``to`` target must be synced for this PR.

    Scope = new redirect entries or entries whose ``to`` changed in RU PR.
    """
    base_by_from = {e["from_path"]: e for e in parse_redirect_entries(ru_base_yaml)}
    scope: set[str] = set()
    for entry in parse_redirect_entries(ru_pr_yaml):
        src = entry["from_path"]
        prev = base_by_from.get(src)
        if prev is None or prev["to_path"] != entry["to_path"]:
            scope.add(src)
    return scope


def merge_en_redirects_yaml(
    en_main_yaml: str,
    ru_pr_yaml: str,
    *,
    translate_from_paths: set[str],
    translate_to: Callable[[str], str] | None = None,
) -> str:
    """Build EN redirects from RU PR with strict scope.

    Redirect ``to`` paths are usually language-neutral (same slug). When
    ``translate_to`` is None, RU ``to`` is copied verbatim. Only entries in
    ``translate_from_paths`` are taken from RU; others keep EN-main blocks.
    """
    en_by_from = {e["from_path"]: e for e in parse_redirect_entries(en_main_yaml)}
    ru_entries = parse_redirect_entries(ru_pr_yaml)
    ru_froms = {e["from_path"] for e in ru_entries}
    merged: list[dict[str, str]] = []
    seen: set[str] = set()

    for rent in ru_entries:
        src = rent["from_path"]
        if src in seen:
            continue
        seen.add(src)
        if src in en_by_from and src not in translate_from_paths:
            merged.append(en_by_from[src])
        elif src in translate_from_paths:
            to_val = rent["to_path"]
            if translate_to is not None:
                to_val = translate_to(to_val).strip()
            merged.append(
                {
                    "from_path": src,
                    "to_path": to_val,
                    "block": _replace_to_path(rent["block"], to_val),
                }
            )

    for entry in parse_redirect_entries(en_main_yaml):
        if entry["from_path"] not in seen and entry["from_path"] not in ru_froms:
            merged.append(entry)

    return _serialize_redirects(merged)


def _replace_to_path(block: str, new_to: str) -> str:
    return re.sub(r"(?m)^  to: .+$", f"  to: {new_to}", block, count=1)


def _serialize_redirects(entries: list[dict[str, str]]) -> str:
    body = "".join(e["block"] for e in entries)
    if not body.endswith("\n"):
        body += "\n"
    return body


@dataclass(frozen=True)
class RedirectValidationIssue:
    kind: str
    detail: str


def validate_redirect_merge(
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    translate_from_paths: set[str],
    en_main_yaml: str,
) -> list[RedirectValidationIssue]:
    """Heuristic checks after redirect merge (Phase E hook)."""
    issues: list[RedirectValidationIssue] = []
    ru_froms = {e["from_path"] for e in parse_redirect_entries(ru_pr_yaml)}
    en_froms = {e["from_path"] for e in parse_redirect_entries(en_merged_yaml)}
    en_main_froms = {e["from_path"] for e in parse_redirect_entries(en_main_yaml)}

    unexpected = en_froms - ru_froms - en_main_froms
    if unexpected:
        issues.append(
            RedirectValidationIssue(
                kind="unexpected_from",
                detail=f"EN redirects have entries not in RU PR: {sorted(unexpected)}",
            )
        )

    missing = ru_froms - en_froms
    if missing:
        issues.append(
            RedirectValidationIssue(
                kind="missing_from",
                detail=f"RU PR redirect keys missing from EN: {sorted(missing)}",
            )
        )

    for src in translate_from_paths:
        if src not in en_froms:
            issues.append(
                RedirectValidationIssue(
                    kind="scope_not_applied",
                    detail=f"from {src!r} was in scope but missing from EN redirects",
                )
            )

    return issues
