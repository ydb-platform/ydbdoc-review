"""F-089 workflow ordering contract tests."""

from __future__ import annotations

import inspect

from ydbdoc_review.github import workflow
from ydbdoc_review.harness.pr_steps import ExecutePairPlansStep, PlanTranslatePairsStep


def test_F089_trace() -> None:
    """Analyze, translate, mechanical QA, one verify, then publication."""
    source = inspect.getsource(workflow.run_doc_translate)
    stages = (
        "begin_ops_job(",
        "freeze_ru_authority(",
        "plan_translation_scope(",
        "run_pr_translation(",
        "_apply_results_to_disk(",
        "prepare_translation_branch_on_base(",
        "build_source_pr_comment(",
    )
    positions = [source.index(stage) for stage in stages[:-1]]
    positions.append(source.rindex(stages[-1]))
    assert positions == sorted(positions)
    assert "run_doc_verify(" not in source

    analyze = inspect.getsource(PlanTranslatePairsStep.run)
    execute = inspect.getsource(ExecutePairPlansStep.run)
    assert analyze.index("plan_pairs(") < execute.index("run_pair_plan(")


def test_F089_early_finish() -> None:
    """No-op exits before branch preparation, while RED remains publishable."""
    source = inspect.getsource(workflow.run_doc_translate)
    no_op = source.index("if not pairs and not nav_pairs:")
    branch = source.index("prepare_translation_branch_on_base(")
    assert no_op < branch
    assert "return job" in source[no_op:branch]
    assert "PublicationImpact.PUBLISH_RED" in source
    assert "draft=False" in source
