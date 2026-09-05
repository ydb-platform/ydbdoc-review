"""Tests for segment translator (mocked LLM)."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError, LLMRetryExhaustedError
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.chunker import (
    Batch,
    chunk_segments,
    estimate_translate_batch_output_chars,
)
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.translation.translator import (
    parse_translate_response,
    translate_batch,
    translate_segments,
    validate_segment_translation,
)


def _segment(seg_id: str, text: str) -> Segment:
    return Segment(
        id=seg_id,
        kind=SegmentKind.PARAGRAPH,
        path=["Intro"],
        text=text,
        placeholders=[],
        ast_path=[0],
    )


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


def _json_response(segments: list[dict[str, str]]) -> str:
    return json.dumps({"segments": segments}, ensure_ascii=False)


def test_parse_translate_response_ok():
    raw = _json_response([{"id": "s1", "text": "Hello"}])
    out = parse_translate_response(raw, expected_ids={"s1"})
    assert out == {"s1": "Hello"}


def test_parse_translate_response_id_mismatch():
    raw = _json_response([{"id": "s2", "text": "x"}])
    with pytest.raises(LLMParseError, match="missing ids"):
        parse_translate_response(raw, expected_ids={"s1"})


def test_parse_translate_response_ignores_echoed_input_fields():
    raw = json.dumps(
        {
            "segments": [
                {
                    "id": "s1",
                    "kind": "paragraph",
                    "path": ["Intro"],
                    "text": "Hello",
                }
            ]
        },
        ensure_ascii=False,
    )
    out = parse_translate_response(raw, expected_ids={"s1"})
    assert out == {"s1": "Hello"}


def test_validate_segment_placeholder_mismatch():
    seg = _segment("s1", "Use ⟦C1⟧")
    with pytest.raises(TranslationValidationError, match="placeholder"):
        validate_segment_translation(seg, "Use ⟦C2⟧")


def test_translate_batch_success():
    seg = _segment("s1", "Привет")
    batch = Batch(index=0, segments=[seg])
    client = _mock_client([_json_response([{"id": "s1", "text": "Hello"}])])
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Hello"}


def test_translate_batch_falls_back_to_single_segment():
    seg1 = _segment("s1", "A")
    seg2 = _segment("s2", "B")
    batch = Batch(index=0, segments=[seg1, seg2])
    # Batch call returns wrong ids → parse error → per-segment retry
    client = _mock_client(
        [
            _json_response([{"id": "s1", "text": "only one"}]),
            _json_response([{"id": "s1", "text": "only one"}]),
            _json_response([{"id": "s1", "text": "only one"}]),
            _json_response([{"id": "s1", "text": "Alpha"}]),
            _json_response([{"id": "s2", "text": "Beta"}]),
        ]
    )
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Alpha", "s2": "Beta"}


def test_translate_segments_uses_cache():
    seg = _segment("s1", "Same text")
    cache: dict[str, str] = {}
    client = _mock_client([_json_response([{"id": "s1", "text": "Cached"}])])

    first = translate_segments(
        [seg],
        client,
        load_glossary(),
        file_path="docs/ru/x.md",
        cache=cache,
    )
    assert first == {"s1": "Cached"}
    assert len(cache) == 1

    # Second call: no new LLM request
    client2 = _mock_client([])
    second = translate_segments(
        [seg],
        client2,
        load_glossary(),
        file_path="docs/ru/x.md",
        cache=cache,
    )
    assert second == {"s1": "Cached"}


def test_translate_batch_rejects_dropped_cli_flag():
    seg = _segment("s1", "Use --yaml-config here")
    batch = Batch(index=0, segments=[seg])
    client = _mock_client(
        [_json_response([{"id": "s1", "text": "Use yaml-config here"}])] * 3
    )
    with pytest.raises(TranslationValidationError, match="CLI"):
        translate_batch(
            client, batch, load_glossary(), file_path="docs/ru/x.md"
        )


def test_translate_batch_realigns_renumbered_placeholders():
    seg = _segment("s1", "Use ⟦C1⟧ flag")
    batch = Batch(index=0, segments=[seg])
    client = _mock_client(
        [_json_response([{"id": "s1", "text": "Use ⟦C99⟧ flag"}])]
    )
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Use ⟦C1⟧ flag"}


def test_translate_segments_keeps_ru_table_on_placeholder_failure():
    seg = Segment(
        id="s1",
        kind=SegmentKind.TABLE_BODY_CELL,
        path=["table:row1:col2"],
        text="Значение `stdin` и [ссылка](⟦U1⟧)",
        placeholders=[],
        ast_path=[0],
    )
    bad = _json_response([{"id": "s1", "text": "Value stdin only"}])
    client = _mock_client([bad, bad, bad, bad, bad])
    notes: list[str] = []
    cache: dict[str, str] = {}
    out = translate_segments(
        [seg],
        client,
        load_glossary(),
        file_path="docs/ru/x.md",
        cache=cache,
        manual_actions=notes,
    )
    assert out == {"s1": seg.text}
    assert notes
    assert "Переведите вручную" in notes[0].message
    assert cache == {}


def test_translate_batch_placeholder_mismatch_tries_fallback_model():
    seg = _segment("s1", "Use ⟦C1⟧ flag")
    batch = Batch(index=0, segments=[seg])
    # Primary model keeps breaking placeholders for 3 attempts.
    bad = _json_response([{"id": "s1", "text": "Use ⟦C2⟧ flag"}])
    # First fallback returns valid output.
    good = _json_response([{"id": "s1", "text": "Use ⟦C1⟧ flag"}])
    client = _mock_client([bad, bad, bad, good])
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Use ⟦C1⟧ flag"}


def test_translate_batch_retries_homoglyph_cyrillic_then_accepts_clean_en():
    seg = _segment("s1", "Это можно сделать")
    batch = Batch(index=0, segments=[seg])
    bad = _json_response([{"id": "s1", "text": "This сould be done"}])
    good = _json_response([{"id": "s1", "text": "This could be done"}])
    client = _mock_client([bad, good])

    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )

    assert out == {"s1": "This could be done"}


def test_translate_batch_all_models_with_cyrillic_fail_closed():
    seg = _segment("s1", "Привет")
    batch = Batch(index=0, segments=[seg])
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.return_value = SimpleNamespace(
        content=_json_response([{"id": "s1", "text": "Hello мир"}])
    )

    with pytest.raises(TranslationValidationError, match="Cyrillic remains"):
        translate_batch(
            client, batch, load_glossary(), file_path="docs/ru/x.md"
        )

    # Three batch attempts plus the existing per-segment repair path. No bad
    # candidate may be accepted from either model.
    assert client.chat.call_count >= 6


def test_translate_batch_rate_limit_tries_fallback_model():
    seg = _segment("s1", "Привет")
    batch = Batch(index=0, segments=[seg])
    good = _json_response([{"id": "s1", "text": "Hello"}])
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]

    exhausted = LLMRetryExhaustedError(
        "Eliza rate-limit (429) retries exhausted (primary): HTTP 429"
    )

    def chat_side_effect(*_args, **kwargs):
        if kwargs.get("model") == "primary":
            raise exhausted
        return SimpleNamespace(content=good)

    client.chat.side_effect = chat_side_effect
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Hello"}
    assert client.chat.call_count == 2


def test_translate_batch_timeout_tries_fallback_model():
    """§6.230 / #40385: monitoring_config timed out on deepseek-only chain."""
    seg = _segment("s1", "Привет")
    batch = Batch(index=0, segments=[seg])
    good = _json_response([{"id": "s1", "text": "Hello"}])
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]

    exhausted = LLMRetryExhaustedError(
        "All models exhausted (primary): Request timed out."
    )

    def chat_side_effect(*_args, **kwargs):
        if kwargs.get("model") == "primary":
            raise exhausted
        return SimpleNamespace(content=good)

    client.chat.side_effect = chat_side_effect
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Hello"}
    assert client.chat.call_count == 2


