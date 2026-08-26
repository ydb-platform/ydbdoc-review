"""Scoped merge of Diplodoc toc/redirect YAML for doc_translate."""

from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath

from ydbdoc_review.config.loader import Config
from ydbdoc_review.github.git_ops import (
    merge_base,
    read_text,
    read_text_at_ref,
    read_text_at_upstream_tip,
)
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.navigation.paths import navigation_yaml_kind
from ydbdoc_review.navigation.redirects import (
    merge_en_redirects_yaml,
    redirect_translate_scope,
)
from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    planned_toc_extras_for_pair,
)
from ydbdoc_review.navigation.toc import (
    TocTranslateScope,
    en_toc_is_absent,
    merge_en_toc_yaml,
    parse_toc_items,
    preserve_en_order_for_skipped_toc_entries,
    resolve_toc_target_path,
    toc_entry_paths,
    toc_reordered_shared_hrefs,
    toc_translate_scope,
)
from ydbdoc_review.pipeline.pairs import NavigationPair
from ydbdoc_review.pipeline.skip_paths import matches_translate_skip, toc_entry_is_skipped
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
        "invalid_yaml",
        "missing_toc_target",
        "toc_structure_parity",
        "orphan_toc_page",
        "duplicate_toc_entry",
    }
)
# Soft drift: keep in the report, but do not downgrade merge recommendation (§6.121).
_NAV_SOFT_WARNING_KINDS = frozenset({"toc_en_only_legacy"})


