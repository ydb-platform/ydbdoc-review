# ruff: noqa: RUF001
"""Read-only fragment detection and v010 source-owned href contracts."""

from __future__ import annotations

import json
import re
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
from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown

MANIFEST = TranslationJobManifest(TranslationModelPolicy(
    translate=ModelPair("translate-primary", "translate-fallback"),
    critic=ModelPair("critic-primary", "critic-fallback"),
    repair=ModelPair("repair-primary", "repair-fallback"),
))


class Client:
    def __init__(self, replacements=()) -> None:
        self.replacements = tuple(replacements)

    def chat_once(self, messages, *, explicit_model, role, **_kwargs):
        if role == "critic":
            return SimpleNamespace(content=json.dumps({"findings": []}))
        payload = json.loads(messages[-1]["content"])
        translated = []
        for item in payload["segments"]:
            text = item["text"]
            for source, target in self.replacements:
                text = text.replace(source, target)
            translated.append({"id": item["id"], "text": text})
        return SimpleNamespace(content=json.dumps({"segments": translated}, ensure_ascii=False))


class RaisingSpy:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError(f"retired {self.name} must be unreachable")


def _plan(*paths: str) -> DependencyPlan:
    return DependencyPlan(
        tuple(QueueEntry(path, "initial" if index == 0 else "auto_added") for index, path in enumerate(paths)),
        (),
        1,
        max(0, len(paths) - 1),
    )


def _href_atom(text: str) -> str:
    match = re.search(r"\[[^]]*]\([^\n]+\)", text)
    assert match is not None
    return match.group(0)


def test_fragment_declared_in_markdown():
    assert fragment_declared_in_markdown("## Sessions {#sessions}\n", "sessions")
    assert not fragment_declared_in_markdown("## Sessions {#sessions}\n", "ldap")


def test_v010_preserves_exact_ru_href_atom_without_fragment_writer():
    """Legacy identity: test_repair_keeps_valid_fragment."""
    source = 'See [node details](../node.md?q=a%20b#sessions "Title quoted").\n'
    baseline_reader = RaisingSpy("baseline EN reader")
    fragment_writer = RaisingSpy("fragment writer")
    result = translate_ru_to_en_once(
        source,
        Client(),
        file_path="ydb/docs/ru/core/concepts/glossary.md",
        manifest=MANIFEST,
    )
    assert _href_atom(result.text) == _href_atom(source)
    assert baseline_reader.calls == 0
    assert fragment_writer.calls == 0


