"""PR-level harness steps."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.pipeline.analyze import PairPlan, plan_pairs

logger = logging.getLogger(__name__)


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
                    authoritative_source_text=content.current_ru_text or content.ru_text,
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
            en_toc_reachable=ctx.en_toc_reachable,
            docs_text_reader=ctx.docs_text_reader,
            docs_repo_path=ctx.docs_repo_path,
        )
        results = []
        total = len(state.plans)
        for idx, plan in enumerate(state.plans, start=1):
            content = content_by_ru.get(plan.pair.ru_path)
            if content is None:
                continue
            logger.info(
                "pair %s/%s start action=%s target=%s",
                idx,
                total,
                plan.action,
                plan.target_path,
            )
            started = time.monotonic()
            result = run_pair_plan(
                content,
                plan,
                file_ctx,
                state.cache,
                historical_merged_provenance=ctx.historical_merged_provenance,
            )
            elapsed = time.monotonic() - started
            status = "error" if result.error else ("skip" if result.skipped else "ok")
            logger.info(
                "pair %s/%s done status=%s elapsed=%.1fs target=%s%s",
                idx,
                total,
                status,
                elapsed,
                plan.target_path,
                f" err={result.error}" if result.error else "",
            )
            results.append(result)
        state.pair_results = results
