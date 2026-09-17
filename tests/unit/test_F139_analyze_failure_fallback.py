"""F-139 contracts for conservative Analyze failure handling."""

from __future__ import annotations

from types import SimpleNamespace

from ydbdoc_review.github.workflow import _analyzed_noop_result
from ydbdoc_review.llm.errors import LLMRetryExhaustedError
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary


def _content() -> PairContent:
    return PairContent(
        pair=DocPair("ru/a.md", "en/a.md", ru_changed=True),
        ru_text="Проза",
        en_text="Prose",
    )


def test_F139_exhausted_analyze() -> None:
    calls = 0

    class ExhaustedClient:
        def chat(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise LLMRetryExhaustedError("analyze chain exhausted")

    # The no-op gate is advisory. On refusal/outage it returns control to the
    # caller, which continues through the ordinary translation pipeline.
    result = _analyzed_noop_result(
        [_content()], ExhaustedClient(), load_glossary(), prompt_version="v1"
    )
    assert result is None
    assert calls == 1


def test_F139_successful_translation() -> None:
    class SuccessfulAnalyzeClient:
        usage_tracker = SimpleNamespace(records=[])

        def chat(self, *args, **kwargs):
            return SimpleNamespace(
                content=(
                    '{"results": [{"ru_path": "ru/a.md", '
                    '"en_path": "en/a.md", "ru_present": true, '
                    '"en_present": true, "semantically_aligned": true, '
                    '"needs_generation_for": null, '
                    '"summary": "Смысл и структура совпадают; перевод не нужен."}]}'
                )
            )

    result = _analyzed_noop_result(
        [_content()], SuccessfulAnalyzeClient(), load_glossary(), prompt_version="v1"
    )
    assert result is not None
    assert len(result.pair_results) == 1
    assert result.pair_results[0].skipped is True
    assert result.pair_results[0].plan.summary.startswith("Смысл")
