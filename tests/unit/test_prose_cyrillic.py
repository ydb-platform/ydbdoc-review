"""Tests for residual Cyrillic cleanup in EN prose."""

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
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.prose_cyrillic import (
    collect_cyrillic_prose_spans,
    translate_cyrillic_prose,
    translate_cyrillic_prose_with_client,
)

TOPIC_OFFSET = dedent(
    """
    ### Offset {#offset}

    All messages within a partition have a unique sequence number called `смещением` (offset).
    """
).strip()

TOPIC_SEQNO = dedent(
    """
    ## Message sequence numbers {#seqno}

    The message sequence number must increase monotonically within a pair `топик`, `источник`.
    When the server receives a message with a sequence number less than or equal to the maximum
    recorded for the pair `топик`, `источник`, the message will be skipped as a duplicate.
    """
).strip()


def test_collect_cyrillic_prose_spans_backticks_and_words():
    spans = collect_cyrillic_prose_spans(TOPIC_OFFSET)
    assert [span.text for span in spans] == ["смещением"]

    spans = collect_cyrillic_prose_spans(TOPIC_SEQNO)
    assert [span.text for span in spans] == ["топик", "источник"]


def test_collect_cyrillic_prose_spans_skips_fenced_code():
    text = dedent(
        """
        Intro in English.

        ```go
        // комментарий на русском
        ```
        """
    ).strip()
    assert collect_cyrillic_prose_spans(text) == []


def test_translate_cyrillic_prose_with_mock_fn():
    def _fake_translate(span):
        mapping = {
            "смещением": "offset",
            "топик": "topic",
            "источник": "source",
        }
        return mapping.get(span.text, span.text)

    translated = translate_cyrillic_prose(TOPIC_SEQNO, _fake_translate)
    assert "`topic`, `source`" in translated
    assert "топик" not in translated
    assert "источник" not in translated


def test_translate_cyrillic_prose_with_client_mock():
    spans = collect_cyrillic_prose_spans(TOPIC_OFFSET)
    payload = {
        "spans": [
            {"id": span.span_id, "text": "offset"} for span in spans
        ]
    }
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload))
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    client = YandexLLMClient(
        folder_id="b1x",
        api_key="k",
        llm=cfg.llm,
        client=mock_openai,
    )
    translated = translate_cyrillic_prose_with_client(
        TOPIC_OFFSET,
        client,
        load_glossary(),
        file_path="docs/en/core/concepts/datamodel/topic.md",
    )
    assert "`offset` (offset)" in translated
    assert "смещением" not in translated


def test_translate_file_prose_cyrillic_finalize_clears_blocking_heuristic():
    source = "Все сообщения имеют номер `смещением` (offset).\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id
    translate_raw = json.dumps(
        {
            "segments": [
                {
                    "id": seg_id,
                    "text": (
                        "All messages have a number called `смещением` (offset)."
                    ),
                }
            ]
        },
        ensure_ascii=False,
    )
    prose_raw = json.dumps(
        {"spans": [{"id": "p1", "text": "offset"}]},
        ensure_ascii=False,
    )
    critic_raw = json.dumps({"verdict": "ok", "issues": []})

    mock_openai = MagicMock()
    mock_openai.chat.completions.create.side_effect = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=translate_raw))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=prose_raw))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=critic_raw))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        ),
    ]
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    client = YandexLLMClient(
        folder_id="b1x",
        api_key="k",
        llm=cfg.llm,
        client=mock_openai,
    )
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/core/concepts/datamodel/topic.md",
        target_lang="en",
    )
    assert "`offset` (offset)" in result.final_text
    assert "смещением" not in result.final_text
    classified = run_file_heuristics_classified(
        source,
        result.final_text,
        normalized_source_text=source,
        source_lang="ru",
        target_lang="en",
    )
    assert classified.blocking == []
    assert result.verdict == "ok"