def test_r_gl_4_translate_resplit_on_length():
    seg1 = _segment("s1", "Alpha text")
    seg2 = _segment("s2", "Beta text")
    batch = Batch(index=0, segments=[seg1, seg2])
    good_s1 = _json_response([{"id": "s1", "text": "Alpha"}])
    good_s2 = _json_response([{"id": "s2", "text": "Beta"}])
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary"]
    client.chat.side_effect = [
        SimpleNamespace(content=""),
        SimpleNamespace(content=good_s1),
        SimpleNamespace(content=good_s2),
    ]
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Alpha", "s2": "Beta"}
    assert client.chat.call_count == 3


def test_r_gl_4_irreducible_segment_raises_manual_action():
    seg = _segment("s1", "X" * 5000)
    batch = Batch(index=0, segments=[seg])
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary"]
    client.chat.return_value = SimpleNamespace(content="")
    actions: list[ManualAction] = []
    with pytest.raises(TranslationValidationError, match="safe translate output budget"):
        translate_batch(
            client,
            batch,
            load_glossary(),
            file_path="docs/ru/x.md",
            manual_actions=actions,
        )
    assert actions
    assert actions[0].segment_id == "s1"
    assert "safe translate output budget" in actions[0].message


# Regression fixture: exact bytes of
# d9fc9f993eb7:ydb/docs/ru/core/reference/configuration/auth_config.md.
_AUTH_CONFIG_D9FC = (
    Path(__file__).parent.parent
    / "fixtures"
    / "markdown_files"
    / "auth_config_d9fc9f993eb7.md"
)
_AUTH_CONFIG_D9FC_SHA256 = (
    "dfacc38cec25371da34a1f456f26c1e4f9cdcfebce3a3b8b8fa28c84def01abc"
)


