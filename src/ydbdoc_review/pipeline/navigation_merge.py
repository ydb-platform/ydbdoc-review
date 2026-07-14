"""Scoped merge of Diplodoc toc/redirect YAML for doc_translate."""

from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath

from ydbdoc_review.config.loader import Config
from ydbdoc_review.github.git_ops import merge_base, read_text, read_text_at_ref
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.navigation.paths import navigation_yaml_kind
from ydbdoc_review.navigation.redirects import (
    merge_en_redirects_yaml,
    redirect_translate_scope,
)
from ydbdoc_review.navigation.toc import (
    TocTranslateScope,
    en_toc_is_absent,
    merge_en_toc_yaml,
    parse_toc_items,
    toc_entry_paths,
    toc_translate_scope,
)
from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    planned_toc_extras_for_pair,
)
from ydbdoc_review.pipeline.pairs import NavigationPair
from ydbdoc_review.pipeline.types import FileVerdict, NavigationRunResult
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.validation.heuristics import validate_navigation_merge_warnings

logger = logging.getLogger(__name__)

_NAV_BLOCKING_WARNING_KINDS = frozenset(
    {
        "scope_not_applied",
        "missing_href",
        "unexpected_href",
        "empty_toc",
        "collapsed_toc",
        "inconsistent_indent",
        "missing_toc_target",
    }
)


def _navigation_verdict(warnings: list[str]) -> FileVerdict:
    for w in warnings:
        kind = w.split(":", 1)[0]
        if kind in _NAV_BLOCKING_WARNING_KINDS:
            return "blocked"
    if warnings:
        return "warnings"
    return "ok"

_MENU_LABELS_PROMPT = """\
Translate Russian Diplodoc sidebar menu labels to English.
Return JSON only: {"translations": [{"ru": "<source>", "en": "<translation>"}, ...]}
Keep technical tokens (INDEX, SET, COMPACT, CLI) when appropriate.
Use the glossary when provided."""


def _read_navigation_baselines(
    repo_path: str,
    merge_base_with: str,
    *,
    ru_path: str,
    en_path: str,
) -> tuple[str, str]:
    """RU/EN navigation YAML at PR merge-base, with upstream EN fallback (§6.44).

    Fork PR checkouts often lack EN files at ``merge-base(merge_base_with, HEAD)``.
    ``en_main`` falls back to ``merge_base_with`` (upstream ``main``).
    """
    mb = merge_base(repo_path, merge_base_with, "HEAD")
    ru_text = read_text_at_ref(repo_path, mb, ru_path)
    ru_base = ru_text if ru_text is not None else ""
    en_text = read_text_at_ref(repo_path, mb, en_path)
    if en_text is None:
        en_text = read_text_at_ref(repo_path, merge_base_with, en_path)
    en_main = en_text if en_text is not None else ""
    return ru_base, en_main


def extra_toc_hrefs_from_md_targets(
    translated_en_paths: set[str],
) -> set[str]:
    """Basenames of newly translated EN pages (§6.17 union with toc scope).

    Locale ``_includes/*.md`` fragments are translated but are not sidebar
    ``href``s — exclude them (§6.42).
    """
    return {
        PurePosixPath(p).name
        for p in translated_en_paths
        if "/_includes/" not in p
    }


def extra_toc_hrefs_for_pair(ru_pr_yaml: str, md_href_basenames: set[str]) -> set[str]:
    """Restrict translated-page hrefs to entries present in this toc (§6.44)."""
    toc_hrefs = {it["href"] for it in parse_toc_items(ru_pr_yaml) if it.get("href")}
    return md_href_basenames & toc_hrefs


