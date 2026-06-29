"""PR-level harness steps."""

from __future__ import annotations

from typing import Protocol

from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan, plan_pairs


class PRHarnessStep(Protocol):
    name: str

    def run(self, state: PRRunState, ctx: PRHarnessContext) -> None: ...


class PlanTranslatePairsStep:
    """Deterministic or LLM analyze → list of pair plans."""

    name = "plan_translate_pairs"

    def run(self, state: PRRunState, ctx: PRHarnessContext) -> None:
        state.plans = plan_pairs(
            state.contents,
            ctx.client,
            ctx.glossary,
            use_analyze_llm=ctx.use_analyze_llm,
            prompt_version=ctx.config.prompts.version,
        )


class PlanVerifyPairsStep:
    """Build critic-only plans for doc_verify (one per content with RU+EN)."""

    name = "plan_verify_pairs"

    def run(self, state: PRRunState, ctx: PRHarnessContext) -> None:
        del ctx
        plans: list[PairPlan] = []
        for content in state.contents:
            pair = content.pair
            if not content.ru_text or not content.en_text:
                plans.append(
                    PairPlan(
                        pair=pair,
                        action="skip",
                        source_path=pair.ru_path,
                        target_path=pair.en_path,
                        source_lang="ru",
                        target_lang="en",
                        summary="verify skip — missing RU or EN text",
                    )
                )
                continue
            plans.append(
                PairPlan(
                    pair=pair,
                    action="critic_only",
                    source_path=pair.ru_path,
                    target_path=pair.en_path,
                    source_lang="ru",
                    target_lang="en",
                    summary="doc_verify critic pass",
                )
            )
        state.plans = plans


class ExecutePairPlansStep:
    """Run each plan sequentially through per-file FileHarness."""

    name = "execute_pair_plans"

    def run(self, state: PRRunState, ctx: PRHarnessContext) -> None:
        content_by_ru = {c.pair.ru_path: c for c in state.contents}
        file_ctx = HarnessContext.from_options(
            ctx.client,
            glossary=ctx.glossary,
            config=ctx.config,
        )
        results = []
        for plan in state.plans:
            content = content_by_ru.get(plan.pair.ru_path)
            if content is None:
                continue
            results.append(run_pair_plan(content, plan, file_ctx, state.cache))
        state.pair_results = results
