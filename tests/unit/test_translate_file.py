"""Mandatory-manifest one-pass replacements for legacy translate_file tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ydbdoc_review.pipeline.dependency_queue import DependencyPlan, QueueEntry
from ydbdoc_review.pipeline.translation_transaction import run_translation_transaction
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)
from ydbdoc_review.translation.one_pass import translate_ru_to_en_once
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified

MANIFEST = TranslationJobManifest(TranslationModelPolicy(
    translate=ModelPair("translate-primary", "translate-fallback"),
    critic=ModelPair("critic-primary", "critic-fallback"),
    repair=ModelPair("repair-primary", "repair-fallback"),
))


def _plan(path: str = "ydb/docs/ru/page.md") -> DependencyPlan:
    return DependencyPlan((QueueEntry(path, "initial"),), (), 1, 0)


class Client:
    def __init__(self, replacements=(), critic_findings=()) -> None:
        self.replacements = tuple(replacements)
        self.critic_findings = list(critic_findings)
        self.calls: list[tuple[str, str]] = []

    def chat_once(self, messages, *, explicit_model, role, **_kwargs):
        self.calls.append((role, explicit_model))
        body = json.loads(messages[-1]["content"])
        if role == "translate":
            translated = []
            for item in body["segments"]:
                text = item["text"]
                for source, target in self.replacements:
                    text = text.replace(source, target)
                translated.append({"id": item["id"], "text": text})
            return SimpleNamespace(content=json.dumps({"segments": translated}, ensure_ascii=False))
        if role == "critic":
            findings = self.critic_findings.pop(0) if self.critic_findings else []
            return SimpleNamespace(content=json.dumps({"findings": findings}))
        return SimpleNamespace(content=json.dumps({
            "finding_id": body["finding_id"],
            "block_id": body["block_id"],
            "replacement": "Correct term translation.",
        }))


def test_translate_file_no_segments():
    """Empty/non-prose RU blocks before a model call and stages nothing."""
    client = Client()
    result = run_translation_transaction(
        _plan("ydb/docs/ru/code.md"),
        read_ru=lambda _path: "```bash\necho hi\n```\n",
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )
    assert not result.publishable
    assert result.staged == {}
    assert client.calls == []
    assert result.report["failures"][0]["message"] == "empty_or_unparseable_ru"


def test_translate_file_end_to_end_no_critic_issues():
    """Accepted full-file payload renders once under the mandatory manifest."""
    client = Client((("Привет, мир", "Hello, world"),))
    result = translate_ru_to_en_once(
        "Привет, мир.\n", client, file_path="ydb/docs/ru/hello.md", manifest=MANIFEST
    )
    assert result.text == "Hello, world.\n"
    assert result.prose_count == 1
    assert [role for role, _model in client.calls] == ["translate", "critic"]
    assert client.calls == [("translate", "translate-primary"), ("critic", "critic-primary")]


def test_translate_file_applies_critic_fix():
    """Bounded local repair may edit one allowed prose block, never retranslate."""
    finding = {
        "finding_id": "model-id-is-not-trusted",
        "rule_id": "terminology",
        "severity": "RED",
        "block_id": "s0001",
        "range": {"start": 0, "end": len(b"Wrong term translation.")},
        "atom_ids": [],
        "message": "Use the required term",
        "required_rule": "Use the approved terminology",
        "context": "Wrong term translation.",
        "repair_class": "prose",
    }
    client = Client(
        (("Неверный перевод термина", "Wrong term translation"),),
        critic_findings=([finding], []),
    )
    result = translate_ru_to_en_once(
        "Неверный перевод термина.\n",
        client,
        file_path="ydb/docs/ru/terms.md",
        manifest=MANIFEST,
    )
    assert result.text == "Correct term translation.\n"
    assert [role for role, _model in client.calls].count("translate") == 1
    assert [role for role, _model in client.calls].count("repair") == 1
    assert [role for role, _model in client.calls].count("critic") == 2


def test_translate_file_skips_critic_when_disabled():
    """Legacy optional-critic identity: critic is now mandatory and read-only."""
    client = Client((("Текст", "Text"),))
    result = translate_ru_to_en_once(
        "Текст.\n", client, file_path="ydb/docs/ru/text.md", manifest=MANIFEST
    )
    assert result.text == "Text.\n"
    assert [role for role, _model in client.calls].count("critic") == 1


def test_translate_file_verdict_blocked_on_unresolved():
    """A non-repairable RED finding blocks the all-or-nothing transaction."""
    finding = {
        "finding_id": "ignored",
        "rule_id": "protected_atom",
        "severity": "RED",
        "block_id": "s0001",
        "range": {"start": 0, "end": len(b"Problem.")},
        "atom_ids": [],
        "message": "Protected atom changed",
        "required_rule": "Preserve every source atom",
        "context": "Problem.",
        "repair_class": "not_repairable",
    }
    client = Client((("Проблема", "Problem"),), critic_findings=([finding],))
    result = run_translation_transaction(
        _plan("ydb/docs/ru/bad.md"),
        read_ru=lambda _path: "Проблема.\n",
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )
    assert not result.publishable
    assert result.staged == {}
    assert "local_repair_failed" in result.report["failures"][0]["message"]


def test_translate_file_verify_preserves_en_fence_bodies():
    """Legacy verify identity: read-only QA reports without mutating EN bytes."""
    source = "Введение.\n\n```mermaid\nparticipant Топик\n```\n"
    target = "Intro.\n\n```mermaid\nparticipant Topic\n```\n"
    before = target.encode()
    classified = run_file_heuristics_classified(
        source,
        target,
        normalized_source_text=source,
        source_lang="ru",
        target_lang="en",
        source_file="ydb/docs/ru/diagram.md",
    )
    assert target.encode() == before
    assert classified.all_non_info


@pytest.mark.parametrize("initial_verdict", ["ok", "blocked"])
def test_translate_file_heuristics_block_residual_cyrillic_without_writer(initial_verdict):
    """Legacy heuristic warning/downgrade identities now share one blocking gate."""
    source = "Текст для перевода.\n"
    target = "Text with привет inside.\n"
    before = target.encode()
    classified = run_file_heuristics_classified(
        source,
        target,
        normalized_source_text=source,
        source_lang="ru",
        target_lang="en",
        source_file="ydb/docs/ru/heuristics.md",
    )
    assert classified.blocking
    assert any("Кириллица в EN-тексте" in finding for finding in classified.blocking)
    assert target.encode() == before
    assert initial_verdict in {"ok", "blocked"}


def test_translate_file_blocks_on_empty_critic_response():
    """Invalid critic protocol exhausts bounded acquisition and publishes nothing."""
    class EmptyCriticClient(Client):
        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            if role == "critic":
                self.calls.append((role, explicit_model))
                return SimpleNamespace(content="")
            return super().chat_once(messages, explicit_model=explicit_model, role=role, **kwargs)

    client = EmptyCriticClient((("Привет", "Hello"),))
    result = run_translation_transaction(
        _plan("ydb/docs/ru/hello.md"),
        read_ru=lambda _path: "Привет.\n",
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )
    assert not result.publishable
    assert result.staged == {}
    assert [role for role, _model in client.calls].count("critic") == 2
    assert "translation_acquisition_exhausted: role=critic" in result.report["failures"][0]["message"]


def test_legacy_critic_only_entry_points_are_not_supported():
    """Legacy alignment/critic_only tests retire optional translate semantics."""
    with pytest.raises(TypeError):
        translate_ru_to_en_once(
            "Привет.\n",
            Client((("Привет", "Hello"),)),
            file_path="ydb/docs/ru/hello.md",
            enable_translate=False,
            existing_target_text="Hello.\n",
        )


def test_manifest_is_mandatory():
    """The old optional-manifest compatibility path is absent."""
    with pytest.raises(TypeError):
        translate_ru_to_en_once(
            "Привет.\n",
            Client((("Привет", "Hello"),)),
            file_path="ydb/docs/ru/hello.md",
        )
