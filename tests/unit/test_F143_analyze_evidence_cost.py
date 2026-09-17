"""F-143 contracts for Analyze evidence, reporting, and accounting."""

from __future__ import annotations

import json

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.usage import LLMUsage, UsageTracker
from ydbdoc_review.ops.recorder import LlmTranscriptRecorder
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.reporting.builder import ReportMeta, _usage_lines, build_source_pr_comment


def _cfg():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"})


def test_F143_evidence() -> None:
    recorder = LlmTranscriptRecorder()
    recorder.record(
        role="analyze",
        messages=[{"role": "user", "content": "full pair"}],
        content='{"decision":"no_translation_needed","reason":"aligned"}',
        model_slug="cheap-analyze",
    )
    store = InMemoryTranscriptStore()
    recorder.flush_to_store(store, "run-143")

    request = json.loads(store.get("run-143", "llm/001-analyze-req.json"))
    response = json.loads(store.get("run-143", "llm/001-analyze-resp.json"))
    assert request["role"] == "analyze"
    assert request["model"] == "cheap-analyze"
    assert response["content"] == '{"decision":"no_translation_needed","reason":"aligned"}'

    plan = PairPlan(
        pair=DocPair("ru/a.md", "en/a.md"),
        action="critic_only",
        source_path="ru/a.md",
        target_path="en/a.md",
        source_lang="ru",
        target_lang="en",
        summary="Форматирование отличается, смысл и обязательные элементы совпадают.",
    )
    body = build_source_pr_comment(
        PRTranslationResult(pair_results=[PairRunResult(plan=plan, skipped=True)]),
        translation_pr_number=None,
        meta=ReportMeta(mode="doc_translate", report_number=143, elapsed_s=1),
        config=_cfg(),
    )
    assert "Причины по парам" in body
    assert "Форматирование отличается" in body


def test_F143_cost_once() -> None:
    usage = UsageTracker(
        [
            LLMUsage("cheap-analyze", 100, 20, 1.0, 0, True, role="analyze"),
            LLMUsage("analyze-fallback", 50, 10, 1.0, 1, True, role="analyze"),
        ]
    )
    body = "\n".join(_usage_lines(_cfg(), PRTranslationResult(), usage))

    assert "Токены (analyze): 150 / 30" in body
    assert body.count("Токены (analyze):") == 1
    assert "cheap-analyze" in body and "analyze-fallback" in body
    assert "Оценка стоимости" in body
