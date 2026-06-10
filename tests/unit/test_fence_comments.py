"""Tests for Cyrillic in fenced code comments (translate + QA)."""

from __future__ import annotations

import json
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.validation.fence_comments import (
    check_cyrillic_in_en_fence_comments,
    collect_cyrillic_fence_comment_lines,
    translate_cyrillic_fence_comments,
)
from ydbdoc_review.validation.heuristics import (
    _classify_heuristic,
    run_file_heuristics_classified,
)


YQL_SAMPLE = dedent("""
    Data enrichment example with enough English prose for length heuristics here.

    ```yql
    -- Секрет с токеном для подключения к YDB
    CREATE SECRET `secrets/ydb_token` WITH (value = "<ydb_token>");

    -- Чтение событий из входного топика
    $topic_data = SELECT * FROM ydb_source.input_topic;
    ```
""").strip()


GO_SAMPLE = dedent("""
    Intro paragraph with enough English words for length checks here.

    ```go
    package main

    func main() {
        // 1. Настраиваем провайдер логов OTel с OTLP-экспортёром.
        // ... используйте db ...
    }
    ```
""").strip()


def test_collect_cyrillic_fence_comment_lines_yql_sql_dash():
    items = collect_cyrillic_fence_comment_lines(YQL_SAMPLE)
    assert len(items) == 2
    assert "Секрет" in items[0].body
    assert "Чтение" in items[1].body


def test_check_cyrillic_in_en_fence_comments_warns_on_yql_dash():
    warnings = check_cyrillic_in_en_fence_comments(YQL_SAMPLE, target_lang="en")
    assert warnings
    assert warnings[0].startswith("cyrillic_in_fence:")


def test_translate_cyrillic_fence_comments_yql_dash():
    def _fake_translate(body: str) -> str:
        mapping = {
            "Секрет с токеном для подключения к YDB": (
                "Secret with token for connecting to YDB"
            ),
            "Чтение событий из входного топика": (
                "Read events from the input topic"
            ),
        }
        return mapping.get(body.strip(), body)

    translated = translate_cyrillic_fence_comments(YQL_SAMPLE, _fake_translate)
    assert "Secret with token" in translated
    assert "Read events from the input topic" in translated
    assert "Секрет" not in translated
    assert check_cyrillic_in_en_fence_comments(translated, target_lang="en") == []


def test_collect_cyrillic_fence_comment_lines():
    items = collect_cyrillic_fence_comment_lines(GO_SAMPLE)
    assert len(items) == 2
    assert "Настраиваем" in items[0].body


def test_check_cyrillic_in_en_fence_comments_warns():
    warnings = check_cyrillic_in_en_fence_comments(GO_SAMPLE, target_lang="en")
    assert warnings
    assert warnings[0].startswith("cyrillic_in_fence:")
    assert _classify_heuristic(warnings[0]) == "warnings"


def test_check_cyrillic_in_en_fence_comments_skips_prose_outside_fence():
    text = "Hello привет.\n"
    assert check_cyrillic_in_en_fence_comments(text, target_lang="en") == []


def test_translate_cyrillic_fence_comments_with_mock_fn():
    def _fake_translate(body: str) -> str:
        mapping = {
            "1. Настраиваем провайдер логов OTel с OTLP-экспортёром.": (
                "1. Set up the OTel log provider with an OTLP exporter."
            ),
            "... используйте db ...": "... use db ...",
        }
        return mapping.get(body, body)

    translated = translate_cyrillic_fence_comments(GO_SAMPLE, _fake_translate)
    assert "Set up the OTel log provider" in translated
    assert "Настраиваем" not in translated
    assert check_cyrillic_in_en_fence_comments(translated, target_lang="en") == []


def test_run_file_heuristics_classified_fence_comment_is_warning_not_blocking():
    classified = run_file_heuristics_classified(
        GO_SAMPLE,
        GO_SAMPLE,
        normalized_source_text=GO_SAMPLE,
        source_lang="ru",
        target_lang="en",
    )
    assert any(w.startswith("cyrillic_in_fence:") for w in classified.warnings)
    assert not any(w.startswith("cyrillic_in_fence:") for w in classified.blocking)


def _completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _mock_client(responses: list[str]) -> YandexLLMClient:
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.side_effect = [
        _completion(r) for r in responses
    ]
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    return YandexLLMClient(
        folder_id="b1x",
        api_key="k",
        llm=cfg.llm,
        client=mock_openai,
    )


def _translate_json(segments, mapping: dict[str, str]) -> str:
    payload = {
        "segments": [
            {"id": seg.id, "text": mapping.get(seg.id, seg.text)}
            for seg in segments
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


def test_translate_file_finalizes_fence_comments_via_llm():
    source = (
        "Описание интеграции OpenTelemetry для SDK. " * 4 + "\n\n"
        + dedent("""
            ```go
            func main() {
                // 1. Настраиваем провайдер логов.
            }
            ```
        """).strip()
        + "\n"
    )
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id
    translate_raw = _translate_json(
        segments,
        {seg_id: "OpenTelemetry SDK integration description. " * 4},
    )
    comment_raw = json.dumps(
        {
            "comments": [
                {
                    "id": "b1-l1",
                    "text": "1. Set up the log provider.",
                }
            ]
        }
    )
    critic_raw = json.dumps({"verdict": "ok", "issues": []})

    client = _mock_client([translate_raw, comment_raw, critic_raw])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/recipes/debug-logs-otel.md",
        enable_critic=True,
    )

    assert "Set up the log provider" in result.final_text
    assert "Настраиваем" not in result.final_text
    assert not any(w.startswith("cyrillic_in_fence:") for w in result.heuristic_warnings)