def _resolve_toc_merge_scope(
    pair: NavigationPair,
    *,
    ru_base: str,
    ru_pr: str,
    en_main: str,
    pair_extra_hrefs: set[str],
    pair_extra_includes: set[str] | None = None,
) -> tuple[TocTranslateScope, bool]:
    """Return merge scope and whether gap-fill is restricted to that scope.

    When EN sidebar yaml is absent, mirror the full RU structure (§6.85).
    ``supplement_only`` pairs add only RU entries missing from EN main, without
    renaming legacy EN href aliases (§6.72).
    """
    ru_hrefs, ru_includes = toc_entry_paths(ru_pr)
    planned_includes = pair_extra_includes or set()
    if en_toc_is_absent(en_main):
        return (
            TocTranslateScope(
                frozenset(ru_hrefs),
                frozenset(ru_includes),
            ).with_extra_hrefs(pair_extra_hrefs),
            False,
        )

    scope = toc_translate_scope(ru_base, ru_pr).with_extra_hrefs(pair_extra_hrefs)
    if not pair.supplement_only:
        if planned_includes:
            scope = scope.with_extra_include_paths(planned_includes)
        return scope, True

    en_hrefs, en_includes = toc_entry_paths(en_main)
    missing_hrefs = ru_hrefs - en_hrefs
    missing_includes = (ru_includes - en_includes) | planned_includes
    return (
        scope.with_extra_hrefs(missing_hrefs).with_extra_include_paths(
            missing_includes
        ),
        True,
    )


def _toc_label_names(
    ru_pr: str,
    scope: TocTranslateScope,
    *,
    gap_hrefs: set[str],
) -> list[str]:
    labels: list[str] = []
    for it in parse_toc_items(ru_pr):
        href = it.get("href")
        include_path = it.get("include_path")
        if href and (href in scope.hrefs or href in gap_hrefs):
            labels.append(it["name"])
        elif include_path and include_path in scope.include_paths:
            if it.get("name"):
                labels.append(it["name"])
    return labels


def _translate_menu_labels(
    client: YandexLLMClient,
    labels: list[str],
    glossary: Glossary,
    *,
    config: Config,
) -> dict[str, str]:
    if not labels:
        return {}
    unique = list(dict.fromkeys(labels))
    glossary_block = glossary.to_prompt_yaml()
    user = json.dumps({"labels": unique}, ensure_ascii=False)
    messages = [
        {"role": "system", "content": _MENU_LABELS_PROMPT},
    ]
    if glossary_block:
        messages.append(
            {"role": "system", "content": f"Glossary:\n{glossary_block}"}
        )
    messages.append({"role": "user", "content": user})
    try:
        response = client.chat(messages, role="translate")
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        mapping: dict[str, str] = {}
        for item in data.get("translations", []):
            ru = str(item.get("ru", "")).strip()
            en = str(item.get("en", "")).strip()
            if ru and en:
                mapping[ru] = en
        for label in unique:
            mapping.setdefault(label, label)
        return mapping
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Menu label translation failed, using RU labels: %s", exc)
        return {label: label for label in unique}


