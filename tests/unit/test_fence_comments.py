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
from ydbdoc_review.harness.render import finalize_en_target, render_with_translations
from ydbdoc_review.validation.fence_comments import (
    check_cyrillic_in_en_fence_comments,
    collect_cyrillic_fence_comment_lines,
    translate_cyrillic_fence_comments,
)
from ydbdoc_review.validation.fence_integrity import check_fence_body_copy
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


# auth-static-style Go snippet: prose outside fence + several ``//`` comments inside.
MULTI_COMMENT_GO_SOURCE = dedent("""
    Аутентификация по логину и паролю в native SDK Go. \
Описание достаточной длины для эвристик длины текста.

    ```go
    func connect() {
        // Создаём контекст и открываем соединение
        ctx := context.Background()
        // Используем статические учётные данные
        db, err := ydb.Open(ctx, ydb.WithStaticCredentials("user", "password"))
        if err != nil {
            // Аварийный выход при ошибке подключения
            panic(err)
        }
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


TRAILING_SLASH_GO_SAMPLE = dedent("""
    Intro paragraph with enough English words for length checks here.

    ```go
    db, err := ydb.Open(ctx, conn)
    if err != nil {
        panic(err) // аварийный выход при ошибке подключения
    }
    url := "grpcs://login:password@localhost:2135/local"
    ```
""").strip()


def test_collect_trailing_slash_comment_on_code_line():
    items = collect_cyrillic_fence_comment_lines(TRAILING_SLASH_GO_SAMPLE)
    assert len(items) == 1
    assert items[0].line_index == 2
    assert "аварийный выход" in items[0].body


def test_translate_trailing_slash_comment_preserves_code():
    def _fake_translate(body: str) -> str:
        return "Abort on connection error"

    translated = translate_cyrillic_fence_comments(
        TRAILING_SLASH_GO_SAMPLE, _fake_translate
    )
    assert "panic(err) // Abort on connection error" in translated
    assert "аварийный" not in translated
    assert 'grpcs://login:password@localhost:2135/local' in translated
    assert check_cyrillic_in_en_fence_comments(translated, target_lang="en") == []


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


def test_fence_comment_translate_skip_surfaces_rate_limit_warning():
    from unittest.mock import MagicMock

    from ydbdoc_review.llm.errors import LLMRetryExhaustedError
    from ydbdoc_review.validation.fence_comments import (
        translate_cyrillic_fence_comments_with_client,
    )
    from ydbdoc_review.validation.heuristics import run_file_heuristics_classified

    client = MagicMock()
    client.model_chain_for_role.return_value = ["deepseek-v4-flash"]
    client.chat.side_effect = LLMRetryExhaustedError(
        "Eliza rate-limit (429) retries exhausted (deepseek-v4-flash): HTTP 429"
    )

    warnings: list[str] = []
    out = translate_cyrillic_fence_comments_with_client(
        GO_SAMPLE,
        client,
        load_glossary(),
        out_warnings=warnings,
    )
    assert out == GO_SAMPLE
    assert len(warnings) == 1
    assert warnings[0].startswith("fence_comment_translate_skipped: rate-limit")

    classified = run_file_heuristics_classified(
        GO_SAMPLE,
        out,
        normalized_source_text=GO_SAMPLE,
        source_lang="ru",
        target_lang="en",
    )
    for warning in warnings:
        classified.warnings.append(warning)
    assert any("rate-limit" in w for w in classified.warnings)
    assert any(w.startswith("cyrillic_in_fence:") for w in classified.warnings)


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


def test_fenced_code_excluded_from_segments_only_prose_translated():
    """Code blocks are not segmented; LLM sees prose with inline placeholders only."""
    doc = parse_markdown(MULTI_COMMENT_GO_SOURCE)
    segments = extract_segments(doc)
    assert len(segments) == 1
    assert "Создаём контекст" not in segments[0].text
    assert "ydb.WithStaticCredentials" not in segments[0].text
    assert "Аутентификация" in segments[0].text


def test_translate_pipeline_prose_then_multiple_fence_comments():
    """§6.39: translate prose → copy fenced code from RU → batch-translate comments."""
    doc = parse_markdown(MULTI_COMMENT_GO_SOURCE)
    segments = extract_segments(doc)
    seg_id = segments[0].id

    comment_items = collect_cyrillic_fence_comment_lines(MULTI_COMMENT_GO_SOURCE)
    assert len(comment_items) == 3

    translate_raw = _translate_json(
        segments,
        {
            seg_id: (
                "Username and password authentication in the Go native SDK. "
                "Long enough description for length heuristics."
            ),
        },
    )
    comment_raw = json.dumps(
        {
            "comments": [
                {
                    "id": "b1-l1",
                    "text": "Create a context and open a connection",
                },
                {
                    "id": "b1-l3",
                    "text": "Use static credentials",
                },
                {
                    "id": "b1-l6",
                    "text": "Abort on connection error",
                },
            ]
        },
        ensure_ascii=False,
    )

    client = _mock_client([translate_raw, comment_raw])
    result = translate_file(
        MULTI_COMMENT_GO_SOURCE,
        client,
        load_glossary(),
        file_path="ydb/docs/ru/core/recipes/ydb-sdk/auth-static.md",
        enable_critic=False,
    )

    assert "Username and password authentication" in result.final_text
    assert "Create a context and open a connection" in result.final_text
    assert "Use static credentials" in result.final_text
    assert "Abort on connection error" in result.final_text
    assert "ydb.WithStaticCredentials(\"user\", \"password\")" in result.final_text
    assert "Создаём" not in result.final_text
    assert "Аварийный" not in result.final_text
    assert check_cyrillic_in_en_fence_comments(result.final_text, target_lang="en") == []
    assert check_fence_body_copy(MULTI_COMMENT_GO_SOURCE, result.final_text) == []


def test_finalize_en_copies_code_then_translates_each_comment_line():
    """After render, fenced bodies still match RU; finalize translates every comment."""
    doc = parse_markdown(MULTI_COMMENT_GO_SOURCE)
    segments = extract_segments(doc)
    rendered = render_with_translations(
        doc,
        segments,
        {
            segments[0].id: (
                "Username and password authentication in the Go native SDK. "
                "Long enough description."
            ),
        },
        target_lang="en",
    )
    assert "Создаём контекст" in rendered
    assert check_fence_body_copy(MULTI_COMMENT_GO_SOURCE, rendered) == []

    def _translate_comment(body: str) -> str:
        return {
            "Создаём контекст и открываем соединение": (
                "Create a context and open a connection"
            ),
            "Используем статические учётные данные": "Use static credentials",
            "Аварийный выход при ошибке подключения": "Abort on connection error",
        }[body.strip()]

    finalized = finalize_en_target(
        rendered,
        MULTI_COMMENT_GO_SOURCE,
        client=None,
        glossary=None,
    )
    finalized = translate_cyrillic_fence_comments(finalized, _translate_comment)

    assert "Create a context and open a connection" in finalized
    assert "Use static credentials" in finalized
    assert "Abort on connection error" in finalized
    assert "ydb.WithStaticCredentials" in finalized
    assert "Создаём" not in finalized
    assert check_fence_body_copy(MULTI_COMMENT_GO_SOURCE, finalized) == []
