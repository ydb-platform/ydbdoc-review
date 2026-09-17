"""F-140 contracts for bounded and scope-closed Analyze decisions."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ydbdoc_review.github.workflow import _analyzed_noop_result
from ydbdoc_review.pipeline.analyze import PairContent, plan_from_analyze
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.schemas import AnalyzePairResult


def _content() -> PairContent:
    return PairContent(
        pair=DocPair("ru/a.md", "en/a.md", ru_changed=True),
        ru_text="Проза",
        en_text="Prose",
    )


def _response(results: list[dict[str, object]]) -> str:
    return json.dumps({"results": results})


def _row(ru_path: str = "ru/a.md", en_path: str = "en/a.md") -> dict[str, object]:
    return {
        "ru_path": ru_path,
        "en_path": en_path,
        "ru_present": True,
        "en_present": True,
        "semantically_aligned": True,
        "needs_generation_for": None,
        "summary": "aligned",
    }


class _AnalyzeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=self.response)


def test_F140_invalid_decisions() -> None:
    for rows in ([], [_row(), _row()], [_row(), _row("ru/foreign.md", "en/foreign.md")]):
        client = _AnalyzeClient(_response(rows))
        result = _analyzed_noop_result(
            [_content()], client, load_glossary(), prompt_version="v1"
        )
        assert result is None
        assert client.calls == 1

    missing_target = PairContent(
        pair=DocPair("ru/a.md", "en/a.md", ru_changed=True),
        ru_text="Проза",
        en_text=None,
    )
    false_noop = AnalyzePairResult(
        ru_path="ru/a.md",
        en_path="en/a.md",
        ru_present=True,
        en_present=False,
        semantically_aligned=True,
        needs_generation_for=None,
        summary="incorrectly aligned",
    )
    assert plan_from_analyze(missing_target, false_noop).action == "translate_to_en"


def test_F140_bounded_work() -> None:
    # A positive generation decision is scoped to the pair supplied to
    # plan_from_analyze and does not invent another pair or another pass.
    result = AnalyzePairResult(
        ru_path="ru/a.md",
        en_path="en/a.md",
        ru_present=True,
        en_present=True,
        semantically_aligned=False,
        needs_generation_for="en",
        summary="technical values differ",
    )
    plan = plan_from_analyze(_content(), result)
    assert plan.action == "translate_to_en"
    assert plan.source_path == "ru/a.md"
    assert plan.target_path == "en/a.md"