def merge_navigation_pair(
    pair: NavigationPair,
    *,
    repo_path: str,
    merge_base_with: str,
    client: YandexLLMClient,
    glossary: Glossary,
    config: Config,
    scope_plan: TranslationScopePlan | None = None,
    extra_toc_hrefs: set[str] | None = None,
) -> NavigationRunResult:
    """Produce merged EN navigation YAML for one RU/EN pair."""
    kind = navigation_yaml_kind(pair.ru_path)
    if kind is None:
        return NavigationRunResult(
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            kind="unknown",
            error=f"not a navigation file: {pair.ru_path!r}",
            verdict="blocked",
        )

    if pair.ru_deleted:
        return NavigationRunResult(
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            kind=kind,
            target_text=None,
            verdict="ok",
        )

    ru_pr = read_text(repo_path, pair.ru_path)
    if ru_pr is None:
        ru_pr = read_text_at_ref(repo_path, "HEAD", pair.ru_path)
    if ru_pr is None:
        return NavigationRunResult(
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            kind=kind,
            error=f"RU navigation text missing for {pair.ru_path!r}",
            verdict="blocked",
        )

    ru_base, en_main = _read_navigation_baselines(
        repo_path,
        merge_base_with,
        ru_path=pair.ru_path,
        en_path=pair.en_path,
    )

    if kind == "toc":
        if scope_plan is not None:
            pair_extra_hrefs, pair_extra_includes = planned_toc_extras_for_pair(
                scope_plan,
                pair.ru_path,
                ru_pr,
                docs_root=config.paths.docs_root,
            )
        else:
            pair_extra_hrefs = extra_toc_hrefs_for_pair(
                ru_pr, extra_toc_hrefs or set()
            )
            pair_extra_includes = set()
        scope, restrict_gap_fill = _resolve_toc_merge_scope(
            pair,
            ru_base=ru_base,
            ru_pr=ru_pr,
            en_main=en_main,
            pair_extra_hrefs=pair_extra_hrefs,
            pair_extra_includes=pair_extra_includes,
        )
        en_main_hrefs = {it["href"] for it in parse_toc_items(en_main) if it.get("href")}
        ru_base_hrefs = {it["href"] for it in parse_toc_items(ru_base) if it.get("href")}
        ru_base_include_paths = {
            it["include_path"]
            for it in parse_toc_items(ru_base)
            if it.get("include_path")
        }
        gap_hrefs = {
            it["href"]
            for it in parse_toc_items(ru_pr)
            if it.get("href")
            and it["href"] not in en_main_hrefs
            and it["href"] in ru_base_hrefs
        }
        labels = _toc_label_names(ru_pr, scope, gap_hrefs=gap_hrefs)
        if en_toc_is_absent(en_main):
            labels = [it["name"] for it in parse_toc_items(ru_pr) if it.get("name")]
        name_map = _translate_menu_labels(
            client, labels, glossary, config=config
        )
        merged = merge_en_toc_yaml(
            en_main,
            ru_pr,
            translate_hrefs=set(scope.hrefs),
            translate_name=lambda n: name_map.get(n, n),
            ru_base_hrefs=ru_base_hrefs,
            translate_include_paths=set(scope.include_paths),
            ru_base_include_paths=ru_base_include_paths,
            restrict_gap_fill_to_scope=restrict_gap_fill,
        )
        warnings = validate_navigation_merge_warnings(
            pair.ru_path,
            ru_pr,
            merged,
            en_main_yaml=en_main,
            translate_scope=set(scope.hrefs),
            translate_include_scope=set(scope.include_paths),
        )
    else:
        scope = redirect_translate_scope(ru_base, ru_pr)
        merged = merge_en_redirects_yaml(
            en_main,
            ru_pr,
            translate_from_paths=scope,
        )
        warnings = validate_navigation_merge_warnings(
            pair.ru_path,
            ru_pr,
            merged,
            en_main_yaml=en_main,
            translate_scope=scope,
        )

    verdict = _navigation_verdict(warnings)
    return NavigationRunResult(
        ru_path=pair.ru_path,
        en_path=pair.en_path,
        kind=kind,
        target_text=merged,
        warnings=warnings,
        verdict=verdict,
    )


def verify_navigation_pair(
    pair: NavigationPair,
    *,
    ru_pr: str,
    en_text: str,
    ru_base: str,
    en_main: str,
    scope_plan: TranslationScopePlan | None = None,
    extra_toc_hrefs: set[str] | None = None,
    docs_root: str = "ydb/docs",
) -> NavigationRunResult:
    """Validate committed EN navigation YAML against RU PR scope (no LLM merge)."""
    kind = navigation_yaml_kind(pair.ru_path)
    if kind is None:
        return NavigationRunResult(
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            kind="unknown",
            error=f"not a navigation file: {pair.ru_path!r}",
            verdict="blocked",
        )

    if pair.ru_deleted:
        return NavigationRunResult(
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            kind=kind,
            verdict="ok",
        )

    if kind == "toc":
        if scope_plan is not None:
            pair_extra_hrefs, pair_extra_includes = planned_toc_extras_for_pair(
                scope_plan,
                pair.ru_path,
                ru_pr,
                docs_root=docs_root,
            )
            scope, _restrict_gap_fill = _resolve_toc_merge_scope(
                pair,
                ru_base=ru_base,
                ru_pr=ru_pr,
                en_main=en_main,
                pair_extra_hrefs=pair_extra_hrefs,
                pair_extra_includes=pair_extra_includes,
            )
        else:
            pair_extra = extra_toc_hrefs_for_pair(ru_pr, extra_toc_hrefs or set())
            scope = toc_translate_scope(ru_base, ru_pr).with_extra_hrefs(pair_extra)
    else:
        scope = redirect_translate_scope(ru_base, ru_pr)

    if kind == "toc":
        warnings = validate_navigation_merge_warnings(
            pair.ru_path,
            ru_pr,
            en_text,
            en_main_yaml=en_main,
            translate_scope=set(scope.hrefs),
            translate_include_scope=set(scope.include_paths),
        )
    else:
        warnings = validate_navigation_merge_warnings(
            pair.ru_path,
            ru_pr,
            en_text,
            en_main_yaml=en_main,
            translate_scope=scope,
        )
    verdict = _navigation_verdict(warnings)
    return NavigationRunResult(
        ru_path=pair.ru_path,
        en_path=pair.en_path,
        kind=kind,
        target_text=None,
        warnings=warnings,
        verdict=verdict,
    )


