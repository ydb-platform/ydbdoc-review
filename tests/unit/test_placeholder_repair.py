"""Read-only publication gates for protected translation atoms."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.dependency_queue import DependencyPlan, QueueEntry
from ydbdoc_review.pipeline.translation_transaction import run_translation_transaction
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.local_repair import run_bounded_local_repair
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)
from ydbdoc_review.translation.one_pass import (
    OnePassTranslationError,
    assert_no_protect_token,
    build_complete_document_validation_context,
    translate_ru_to_en_once,
    validate_complete_document,
)

RU_TABLE = "| Параметр | Описание |\n| --- | --- |\n| `--force` | Плохое описание. |\n"
EN_TABLE = "| Parameter | Description |\n| --- | --- |\n| `--force` | Bad description. |\n"
SOURCE_PATH = "ydb/docs/ru/table.md"
OUTPUT_PATH = "ydb/docs/en/table.md"
MANIFEST = TranslationJobManifest(
    TranslationModelPolicy(
        translate=ModelPair("translate-primary", "translate-fallback"),
        critic=ModelPair("critic-primary", "critic-fallback"),
        repair=ModelPair("repair-primary", "repair-fallback"),
    )
)


def _plan() -> DependencyPlan:
    return DependencyPlan((QueueEntry(SOURCE_PATH, "initial"),), (), 1, 0)


def _table_shape(text: str) -> tuple[int, int, int]:
    rows = text.splitlines()
    return 1, len(rows) - 2, sum(row.count("|") - 1 for row in rows)


class _TableRepairClient:
    def __init__(self, *, accept_fallback: bool) -> None:
        self.accept_fallback = accept_fallback
        self.calls: list[tuple[str, str, int]] = []
        self.critic_documents: list[str] = []
        self.invalid_candidates: list[str] = []

    def chat_once(self, messages, *, explicit_model, role, **_kwargs):
        response_index = sum(call[0] == role for call in self.calls)
        self.calls.append((role, explicit_model, response_index))
        body = json.loads(messages[-1]["content"])
        if role == "translate":
            translations = {
                "Параметр": "Parameter",
                "Описание": "Description",
                "Плохое описание.": "Bad description.",
            }
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "segments": [
                            {"id": item["id"], "text": translations.get(item["text"], item["text"])}
                            for item in body["segments"]
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        if role == "critic":
            records = body["block_records"]
            self.critic_documents.append(
                "\n".join(record["en_editable_prose"] for record in records.values())
            )
            target = next(
                (
                    (block_id, record)
                    for block_id, record in records.items()
                    if "Bad description." in record["en_editable_prose"]
                ),
                None,
            )
            if target is None:
                return SimpleNamespace(content=json.dumps({"findings": []}))
            block_id, record = target
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "findings": [
                            {
                                "rule_id": "grammar",
                                "severity": "RED",
                                "block_id": block_id,
                                "range": {"start": 0, "end": len(b"Bad description.")},
                                "atom_ids": [atom["id"] for atom in record["atom_manifest"]],
                                "message": "Replace unsafe prose",
                                "required_rule": "Use safe English prose",
                                "context": record["en_editable_prose"],
                                "repair_class": "prose",
                            }
                        ]
                    }
                )
            )
        replacement = (
            "Safe description."
            if self.accept_fallback and explicit_model == "repair-fallback"
            else "`--delete` | Unsafe description."
        )
        if replacement != "Safe description.":
            self.invalid_candidates.append(replacement)
        return SimpleNamespace(
            content=json.dumps(
                {
                    "finding_id": body["finding_id"],
                    "block_id": body["block_id"],
                    "replacement": replacement,
                }
            )
        )


@pytest.mark.parametrize("marker", ["⟦U1⟧", "%E2%9F%A6U1%E2%9F%A7"])
def test_protect_marker_detection(marker: str) -> None:
    with pytest.raises(OnePassTranslationError, match="unrestored_protect_token"):
        assert_no_protect_token(f"English {marker}")


def test_protect_marker_blocks_publication() -> None:
    with pytest.raises(OnePassTranslationError, match="unrestored_protect_token"):
        assert_no_protect_token("| English | ⟦U2⟧ |")


def test_protect_marker_transaction_stages_nothing() -> None:
    publication_calls: list[dict[str, str]] = []

    class MarkerClient:
        def chat_once(self, messages, *, explicit_model, role, **_kwargs):
            assert role == "translate"
            body = json.loads(messages[-1]["content"])
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "segments": [
                            {
                                "id": item["id"],
                                "text": "%E2%9F%A6U1%E2%9F%A7",
                            }
                            for item in body["segments"]
                        ]
                    }
                )
            )

    result = run_translation_transaction(
        _plan(),
        read_ru=lambda _path: "Текст.\n",
        client=MarkerClient(),
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )
    if result.publishable:
        publication_calls.append(result.staged)

    assert result.publishable is False
    assert result.staged == {}
    assert publication_calls == []
    assert result.report["failures"] == [
        {
            "file": SOURCE_PATH,
            "category": "translation_failed",
            "message": "unrestored_protect_token",
        }
    ]
    assert result.report["files"][0]["output_file"] == OUTPUT_PATH


def test_table_cell_invalid_primary_is_rejected_inside_acquisition_and_fallback_accepted() -> None:
    client = _TableRepairClient(accept_fallback=True)

    result = translate_ru_to_en_once(
        RU_TABLE, client, file_path=SOURCE_PATH, manifest=MANIFEST
    )

    assert "Safe description." in result.text
    assert result.text.count("`--force`") == 1
    assert "--delete" not in result.text
    assert _table_shape(result.text) == _table_shape(EN_TABLE)
    assert [call for call in client.calls if call[0] == "repair"] == [
        ("repair", "repair-primary", 0),
        ("repair", "repair-fallback", 1),
    ]
    assert all(candidate not in result.text for candidate in client.invalid_candidates)
    assert client.critic_documents == [
        "Parameter\nDescription\n⟦C1⟧\nBad description.",
        "Parameter\nDescription\n⟦C1⟧\nSafe description.",
    ]


def test_table_cell_exhausted_invalid_candidates_keep_pre_repair_bytes_and_stage_nothing() -> None:
    segments = extract_segments(parse_markdown(RU_TABLE))
    validation_context = build_complete_document_validation_context(
        RU_TABLE, SOURCE_PATH, segments, ()
    )
    client = _TableRepairClient(accept_fallback=False)

    local = run_bounded_local_repair(
        EN_TABLE,
        RU_TABLE,
        client,
        critic_models=MANIFEST.model_policy.critic,
        repair_models=MANIFEST.model_policy.repair,
        validation_context=validation_context,
        validate_complete_document=validate_complete_document,
        source_file=SOURCE_PATH,
    )

    assert local.text.encode() == EN_TABLE.encode()
    assert local.reports[-1]["terminal_reason"] == "attempts_exhausted"
    assert local.reports[-1]["source_file"] == SOURCE_PATH
    assert local.reports[-1]["output_file"] == OUTPUT_PATH
    assert all(attempt["outcome"] != "recritic" for attempt in local.reports[-1]["attempts"])
    assert all(candidate not in local.text for candidate in client.invalid_candidates)

    transaction = run_translation_transaction(
        _plan(),
        read_ru=lambda _path: RU_TABLE,
        client=client,
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
    )

    assert transaction.publishable is False
    assert transaction.staged == {}
    assert transaction.report["failures"][0]["file"] == SOURCE_PATH
    assert transaction.report["files"][0]["output_file"] == OUTPUT_PATH
    assert all(candidate not in local.text for candidate in client.invalid_candidates)
    assert all(candidate not in "".join(transaction.staged.values()) for candidate in client.invalid_candidates)
