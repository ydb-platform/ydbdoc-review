from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.unit.test_translator import _json_response, _segment
from ydbdoc_review.github.workflow import _publication_failure_error
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import (
    LLMModelUnavailableError,
    LLMParseError,
    LLMRetryExhaustedError,
)
from ydbdoc_review.ops.gates import (
    acl_deny_comment,
    quota_deny_comment,
    store_unavailable_comment,
)
from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.translator import translate_batch


@pytest.mark.parametrize(
    ("error", "diagnosis"),
    [
        (LLMModelUnavailableError("model unavailable"), "model unavailable"),
        (LLMParseError("incomplete response"), "incomplete response"),
        (LLMRetryExhaustedError("retries exhausted"), "retries exhausted"),
    ],
)
def test_F129_failure_kinds(error: Exception, diagnosis: str) -> None:
    assert diagnosis in str(error)
    assert type(error).__name__ != "LLMError"

    messages = {
        acl_deny_comment("guest"),
        quota_deny_comment(spent_rub=10, budget_rub=10, run_day="2026-09-13", mode="translate"),
        store_unavailable_comment(128, detail="YDB unavailable"),
    }
    assert len(messages) == 3


def test_F129_recovered_analyze() -> None:
    seg = _segment("s1", "Привет")
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.side_effect = [
        LLMRetryExhaustedError("model unavailable after retries"),
        SimpleNamespace(content=_json_response([{"id": "s1", "text": "Hello"}])),
    ]
    reasons: list[str] = []
    result = translate_batch(
        client,
        Batch(index=0, segments=[seg]),
        load_glossary(),
        file_path="docs/ru/x.md",
        fallback_reasons=reasons,
    )

    assert result == {"s1": "Hello"}
    assert reasons == ["primary -> fallback: model unavailable after retries"]
    assert "translation_failed" not in reasons

    publication_error = _publication_failure_error(
        stage="push",
        branch="ydbdoc-review/pr-128",
        candidate_sha="abc123",
        reason="old=/old.md target=/new.md owner=docs; update redirect",
        pr_number=128,
    )
    message = str(publication_error)
    assert all(part in message for part in ("/old.md", "/new.md", "docs", "update redirect"))
    assert "abc123" in message and "#128" in message
