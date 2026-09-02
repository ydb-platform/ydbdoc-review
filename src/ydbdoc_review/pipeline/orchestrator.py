"""RU-authoritative PR translation orchestrator."""

from __future__ import annotations

from collections.abc import Callable

from ydbdoc_review.config.loader import Config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.dependency_queue import DependencyPlan, QueueEntry
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.translation_transaction import run_translation_transaction
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.model_policy import TranslationJobManifest

DocsTextReader = Callable[[str], str | None]


def run_pr_translation(
    contents: list[PairContent],
    client: YandexLLMClient,
    glossary: Glossary | None = None,
    *,
    config: Config | None = None,
    use_analyze_llm: bool = False,
    per_pr_cache: dict[str, str] | None = None,
    en_toc_reachable: frozenset[str] | None = None,
    redirect_source_en_paths: frozenset[str] | None = None,
    docs_text_reader: DocsTextReader | None = None,
    docs_repo_path: str | None = None,
    manifest: TranslationJobManifest,
    dependency_plan: DependencyPlan | None = None,
    pinned_en_paths: set[str] | None = None,
    read_pinned_en=None,
    read_ru_source: DocsTextReader | None = None,
) -> PRTranslationResult:
    """Translate each canonical RU path once; old EN bytes are ignored."""
    result = PRTranslationResult()
    by_ru = {content.pair.ru_path: content for content in contents}
    ru_sources: dict[str, str] = {}

    def read_ru(path: str) -> str:
        if path in ru_sources:
            return ru_sources[path]
        content = by_ru.get(path)
        value = content.ru_text if content is not None else None
        if value is None and read_ru_source is not None:
            value = read_ru_source(path)
        if value is None:
            return _raise_missing_source(path)
        ru_sources[path] = value
        return value

    active = [content for content in contents if not content.pair.ru_deleted]
    if dependency_plan is None:
        entries = tuple(QueueEntry(content.pair.ru_path, "initial") for content in active)
        dependency_plan = DependencyPlan(entries, (), len(entries), 0)
    transaction = run_translation_transaction(
        dependency_plan,
        read_ru=read_ru,
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/", 1),
        manifest=manifest,
        pinned_en_paths=pinned_en_paths,
        read_pinned_en=read_pinned_en,
        en_toc_reachable=en_toc_reachable,
        docs_text_reader=docs_text_reader,
    )
    deleted_contents = [content for content in contents if content.pair.ru_deleted]
    queue_contents: list[tuple[QueueEntry, PairContent]] = []
    for entry in dependency_plan.entries:
        content = by_ru.get(entry.ru_path)
        if content is None:
            content = PairContent(
                pair=DocPair(
                    ru_path=entry.ru_path,
                    en_path=entry.ru_path.replace("/ru/", "/en/", 1),
                    ru_changed=True,
                ),
                ru_text=ru_sources.get(entry.ru_path),
            )
        queue_contents.append((entry, content))

    for content in deleted_contents:
        pair = content.pair
        plan = PairPlan(
            pair=pair,
            action="delete_en",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary="one-pass RU-authoritative translation",
        )
        result.pair_results.append(PairRunResult(plan=plan, deleted=True))

    for entry, content in queue_contents:
        pair = content.pair
        plan = PairPlan(
            pair=pair,
            action="translate_ru_to_en_once",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary=f"one-pass RU-authoritative translation ({entry.origin})",
        )
        if transaction.publishable:
            target_text = transaction.staged.get(pair.en_path)
            result.pair_results.append(
                PairRunResult(
                    plan=plan,
                    target_text=target_text,
                    source_text=ru_sources.get(pair.ru_path, content.ru_text),
                )
            )
        else:
            result.pair_results.append(
                PairRunResult(
                    plan=plan,
                    error=f"one-pass transaction blocked: {transaction.report}",
                    source_text=ru_sources.get(pair.ru_path, content.ru_text),
                )
            )
    if not transaction.publishable:
        result.completeness_gaps.extend(
            entry.ru_path.replace("/ru/", "/en/", 1)
            for entry in dependency_plan.entries
        )
    return result


def _raise_missing_source(path: str) -> str:
    raise RuntimeError(f"missing RU source: {path}")
