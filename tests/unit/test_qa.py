"""Unified QA: round-trip gate, classified heuristics, verdict parity."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.qa import (
    align_translations_from_target,
    compose_file_verdict,
    describe_segment_alignment_mismatch,
    gate_round_trip,
)
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.pipeline.translate_file import _compute_critic_verdict, translate_file
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.heuristics import (
    ClassifiedHeuristics,
    run_file_heuristics_classified,
)
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation


def test_describe_segment_alignment_extra_ru_segment():
    src = [
        Segment(
            id="s1",
            kind=SegmentKind.PARAGRAPH,
            path=[],
            text="One.",
            placeholders=[],
            ast_path=[0],
        ),
        Segment(
            id="s2",
            kind=SegmentKind.PARAGRAPH,
            path=[],
            text="Two.",
            placeholders=[],
            ast_path=[1],
        ),
    ]
    tgt = [
        Segment(
            id="s1",
            kind=SegmentKind.PARAGRAPH,
            path=[],
            text="One.",
            placeholders=[],
            ast_path=[0],
        ),
    ]
    msg = describe_segment_alignment_mismatch(src, tgt)
    assert "source 2 vs target 1" in msg
    assert "extra RU segment" in msg
    assert "s2" in msg


def test_gate_round_trip_ok():
    text = "Hello.\n\nSecond paragraph.\n"
    segments = extract_segments(parse_markdown(text))
    translations, err = gate_round_trip(segments, text)
    assert err is None
    assert len(translations) == len(segments)


def test_gate_round_trip_mismatch():
    ru = "One.\n\nTwo.\n"
    en = "Only one.\n"
    segments = extract_segments(parse_markdown(ru))
    translations, err = gate_round_trip(segments, en)
    assert translations == {}
    assert err is not None
    assert "segment count mismatch" in err


def test_align_raises_on_mismatch():
    ru = "A.\n\nB.\n"
    en = "A only.\n"
    segments = extract_segments(parse_markdown(ru))
    try:
        align_translations_from_target(segments, en)
        raise AssertionError("expected TranslationValidationError")
    except TranslationValidationError as exc:
        assert "segment count mismatch" in str(exc)


def test_compose_verdict_blocked_on_alignment():
    v = compose_file_verdict(
        critic_verdict="ok",
        alignment_error="segment count mismatch: source 2 vs target 1",
        heuristics=ClassifiedHeuristics(),
        manual_actions=False,
    )
    assert v == "blocked"


def test_compose_verdict_blocking_heuristics():
    v = compose_file_verdict(
        critic_verdict="ok",
        alignment_error=None,
        heuristics=ClassifiedHeuristics(blocking=["fence_parity: source 2 vs target 1"]),
        manual_actions=False,
    )
    assert v == "blocked"


def test_ru_source_classified_as_info():
    ru = "init --config-dir/opt/ydb/cfg\n"
    norm = normalize_ru_source_for_translation(ru)
    classified = run_file_heuristics_classified(
        ru, "init --config-dir /opt/ydb/cfg\n", normalized_source_text=norm
    )
    assert classified.info
    assert not classified.blocking
    assert any("ru_source" in m for m in classified.info)


def test_homoglyph_fence_not_fence_body_copy():
    ru = "```yaml\n    - host: x #FQDN ВМ\n```\n"
    en = "```yaml\n    - host: x #FQDN VM\n```\n"
    norm = normalize_ru_source_for_translation(ru)
    classified = run_file_heuristics_classified(ru, en, normalized_source_text=norm)
    assert not any("fence_body_copy" in m for m in classified.all_non_info)


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


def test_translate_and_verify_same_verdict_on_identical_en():
    """doc_translate final text and doc_verify on same EN must share QA outcome."""
    source = "Привет мир.\n"
    segments = extract_segments(parse_markdown(normalize_ru_source_for_translation(source)))
    seg_id = segments[0].id
    translate_raw = json.dumps(
        {"segments": [{"id": seg_id, "text": "Hello world."}]}
    )
    critic_raw = json.dumps({"verdict": "ok", "issues": []})
    client = _mock_client([translate_raw, critic_raw])

    translated = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/a.md",
        enable_translate=True,
        enable_critic=True,
    )
    final_en = translated.final_text

    client2 = _mock_client([critic_raw])
    verified = translate_file(
        source,
        client2,
        load_glossary(),
        file_path="docs/ru/a.md",
        enable_translate=False,
        existing_target_text=final_en,
        enable_critic=True,
    )

    assert translated.segment_alignment_error is None
    assert verified.segment_alignment_error is None
    assert translated.verdict == verified.verdict
    assert translated.heuristic_blocking == verified.heuristic_blocking
    assert translated.heuristic_warnings == verified.heuristic_warnings


def test_verify_blocked_when_round_trip_fails():
    """Structural gate must block doc_verify and match translate rules."""
    source = "Первый.\n\nВторой.\n"
    broken_en = "Only first.\n"
    client = _mock_client([])
    result = translate_file(
        source,
        client,
        load_glossary(),
        enable_translate=False,
        existing_target_text=broken_en,
        enable_critic=False,
    )
    assert result.verdict == "blocked"
    assert result.segment_alignment_error


def test_compute_critic_verdict_initial_warnings_without_issues_is_ok():
    from ydbdoc_review.translation.schemas import CriticResponse

    initial = CriticResponse(verdict="warnings", issues=[])
    assert _compute_critic_verdict(initial=initial, unresolved=None) == "ok"
