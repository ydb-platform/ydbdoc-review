"""PR-level translation orchestrator — delegates to ``harness`` PR profiles."""

from __future__ import annotations

from ydbdoc_review.config.loader import Config, load_config
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_profiles import TRANSLATE_PR_PROFILE
from ydbdoc_review.harness.pr_runner import PRHarness
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.translation.glossary import Glossary, load_glossary


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
    state = PRRunState(
        contents=contents,
        cache=per_pr_cache if per_pr_cache is not None else {},
    )
    ctx = PRHarnessContext.from_options(
        client,
        glossary=glossary,
        config=config,
        use_analyze_llm=use_analyze_llm,
    )
    return PRHarness(TRANSLATE_PR_PROFILE).run(state, ctx)