def _auth_config_segments() -> list[Segment]:
    # ``apply_patch`` materializes a terminal newline; the merge-commit blob
    # deliberately has none, so remove only that transport artifact.
    source = _AUTH_CONFIG_D9FC.read_text(encoding="utf-8").removesuffix("\n")
    assert sha256(source.encode()).hexdigest() == _AUTH_CONFIG_D9FC_SHA256
    return extract_segments(parse_markdown(source))


def _ascii_candidate(source: str) -> str:
    """A model double's structurally valid EN-shaped response, not production logic."""
    return re.sub(r"[\u0410-\u042f\u0430-\u044f\u0401\u0451]", "x", source)


def _auth_config_s0052_batch() -> tuple[Segment, Batch]:
    seg = next(seg for seg in _auth_config_segments() if seg.id == "s0052")
    batches = chunk_segments(
        [seg],
        max_chars=2500,
        max_output_chars=2200,
        segment_max_chars=2500,
    )
    assert len(batches) == 1
    assert batches[0].segments == [seg]
    assert estimate_translate_batch_output_chars([seg]) == 2189
    return seg, batches[0]


def test_auth_config_s0052_empty_primary_retries_then_uses_fallback():
    """Catches removal of the length/empty parse-error fallback branch."""
    seg, batch = _auth_config_s0052_batch()
    good = _json_response([{"id": "s0052", "text": _ascii_candidate(seg.text)}])
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.side_effect = [
        SimpleNamespace(content=""),
        SimpleNamespace(content=""),
        SimpleNamespace(content=""),
        SimpleNamespace(content=good),
    ]
    actions: list[ManualAction] = []

    out = translate_batch(
        client,
        batch,
        load_glossary(),
        file_path="ydb/docs/ru/core/reference/configuration/auth_config.md",
        manual_actions=actions,
    )

    assert out == {"s0052": _ascii_candidate(seg.text)}
    assert actions == []
    assert [call.kwargs["model"] for call in client.chat.call_args_list] == [
        "primary",
        "primary",
        "primary",
        "fallback",
    ]


def test_translate_segments_auth_config_preserves_all_ids_after_s0052_fallback():
    """Catches a fallback that succeeds locally but loses file-level coverage."""
    segments = _auth_config_segments()
    expected = {seg.id: _ascii_candidate(seg.text) for seg in segments}
    calls: list[tuple[str, tuple[str, ...]]] = []
    primary_s0052_attempts = 0

    def chat(messages, *, model, role):
        nonlocal primary_s0052_attempts
        assert role == "translate"
        user = messages[-1]["content"]
        payload = user.split("```json\n", 1)[1].split("\n```", 1)[0]
        requested = json.loads(payload)["segments"]
        ids = tuple(item["id"] for item in requested)
        calls.append((model, ids))
        if ids == ("s0052",) and model == "primary":
            primary_s0052_attempts += 1
            if primary_s0052_attempts <= 3:
                return SimpleNamespace(content="")
        return SimpleNamespace(
            content=_json_response(
                [{"id": item["id"], "text": expected[item["id"]]} for item in requested]
            )
        )

    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.side_effect = chat
    actions: list[ManualAction] = []

    out = translate_segments(
        segments,
        client,
        load_glossary(),
        file_path="ydb/docs/ru/core/reference/configuration/auth_config.md",
        max_chars=2500,
        max_output_chars=2200,
        segment_max_chars=2500,
        max_parallel_batches=1,
        manual_actions=actions,
    )

    assert out == expected
    assert list(out) == [seg.id for seg in segments]
    assert actions == []
    assert [model for model, ids in calls if ids == ("s0052",)] == [
        "primary",
        "primary",
        "primary",
        "fallback",
    ]


