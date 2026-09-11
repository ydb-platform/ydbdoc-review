"""Diplodoc redirect/preservation YAML — parse, diff-scoped merge, validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import unquote

_ENTRY_SPLIT = re.compile(r"(?m)^- from: ")
_FROM_LINE = re.compile(r"^- from: (.+)$", re.MULTILINE)
_TO_LINE = re.compile(r"^  to: (.+)$", re.MULTILINE)
_PREFIX_FROM = re.compile(
    r"^\^?/((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+)/\(\.\*\)\$$"
)
_PREFIX_TO = re.compile(r"^/((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+)/\$1$")


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


@lru_cache(maxsize=8)
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


@lru_cache(maxsize=8)
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


def _redirect_rows_for_locale(
    redirects_yaml: str,
    *,
    locale: str,
) -> list[tuple[str, str]]:
    """Return common plus selected-locale rows without collapsing conflicts."""
    text = (redirects_yaml or "").strip()
    if not text:
        return []
    try:
        import yaml

        data = yaml.safe_load(text)
    except Exception:
        data = None
    rows: list[tuple[str, str]] = []
    if isinstance(data, dict):
        sections = ("common", locale) if locale in {"ru", "en"} else ("common",)
        for key in sections:
            for row in data.get(key) or []:
                if isinstance(row, dict) and row.get("from") is not None:
                    rows.append(
                        (
                            str(row["from"]).strip(),
                            str(row.get("to") or "").strip(),
                        )
                    )
        return rows
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("from") is not None:
                rows.append(
                    (
                        str(row["from"]).strip(),
                        str(row.get("to") or "").strip(),
                    )
                )
        return rows
    return [
        (entry["from_path"], entry["to_path"])
        for entry in parse_redirect_entries(text)
    ]


def _safe_literal_prefix(value: str) -> bool:
    return bool(value) and all(
        segment not in {"", ".", ".."}
        and re.fullmatch(r"[A-Za-z0-9_.-]+", segment) is not None
        for segment in value.split("/")
    )


def _safe_md_suffix(value: str) -> bool:
    if (
        not value
        or not value.endswith(".md")
        or "\\" in value
        or "?" in value
        or "#" in value
    ):
        return False
    decoded = unquote(value)
    if "/" in decoded.replace(value, "", 1) or "\\" in decoded:
        return False
    return all(segment not in {"", ".", ".."} for segment in decoded.split("/"))


def _repo_md_locale_public(
    repo_md: str,
    *,
    docs_root: str,
) -> tuple[str, str, str] | None:
    path = repo_md.replace("\\", "/")
    root = docs_root.strip("/")
    for locale in ("ru", "en"):
        prefix = f"{root}/{locale}/core/"
        if not path.startswith(prefix):
            continue
        suffix = path[len(prefix) :]
        if not _safe_md_suffix(suffix):
            return None
        return path, locale, f"/{suffix}"
    return None


def prefix_redirect_repo_md_path(
    repo_md: str,
    redirects_yaml: str,
    *,
    docs_root: str = "ydb/docs",
) -> str | None:
    """Return one proven prefix step, unchanged for no match, None for refusal."""
    identity = _repo_md_locale_public(repo_md, docs_root=docs_root)
    if identity is None:
        return None
    path, locale, public = identity
    matches: list[tuple[int, str, str]] = []
    for source, destination in _redirect_rows_for_locale(
        redirects_yaml, locale=locale
    ):
        source_match = _PREFIX_FROM.fullmatch(source)
        if source_match is None:
            continue
        source_prefix = source_match.group(1)
        if not _safe_literal_prefix(source_prefix):
            continue
        marker = f"/{source_prefix}/"
        if not public.startswith(marker):
            continue
        matches.append((len(source_prefix), source_prefix, destination))
    if not matches:
        return path

    longest = max(length for length, _source, _destination in matches)
    selected = [row for row in matches if row[0] == longest]
    destinations: set[str] = set()
    source_prefix = selected[0][1]
    suffix = public[len(source_prefix) + 2 :]
    if not _safe_md_suffix(suffix):
        return None
    for _length, _source, destination in selected:
        destination_match = _PREFIX_TO.fullmatch(destination)
        if destination_match is None:
            return None
        destination_prefix = destination_match.group(1)
        if not _safe_literal_prefix(destination_prefix):
            return None
        destinations.add(destination_prefix)
    if len(destinations) != 1:
        return None
    destination_prefix = destinations.pop()
    result = redirect_public_path_to_repo_md(
        f"/{destination_prefix}/{suffix}",
        locale=locale,
        docs_root=docs_root,
    )
    if result == path or _repo_md_locale_public(result, docs_root=docs_root) is None:
        return None
    return result


def redirect_source_repo_md_paths(
    redirects_yaml: str,
    *,
    locale: str,
    docs_root: str = "ydb/docs",
    candidate_repo_paths: tuple[str, ...] | list[str] | set[str] | frozenset[str] = (),
) -> frozenset[str]:
    """Repo ``.md`` paths that appear as redirect ``from`` keys.

    Diplodoc ``from`` values are locale-neutral public paths under ``core/``.
    RU tombstones often remain on disk for content history while EN never had
    a mirror; translating them creates ``orphan_toc_page`` EN files (#45949).
    """
    literal_paths = {
        redirect_public_path_to_repo_md(public, locale=locale, docs_root=docs_root)
        for public in iter_redirect_from_paths(redirects_yaml)
        if public.startswith("/")
        and _PREFIX_FROM.fullmatch(public) is None
        and _safe_md_suffix(public.removeprefix("/"))
    }
    for candidate in candidate_repo_paths:
        normalized = candidate.replace("\\", "/")
        identity = _repo_md_locale_public(normalized, docs_root=docs_root)
        if identity is None or identity[1] != locale:
            continue
        redirected = prefix_redirect_repo_md_path(
            normalized,
            redirects_yaml,
            docs_root=docs_root,
        )
        if redirected is None or redirected != normalized:
            literal_paths.add(normalized)
    return frozenset(literal_paths)


def repo_md_to_public_path(repo_md: str, *, docs_root: str = "ydb/docs") -> str | None:
    """``ydb/docs/{ru|en}/core/foo.md`` → ``/foo.md`` (Diplodoc public path)."""
    p = repo_md.replace("\\", "/")
    root = docs_root.strip("/")
    for locale in ("ru", "en"):
        prefix = f"{root}/{locale}/core"
        if p.startswith(prefix + "/") or p == prefix:
            rest = p[len(prefix) :]
            if not rest.startswith("/"):
                rest = "/" + rest
            return rest
    return None


def follow_redirect_repo_md_path(
    repo_md: str,
    redirects_yaml: str,
    *,
    docs_root: str = "ydb/docs",
) -> str:
    """If ``repo_md`` is a redirect ``from``, return the same-locale ``to`` twin.

    Otherwise return ``repo_md`` unchanged. Used so merged-PR scope/fragment
    owners follow tip live paths instead of historical tombstones (§6.242).
    """
    p = repo_md.replace("\\", "/")
    public = repo_md_to_public_path(p, docs_root=docs_root)
    if public is None:
        return p
    to_public = iter_redirect_mappings(redirects_yaml).get(public)
    if not to_public:
        return p
    root = docs_root.strip("/")
    if p.startswith(f"{root}/en/"):
        locale = "en"
    elif p.startswith(f"{root}/ru/"):
        locale = "ru"
    else:
        return p
    return redirect_public_path_to_repo_md(to_public, locale=locale, docs_root=docs_root)


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