def run_navigation_verifies(
    pairs: list[NavigationPair],
    *,
    repo_path: str,
    merge_base_with: str,
    ru_pr_by_path: dict[str, str],
    scope_plan: TranslationScopePlan | None = None,
    extra_toc_hrefs: set[str] | None = None,
    docs_root: str = "ydb/docs",
) -> list[NavigationRunResult]:
    """Validate navigation YAML pairs for ``doc_verify``."""
    hrefs = extra_toc_hrefs or set()
    results: list[NavigationRunResult] = []
    for pair in pairs:
        if not pair.en_changed and not pair.ru_changed:
            continue
        kind = navigation_yaml_kind(pair.ru_path)
        if pair.ru_deleted:
            results.append(
                NavigationRunResult(
                    ru_path=pair.ru_path,
                    en_path=pair.en_path,
                    kind=kind or "unknown",
                    verdict="ok",
                )
            )
            continue

        ru_pr = ru_pr_by_path.get(pair.ru_path)
        if ru_pr is None:
            results.append(
                NavigationRunResult(
                    ru_path=pair.ru_path,
                    en_path=pair.en_path,
                    kind=kind or "unknown",
                    error=f"RU navigation text missing for {pair.ru_path!r}",
                    verdict="blocked",
                )
            )
            continue

        en_text = read_text(repo_path, pair.en_path)
        if en_text is None:
            en_text = read_text_at_ref(repo_path, "HEAD", pair.en_path)
        if en_text is None:
            results.append(
                NavigationRunResult(
                    ru_path=pair.ru_path,
                    en_path=pair.en_path,
                    kind=kind or "unknown",
                    error=f"EN navigation text missing for {pair.en_path!r}",
                    verdict="blocked",
                )
            )
            continue

        ru_base, en_main = _read_navigation_baselines(
            repo_path,
            merge_base_with,
            ru_path=pair.ru_path,
            en_path=pair.en_path,
        )
        results.append(
            verify_navigation_pair(
                pair,
                ru_pr=ru_pr,
                en_text=en_text,
                ru_base=ru_base,
                en_main=en_main,
                scope_plan=scope_plan,
                extra_toc_hrefs=hrefs if scope_plan is None else None,
                docs_root=docs_root,
            )
        )
    return results


def run_navigation_merges(
    pairs: list[NavigationPair],
    *,
    repo_path: str,
    merge_base_with: str,
    client: YandexLLMClient,
    glossary: Glossary,
    config: Config,
    scope_plan: TranslationScopePlan | None = None,
    extra_toc_hrefs: set[str] | None = None,
) -> list[NavigationRunResult]:
    """Merge all navigation YAML pairs changed in the source PR."""
    results: list[NavigationRunResult] = []
    for pair in pairs:
        if not pair.ru_changed:
            continue
        if pair.en_changed:
            continue
        results.append(
            merge_navigation_pair(
                pair,
                repo_path=repo_path,
                merge_base_with=merge_base_with,
                client=client,
                glossary=glossary,
                config=config,
                scope_plan=scope_plan,
                extra_toc_hrefs=extra_toc_hrefs,
            )
        )
    return results
