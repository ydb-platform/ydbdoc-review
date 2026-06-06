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
    merge_en_toc_yaml,
    parse_toc_items,
    toc_translate_scope,
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
        "inconsistent_indent",
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


def _read_at_base(repo_path: str, merge_base_with: str, rel_path: str) -> str:
    mb = merge_base(repo_path, merge_base_with, "HEAD")
    text = read_text_at_ref(repo_path, mb, rel_path)
    return text if text is not None else ""


def extra_toc_hrefs_from_md_targets(
    translated_en_paths: set[str],
) -> set[str]:
    """Basenames of newly translated EN pages (§6.17 union with toc scope)."""
    return {PurePosixPath(p).name for p in translated_en_paths}


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
    extra_toc_hrefs: set[str],
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

    ru_base = _read_at_base(repo_path, merge_base_with, pair.ru_path)
    en_main = _read_at_base(repo_path, merge_base_with, pair.en_path)

    if kind == "toc":
        scope = toc_translate_scope(ru_base, ru_pr) | extra_toc_hrefs
        labels = [
            it["name"]
            for it in parse_toc_items(ru_pr)
            if it["href"] in scope
        ]
        name_map = _translate_menu_labels(
            client, labels, glossary, config=config
        )
        merged = merge_en_toc_yaml(
            en_main,
            ru_pr,
            translate_hrefs=scope,
            translate_name=lambda n: name_map.get(n, n),
        )
        warnings = validate_navigation_merge_warnings(
            pair.ru_path,
            ru_pr,
            merged,
            en_main_yaml=en_main,
            translate_scope=scope,
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
    extra_toc_hrefs: set[str],
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
        scope = toc_translate_scope(ru_base, ru_pr) | extra_toc_hrefs
    else:
        scope = redirect_translate_scope(ru_base, ru_pr)

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
    extra_toc_hrefs: set[str] | None = None,
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

        ru_base = _read_at_base(repo_path, merge_base_with, pair.ru_path)
        en_main = _read_at_base(repo_path, merge_base_with, pair.en_path)
        results.append(
            verify_navigation_pair(
                pair,
                ru_pr=ru_pr,
                en_text=en_text,
                ru_base=ru_base,
                en_main=en_main,
                extra_toc_hrefs=hrefs,
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
    extra_toc_hrefs: set[str] | None = None,
) -> list[NavigationRunResult]:
    """Merge all navigation YAML pairs changed in the source PR."""
    hrefs = extra_toc_hrefs or set()
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
                extra_toc_hrefs=hrefs,
            )
        )
    return results
