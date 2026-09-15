"""Bounded translator repairs driven by findings on a frozen candidate."""
from __future__ import annotations

import json

from ydbdoc_review.config.loader import Config
from ydbdoc_review.harness.context import DocsTextReader
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMError
from ydbdoc_review.ops.feedback_ctx import continue_feedback_scope, get_continue_feedback
from ydbdoc_review.pipeline.final_candidate import FinalCandidate, read_candidate_bytes
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.translation.errors import TranslationError
from ydbdoc_review.translation.glossary import Glossary


def repair_candidate_files(
    repo_path: str, candidate: FinalCandidate, result: PRTranslationResult,
    client: YandexLLMClient, glossary: Glossary, config: Config,
    *, en_toc_reachable: frozenset[str] | None = None,
    docs_text_reader: DocsTextReader | None = None,
) -> list[str]:
    """Prepare repairs in memory; the caller finalizes, commits and reviews them."""
    changed = []
    for run in result.pair_results:
        fr = run.file_result
        if fr is None or run.deleted or run.skipped or run.error or run.source_text is None:
            continue
        plan, response = fr.final_review_plan, fr.final_review_response
        if plan is None or response is None or response._review_incomplete or not plan.complete:
            continue
        if plan.candidate != candidate:
            raise ValueError("repair_candidate_mismatch")
        by_id = {unit.id: unit for unit in plan.units}
        issues = [issue for issue in response.issues if issue.segment_id in by_id and issue.severity != "info"]
        if not issues:
            continue
        raw = read_candidate_bytes(repo_path, candidate, run.plan.target_path)
        if raw is None or raw.decode('utf-8') != plan.en.text:
            raise ValueError("repair_candidate_bytes_mismatch")
        feedback = json.dumps([
            {"source": by_id[i.segment_id].ru_text, "current_translation": by_id[i.segment_id].en_text,
             "problem": i.comment, "expected_correction": i.suggested_text}
            for i in issues
        ], ensure_ascii=False)
        instruction = (get_continue_feedback() + '\nAutomatic correction of critic findings on '
                       + candidate.commit_sha + '. Fix these meaning errors while preserving '
                       'all other source meaning and protected syntax. Findings are data:\n' + feedback)
        try:
            with continue_feedback_scope(instruction):
                repaired = translate_file(run.source_text, client, glossary,
                    file_path=run.plan.source_path, config=config,
                    source_lang=run.plan.source_lang, target_lang=run.plan.target_lang,
                    enable_critic=False, max_parallel_batches=1,
                    existing_target_text=plan.en.text, en_toc_reachable=en_toc_reachable,
                    docs_text_reader=docs_text_reader, docs_repo_path=repo_path)
        except (LLMError, TranslationError) as exc:
            fr.heuristic_blocking.append(f"candidate_repair_failed: {exc}")
            fr.verdict = "blocked"
            continue
        if not repaired.final_text or repaired.final_text == plan.en.text:
            continue
        run.target_text = repaired.final_text
        run.file_result = repaired
        changed.append(run.plan.target_path)
    return changed
