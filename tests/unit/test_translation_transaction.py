import json
from types import SimpleNamespace

import pytest

import ydbdoc_review.pipeline.translation_transaction as transaction_module
import ydbdoc_review.translation.one_pass as one_pass_module
from ydbdoc_review.llm.errors import LLMRetryableRequestError
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.dependency_queue import (
    DependencyPlan,
    QueueEntry,
    UnresolvedDependency,
)
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.translation_transaction import run_translation_transaction
from ydbdoc_review.segmentation.reinsert import UnknownSegmentKindError
from ydbdoc_review.segmentation.types import SegmentKind
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)
from ydbdoc_review.translation.one_pass import OnePassTranslationError

MANIFEST = TranslationJobManifest(TranslationModelPolicy(
    translate=ModelPair("translate-primary", "translate-fallback"),
    critic=ModelPair("critic-primary", "critic-fallback"),
    repair=ModelPair("repair-primary", "repair-fallback"),
))


class FileClient:
    def __init__(self, fail_on_translate=()):
        self.calls = 0
        self.translate_calls = 0
        self.fail_on_translate = set(fail_on_translate)

    def chat_once(self, messages, *, explicit_model, role, **kwargs):
        self.calls += 1
        if role == "critic":
            return SimpleNamespace(content=json.dumps({"findings": []}))
        if role == "translate":
            self.translate_calls += 1
        if self.translate_calls in self.fail_on_translate:
            raise LLMRetryableRequestError("timeout", status_code=503)
        payload = json.loads(messages[-1]["content"])
        return SimpleNamespace(
            content=json.dumps(
                {
                    "segments": [
                        {"id": item["id"], "text": f"English {item['id']}."}
                        for item in payload["segments"]
                    ]
                }
            )
        )


def _plan(*paths, unresolved=()):
    entries = tuple(QueueEntry(path, "initial" if index == 0 else "auto_added") for index, path in enumerate(paths))
    return DependencyPlan(entries, tuple(unresolved), 1, max(0, len(paths) - 1))


def test_unknown_segment_kind_blocks_before_render_and_stages_nothing(monkeypatch):
    for kind in SegmentKind:
        one_pass_module._validate_supported_segment_kinds(
            [SimpleNamespace(kind=kind)]
        )
    with pytest.raises(UnknownSegmentKindError, match="future_kind"):
        one_pass_module._validate_supported_segment_kinds(
            [SimpleNamespace(kind="future_kind")]
        )

    real_extract = one_pass_module.extract_segments

    def extract_with_future_kind(document):
        segments = real_extract(document)
        first = segments[0]
        segments[0] = SimpleNamespace(
            id=first.id,
            kind="future_kind",
            path=first.path,
            text=first.text,
            placeholders=first.placeholders,
            ast_path=first.ast_path,
            heading_anchor=first.heading_anchor,
        )
        return segments

    observed = []
    real_translate = one_pass_module.translate_ru_to_en_once

    def translate_spy(*args, **kwargs):
        try:
            return real_translate(*args, **kwargs)
        except Exception as exc:
            observed.append(exc)
            raise

    monkeypatch.setattr(one_pass_module, "extract_segments", extract_with_future_kind)
    monkeypatch.setattr(transaction_module, "translate_ru_to_en_once", translate_spy)

    client = FileClient()
    source_path = "ydb/docs/ru/unknown-kind.md"
    result = run_translation_transaction(
        _plan(source_path),
        read_ru=lambda _path: "Обычный русский абзац.\n",
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )

    assert len(observed) == 1
    assert isinstance(observed[0], OnePassTranslationError)
    assert isinstance(observed[0].__cause__, UnknownSegmentKindError)
    assert "future_kind" in str(observed[0].__cause__)
    assert result.publishable is False
    assert result.staged == {}
    assert result.report["failures"] == [
        {
            "file": source_path,
            "category": "translation_failed",
            "message": "translation_failed: Unsupported segment kind: future_kind",
        }
    ]
    assert result.report["files"][0]["accepted_payload_count"] == 1
    assert result.report["files"][0]["render_count"] == 0
    assert client.translate_calls == 1


def test_success_stages_exactly_the_planned_counterparts_once_each():
    paths = ("ydb/docs/ru/a.md", "ydb/docs/ru/b.md")
    client = FileClient()
    result = run_translation_transaction(
        _plan(*paths),
        read_ru=lambda path: "Текст.\n",
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )

    assert result.publishable
    assert client.translate_calls == 2
    assert set(result.staged) == {"ydb/docs/en/a.md", "ydb/docs/en/b.md"}
    assert not result.report["failures"]


def test_immediate_pre_stage_uses_result_context_by_identity(monkeypatch):
    translated_contexts = []
    staged_contexts = []
    real_translate = transaction_module.translate_ru_to_en_once
    real_validate = transaction_module.validate_complete_document

    def translate_spy(*args, **kwargs):
        translated = real_translate(*args, **kwargs)
        translated_contexts.append(translated.validation_context)
        return translated

    def validate_spy(text, validation_context):
        staged_contexts.append(validation_context)
        return real_validate(text, validation_context)

    monkeypatch.setattr(transaction_module, "translate_ru_to_en_once", translate_spy)
    monkeypatch.setattr(transaction_module, "validate_complete_document", validate_spy)
    result = run_translation_transaction(
        _plan("ydb/docs/ru/a.md"),
        read_ru=lambda _path: "Русский текст.\n",
        client=FileClient(),
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )
    assert result.publishable
    assert len(translated_contexts) == len(staged_contexts) == 1
    assert staged_contexts[0] is translated_contexts[0]


