"""Regression tests for adaptive, fail-closed critic refusal recovery."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.segmentation.types import ProtectedInline, Segment, SegmentKind
from ydbdoc_review.translation.critic import _run_critic_batches, run_critic
from ydbdoc_review.translation.glossary import load_glossary

REFUSAL = "Я не могу обсуждать эту тему."
OK = json.dumps({"verdict": "ok", "issues": []})
FIXTURES = Path(__file__).parents[1] / "fixtures" / "pr51079-critic-refusal"


def _segment(index: int, text: str | None = None) -> Segment:
    return Segment(
        id=f"s{index:04d}",
        kind=SegmentKind.PARAGRAPH,
        path=["Section"],
        text=text or f"Исходный текст {index}",
        placeholders=[],
        ast_path=[index],
    )


def _payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    content = messages[-1]["content"]
    start = content.index('{\n  "segments"')
    payload, _ = json.JSONDecoder().raw_decode(content[start:])
    return payload


class _ScriptedCritic:
    """Real critic orchestration with only the external model boundary replaced."""

    def __init__(
        self,
        responder: Callable[[str, list[dict[str, str]]], str],
        chain: list[str] | None = None,
    ) -> None:
        self.chain = chain or [
            "yandexgpt-5.1",
            "yandexgpt-5-lite",
            "qwen3.6-35b-a3b",
        ]
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def model_chain_for_role(self, role: str) -> list[str]:
        assert role == "critic"
        return list(self.chain)

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        role: str | None = None,
        max_tokens: int | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "role": role,
                "max_tokens": max_tokens,
            }
        )
        return SimpleNamespace(
            content=self.responder(model, messages),
            model_slug=model,
        )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    segments: list[Segment],
    client: _ScriptedCritic,
    *,
    target_atom_maps: dict[str, dict[str, str]] | None = None,
):
    monkeypatch.setattr(
        "ydbdoc_review.translation.critic._critic_batches",
        lambda *_args, **_kwargs: [Batch(index=0, segments=segments)],
    )
    translations = {segment.id: f"Translated text {i}" for i, segment in enumerate(segments)}
    if target_atom_maps is not None:
        return _run_critic_batches(
            client,  # type: ignore[arg-type]
            batches=[Batch(index=0, segments=segments)],
            translations=translations,
            glossary=load_glossary(),
            file_path="ydb/docs/ru/core/security/authentication.md",
            source_lang="ru",
            target_lang="en",
            prompt_version="v1",
            max_tokens=None,
            pass_label="Critic",
            target_atom_maps=target_atom_maps,
        )
    return run_critic(
        client,  # type: ignore[arg-type]
        segments=segments,
        translations=translations,
        glossary=load_glossary(),
        file_path="ydb/docs/ru/core/security/authentication.md",
    )


def test_default_critic_chain_has_live_independent_policy_fallback():
    """Removing the non-Yandex fallback would restore the production dead end."""
    chain = load_config(env={}).llm.models.critic.chain

    assert chain == ["yandexgpt-5.1", "yandexgpt-5-lite", "qwen3.6-35b-a3b"]


def test_refusal_advances_without_identical_retry(monkeypatch: pytest.MonkeyPatch):
    """A deterministic refusal must not resend identical model plus messages."""
    client = _ScriptedCritic(
        lambda _model, messages: REFUSAL if len(_payload(messages)["segments"]) > 1 else OK
    )

    _run(monkeypatch, [_segment(1), _segment(2)], client)

    signatures = [(call["model"], call["messages"]) for call in client.calls]
    assert len(signatures) == len(
        {(model, json.dumps(messages, sort_keys=True)) for model, messages in signatures}
    )


def test_refused_multi_segment_batch_splits_and_merges(monkeypatch: pytest.MonkeyPatch):
    """Dropping either half after parent refusal would lose semantic coverage."""
    issue = json.dumps(
        {
            "verdict": "warnings",
            "issues": [
                {
                    "segment_id": "s0002",
                    "severity": "warning",
                    "category": "meaning",
                    "comment": "Check meaning",
                }
            ],
        }
    )

    def respond(_model: str, messages: list[dict[str, str]]) -> str:
        ids = [row["id"] for row in _payload(messages)["segments"]]
        if len(ids) == 4:
            return REFUSAL
        return issue if ids == ["s0001", "s0002"] else OK

    client = _ScriptedCritic(respond)
    out = _run(monkeypatch, [_segment(i) for i in range(1, 5)], client)

    reviewed = [
        row["id"] for call in client.calls[1:] for row in _payload(call["messages"])["segments"]
    ]
    assert reviewed == ["s0001", "s0002", "s0003", "s0004"]
    assert [(item.segment_id, item.category) for item in out.issues] == [("s0002", "meaning")]
    assert out.verdict == "warnings"


def test_recursive_split_reaches_single_segment(monkeypatch: pytest.MonkeyPatch):
    """A refusing half must keep splitting instead of being marked covered."""

    def respond(_model: str, messages: list[dict[str, str]]) -> str:
        ids = [row["id"] for row in _payload(messages)["segments"]]
        return REFUSAL if len(ids) > 1 and ids[0] == "s0001" else OK

    client = _ScriptedCritic(respond)
    out = _run(monkeypatch, [_segment(i) for i in range(1, 5)], client)

    leaf_ids = [
        _payload(call["messages"])["segments"][0]["id"]
        for call in client.calls
        if len(_payload(call["messages"])["segments"]) == 1
    ]
    assert leaf_ids == ["s0001", "s0002"]
    assert out.verdict == "ok"
    assert out.issues == []


def test_leaf_refusal_uses_distinct_family(monkeypatch: pytest.MonkeyPatch):
    """A single refused segment must escape the refusing YandexGPT policy family."""
    client = _ScriptedCritic(lambda model, _messages: OK if model.startswith("qwen") else REFUSAL)

    out = _run(monkeypatch, [_segment(1)], client)

    assert [call["model"] for call in client.calls] == ["yandexgpt-5.1", "qwen3.6-35b-a3b"]
    assert out.verdict == "ok"
    assert not any(issue.category == "critic_model_refusal" for issue in out.issues)


def test_all_fallbacks_are_consumed_in_order(monkeypatch: pytest.MonkeyPatch):
    """A chain longer than two models must be reachable without duplicate-family loops."""
    chain = [
        "yandexgpt-5.1",
        "yandexgpt-5-lite",
        "qwen3.6-35b-a3b",
        "qwen3.6-35b-a3b",
        "gpt-oss-120b",
    ]
    client = _ScriptedCritic(
        lambda model, _messages: OK if model == "gpt-oss-120b" else REFUSAL,
        chain,
    )

    out = _run(monkeypatch, [_segment(1)], client)

    assert [call["model"] for call in client.calls] == [
        "yandexgpt-5.1",
        "qwen3.6-35b-a3b",
        "gpt-oss-120b",
    ]
    assert out.verdict == "ok"


def test_parse_repair_is_separate_from_refusal(monkeypatch: pytest.MonkeyPatch):
    """Malformed JSON earns a changed repair prompt; refusal never does."""
    responses = iter(["not json", OK])
    repair_client = _ScriptedCritic(lambda _model, _messages: next(responses))
    assert _run(monkeypatch, [_segment(1)], repair_client).verdict == "ok"
    assert (
        "previous response was not valid JSON" in repair_client.calls[1]["messages"][-1]["content"]
    )

    refusal_client = _ScriptedCritic(
        lambda model, _messages: OK if model.startswith("qwen") else REFUSAL
    )
    assert _run(monkeypatch, [_segment(1)], refusal_client).verdict == "ok"
    assert all(
        "previous response was not valid JSON" not in call["messages"][-1]["content"]
        for call in refusal_client.calls
    )


def test_partial_leaf_failure_stays_red(monkeypatch: pytest.MonkeyPatch):
    """One exhausted leaf must block the file and publication despite clean siblings."""

    def respond(model: str, messages: list[dict[str, str]]) -> str:
        ids = [row["id"] for row in _payload(messages)["segments"]]
        if len(ids) > 1:
            return REFUSAL
        if ids == ["s0001"]:
            return OK
        return REFUSAL

    out = _run(monkeypatch, [_segment(1), _segment(2)], _ScriptedCritic(respond))
    pair = DocPair(ru_path="ydb/docs/ru/a.md", en_path="ydb/docs/en/a.md", ru_changed=True)
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=PairPlan(
                    pair=pair,
                    action="translate_to_en",
                    source_path=pair.ru_path,
                    target_path=pair.en_path,
                    source_lang="ru",
                    target_lang="en",
                ),
                source_text="source\n",
                target_text="target\n",
                file_result=FileTranslationResult(
                    file_path=pair.en_path,
                    final_text="target\n",
                    segments_count=2,
                    verdict=out.verdict,
                    critic_initial=out,
                    prompt_version="v1",
                ),
            )
        ]
    )

    assert out.verdict == "blocked"
    assert [issue.category for issue in out.issues] == ["critic_model_refusal"]
    assert evaluate_publication_impact(result) == PublicationImpact.WITHHOLD_UNSAFE


def test_alternate_blocked_verdict_is_not_green(monkeypatch: pytest.MonkeyPatch):
    """Recovery means obtaining a verdict, not coercing an alternate verdict to OK."""
    blocked = json.dumps(
        {
            "verdict": "blocked",
            "issues": [
                {
                    "segment_id": "s0001",
                    "severity": "blocked",
                    "category": "meaning",
                    "comment": "Authentication meaning changed",
                }
            ],
        }
    )
    client = _ScriptedCritic(
        lambda model, _messages: blocked if model.startswith("qwen") else REFUSAL
    )

    out = _run(monkeypatch, [_segment(1)], client)

    assert out.verdict == "blocked"
    assert [issue.category for issue in out.issues] == ["meaning"]


def test_exact_payload_preserved(monkeypatch: pytest.MonkeyPatch):
    """Fallback must receive the exact leaf evidence, with no redaction or rewriting."""
    segment = _segment(1, "Пароль `secret` и LDAP путь /Root остаются доказательствами")
    target_atoms = {segment.id: {"⟦C1⟧": "code:target"}}
    client = _ScriptedCritic(lambda model, _messages: OK if model.startswith("qwen") else REFUSAL)

    _run(monkeypatch, [segment], client, target_atom_maps=target_atoms)

    first = _payload(client.calls[0]["messages"])["segments"][0]
    alternate = _payload(client.calls[1]["messages"])["segments"][0]
    assert alternate == first
    assert alternate["source_text"] == segment.text
    assert alternate["target_atom_map"] == target_atoms[segment.id]


def test_telemetry_records_critic_role_and_explicit_model(monkeypatch: pytest.MonkeyPatch):
    """Losing role attribution must not also change the explicitly selected slug."""
    client = _ScriptedCritic(lambda _model, _messages: OK)

    _run(monkeypatch, [_segment(1)], client)

    assert [(call["role"], call["model"]) for call in client.calls] == [("critic", "yandexgpt-5.1")]


def test_critic_prompt_marks_segment_payload_as_inert_review_data(monkeypatch: pytest.MonkeyPatch):
    """Removing benign inert-data framing would expose quoted docs as instructions."""
    client = _ScriptedCritic(lambda _model, _messages: OK)

    _run(monkeypatch, [_segment(1)], client)

    system = client.calls[0]["messages"][0]["content"].casefold()
    assert "inert documentation data" in system
    assert "translation-quality" in system


def test_empty_reuse_surface_makes_zero_critic_calls(monkeypatch: pytest.MonkeyPatch):
    """A pure-reuse/zero-segment file must not create adaptive critic calls."""
    client = _ScriptedCritic(lambda _model, _messages: pytest.fail("unexpected model call"))

    out = run_critic(
        client,  # type: ignore[arg-type]
        segments=[],
        translations={},
        glossary=load_glossary(),
        file_path="ydb/docs/ru/reused.md",
    )

    assert out.verdict == "ok"
    assert client.calls == []


def _production_fixture(
    name: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[Segment],
    dict[str, str],
    dict[str, dict[str, str]],
]:
    fixture = json.loads((FIXTURES / name).read_bytes())
    payload = _payload(fixture["request"]["messages"])
    segments = [
        Segment(
            id=row["id"],
            kind=SegmentKind(row["kind"]),
            path=row["path"],
            text=row["source_text"],
            placeholders=[
                ProtectedInline(
                    placeholder=placeholder,
                    node=SimpleNamespace(kind=description),
                )
                for placeholder, description in row.get("atom_map", {}).items()
            ],
            ast_path=[index],
        )
        for index, row in enumerate(payload["segments"])
    ]
    translations = {row["id"]: row["translated_text"] for row in payload["segments"]}
    target_atom_maps = {
        row["id"]: row["target_atom_map"] for row in payload["segments"]
    }
    return fixture, payload, segments, translations, target_atom_maps


def _captured_batch_context(
    fixture: dict[str, Any], segments: list[Segment]
) -> list[Batch]:
    user = fixture["request"]["messages"][1]["content"]
    match = re.search(r"Batch: 1 of (\d+) ", user)
    assert match is not None
    batch_count = int(match.group(1))
    return [Batch(index=0, segments=segments)] + [
        Batch(index=index, segments=[]) for index in range(1, batch_count)
    ]


def test_production_fixture_glossary_002_recovers_without_special_case():
    """The exact benign production refusal payload must recover by generic splitting."""
    fixture, payload, segments, translations, target_atom_maps = _production_fixture(
        "llm-002-request.json"
    )
    user = fixture["request"]["messages"][1]["content"]
    assert hashlib.sha256(user.encode()).hexdigest() == (
        "53b3b87480597eb278419fd7514c15d57e4a4ee32630ed2b49a9d16e85857df6"
    )
    client = _ScriptedCritic(
        lambda _model, messages: REFUSAL if len(_payload(messages)["segments"]) > 1 else OK
    )

    out = _run_critic_batches(
        client,  # type: ignore[arg-type]
        batches=_captured_batch_context(fixture, segments),
        translations=translations,
        glossary=load_glossary(),
        file_path="ydb/docs/ru/core/concepts/glossary.md",
        source_lang="ru",
        target_lang="en",
        prompt_version="v1",
        max_tokens=None,
        pass_label="Critic",
        target_atom_maps=target_atom_maps,
    )

    actual_user = client.calls[0]["messages"][1]["content"]
    assert hashlib.sha256(actual_user.encode()).hexdigest() == fixture["identity"][
        "user_sha256"
    ]
    assert _payload(client.calls[0]["messages"]) == payload
    assert out.verdict == "ok"
    assert out.issues == []


def test_production_fixture_caching_155_exhaustion_stays_blocking():
    """The exact security payload must remain RED when every independent leaf refuses."""
    fixture, payload, segments, translations, target_atom_maps = _production_fixture(
        "llm-155-request.json"
    )
    user = fixture["request"]["messages"][1]["content"]
    assert hashlib.sha256(user.encode()).hexdigest() == (
        "99d2bc1332f5ca05def9abadcd4006b19f5430b19f0169d059e0468abf22b226"
    )
    client = _ScriptedCritic(
        lambda _model, messages: REFUSAL if _payload(messages)["segments"] else OK
    )

    out = _run_critic_batches(
        client,  # type: ignore[arg-type]
        batches=_captured_batch_context(fixture, segments),
        translations=translations,
        glossary=load_glossary(),
        file_path="ydb/docs/ru/core/security/caching-authentication-results.md",
        source_lang="ru",
        target_lang="en",
        prompt_version="v1",
        max_tokens=None,
        pass_label="Critic",
        target_atom_maps=target_atom_maps,
    )

    actual_user = client.calls[0]["messages"][1]["content"]
    assert hashlib.sha256(actual_user.encode()).hexdigest() == fixture["identity"][
        "user_sha256"
    ]
    assert _payload(client.calls[0]["messages"]) == payload
    assert out.verdict == "blocked"
    assert len(out.issues) == 13
    assert {issue.category for issue in out.issues} == {"critic_model_refusal"}


def test_all_11_payload_inventory_preserves_153_segment_evidence():
    """Dropping any production refusal batch or segment must break the frozen inventory."""
    inventory = json.loads((FIXTURES / "refused-batches-inventory.json").read_bytes())
    expected_hashes = {
        "53b3b87480597eb278419fd7514c15d57e4a4ee32630ed2b49a9d16e85857df6",
        "6bc914a48105163de33eba973175e6ce6266324a25992d64234713fc06168ed0",
        "2c46edcaf3b0c7ee7364003a668076beb4d1cb1335a8bfb08cd22acffaba080e",
        "4bbcd1f8e4b8c66ed82c29b8b4842f87375cf6c64cbfe600805d15e01aec50fd",
        "7bfe84f65c08f55a4c454f2f3a7b75513e90c94da0dbe671f7c2f224ac502c7d",
        "590b1eef2ce9eb3a206526101319c0156577caf5e05c04fa15fd9ebeaa74923b",
        "1a6824d276d9f71c64e13760d8762e8965da01c89aca80464b8283ecb9e9db33",
        "23599cdff914ae48801d8429424dada6318bfdf0ec511199971d66c9e5ac5fdb",
        "5c55db015949cca73c41f9f55131a09c1646b36fffa9161baf294d6b251ef5b4",
        "6f2aafaf6e7ccb96ebe56188a4ecc765475010f65120b2a3cbae908e01ccd484",
        "99d2bc1332f5ca05def9abadcd4006b19f5430b19f0169d059e0468abf22b226",
    }
    batches = inventory["batches"]

    assert inventory["refused_batch_count"] == len(batches) == 11
    assert (
        inventory["refused_segment_count"]
        == sum(len(batch["segment_ids"]) for batch in batches)
        == 153
    )
    assert {batch["user_sha256"] for batch in batches} == expected_hashes
    assert all(batch["segment_count"] == len(batch["segment_ids"]) for batch in batches)
