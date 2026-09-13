"""Regression coverage for deterministic QA on protected-only files."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness import (
    TRANSLATE_PROFILE,
    TRANSLATE_WITH_QA_PROFILE,
    VERIFY_PROFILE,
    FileHarness,
    FileRunState,
    HarnessContext,
)
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.validation.include_targets import apply_include_target_checks

PROFILES = [TRANSLATE_PROFILE, TRANSLATE_WITH_QA_PROFILE, VERIFY_PROFILE]


def _completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _mock_client(responses: list[str]) -> YandexLLMClient:
    mock_openai = MagicMock()
    completion = mock_openai.chat.completions.create
    completion.side_effect = (
        [_completion(response) for response in responses]
        if responses
        else AssertionError("unexpected model call for protected-only file")
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    return YandexLLMClient(
        folder_id="b1x",
        api_key="k",
        llm=cfg.llm,
        client=mock_openai,
    )


@contextmanager
def _identity_optional_finalizer_model_helpers():
    def identity(text: str, *args, **kwargs) -> str:
        del args, kwargs
        return text

    with (
        patch(
            "ydbdoc_review.harness.render.translate_cyrillic_fence_comments_with_client",
            side_effect=identity,
        ),
        patch(
            "ydbdoc_review.harness.render.translate_cyrillic_text_fences_with_client",
            side_effect=identity,
        ),
        patch(
            "ydbdoc_review.harness.render.translate_cyrillic_prose_with_client",
            side_effect=identity,
        ),
    ):
        yield


def _run_protected(profile, source: str, *, target: str | None = None):
    client = _mock_client([])
    existing_target = source if target is None else target
    state = FileRunState(
        mode="verify" if profile is VERIFY_PROFILE else "translate",
        file_path="ydb/docs/ru/core/security/_assets/raw.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text=existing_target,
    )
    with _identity_optional_finalizer_model_helpers():
        result = FileHarness(profile).run(state, HarnessContext.from_options(client))
    return result, client


@pytest.mark.parametrize("profile", PROFILES)
def test_protected_only_residual_cyrillic_is_blocked(profile):
    text = "```yaml\nkey: Русский\n```\n"
    result, client = _run_protected(profile, text)

    assert result.verdict == "blocked"
    assert result.heuristic_blocking
    assert any(
        finding.startswith("cyrillic_in_code_fence:")
        for finding in result.heuristic_blocking
    )
    assert client._client.chat.completions.create.call_count == 0


@pytest.mark.parametrize("profile", PROFILES)
def test_protected_only_placeholder_leak_is_blocked(profile):
    text = "```yaml\nkey: ⟦C1⟧\n```\n"
    result, client = _run_protected(profile, text)

    assert result.verdict == "blocked"
    assert any(
        finding.startswith("unrestored_placeholder:")
        for finding in result.heuristic_blocking
    )
    assert client._client.chat.completions.create.call_count == 0


def test_include_only_missing_target_is_blocked(tmp_path):
    source_path = "ydb/docs/ru/core/security/include-only.md"
    target_path = "ydb/docs/en/core/security/include-only.md"
    text = "{% include [missing](missing.md) %}\n"
    client = _mock_client([])
    state = FileRunState(
        mode="translate",
        file_path=source_path,
        raw_source_text=text,
        source_text=text,
        existing_target_text=text,
    )
    file_result = FileHarness(TRANSLATE_PROFILE).run(
        state, HarnessContext.from_options(client)
    )
    pair = DocPair(ru_path=source_path, en_path=target_path)
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=source_path,
        target_path=target_path,
        source_lang="ru",
        target_lang="en",
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, file_result=file_result)]
    )

    apply_include_target_checks(result, repo_path=str(tmp_path))

    assert file_result.verdict == "blocked"
    assert any(
        finding.startswith("include_target:")
        and "ydb/docs/en/core/security/missing.md" in finding
        for finding in file_result.heuristic_blocking
    )
    assert client._client.chat.completions.create.call_count == 0


def test_protected_only_fence_damage_is_blocked():
    source = """{% list tabs %}

- Python

    ```python
    A

      ```python
      B

    ```python
    C

      ```

    - Native SDK

      ```python
      D
      ```

{% endlist %}
"""
    damaged_target = source + "```\n"
    result, client = _run_protected(
        TRANSLATE_PROFILE, source, target=damaged_target
    )

    assert result.verdict == "blocked"
    assert any(
        finding.startswith("fence_parity:") for finding in result.heuristic_blocking
    )
    assert client._client.chat.completions.create.call_count == 0


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    "text",
    ["", "```mermaid\ngraph LR\n A --> B\n```\n"],
    ids=["empty", "ascii-mermaid"],
)
def test_empty_and_ascii_protected_files_make_no_model_calls(profile, text):
    result, client = _run_protected(profile, text)

    assert result.verdict == "ok"
    assert result.final_text == text
    assert client._client.chat.completions.create.call_count == 0


def test_verify_empty_target_does_not_fall_back_to_ru():
    source = "```yaml\nkey: Русский\n```\n"
    result, client = _run_protected(VERIFY_PROFILE, source, target="")

    assert result.final_text == ""
    assert result.verdict == "blocked"
    assert result.heuristic_blocking
    assert client._client.chat.completions.create.call_count == 0
