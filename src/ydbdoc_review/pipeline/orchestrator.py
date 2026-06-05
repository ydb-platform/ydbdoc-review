"""PR-level translation orchestrator (sequential files, shared cache)."""

from __future__ import annotations

import logging

from ydbdoc_review.config.loader import Config, load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMError
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan, plan_pairs
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.translation.glossary import Glossary, load_glossary
from ydbdoc_review.translation.errors import TranslationError

logger = logging.getLogger(__name__)


def _read_source_text(content: PairContent, plan: PairPlan) -> str | None:
    if plan.source_path == content.pair.ru_path:
        return content.ru_text
    return content.en_text


def _read_target_text(content: PairContent, plan: PairPlan) -> str | None:
    if plan.target_path == content.pair.en_path:
        return content.en_text
    return content.ru_text


def _run_plan(
    content: PairContent,
    plan: PairPlan,
    client: YandexLLMClient,
    glossary: Glossary,
    config: Config,
    cache: dict[str, str],
) -> PairRunResult:
    if plan.action == "skip":
        return PairRunResult(plan=plan, skipped=True)

    if plan.action == "delete_en":
        return PairRunResult(plan=plan, deleted=True, target_text=None)

    source_text = _read_source_text(content, plan)
    if source_text is None:
        return PairRunResult(
            plan=plan,
            error=f"Missing source text for {plan.source_path!r}",
        )

    existing_target = _read_target_text(content, plan)
    enable_translate = plan.action in ("translate_to_en", "translate_to_ru")
    enable_critic = plan.action != "skip"

    try:
        file_result = translate_file(
            source_text,
            client,
            glossary,
            file_path=plan.source_path,
            config=config,
            source_lang=plan.source_lang,
            target_lang=plan.target_lang,
            cache=cache,
            enable_translate=enable_translate,
            existing_target_text=existing_target if not enable_translate else None,
            enable_critic=enable_critic,
        )
    except (LLMError, TranslationError, ValueError) as exc:
        logger.exception("Failed to process %s", plan.target_path)
        return PairRunResult(plan=plan, error=str(exc))

    return PairRunResult(
        plan=plan,
        target_text=file_result.final_text,
        file_result=file_result,
    )


def run_pr_translation(
    contents: list[PairContent],
    client: YandexLLMClient,
    glossary: Glossary | None = None,
    *,
    config: Config | None = None,
    use_analyze_llm: bool = False,
    per_pr_cache: dict[str, str] | None = None,
) -> PRTranslationResult:
    """Plan and execute translation for all pairs (sequential, one shared cache)."""
    cfg = config or load_config()
    glossary = glossary or load_glossary()
    cache = per_pr_cache if per_pr_cache is not None else {}

    plans = plan_pairs(
        contents,
        client,
        glossary,
        use_analyze_llm=use_analyze_llm,
        prompt_version=cfg.prompts.version,
    )
    content_by_ru = {c.pair.ru_path: c for c in contents}
    results: list[PairRunResult] = []

    for plan in plans:
        content = content_by_ru.get(plan.pair.ru_path)
        if content is None:
            continue
        results.append(
            _run_plan(content, plan, client, glossary, cfg, cache)
        )

    return PRTranslationResult(pair_results=results)
