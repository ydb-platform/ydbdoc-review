"""PR-level harness steps."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.navigation.redirects import (
    REDIRECT_TOMBSTONE_SKIP_SUMMARY,
    should_skip_redirect_tombstone_en,
)
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan, plan_pairs
from ydbdoc_review.validation.yfm_anchor import JobAnchorDictionary

logger = logging.getLogger(__name__)


def _skip_redirect_tombstone_plans(
    plans: list[PairPlan],
    *,
    redirect_source_en_paths: frozenset[str] | None,
    en_toc_reachable: frozenset[str] | None,
) -> list[PairPlan]:
    """Rewrite translate/critic plans that would write EN at redirect ``from`` paths."""
    if not redirect_source_en_paths:
        return plans
    out: list[PairPlan] = []
    for plan in plans:
        if plan.action not in ("translate_to_en", "critic_only"):
            out.append(plan)
            continue
        en_path = plan.pair.en_path
        if not should_skip_redirect_tombstone_en(
            en_path,
            redirect_source_en_paths=redirect_source_en_paths,
            en_toc_reachable=en_toc_reachable,
        ):
            out.append(plan)
            continue
        out.append(
            PairPlan(
                pair=plan.pair,
                action="skip",
                source_path=plan.pair.ru_path,
                target_path=plan.pair.en_path,
                source_lang="ru",
                target_lang="en",
                summary=REDIRECT_TOMBSTONE_SKIP_SUMMARY,
            )
        )
    return out


class PRHarnessStep(Protocol):
    name: str

    def run(self, state: PRRunState, ctx: PRHarnessContext) -> None: ...


class PlanTranslatePairsStep:
    """Deterministic or LLM analyze → list of pair plans."""

    name = "plan_translate_pairs"

    def run(self, state: PRRunState, ctx: PRHarnessContext) -> None:
        state.plans = _skip_redirect_tombstone_plans(
            plan_pairs(
                state.contents,
                ctx.client,
                ctx.glossary,
                use_analyze_llm=ctx.use_analyze_llm,
                prompt_version=ctx.config.prompts.version,
            ),
            redirect_source_en_paths=ctx.redirect_source_en_paths,
            en_toc_reachable=ctx.en_toc_reachable,
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
            en_toc_reachable=ctx.en_toc_reachable,
            docs_text_reader=ctx.docs_text_reader,
            docs_repo_path=ctx.docs_repo_path,
            job_anchor_dictionary=ctx.job_anchor_dictionary or JobAnchorDictionary(),
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
            result = run_pair_plan(content, plan, file_ctx, state.cache)
            elapsed = time.monotonic() - started
            status = "error" if result.error else ("skip" if result.skipped else "ok")
            soft_keep = bool(
                result.file_result
                and any(
                    str(w).startswith("translate_soft_keep:")
                    for w in (result.file_result.heuristic_warnings or [])
                )
            )
            if soft_keep and status == "ok":
                status = "soft_keep"
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