def _navigation_verdict(warnings: list[str]) -> FileVerdict:
    for w in warnings:
        kind = w.split(":", 1)[0]
        if kind in _NAV_BLOCKING_WARNING_KINDS:
            return "blocked"
    hard = [
        w
        for w in warnings
        if w.split(":", 1)[0] not in _NAV_SOFT_WARNING_KINDS
    ]
    if hard:
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
    ru_base_ref: str | None = None,
) -> tuple[str, str]:
    """RU at PR merge-base; EN from current upstream main (§6.44, §6.111).

    ``ru_base`` must be the merge-base snapshot so toc scope reflects what the
    source PR changed. ``en_main`` must be **current** ``merge_base_with``
    (usually ``origin/main``): long-lived source PRs have an old merge-base
    whose EN toc predates EN-only entries added later on main. Using that stale
    EN baseline drops those entries (YFM003 / #46845). Fall back to merge-base
    EN only when the file is still absent on upstream main (new sidebar).
    """
    mb = ru_base_ref or merge_base(repo_path, merge_base_with, "HEAD")
    ru_text = read_text_at_ref(repo_path, mb, ru_path)
    ru_base = ru_text if ru_text is not None else ""
    en_text = read_text_at_upstream_tip(repo_path, merge_base_with, en_path)
    if en_text is None:
        # Worktree may already be at/near main after fetch (better than empty).
        en_text = read_text(repo_path, en_path)
    if en_text is None:
        en_text = read_text_at_ref(repo_path, mb, en_path)
    en_main = en_text if en_text is not None else ""
    if not en_main.strip():
        logger.warning(
            "EN navigation baseline empty for %s (merge_base_with=%s, mb=%s) — "
            "merge may drop EN-only toc entries",
            en_path,
            merge_base_with,
            mb[:12] if mb else mb,
        )
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
    Otherwise scope = ``toc_translate_scope`` (RU base→PR diff) ∪ planned
    extras from the translation plan. ``supplement_only`` no longer expands to
    every RU−EN missing href (§6.72 / #46878).

    When the source PR also changed EN toc (``pair.en_changed``, bilingual),
    drop href/include entries that already exist on EN main from the *name*
    translate scope — the author already refreshed those labels. Keep extras
    for pages not yet on EN (§6.165 / #48411).
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
    if planned_includes:
        scope = scope.with_extra_include_paths(planned_includes)

    if pair.en_changed:
        en_hrefs = {
            it["href"] for it in parse_toc_items(en_main) if it.get("href")
        }
        en_includes = {
            it["include_path"]
            for it in parse_toc_items(en_main)
            if it.get("include_path")
        }
        # New pages (extras not yet on EN) stay in scope for label translate.
        keep_hrefs = frozenset(
            h for h in scope.hrefs if h not in en_hrefs or h in pair_extra_hrefs
        )
        # Extras are hrefs; includes already on EN were author-maintained.
        keep_includes = frozenset(
            p for p in scope.include_paths if p not in en_includes
        )
        scope = TocTranslateScope(keep_hrefs, keep_includes)

    # Always restrict gap-fill to ``scope`` (§6.82). For ``supplement_only``
    # parents (§6.72 / #46878) do **not** expand scope with every RU−EN missing
    # href — that pulled ``secondary_indexes.md`` / stale flat paths into EN and
    # failed ``missing_toc_target``. Planned extras already list the pages/includes
    # that caused the parent to be queued.
    return scope, True


def _drop_skipped_from_toc_scope(
    scope: TocTranslateScope,
    skip_globs: list[str] | tuple[str, ...] | None,
) -> TocTranslateScope:
    """Remove href/include targets under ``translate_skip_globs`` (§6.167)."""
    if not skip_globs:
        return scope
    return TocTranslateScope(
        frozenset(h for h in scope.hrefs if not matches_translate_skip(h, skip_globs)),
        frozenset(
            p for p in scope.include_paths if not matches_translate_skip(p, skip_globs)
        ),
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
    ru_content_ref: str | None = None,
    ru_base_ref: str | None = None,
    active_doc_ru_paths: frozenset[str] | set[str] | None = None,
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

    use_historical = bool(
        ru_content_ref
        and (scope_plan is None or pair.ru_path in scope_plan.nav_from_diff)
    )
    ru_pr: str | None = None
    if use_historical:
        ru_pr = read_text_at_ref(repo_path, ru_content_ref, pair.ru_path)
    if ru_pr is None:
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
        ru_base_ref=ru_base_ref if use_historical else None,
    )

    if kind == "toc":
        if scope_plan is not None:
            pair_extra_hrefs, pair_extra_includes = planned_toc_extras_for_pair(
                scope_plan,
                pair.ru_path,
                ru_pr,
                docs_root=config.paths.docs_root,
                active_doc_ru_paths=active_doc_ru_paths,
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
        scope = _drop_skipped_from_toc_scope(scope, config.paths.translate_skip_globs)
        en_main_hrefs = {it["href"] for it in parse_toc_items(en_main) if it.get("href")}
        ru_base_hrefs = {it["href"] for it in parse_toc_items(ru_base) if it.get("href")}
        ru_base_include_paths = {
            it["include_path"]
            for it in parse_toc_items(ru_base)
            if it.get("include_path")
        }
        # §6.150 / #47856: RU menu reshuffle among existing entries. Shared EN
        # entries are already repositioned by the RU walk in ``merge_en_toc_yaml``.
        # If the EN page exists on upstream but the href is missing from EN toc,
        # pull it into translate scope so gap-fill places it at the RU position.
        reorder_hrefs = toc_reordered_shared_hrefs(ru_base, ru_pr)
        if reorder_hrefs and restrict_gap_fill:
            for href in sorted(reorder_hrefs):
                if href in en_main_hrefs or href in scope.hrefs:
                    continue
                target = resolve_toc_target_path(pair.en_path, href)
                if (
                    read_text_at_upstream_tip(repo_path, merge_base_with, target)
                    is not None
                ):
                    scope = scope.with_extra_hrefs({href})
                    logger.info(
                        "Nav reorder: EN page exists for %s — adding to toc at RU position",
                        href,
                    )
        gap_hrefs = {
            it["href"]
            for it in parse_toc_items(ru_pr)
            if it.get("href")
            and it["href"] not in en_main_hrefs
            and it["href"] in ru_base_hrefs
        }
        # When gap-fill is restricted to translate scope (§6.82), labels for
        # out-of-scope gap hrefs are never applied — skip the LLM call (#47856).
        label_gaps = set() if restrict_gap_fill else gap_hrefs
        labels = _toc_label_names(ru_pr, scope, gap_hrefs=label_gaps)
        if en_toc_is_absent(en_main):
            labels = [it["name"] for it in parse_toc_items(ru_pr) if it.get("name")]
        name_map = _translate_menu_labels(
            client, labels, glossary, config=config
        )
        # Keep ambient EN-main hrefs whose page still exists, but never retain
        # an entry explicitly removed by this RU source change (#45949).
        removed_ru_hrefs = ru_base_hrefs - {
            it["href"] for it in parse_toc_items(ru_pr) if it.get("href")
        }
        keep_en_hrefs: set[str] = set()
        for href in en_main_hrefs:
            if href in removed_ru_hrefs:
                continue
            target = resolve_toc_target_path(pair.en_path, href)
            if (
                read_text_at_upstream_tip(repo_path, merge_base_with, target)
                is not None
            ):
                keep_en_hrefs.add(href)
        merged = merge_en_toc_yaml(
            en_main,
            ru_pr,
            translate_hrefs=set(scope.hrefs),
            translate_name=lambda n: name_map.get(n, n),
            ru_base_hrefs=ru_base_hrefs,
            translate_include_paths=set(scope.include_paths),
            ru_base_include_paths=ru_base_include_paths,
            restrict_gap_fill_to_scope=restrict_gap_fill,
            keep_en_hrefs=keep_en_hrefs,
        )
        skip_globs = list(config.paths.translate_skip_globs or ())
        if skip_globs:
            merged = preserve_en_order_for_skipped_toc_entries(
                en_main,
                merged,
                entry_is_skipped=lambda it: toc_entry_is_skipped(it, skip_globs),
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

    # Pure RU reorder / RU-only toc entries that §6.82 will not mirror leave EN
    # identical to main — do not write or count as translated (§6.141 / #47856).
    if merged == en_main or merged.strip() == en_main.strip():
        logger.info(
            "Navigation merge no-op for %s — EN unchanged vs upstream baseline",
            pair.en_path,
        )
        return NavigationRunResult(
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            kind=kind,
            target_text=None,
            warnings=warnings,
            verdict="ok",
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
    active_doc_ru_paths: frozenset[str] | set[str] | None = None,
    skip_globs: list[str] | tuple[str, ...] | None = None,
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
                active_doc_ru_paths=active_doc_ru_paths,
            )
            scope, _restrict_gap_fill = _resolve_toc_merge_scope(
                pair,
                ru_base=ru_base,
                ru_pr=ru_pr,
                en_main=en_main,
                pair_extra_hrefs=pair_extra_hrefs,
                pair_extra_includes=pair_extra_includes,
            )
            scope = _drop_skipped_from_toc_scope(scope, skip_globs)
        else:
            pair_extra = extra_toc_hrefs_for_pair(ru_pr, extra_toc_hrefs or set())
            scope = toc_translate_scope(ru_base, ru_pr).with_extra_hrefs(pair_extra)
            scope = _drop_skipped_from_toc_scope(scope, skip_globs)
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
    active_doc_ru_paths: frozenset[str] | set[str] | None = None,
    skip_globs: list[str] | tuple[str, ...] | None = None,
) -> list[NavigationRunResult]:
    """Validate navigation YAML pairs for ``doc_verify``.

    ``active_doc_ru_paths`` (§6.165): same filter as ``run_navigation_merges``.
    """
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

        # Prefer HEAD of the translation branch tip (§6.133): working tree may
        # briefly mirror main / merge-base EN and false-🔴 scope_not_applied.
        en_text = read_text_at_ref(repo_path, "HEAD", pair.en_path)
        if en_text is None:
            en_text = read_text(repo_path, pair.en_path)
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
                active_doc_ru_paths=active_doc_ru_paths,
                skip_globs=skip_globs,
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
    ru_content_ref: str | None = None,
    ru_base_ref: str | None = None,
    active_doc_ru_paths: frozenset[str] | set[str] | None = None,
) -> list[NavigationRunResult]:
    """Merge all navigation YAML pairs with a RU change in the source PR.

    Unlike markdown bilingual skip (§6.76), toc merge still runs when both RU
    and EN sidebars changed. Authors often touch EN toc for a partial reorder
    while RU adds new ``href``s (#41271 / #47104); skipping left translated
    pages as ``orphan_toc_page``. Merge keeps out-of-scope EN ``name`` blocks
    and EN-only legacy hrefs.

    ``active_doc_ru_paths`` (§6.165): RU markdown paths actually translated in
    this run (after bilingual skip). Restricts toc href extras so skipped
    bilingual pages do not trigger menu-label retranslation.
    """
    results: list[NavigationRunResult] = []
    for pair in pairs:
        if not pair.ru_changed:
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
                ru_content_ref=ru_content_ref,
                ru_base_ref=ru_base_ref,
                active_doc_ru_paths=active_doc_ru_paths,
            )
        )
    return results
