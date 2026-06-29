"""Tests for per-file translate_file pipeline."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.pipeline.translate_file import translate_file


def _unit_cfg(**extra: str):
    env = {
        "YDBDOC_YC_FOLDER_ID": "b1x",
        "YDBDOC_YC_API_KEY": "k",
        "YDBDOC_TRANSLATION_CRITIC_FEEDBACK_RETRIES": "0",
    }
    env.update(extra)
    return load_config(env=env)


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


def _prose_unfixed_cyrillic_json(text: str) -> str:
    """Prose-pass JSON that keeps Cyrillic (rejected when applying fixes)."""
    from ydbdoc_review.validation.prose_cyrillic import collect_cyrillic_prose_spans

    spans = collect_cyrillic_prose_spans(text)
    if not spans:
        return json.dumps({"spans": []})
    return json.dumps(
        {
            "spans": [
                {"id": span.span_id, "text": span.text} for span in spans
            ]
        },
        ensure_ascii=False,
    )


def _translate_json(segments, mapping: dict[str, str]) -> str:
    payload = {
        "segments": [
            {"id": seg.id, "text": mapping.get(seg.id, seg.text)}
            for seg in segments
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def test_translate_file_no_segments():
    source = "```bash\necho hi\n```\n"
    client = _mock_client([])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/code.md",
        enable_critic=False,
        config=_unit_cfg(),
    )
    assert result.segments_count == 0
    assert result.final_text == source
    assert result.verdict == "ok"


def test_translate_file_end_to_end_no_critic_issues():
    source = "Привет, мир.\n"
    segments = extract_segments(parse_markdown(source))
    assert len(segments) == 1
    seg_id = segments[0].id

    translate_raw = _translate_json(segments, {seg_id: "Hello, world."})
    critic_raw = json.dumps({"verdict": "ok", "issues": []})

    client = _mock_client([translate_raw, critic_raw])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/hello.md",
        config=_unit_cfg(),
        enable_critic=True,
    )

    assert result.segments_count == 1
    assert "Hello, world." in result.final_text
    assert result.verdict == "ok"
    assert result.critic_initial is not None
    assert result.critic_unresolved is None or result.critic_unresolved.issues == []
    assert result.input_tokens > 0


def test_translate_file_applies_critic_fix():
    source = "Неверный перевод термина.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id

    translate_raw = _translate_json(segments, {seg_id: "Wrong term translation."})
    critic_raw = json.dumps(
        {
            "verdict": "warnings",
            "issues": [
                {
                    "segment_id": seg_id,
                    "severity": "warning",
                    "category": "terminology",
                    "comment": "fix term",
                    "suggested_text": "Correct term translation.",
                }
            ],
        }
    )
    verify_raw = json.dumps({"verdict": "ok", "issues": []})

    client = _mock_client([translate_raw, critic_raw, verify_raw])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/terms.md",
        config=_unit_cfg(),
        enable_critic=True,
    )

    assert "Correct term translation." in result.final_text
    assert len(result.critic_applied) == 1
    assert result.critic_unresolved is not None
    assert result.verdict == "ok"


def test_translate_file_skips_critic_when_disabled():
    source = "Текст.\n"
    segments = extract_segments(parse_markdown(source))
    translate_raw = _translate_json(segments, {segments[0].id: "Text."})

    client = _mock_client([translate_raw])
    result = translate_file(
        source,
        client,
        load_glossary(),
        enable_critic=False,
        config=_unit_cfg(),
    )

    assert result.critic_initial is None
    assert "Text." in result.final_text


def test_translate_file_verdict_blocked_on_unresolved():
    source = "Проблема.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id

    translate_raw = _translate_json(segments, {seg_id: "Problem."})
    critic_raw = json.dumps(
        {
            "verdict": "blocked",
            "issues": [
                {
                    "segment_id": seg_id,
                    "severity": "blocked",
                    "category": "meaning",
                    "comment": "still wrong",
                    "suggested_text": None,
                }
            ],
        }
    )
    verify_raw = json.dumps(
        {
            "verdict": "blocked",
            "issues": [
                {
                    "segment_id": seg_id,
                    "severity": "blocked",
                    "category": "meaning",
                    "comment": "unresolved",
                    "suggested_text": None,
                }
            ],
        }
    )

    client = _mock_client([translate_raw, critic_raw, verify_raw])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/bad.md",
        config=_unit_cfg(),
        enable_critic=True,
    )

    assert result.verdict == "blocked"
    assert result.critic_unresolved is not None


def test_translate_file_critic_only_alignment_mismatch_blocks():
    source = "Первый.\n\nВторой.\n"
    target = "Only one paragraph.\n"
    client = _mock_client([])
    result = translate_file(
        source,
        client,
        load_glossary(),
        enable_translate=False,
        existing_target_text=target,
        config=_unit_cfg(),
    )
    assert result.verdict == "blocked"
    assert result.segment_alignment_error
    assert "segment count mismatch" in result.segment_alignment_error
    assert result.critic_initial is None


def test_translate_file_critic_only_mode():
    source = "Привет.\n"
    target = "Hello.\n"
    segments = extract_segments(parse_markdown(source))
    critic_raw = json.dumps({"verdict": "ok", "issues": []})
    # critic_only: no translate call — only critic
    client = _mock_client([critic_raw])
    result = translate_file(
        source,
        client,
        load_glossary(),
        enable_translate=False,
        existing_target_text=target,
        config=_unit_cfg(),
    )
    assert result.final_text == target
    assert result.critic_initial is not None


def test_translate_file_verify_preserves_en_fence_bodies():
    """Regression for the doc_verify bug where critic fixes caused EN fence
    contents (mermaid `participant Topic`, etc.) to be overwritten by RU
    fence bodies. See PR ydb-platform/ydb#43399 fixup. The EN AST must be
    the render base in verify mode so its fenced code blocks survive.
    """
    source = (
        "Введение.\n\n"
        "```mermaid\n"
        "sequenceDiagram\n"
        "    participant Топик\n"
        "    participant Запрос v1\n"
        "```\n\n"
        "Заключение.\n"
    )
    target = (
        "Intro.\n\n"
        "```mermaid\n"
        "sequenceDiagram\n"
        "    participant Topic\n"
        "    participant Query v1\n"
        "```\n\n"
        "Conclusion.\n"
    )
    ru_segs = extract_segments(parse_markdown(source))
    en_segs = extract_segments(parse_markdown(target))
    # Critic fixes the second paragraph but leaves the first alone.
    fixed_id = ru_segs[1].id
    critic_raw = json.dumps(
        {
            "verdict": "warnings",
            "issues": [
                {
                    "segment_id": fixed_id,
                    "severity": "warning",
                    "category": "style",
                    "comment": "tighten",
                    "suggested_text": "Wrap-up.",
                }
            ],
        }
    )
    # Verify pass: no further issues, and the residual-prose helper is also
    # called after re-render for EN targets.
    prose_raw_after = json.dumps({"spans": []})
    verify_raw = json.dumps({"verdict": "ok", "issues": []})
    client = _mock_client([critic_raw, prose_raw_after, verify_raw])

    result = translate_file(
        source,
        client,
        load_glossary(),
        target_lang="en",
        enable_translate=False,
        existing_target_text=target,
        config=_unit_cfg(),
    )

    # Fence body must remain English.
    assert "participant Topic" in result.final_text
    assert "participant Query v1" in result.final_text
    assert "Топик" not in result.final_text
    assert "Запрос" not in result.final_text
    # Critic fix to the closing paragraph must have been applied.
    assert "Wrap-up." in result.final_text
    assert "Conclusion." not in result.final_text
    # And the untouched paragraph keeps its EN text.
    assert "Intro." in result.final_text


def test_translate_file_heuristics_bump_verdict_to_warnings():
    source = "Текст для перевода.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id
    # Critic OK, but translation leaves Cyrillic in EN target.
    translated = "Text with привет inside."
    translate_raw = _translate_json(segments, {seg_id: translated})
    critic_raw = json.dumps({"verdict": "ok", "issues": []})

    client = _mock_client(
        [translate_raw, _prose_unfixed_cyrillic_json(translated), critic_raw]
    )
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/heuristics.md",
        target_lang="en",
        config=_unit_cfg(),
        enable_critic=True,
    )

    assert result.verdict == "blocked"
    assert result.heuristic_blocking
    assert any("Кириллица в EN-тексте" in w for w in result.heuristic_blocking)


def test_translate_file_heuristics_do_not_downgrade_blocked():
    source = "Проблема.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id

    translated = "Problem with привет."
    translate_raw = _translate_json(segments, {seg_id: translated})
    critic_raw = json.dumps(
        {
            "verdict": "blocked",
            "issues": [
                {
                    "segment_id": seg_id,
                    "severity": "blocked",
                    "category": "meaning",
                    "comment": "wrong",
                    "suggested_text": None,
                }
            ],
        }
    )
    verify_raw = json.dumps(
        {
            "verdict": "blocked",
            "issues": [
                {
                    "segment_id": seg_id,
                    "severity": "blocked",
                    "category": "meaning",
                    "comment": "still wrong",
                    "suggested_text": None,
                }
            ],
        }
    )

    prose_raw = _prose_unfixed_cyrillic_json(translated)
    client = _mock_client(
        [
            translate_raw,
            prose_raw,
            critic_raw,
            prose_raw,
            verify_raw,
        ]
    )
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/blocked.md",
        target_lang="en",
        config=_unit_cfg(),
        enable_critic=True,
    )

    assert result.verdict == "blocked"
    assert result.heuristic_blocking or result.heuristic_warnings


def test_translate_file_survives_empty_critic_response():
    source = "Привет.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id
    translate_raw = _translate_json(segments, {seg_id: "Hello."})

    client = _mock_client([translate_raw, "", "", ""])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/hello.md",
        config=_unit_cfg(),
        enable_critic=True,
    )

    assert "Hello." in result.final_text
    assert result.verdict == "ok"
    assert result.critic_initial is not None
    assert result.critic_initial.issues == []
