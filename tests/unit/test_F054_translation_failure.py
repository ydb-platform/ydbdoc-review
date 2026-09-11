"""F-054: a failed translation is explicit and never published as complete."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.github.workflow import DocJobResult, job_requires_nonzero_exit
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.pipeline.analyze import PairContent, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.translation.errors import TranslationError


def _pair() -> DocPair:
    return DocPair(
        ru_path="ydb/docs/ru/f054.md",
        en_path="ydb/docs/en/f054.md",
        ru_changed=True,
    )


def _ctx() -> PRHarnessContext:
    return PRHarnessContext.from_options(MagicMock(), glossary=MagicMock(), config=MagicMock())


def test_F054_failed_file() -> None:
    pair = _pair()
    content = PairContent(pair=pair, ru_text="# Source\n")
    plan = plan_pair_heuristic(content)

    with patch(
        "ydbdoc_review.harness.pair.FileHarness.run",
        side_effect=TranslationError("truncated response after retries"),
    ):
        result = run_pair_plan(content, plan, _ctx(), cache={})

    assert result.error == "truncated response after retries"
    assert result.target_text is None
    job = DocJobResult(mode="doc_translate", pr_number=1, pr_result=PRTranslationResult())
    job.pr_result.pair_results = [result]
    assert job.translation_pr_number is None
    assert job_requires_nonzero_exit(job)


def test_F054_existing_and_noop() -> None:
    pair = _pair()
    existing = PairContent(
        pair=pair,
        ru_text="# Source\n",
        en_text="# Existing\n",
        en_base_text="# Existing\n",
    )
    plan = plan_pair_heuristic(existing)
    with patch(
        "ydbdoc_review.harness.pair.FileHarness.run",
        side_effect=TranslationError("models exhausted"),
    ):
        result = run_pair_plan(existing, plan, _ctx(), cache={})
    assert result.error is None
    assert result.target_text == "# Existing\n"
    assert result.soft_keep_reason == "models exhausted"

    unchanged_pair = DocPair(
        ru_path=pair.ru_path,
        en_path=pair.en_path,
        ru_changed=False,
        en_changed=False,
    )
    unchanged = PairContent(
        pair=unchanged_pair, ru_text="# Source\n", en_text="# Existing\n"
    )
    noop_plan = plan_pair_heuristic(unchanged)
    assert noop_plan.action == "skip"
    assert "no_translation_needed" not in str(noop_plan.summary).lower()
