"""Deterministic bounded dependency closure for one-pass translation."""

from __future__ import annotations

import posixpath
import subprocess
from collections import deque
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from ydbdoc_review.parsing.ast_types import InlineLink, SourceSpan, YfmInclude
from ydbdoc_review.parsing.markdown_parser import parse_markdown


@dataclass(frozen=True)
class QueueEntry:
    ru_path: str
    origin: str


@dataclass(frozen=True)
class DependencyOccurrence:
    source_file: str
    edge_kind: str
    raw_destination: str
    source_span: SourceSpan


@dataclass(frozen=True)
class UnresolvedDependency:
    category: str
    source_file: str
    output_file: str
    original_href: str
    resolved_ru_target: str | None
    resolved_en_target: str
    reason: str
    manual_action: str
    dependency_kind: str
    occurrences: tuple[DependencyOccurrence, ...] = ()


@dataclass(frozen=True)
class DependencyPlan:
    entries: tuple[QueueEntry, ...]
    unresolved: tuple[UnresolvedDependency, ...]
    initial_count: int
    auto_added_count: int


def paths_at_tree(repo_path: str, source_tree_sha: str) -> set[str]:
    """Return the immutable path-existence snapshot for a git tree."""
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", source_tree_sha],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(completed.stdout.splitlines())


def _canonical(source: str, href: str, docs_root: str) -> str | None:
    split = urlsplit(href)
    if split.scheme or href.startswith("//") or not split.path or split.path.startswith("#") or not unquote(split.path).endswith(".md"):
        return None
    path = unquote(split.path)
    if path.startswith("/ru/"):
        out = f"{docs_root.rstrip('/')}{path}"
    elif path.startswith("/"):
        out = f"{docs_root.rstrip('/')}/{path.lstrip('/')}"
    else:
        out = posixpath.join(posixpath.dirname(source), path)
    out = posixpath.normpath(out)
    ru_root = f"{docs_root.strip('/')}/ru"
    if not out.startswith(ru_root + "/"):
        raise ValueError("invalid_internal_target")
    return out


def _en_path(ru_path: str, docs_root: str) -> str:
    return ru_path.replace(f"{docs_root.strip('/')}/ru/", f"{docs_root.strip('/')}/en/", 1)


def parser_link_edge_walker(source_file: str, text: str) -> tuple[DependencyOccurrence, ...]:
    """Return parser-owned link/include edges, in their source occurrence order."""
    document = parse_markdown(text)
    found: list[DependencyOccurrence] = []

    def walk(value: object) -> None:
        if isinstance(value, InlineLink):
            if value.source_span is not None:
                found.append(DependencyOccurrence(source_file, "link", value.href, value.source_span))
            for child in value.children:
                walk(child)
            return
        if isinstance(value, YfmInclude):
            if value.source_span is not None:
                found.append(DependencyOccurrence(source_file, "include", value.path, value.source_span))
            return
        if isinstance(value, list):
            for child in value:
                walk(child)
            return
        if isinstance(value, tuple):
            for child in value:
                walk(child)
            return
        if not hasattr(value, "kind"):
            return
        for field in ("children", "header", "rows", "cells", "branches"):
            child = getattr(value, field, None)
            if child is not None:
                walk(child)

    walk(document.children)
    return tuple(
        sorted(
            {
                (edge.source_file, edge.edge_kind, edge.source_span.byte_start, edge.source_span.byte_end): edge
                for edge in found
            }.values(),
            key=lambda edge: (
                edge.source_file,
                edge.edge_kind,
                edge.source_span.byte_start,
                edge.source_span.byte_end,
            ),
        )
    )


def plan_dependency_queue(initial_paths: list[str], *, read_ru, ru_exists, en_paths: set[str], docs_root: str = "ydb/docs", budget: int = 20) -> DependencyPlan:
    initial = list(dict.fromkeys(sorted(initial_paths)))
    queue = deque(QueueEntry(p, "initial") for p in initial)
    entries: list[QueueEntry] = []
    seen = set(initial)
    unresolved: dict[tuple[str, str], list[DependencyOccurrence]] = {}
    unresolved_details: dict[tuple[str, str], tuple[str, str, str | None, str]] = {}
    auto = 0
    while queue:
        entry = queue.popleft()
        entries.append(entry)
        text = read_ru(entry.ru_path)
        discovered: list[tuple[str, DependencyOccurrence]] = []
        for occurrence in parser_link_edge_walker(entry.ru_path, text):
            href = occurrence.raw_destination
            try:
                target = _canonical(entry.ru_path, href, docs_root)
            except ValueError:
                key = (href, "invalid_internal_target")
                unresolved.setdefault(key, []).append(occurrence)
                unresolved_details.setdefault(
                    key,
                    ("invalid_internal_target", href, None, ""),
                )
                continue
            if target is not None:
                discovered.append((target, occurrence))

        admitted_here: set[str] = set()
        for target, occurrence in sorted(
            discovered,
            key=lambda item: (item[0], item[1].source_span.byte_start, item[1].source_span.byte_end),
        ):
            if target in seen or _en_path(target, docs_root) in en_paths:
                continue
            reason = None
            if not ru_exists(target):
                reason = "missing_source"
            elif auto >= budget:
                reason = "budget_exceeded"
            if reason:
                key = (target, reason)
                unresolved.setdefault(key, []).append(occurrence)
                unresolved_details.setdefault(
                    key,
                    (
                        "unresolved_translation_dependency",
                        occurrence.raw_destination,
                        target if reason != "missing_source" else None,
                        _en_path(target, docs_root),
                    ),
                )
                continue
            if target in admitted_here:
                continue
            seen.add(target)
            admitted_here.add(target)
            auto += 1
            queue.append(QueueEntry(target, "auto_added"))
    unresolved_items: list[UnresolvedDependency] = []
    for (target, reason), occurrences in sorted(unresolved.items()):
        category, original_href, resolved_ru_target, resolved_en_target = unresolved_details[(target, reason)]
        ordered = tuple(
            sorted(
                occurrences,
                key=lambda edge: (
                    edge.source_file,
                    edge.edge_kind,
                    edge.source_span.byte_start,
                    edge.source_span.byte_end,
                ),
            )
        )
        unresolved_items.append(
            UnresolvedDependency(
                category,
                ordered[0].source_file,
                _en_path(ordered[0].source_file, docs_root),
                original_href,
                resolved_ru_target,
                resolved_en_target,
                reason,
                "translate/add the named RU target, fix the href, or explicitly add an EN counterpart",
                ordered[0].edge_kind,
                ordered,
            )
        )
    return DependencyPlan(tuple(entries), tuple(unresolved_items), len(initial), auto)