def test_v010_cyrillic_anchor_proposal_rewrites_staged_inbound_links():
    """Legacy identity: test_pr_45949_client_cert_legacy_translit_fragment."""
    anchor = "включение-режима-аутентификации-и-авторизации-узлов"
    sources = {
        "ydb/docs/ru/core/reference/configuration/client_certificate_authorization.md": (
            f"when [registering dynamic nodes](../../devops/concepts/node-authorization.md#{anchor}).\n"
        ),
        "ydb/docs/ru/core/devops/concepts/node-authorization.md": (
            f"## Включение режима аутентификации и авторизации узлов {{#{anchor}}}\n\nТело.\n"
        ),
    }
    result = run_translation_transaction(
        _plan(*sources),
        read_ru=sources.__getitem__,
        client=Client((("Включение режима аутентификации и авторизации узлов", "Enabling node authentication and authorization"), ("Тело", "Body"))),
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
        pinned_en_paths={path.replace("/ru/", "/en/") for path in sources},
        read_pinned_en=lambda _path: None,
    )
    proposed = "enabling-node-authentication-and-authorization"
    assert result.publishable
    assert f"node-authorization.md#{proposed}" in result.staged[
        "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    ]
    assert f"{{#{proposed}}}" in result.staged[
        "ydb/docs/en/core/devops/concepts/node-authorization.md"
    ]
    assert anchor not in "".join(result.staged.values())
    assert result.report["link_findings"] == []


def test_v010_out_of_scope_inbound_anchor_change_blocks_transaction():
    """Legacy identity: test_pr_48012_sessions_finds_sibling_when_ru_and_en_baseline_stale."""
    source_path = "ydb/docs/ru/core/concepts/query_execution/execution_process.md"
    anchor = "сессии"
    outside = "ydb/docs/en/core/concepts/glossary.md"
    publish_calls: list[dict[str, str]] = []
    result = run_translation_transaction(
        _plan(source_path),
        read_ru=lambda _path: f"## Сессии {{#{anchor}}}\n",
        client=Client((("Сессии", "Sessions"),)),
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
        manifest=MANIFEST,
        pinned_en_paths={source_path.replace("/ru/", "/en/"), outside},
        read_pinned_en=lambda path: (
            f"[Sessions](query_execution/execution_process.md#{anchor})\n" if path == outside else None
        ),
    )
    if result.publishable:
        publish_calls.append(result.staged)
    assert not result.publishable
    assert result.staged == {}
    assert publish_calls == []
    assert result.report["anchor_findings"] == [{
        "category": "local_repair_failed",
        "terminal_reason": "out_of_scope_link",
        "source_file": outside,
        "original_href": f"query_execution/execution_process.md#{anchor}",
        "manual_action": "update this inbound fragment in an explicitly authorized source job",
    }]


def test_v010_never_prefers_or_mutates_to_baseline_en_href():
    """Legacy identity: test_pr_40385_prefers_valid_en_baseline_href_after_ru_restore."""
    source = "Configure [authentication](../reference/configuration/auth_config.md#security-auth).\n"
    baseline_href = "../reference/configuration/auth_config.md#authentication"
    baseline_reader = RaisingSpy("baseline EN reader")
    prefer_writer = RaisingSpy("prefer baseline href writer")
    fragment_writer = RaisingSpy("fragment writer")
    path = "ydb/docs/ru/core/security/authentication.md"
    result = run_translation_transaction(
        _plan(path),
        read_ru=lambda _path: source,
        client=Client(),
        to_en_path=lambda value: value.replace("/ru/", "/en/"),
        manifest=MANIFEST,
        pinned_en_paths={path.replace("/ru/", "/en/")},
        read_pinned_en=baseline_reader,
    )
    assert result.publishable
    assert "#security-auth" in result.staged[path.replace("/ru/", "/en/")]
    assert baseline_href not in result.staged[path.replace("/ru/", "/en/")]
    assert baseline_reader.calls == prefer_writer.calls == fragment_writer.calls == 0


@pytest.mark.parametrize(
    ("source", "replacements"),
    [
        ("Сессии [{#T}](query_execution/index.md#sessions).\n", (("Сессии", "Sessions"),)),
        ("Сессии [{#T}](query_execution/execution_process.md#sessions).\n", (("Сессии", "Sessions"),)),
        ("See [{#T}](../../../security/authentication.md#ldap).\n", ()),
        ("See [node](../../devops/deployment-options/manual/node-authorization.md#legacy).\n", ()),
        ("См. [view](../dev/system-views.md#информация-о-пользователях-users).\n", (("См", "See"),)),
        ("См. [SID](./authorization.md#sid).\n", (("См", "See"),)),
        ("See [table](../concepts/datamodel/table.md#partitioning).\n", ()),
    ],
    ids=[
        "test_pr_48047_sessions_prefers_en_baseline_path",
        "test_pr_48047_sessions_uses_ru_overlay_path_when_en_declares",
        "test_pr_48047_ldap_does_not_remap_to_en_only_fragment",
        "test_pr_40385_redirect_from_path_uses_live_ru_twin_and_existing_en_target",
        "test_pr_40385_system_views_users_fragment-and-localizes_to_declared_en_fragment",
        "test_pr_50976_sid_fragment_localizes_to_declared_en_fragment",
        "test_pr_48223_does_not_mangle_existing_targets_to_bare_basenames",
    ],
)
def test_remaining_legacy_writer_cases_preserve_exact_ru_href_atom(source, replacements):
    translated = translate_ru_to_en_once(
        source,
        Client(replacements),
        file_path="ydb/docs/ru/core/reference/page.md",
        manifest=MANIFEST,
    )
    assert _href_atom(translated.text) == _href_atom(source).replace("См.", "See.")


def test_fragment_declared_accepts_diplodoc_auto_slug():
    assert fragment_declared_in_markdown("### Parameters\n\nbody\n", "parameters")
