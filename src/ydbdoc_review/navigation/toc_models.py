"""Additive TOC merge models (§6.131) — do not replace public toc.py APIs yet.

``TocTranslateScope`` / ``merge_en_toc_yaml`` / ``validate_toc_merge`` remain the
stable facades. These types make scope / legacy aliases / issue severity
explicit for gradual refactor toward a unified AST.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ydbdoc_review.navigation.toc import TocTranslateScope, parse_toc_items


@dataclass(frozen=True)
class TocMergeScope:
    """Explicit RU→EN toc delta (richer view of ``TocTranslateScope``).

    ``added_*`` / ``modified_*`` feed translate scope. ``removed_*`` is
    informational today — merge still mirrors RU order and drops absent
    entries unless ``keep_en_hrefs`` (§6.112).
    """

    added_hrefs: frozenset[str]
    modified_hrefs: frozenset[str]
    added_includes: frozenset[str]
    modified_includes: frozenset[str]
    removed_hrefs: frozenset[str] = frozenset()
    removed_includes: frozenset[str] = frozenset()

    def to_translate_scope(self) -> TocTranslateScope:
        return TocTranslateScope(
            self.added_hrefs | self.modified_hrefs,
            self.added_includes | self.modified_includes,
        )


@dataclass(frozen=True)
class TocEntryMapping:
    """Correspondence between a RU toc entry and its EN mirror.

    ``legacy_aliases`` holds EN hrefs that cover the RU href under the
    name+en_main heuristic (§6.74 / #44942) — e.g. RU ``hive_config.md`` vs
    EN ``hive.md``.
    """

    ru_href: str
    en_href: str
    en_name: str
    legacy_aliases: frozenset[str] = field(default_factory=frozenset)


TocMergeIssueKind = Literal[
    "scope_not_applied",
    "orphan_ru_entry",
    "orphan_en_entry",
    "missing_href_target",
    "href_mismatch",
    "structure_mismatch",
    # Stable kinds still emitted by validate_toc_merge today:
    "collapsed_toc",
    "toc_structure_parity",
    "toc_en_only_legacy",
    "unexpected_href",
    "empty_toc",
    "inconsistent_indent",
]

TocMergeSeverity = Literal["INFO", "WARNING", "ERROR", "BLOCKING"]


@dataclass(frozen=True)
class TocMergeIssue:
    """Categorized toc merge finding (severity for docs / future pipeline)."""

    kind: TocMergeIssueKind | str
    detail: str
    severity: TocMergeSeverity = "WARNING"


# Map current validate_toc_merge kind strings → default severity.
_KIND_SEVERITY: dict[str, TocMergeSeverity] = {
    "scope_not_applied": "ERROR",
    "orphan_ru_entry": "ERROR",
    "collapsed_toc": "BLOCKING",
    "toc_structure_parity": "BLOCKING",
    "missing_href_target": "BLOCKING",
    "empty_toc": "BLOCKING",
    "unexpected_href": "ERROR",
    "inconsistent_indent": "WARNING",
    "toc_en_only_legacy": "WARNING",
    "orphan_en_entry": "WARNING",
    "href_mismatch": "WARNING",
    "structure_mismatch": "WARNING",
}


def severity_for_kind(kind: str) -> TocMergeSeverity:
    return _KIND_SEVERITY.get(kind, "WARNING")


def build_toc_merge_scope(ru_base_yaml: str, ru_pr_yaml: str) -> TocMergeScope:
    """Derive added/modified/removed href and include sets from RU base vs PR."""
    base_items = parse_toc_items(ru_base_yaml)
    pr_items = parse_toc_items(ru_pr_yaml)
    base_by_href = {it["href"]: it for it in base_items if it.get("href")}
    base_by_include = {
        it["include_path"]: it for it in base_items if it.get("include_path")
    }
    pr_hrefs = {it["href"] for it in pr_items if it.get("href")}
    pr_includes = {it["include_path"] for it in pr_items if it.get("include_path")}

    added_hrefs: set[str] = set()
    modified_hrefs: set[str] = set()
    added_includes: set[str] = set()
    modified_includes: set[str] = set()

    for it in pr_items:
        href = it.get("href")
        include_path = it.get("include_path")
        if href:
            prev = base_by_href.get(href)
            if prev is None:
                added_hrefs.add(href)
            elif prev.get("name", "") != it.get("name", ""):
                modified_hrefs.add(href)
        if include_path:
            prev = base_by_include.get(include_path)
            if prev is None:
                added_includes.add(include_path)
            elif prev.get("name", "") != it.get("name", ""):
                modified_includes.add(include_path)

    return TocMergeScope(
        added_hrefs=frozenset(added_hrefs),
        modified_hrefs=frozenset(modified_hrefs),
        added_includes=frozenset(added_includes),
        modified_includes=frozenset(modified_includes),
        removed_hrefs=frozenset(set(base_by_href) - pr_hrefs),
        removed_includes=frozenset(set(base_by_include) - pr_includes),
    )


def build_toc_entry_mappings(
    ru_items: list[dict[str, str]],
    en_items: list[dict[str, str]],
    *,
    en_main_hrefs: set[str],
) -> list[TocEntryMapping]:
    """Build RU→EN href mappings (exact + legacy name/en_main aliases)."""
    en_by_href = {it["href"]: it for it in en_items if it.get("href")}
    mappings: list[TocEntryMapping] = []
    for rit in ru_items:
        ru_href = rit.get("href")
        if not ru_href:
            continue
        if ru_href in en_by_href:
            en_it = en_by_href[ru_href]
            mappings.append(
                TocEntryMapping(
                    ru_href=ru_href,
                    en_href=ru_href,
                    en_name=en_it.get("name", "") or "",
                )
            )
            continue
        name = rit.get("name")
        if not name:
            continue
        aliases: set[str] = set()
        en_name = ""
        en_href = ""
        for en_it in en_items:
            eh = en_it.get("href")
            if en_it.get("name") == name and eh and eh in en_main_hrefs:
                aliases.add(eh)
                en_href = eh
                en_name = en_it.get("name", "") or ""
        if aliases:
            mappings.append(
                TocEntryMapping(
                    ru_href=ru_href,
                    en_href=en_href,
                    en_name=en_name,
                    legacy_aliases=frozenset(aliases),
                )
            )
    return mappings


def mapping_covers_ru_href(
    mappings: list[TocEntryMapping],
    ru_href: str,
) -> bool:
    """True when ``ru_href`` has an exact or legacy EN mapping."""
    for m in mappings:
        if m.ru_href == ru_href:
            return True
    return False
