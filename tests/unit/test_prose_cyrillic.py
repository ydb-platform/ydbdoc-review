# ruff: noqa: RUF001
"""Read-only residual-Cyrillic detection and v010 blocking contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ydbdoc_review.pipeline.dependency_queue import DependencyPlan, QueueEntry
from ydbdoc_review.pipeline.translation_transaction import run_translation_transaction
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)
from ydbdoc_review.validation.prose_cyrillic import collect_cyrillic_prose_spans

TOPIC_OFFSET = """### Offset {#offset}

All messages within a partition have a unique sequence number called `смещением` (offset)."""

TOPIC_SEQNO = """## Message sequence numbers {#seqno}

The message sequence number must increase monotonically within a pair `топик`, `источник`.
When the server receives a message with a sequence number less than or equal to the maximum
recorded for the pair `топик`, `источник`, the message will be skipped as a duplicate."""

MANIFEST = TranslationJobManifest(TranslationModelPolicy(
    translate=ModelPair("translate-primary", "translate-fallback"),
    critic=ModelPair("critic-primary", "critic-fallback"),
    repair=ModelPair("repair-primary", "repair-fallback"),
))


class RaisingWriter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("read-only detector must not invoke a model writer")


def test_collect_cyrillic_prose_spans_backticks_and_words():
    spans = collect_cyrillic_prose_spans(TOPIC_OFFSET)
    assert [(span.span_id, span.text, span.context) for span in spans] == [
        ("p1", "смещением", "All messages within a partition have a unique sequence number called `смещением` (offset).")
    ]
    spans = collect_cyrillic_prose_spans(TOPIC_SEQNO)
    assert [span.text for span in spans] == ["топик", "источник"]


def test_collect_cyrillic_prose_spans_skips_fenced_code():
    text = """Intro in English.

```go
// комментарий на русском
```"""
    assert collect_cyrillic_prose_spans(text) == []


def test_translate_cyrillic_prose_with_mock_fn():
    """Legacy writer identity: detector output is read-only and never calls it."""
    writer = RaisingWriter()
    spans = collect_cyrillic_prose_spans(TOPIC_SEQNO)
    assert [(span.span_id, span.text) for span in spans] == [("p1", "топик"), ("p2", "источник")]
    assert writer.calls == 0


def test_translate_cyrillic_prose_with_client_mock():
    """Legacy client-writer identity: detection performs no model request."""
    writer = RaisingWriter()
    spans = collect_cyrillic_prose_spans(TOPIC_OFFSET)
    assert spans[0].text == "смещением"
    assert spans[0].context.endswith("`смещением` (offset).")
    assert writer.calls == 0


def test_one_pass_cyrillic_prose_finding_blocks_without_finalize_writer():
    source = "Все сообщения имеют номер `смещением` (offset).\n"
    rendered_with_residual = "All messages have a number called `смещением` (offset)."
    writer = RaisingWriter()
    publish_calls: list[dict[str, str]] = []
    findings = collect_cyrillic_prose_spans(rendered_with_residual)
    assert [(item.span_id, item.text, item.context) for item in findings] == [
        ("p1", "смещением", rendered_with_residual)
    ]

    class ResidualClient:
        def chat_once(self, messages, *, explicit_model, role, **_kwargs):
            if role == "critic":
                return SimpleNamespace(content=json.dumps({"findings": []}))
            payload = json.loads(messages[-1]["content"])
            return SimpleNamespace(content=json.dumps({"segments": [
                {
                    "id": item["id"],
                    "text": item["text"]
                    .replace("Все сообщения имеют номер", "All messages have a number called")
                    .replace(".", ".", 1),
                }
                for item in payload["segments"]
            ]}, ensure_ascii=False))

    path = "ydb/docs/ru/core/concepts/datamodel/topic.md"
    result = run_translation_transaction(
        DependencyPlan((QueueEntry(path, "initial"),), (), 1, 0),
        read_ru=lambda _path: source,
        client=ResidualClient(),
        to_en_path=lambda value: value.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )
    if result.publishable:
        publish_calls.append(result.staged)

    assert not result.publishable
    assert result.staged == {}
    assert publish_calls == []
    assert writer.calls == 0
    assert rendered_with_residual == "All messages have a number called `смещением` (offset)."
