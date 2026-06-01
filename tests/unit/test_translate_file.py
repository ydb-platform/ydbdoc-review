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
from ydbdoc_review.pipeline.translate_file import _compute_verdict, translate_file


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


def _translate_json(segments, mapping: dict[str, str]) -> str:
    payload = {
        "segments": [
            {"id": seg.id, "text": mapping.get(seg.id, seg.text)}
            for seg in segments
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def test_compute_verdict_initial_blocked_without_verify():
    initial = CriticResponse(verdict="blocked", issues=[])
    assert _compute_verdict(initial=initial, unresolved=None) == "blocked"


def test_compute_verdict_unresolved_blocked_severity():
    unresolved = CriticResponse(
        verdict="warnings",
        issues=[
            CriticIssueOut(
                segment_id="s1",
                severity="blocked",
                category="x",
                comment="y",
            )
        ],
    )
    assert _compute_verdict(initial=None, unresolved=unresolved) == "blocked"


def test_translate_file_no_segments():
    source = "```bash\necho hi\n```\n"
    client = _mock_client([])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/code.md",
        enable_critic=False,
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
    )

    assert result.segments_count == 1
    assert "Hello, world." in result.final_text
    assert result.verdict == "ok"
    assert result.critic_initial is not None
    assert result.critic_unresolved is None
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
    )

    assert result.verdict == "blocked"
    assert result.critic_unresolved is not None


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
    )
    assert result.final_text == target
    assert result.critic_initial is not None


def test_translate_file_heuristics_bump_verdict_to_warnings():
    source = "Текст для перевода.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id
    # Critic OK, but translation leaves Cyrillic in EN target.
    translate_raw = _translate_json(segments, {seg_id: "Text with привет inside."})
    critic_raw = json.dumps({"verdict": "ok", "issues": []})

    client = _mock_client([translate_raw, critic_raw])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/heuristics.md",
        target_lang="en",
    )

    assert result.verdict == "warnings"
    assert result.heuristic_warnings
    assert any("cyrillic_in_en" in w for w in result.heuristic_warnings)


def test_translate_file_heuristics_do_not_downgrade_blocked():
    source = "Проблема.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id

    translate_raw = _translate_json(segments, {seg_id: "Problem with привет."})
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

    client = _mock_client([translate_raw, critic_raw, verify_raw])
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/blocked.md",
        target_lang="en",
    )

    assert result.verdict == "blocked"
    assert result.heuristic_warnings


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
    )

    assert "Hello." in result.final_text
    assert result.verdict == "warnings"
    assert result.critic_initial is not None
    assert result.critic_initial.issues == []