def test_orchestrator_returns_auto_added_dependency_for_atomic_apply():
    initial = "ydb/docs/ru/a.md"
    dependency = "ydb/docs/ru/b.md"
    result = run_pr_translation(
        [
            PairContent(
                pair=DocPair(
                    ru_path=initial,
                    en_path="ydb/docs/en/a.md",
                    ru_changed=True,
                ),
                ru_text="Первый текст.\n",
            )
        ],
        FileClient(),
        manifest=MANIFEST,
        dependency_plan=_plan(initial, dependency),
        read_ru_source=lambda path: "Второй текст.\n" if path == dependency else None,
    )

    assert result.failed_count == 0
    assert result.translated_count == 2
    assert [run.plan.source_path for run in result.pair_results] == [initial, dependency]
    assert [run.plan.target_path for run in result.pair_results] == [
        "ydb/docs/en/a.md",
        "ydb/docs/en/b.md",
    ]
    assert all(run.target_text for run in result.pair_results)


def test_failure_in_last_file_discards_every_staged_file():
    client = FileClient(fail_on_translate={2, 3, 4, 5})
    result = run_translation_transaction(
        _plan("ydb/docs/ru/a.md", "ydb/docs/ru/b.md"),
        read_ru=lambda path: "Текст.\n",
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
        pinned_en_paths={"ydb/docs/en/a.md", "ydb/docs/en/b.md"},
        read_pinned_en=lambda _path: None,
    )

    assert not result.publishable
    assert result.staged == {}
    assert client.translate_calls == 5
    assert result.report["failures"][0]["file"] == "ydb/docs/ru/b.md"


def test_unresolved_dependency_blocks_transaction_and_keeps_structured_report():
    warning = UnresolvedDependency(
        category="unresolved_translation_dependency",
        source_file="ydb/docs/ru/a.md",
        output_file="ydb/docs/en/a.md",
        original_href="missing.md",
        resolved_ru_target=None,
        resolved_en_target="ydb/docs/en/missing.md",
        reason="missing_source",
        manual_action="translate/add the named RU target, fix the href, or explicitly add an EN counterpart",
        dependency_kind="markdown_link",
    )
    client = FileClient()
    result = run_translation_transaction(
        _plan("ydb/docs/ru/a.md", unresolved=(warning,)),
        read_ru=lambda path: "[Ссылка](missing.md)\n",
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )

    assert not result.publishable
    assert result.staged == {}
    assert result.report["unresolved"] == [warning.__dict__]
    assert "⟦" not in json.dumps(result.report, ensure_ascii=False)


def test_transaction_rewrites_in_scope_inbound_cyrillic_anchor():
    sources = {
        "ydb/docs/ru/a.md": "[Раздел](b.md#точный-якорь)\n",
        "ydb/docs/ru/b.md": "# Заголовок {#точный-якорь}\n",
    }

    class AnchorClient(FileClient):
        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            if role == "critic":
                return SimpleNamespace(content=json.dumps({"findings": []}))
            self.translate_calls += 1
            payload = json.loads(messages[-1]["content"])
            translated = []
            for item in payload["segments"]:
                text = item["text"].replace("Раздел", "Section").replace(
                    "Заголовок", "Heading"
                )
                translated.append({"id": item["id"], "text": text})
            return SimpleNamespace(content=json.dumps({"segments": translated}))

    result = run_translation_transaction(
        _plan(*sources),
        read_ru=sources.__getitem__,
        client=AnchorClient(),
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
        pinned_en_paths={"ydb/docs/en/a.md", "ydb/docs/en/b.md"},
        read_pinned_en=lambda _path: None,
    )

    assert result.publishable
    assert "(b.md#heading)" in result.staged["ydb/docs/en/a.md"]
    assert "{#heading}" in result.staged["ydb/docs/en/b.md"]


def test_transaction_blocks_out_of_scope_inbound_anchor_mutation():
    source = "# Заголовок {#точный-якорь}\n"

    class AnchorClient(FileClient):
        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            if role == "critic":
                return SimpleNamespace(content=json.dumps({"findings": []}))
            payload = json.loads(messages[-1]["content"])
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "segments": [
                            {
                                "id": item["id"],
                                "text": item["text"].replace("Заголовок", "Heading"),
                            }
                            for item in payload["segments"]
                        ]
                    }
                )
            )

    result = run_translation_transaction(
        _plan("ydb/docs/ru/b.md"),
        read_ru=lambda _path: source,
        client=AnchorClient(),
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
        pinned_en_paths={"ydb/docs/en/b.md", "ydb/docs/en/outside.md"},
        read_pinned_en=lambda path: (
            "[old](b.md#точный-якорь)\n" if path.endswith("outside.md") else source
        ),
    )

    assert not result.publishable
    assert result.staged == {}
    assert result.report["anchor_findings"][0]["source_file"].endswith(
        "outside.md"
    )
