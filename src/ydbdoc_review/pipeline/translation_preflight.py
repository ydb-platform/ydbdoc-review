"""Deterministic checks before translation model work."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ydbdoc_review.navigation.paths import navigation_yaml_kind
from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    navigation_pairs_from_plan,
    planned_toc_extras_for_pair,
)
from ydbdoc_review.navigation.toc import (
    collect_toc_link_targets,
    merge_en_toc_yaml,
    parse_toc_items,
    resolve_toc_target_path,
    toc_reordered_shared_hrefs,
)
from ydbdoc_review.parsing.include_paths import (
    collect_yfm_includes,
    resolve_locale_md_path,
)
from ydbdoc_review.pipeline.navigation_merge import _resolve_toc_merge_scope
from ydbdoc_review.pipeline.pairs import counterpart
from ydbdoc_review.validation.glossary_toc_links import (
    collect_en_toc_reachable_md,
    normalize_repo_path,
)


@dataclass(frozen=True)
class PreflightResult:
    blockers: tuple[str, ...]
    deferred_checks: tuple[str, ...]


def _append_once(messages: list[str], message: str) -> None:
    if message not in messages:
        messages.append(message)


def _en_path(ru_path: str, docs_root: str) -> str | None:
    path = counterpart(ru_path, docs_root)
    return normalize_repo_path(path) if path is not None else None


def _reachable_tocs(
    read_text: Callable[[str], str | None],
    *,
    root_toc: str,
) -> frozenset[str]:
    """Return sidebar YAML reachable from the one real locale root."""
    queue = deque([normalize_repo_path(root_toc)])
    seen: set[str] = set()
    while queue:
        toc_path = queue.popleft()
        if toc_path in seen:
            continue
        seen.add(toc_path)
        text = read_text(toc_path)
        if text is None:
            continue
        for kind, rel in collect_toc_link_targets(text):
            if kind != "include":
                continue
            child = normalize_repo_path(resolve_toc_target_path(toc_path, rel))
            if child not in seen:
                queue.append(child)
    return frozenset(seen)


def _reachable_include_docs(
    read_text: Callable[[str], str | None],
    *,
    toc_reachable: frozenset[str],
    docs_root: str,
) -> frozenset[str]:
    """Close real locale-relative YFM includes from TOC-reachable pages."""
    locale_root = f"{docs_root.strip('/')}/en/"
    queue = deque(sorted(toc_reachable))
    seen: set[str] = set()
    reachable: set[str] = set()
    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        text = read_text(path)
        if text is None:
            continue
        reachable.add(path)
        for include in collect_yfm_includes(text):
            resolved = resolve_locale_md_path(
                path,
                include.path,
                docs_root=docs_root,
            )
            if resolved is None:
                continue
            target = normalize_repo_path(resolved)
            if not target.startswith(locale_root) or target in seen:
                continue
            if read_text(target) is not None:
                queue.append(target)
    return frozenset(reachable)


def preflight_translation(
    plan: TranslationScopePlan,
    *,
    read_ru: Callable[[str], str | None],
    read_ru_base: Callable[[str], str | None],
    read_en_base: Callable[[str], str | None],
    docs_root: str = "ydb/docs",
) -> PreflightResult:
    """Validate frozen mandatory inputs and simulated EN topology without a model."""
    blockers: list[str] = []
    active_ru_docs = frozenset(plan.doc_ru_paths - plan.doc_deleted)
    planned_en_to_ru = {
        en_path: ru_path
        for ru_path in active_ru_docs
        if (en_path := _en_path(ru_path, docs_root)) is not None
    }

    for path in sorted(active_ru_docs):
        source = read_ru(path)
        if source is None:
            _append_once(blockers, f"missing_source: {path}")
            continue
        for include in collect_yfm_includes(source):
            target_ru = resolve_locale_md_path(
                path,
                include.path,
                docs_root=docs_root,
            )
            if target_ru is None or "/ru/" not in target_ru:
                continue
            target_ru = normalize_repo_path(target_ru)
            target_en = _en_path(target_ru, docs_root)
            if read_ru(target_ru) is None:
                _append_once(
                    blockers,
                    "missing_include_source: "
                    f"{path} includes missing mandatory RU source {target_ru}",
                )
            elif (
                target_en is not None
                and target_ru not in active_ru_docs
                and read_en_base(target_en) is None
            ):
                _append_once(
                    blockers,
                    "missing_include_dependency: "
                    f"{path} requires unplanned EN include {target_en}",
                )

    simulated_tocs: dict[str, str] = {}
    planned_en_tocs: set[str] = set()
    mandatory_toc_targets: set[str] = set()
    for pair in navigation_pairs_from_plan(plan, docs_root=docs_root):
        if navigation_yaml_kind(pair.ru_path) != "toc":
            continue
        planned_en_tocs.add(normalize_repo_path(pair.en_path))
        ru_pr = read_ru(pair.ru_path)
        if ru_pr is None:
            _append_once(
                blockers,
                f"missing_navigation_source: {pair.ru_path}",
            )
            continue
        ru_base = read_ru_base(pair.ru_path) or ""
        en_main = read_en_base(pair.en_path) or ""
        try:
            extra_hrefs, extra_includes = planned_toc_extras_for_pair(
                plan,
                pair.ru_path,
                ru_pr,
                docs_root=docs_root,
                active_doc_ru_paths=active_ru_docs,
            )
            scope, restrict_gap_fill = _resolve_toc_merge_scope(
                pair,
                ru_base=ru_base,
                ru_pr=ru_pr,
                en_main=en_main,
                pair_extra_hrefs=extra_hrefs,
                pair_extra_includes=extra_includes,
            )
            en_main_hrefs = {
                item["href"]
                for item in parse_toc_items(en_main)
                if item.get("href")
            }
            ru_base_hrefs = {
                item["href"]
                for item in parse_toc_items(ru_base)
                if item.get("href")
            }
            ru_base_includes = {
                item["include_path"]
                for item in parse_toc_items(ru_base)
                if item.get("include_path")
            }
            if restrict_gap_fill:
                for href in toc_reordered_shared_hrefs(ru_base, ru_pr):
                    target = normalize_repo_path(
                        resolve_toc_target_path(pair.en_path, href)
                    )
                    if (
                        href not in en_main_hrefs
                        and href not in scope.hrefs
                        and read_en_base(target) is not None
                    ):
                        scope = scope.with_extra_hrefs({href})
            removed_ru_hrefs = ru_base_hrefs - {
                item["href"]
                for item in parse_toc_items(ru_pr)
                if item.get("href")
            }
            keep_en_hrefs = {
                href
                for href in en_main_hrefs
                if href not in removed_ru_hrefs
                and read_en_base(
                    normalize_repo_path(resolve_toc_target_path(pair.en_path, href))
                )
                is not None
            }
            mandatory_toc_targets.update(
                normalize_repo_path(resolve_toc_target_path(pair.en_path, href))
                for href in scope.hrefs
            )
            mandatory_toc_targets.update(
                normalize_repo_path(resolve_toc_target_path(pair.en_path, include))
                for include in scope.include_paths
            )
            simulated_tocs[normalize_repo_path(pair.en_path)] = merge_en_toc_yaml(
                en_main,
                ru_pr,
                translate_hrefs=set(scope.hrefs),
                translate_name=lambda name: name,
                ru_base_hrefs=ru_base_hrefs,
                translate_include_paths=set(scope.include_paths),
                ru_base_include_paths=ru_base_includes,
                restrict_gap_fill_to_scope=restrict_gap_fill,
                keep_en_hrefs=keep_en_hrefs,
            )
        except (KeyError, TypeError, ValueError) as exc:
            _append_once(
                blockers,
                f"invalid_navigation_source: {pair.ru_path}: {exc}",
            )

    deferred = [
        f"generated_output_validation: {path}"
        for path in sorted(planned_en_to_ru)
    ]
    deferred.extend(
        f"generated_navigation_validation: {path}"
        for path in sorted(planned_en_tocs)
    )
    if not planned_en_tocs:
        return PreflightResult(
            blockers=tuple(blockers),
            deferred_checks=tuple(deferred),
        )

    def read_simulated_en(path: str) -> str | None:
        normalized = normalize_repo_path(path)
        if normalized in simulated_tocs:
            return simulated_tocs[normalized]
        source_path = planned_en_to_ru.get(normalized)
        if source_path is not None:
            return read_ru(source_path)
        return read_en_base(normalized)

    for toc_path, text in sorted(simulated_tocs.items()):
        for kind, rel in collect_toc_link_targets(text):
            target = normalize_repo_path(resolve_toc_target_path(toc_path, rel))
            if kind == "href" and target not in planned_en_to_ru:
                continue
            if (
                kind == "include"
                and target not in mandatory_toc_targets
                and target not in planned_en_tocs
            ):
                continue
            if read_simulated_en(target) is None:
                _append_once(
                    blockers,
                    "missing_toc_target: "
                    f"EN toc {toc_path} {kind} {rel} points to missing {target}",
                )

    root_toc = f"{docs_root.strip('/')}/en/core/toc_p.yaml"
    if read_simulated_en(root_toc) is None:
        _append_once(blockers, f"missing_toc_root: {root_toc}")
    reachable_tocs = _reachable_tocs(read_simulated_en, root_toc=root_toc)
    for toc_path in sorted(planned_en_tocs - reachable_tocs):
        _append_once(blockers, f"disconnected_sidebar: {toc_path}")

    planned_en_docs = frozenset(planned_en_to_ru)
    toc_reachable = collect_en_toc_reachable_md(
        read_simulated_en,
        root_toc=root_toc,
        extra_md_paths=planned_en_docs,
        seed_extra_md=False,
    )
    include_reachable = _reachable_include_docs(
        read_simulated_en,
        toc_reachable=toc_reachable,
        docs_root=docs_root,
    )
    reachable_docs = toc_reachable | include_reachable
    for path in sorted(planned_en_docs - reachable_docs):
        _append_once(
            blockers,
            "orphan_toc_page: "
            f"planned EN page {path} is not reachable from {root_toc}",
        )

    return PreflightResult(
        blockers=tuple(blockers),
        deferred_checks=tuple(deferred),
    )