def test_empty_length_failure_advances_through_three_model_chain():
    """Catches advancing only one fallback, or retrying a fallback three times."""
    seg = _segment("s1", "Привет")
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback1", "fallback2"]
    client.chat.side_effect = [
        SimpleNamespace(content=""),
        SimpleNamespace(content=""),
        SimpleNamespace(content=""),
        SimpleNamespace(content=""),
        SimpleNamespace(content=_json_response([{"id": "s1", "text": "Hello"}])),
    ]

    out = translate_batch(
        client, Batch(index=0, segments=[seg]), load_glossary(), file_path="docs/ru/x.md"
    )

    assert out == {"s1": "Hello"}
    assert [call.kwargs["model"] for call in client.chat.call_args_list] == [
        "primary",
        "primary",
        "primary",
        "fallback1",
        "fallback2",
    ]


def test_empty_length_failure_exhaustion_is_blocking_after_fallback_chain():
    """Catches false success or unbounded retries after every model is empty."""
    seg = _segment("s1", "Привет")
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.return_value = SimpleNamespace(content="")
    actions: list[ManualAction] = []

    with pytest.raises(TranslationValidationError, match="safe translate output budget"):
        translate_batch(
            client,
            Batch(index=0, segments=[seg]),
            load_glossary(),
            file_path="docs/ru/x.md",
            manual_actions=actions,
        )

    assert [call.kwargs["model"] for call in client.chat.call_args_list] == [
        "primary",
        "primary",
        "primary",
        "fallback",
    ]
    assert [action.segment_id for action in actions] == ["s1"]


def test_empty_length_failure_without_fallback_keeps_irreducible_monolith_blocked():
    """Catches weakening the established no-fallback manual-action behavior."""
    seg = _segment("s1", "X" * 5000)
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary"]
    client.chat.return_value = SimpleNamespace(content="")
    actions: list[ManualAction] = []

    with pytest.raises(TranslationValidationError, match="safe translate output budget"):
        translate_batch(
            client,
            Batch(index=0, segments=[seg]),
            load_glossary(),
            file_path="docs/ru/x.md",
            manual_actions=actions,
        )

    assert client.chat.call_count == 3
    assert [action.segment_id for action in actions] == ["s1"]


def test_non_length_wrong_ids_does_not_advance_to_fallback():
    """Catches broadening the new branch to ordinary schema/id failures."""
    seg = _segment("s1", "Привет")
    wrong_ids = _json_response([{"id": "not-s1", "text": "Hello"}])
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.side_effect = [SimpleNamespace(content=wrong_ids)] * 3

    with pytest.raises(LLMParseError, match="Segment id mismatch"):
        translate_batch(
            client, Batch(index=0, segments=[seg]), load_glossary(), file_path="docs/ru/x.md"
        )

    assert [call.kwargs["model"] for call in client.chat.call_args_list] == [
        "primary",
        "primary",
        "primary",
    ]


@pytest.mark.parametrize(
    ("source", "unsafe", "error"),
    [
        ("Привет", "Hello мир", "Cyrillic remains"),
        ("Use ⟦C1⟧", "Use marker", "placeholder mismatch"),
        ("Run --safe", "Run safely", "CLI/shell token missing"),
    ],
)
def test_empty_length_fallback_rejects_unsafe_translation(source, unsafe, error):
    """Catches accepting a fallback that violates a structural safety gate."""
    seg = _segment("s1", source)
    unsafe_response = _json_response([{"id": "s1", "text": unsafe}])
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.side_effect = [
        SimpleNamespace(content=""),
        SimpleNamespace(content=""),
        SimpleNamespace(content=""),
        SimpleNamespace(content=unsafe_response),
        SimpleNamespace(content=unsafe_response),
        SimpleNamespace(content=unsafe_response),
        SimpleNamespace(content=unsafe_response),
        SimpleNamespace(content=unsafe_response),
    ]

    with pytest.raises(TranslationValidationError, match=error):
        translate_batch(
            client, Batch(index=0, segments=[seg]), load_glossary(), file_path="docs/ru/x.md"
        )

    assert [call.kwargs["model"] for call in client.chat.call_args_list][:4] == [
        "primary",
        "primary",
        "primary",
        "fallback",
    ]
