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


def iter_redirect_from_paths(redirects_yaml: str) -> set[str]:
    """Collect Diplodoc redirect ``from`` public paths from full ``redirects.yaml``.

    Production files use ``common`` / ``ru`` / ``en`` sections with indented
    entries. Flat ``- from:`` lists (unit fixtures / merge payloads) are also
    accepted. ``parse_redirect_entries`` only matches unindented ``^- from:``.
    """
    text = (redirects_yaml or "").strip()
    if not text:
        return set()
    try:
        import yaml

        data = yaml.safe_load(text)
    except Exception:
        data = None
    out: set[str] = set()
    if isinstance(data, dict):
        for key in ("common", "ru", "en"):
            for row in data.get(key) or []:
                if isinstance(row, dict) and row.get("from"):
                    out.add(str(row["from"]).strip())
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("from"):
                out.add(str(row["from"]).strip())
    if out:
        return out
    # Flat merge payloads / fixtures without a YAML mapping root.
    return {e["from_path"] for e in parse_redirect_entries(text)}


def iter_redirect_mappings(redirects_yaml: str) -> dict[str, str]:
    """Collect Diplodoc public ``from`` → ``to`` mappings.

    Accepts production ``common`` / ``ru`` / ``en`` sections and the flat
    redirect lists used by merge payloads and unit fixtures.
    """
    text = (redirects_yaml or "").strip()
    if not text:
        return {}
    try:
        import yaml

        data = yaml.safe_load(text)
    except Exception:
        data = None
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for key in ("common", "ru", "en"):
            for row in data.get(key) or []:
                if isinstance(row, dict) and row.get("from") and row.get("to"):
                    out[str(row["from"]).strip()] = str(row["to"]).strip()
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("from") and row.get("to"):
                out[str(row["from"]).strip()] = str(row["to"]).strip()
    if out:
        return out
    return {entry["from_path"]: entry["to_path"] for entry in parse_redirect_entries(text)}


def redirect_public_path_to_repo_md(
    public_path: str,
    *,
    locale: str,
    docs_root: str = "ydb/docs",
) -> str:
    """Map Diplodoc ``/maintenance/manual/foo.md`` → repo locale md path."""
    p = public_path.strip().replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    root = docs_root.strip("/")
    return f"{root}/{locale}/core{p}"


def redirect_source_repo_md_paths(
    redirects_yaml: str,
    *,
    locale: str,
    docs_root: str = "ydb/docs",
) -> frozenset[str]:
    """Repo ``.md`` paths that appear as redirect ``from`` keys.

    Diplodoc ``from`` values are locale-neutral public paths under ``core/``.
    RU tombstones often remain on disk for content history while EN never had
    a mirror; translating them creates ``orphan_toc_page`` EN files (#45949).
    """
    return frozenset(
        redirect_public_path_to_repo_md(public, locale=locale, docs_root=docs_root)
        for public in iter_redirect_from_paths(redirects_yaml)
    )


REDIRECT_TOMBSTONE_SKIP_SUMMARY = (
    "redirect tombstone — skip EN at redirects.yaml from path (live page is to)"
)


def should_skip_redirect_tombstone_en(
    en_path: str,
    *,
    redirect_source_en_paths: frozenset[str] | set[str],
    en_toc_reachable: frozenset[str] | set[str] | None = None,
) -> bool:
    """True when EN at a redirect ``from`` path must not be created/updated.

    Live content is the redirect ``to`` target. Never write EN at ``from`` —
    that is how #51703 produced ``orphan_toc_page`` for
    ``maintenance/manual/dynamic-config.md``.

    ``en_toc_reachable`` is ignored: the translate-time reachable set seeds
    pending pair targets (``seed_extra_md=True``), so a tombstone about to be
    created would look "reachable" and defeat the skip.
    """
    del en_toc_reachable
    return en_path.replace("\\", "/") in redirect_source_en_paths


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
