"""F-024: a required translation replaces the target with the result from S."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github import workflow
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.pipeline.analyze import (
    PairContent,
    PairPlan,
    plan_from_analyze,
)
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.schemas import AnalyzePairResult


def _ctx() -> HarnessContext:
    return HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )


def _plan(pair: DocPair, action: str = "translate_to_en") -> PairPlan:
    return PairPlan(
        pair=pair,
        action=action,  # type: ignore[arg-type]
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="F-024",
    )


def test_F024_replace() -> None:
    pair = DocPair(ru_path="ydb/docs/ru/page.md", en_path="ydb/docs/en/page.md", ru_changed=True)
    old_target = "B-only prose that must not survive\n"
    fresh_target = "Full translation rendered from S\n"
    content = PairContent(pair=pair, ru_text="S source\n", en_text=old_target)
    seen: dict[str, object] = {}

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, _ctx):
            seen["existing_target"] = state.existing_target_text
            result = MagicMock()
            result.final_text = fresh_target
            result.differential_meta = {"mode": "full", "semantic_noop": False}
            result.link_contract_issues = ()
            result.critic_initial = None
            result.critic_unresolved = None
            result.segment_alignment_error = None
            result.manual_actions = []
            result.heuristic_blocking = []
            result.heuristic_warnings = []
            result.heuristic_info = []
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        result = run_pair_plan(content, _plan(pair), _ctx(), cache={})

    assert seen["existing_target"] == old_target
    assert result.target_text == fresh_target
    assert old_target not in result.target_text


def test_F024_analyze_noop() -> None:
    pair = DocPair(ru_path="ydb/docs/ru/page.md", en_path="ydb/docs/en/page.md")
    content = PairContent(pair=pair, ru_text="S source\n", en_text="Aligned target\n")
    analyzed = AnalyzePairResult(
        ru_path=pair.ru_path,
        en_path=pair.en_path,
        ru_present=True,
        en_present=True,
        semantically_aligned=True,
        summary="already aligned",
    )
    plan = plan_from_analyze(content, analyzed)

    assert plan.action == "critic_only"
    with patch(
        "ydbdoc_review.harness.pair._try_deterministic_en_preserve",
        return_value=content.en_text,
    ) as preserve:
        result = run_pair_plan(content, plan, _ctx(), cache={})

    preserve.assert_called_once()
    assert result.target_text == content.en_text
    assert "guard_remote_ref=True" in inspect.getsource(workflow.run_doc_translate)

