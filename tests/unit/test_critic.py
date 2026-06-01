"""Tests for critic parse, apply fixes, and review flow."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.critic import (
    apply_critic_fixes,
    parse_critic_response,
    review_with_critic,
    run_critic,
    run_verify,
)
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.schemas import CriticIssueOut


def _segment(seg_id: str, text: str) -> Segment:
    return Segment(
        id=seg_id,
        kind=SegmentKind.PARAGRAPH,
        path=["Intro"],
        text=text,
        placeholders=[],
        ast_path=[0],
    )


def _issue(**kwargs: object) -> CriticIssueOut:
    defaults = {
        "severity": "warning",
        "category": "terminology",
        "comment": "fix me",
        "suggested_text": None,
    }
    defaults.update(kwargs)
    return CriticIssueOut.model_validate(defaults)


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


def test_parse_critic_response_ok():
    raw = json.dumps(
        {
            "verdict": "warnings",
            "issues": [
                {
                    "segment_id": "s1",
                    "severity": "warning",
                    "category": "terminology",
                    "comment": "wrong term",
                    "suggested_text": "fixed",
                }
            ],
        }
    )
    out = parse_critic_response(raw)
    assert out.verdict == "warnings"
    assert len(out.issues) == 1
    assert out.issues[0].segment_id == "s1"


def test_apply_critic_fixes_applies_valid_suggestion():
    seg = _segment("s1", "Use ⟦C1⟧ flag")
    translations = {"s1": "Bad text"}
    issues = [_issue(segment_id="s1", suggested_text="Use ⟦C1⟧ properly")]
    updated, applied, skipped = apply_critic_fixes(translations, [seg], issues)
    assert updated["s1"] == "Use ⟦C1⟧ properly"
    assert len(applied) == 1
    assert skipped == []


def test_apply_critic_fixes_skips_broken_placeholder():
    seg = _segment("s1", "Use ⟦C1⟧")
    translations = {"s1": "Use ⟦C1⟧"}
    issues = [_issue(segment_id="s1", suggested_text="Use ⟦C2⟧")]
    updated, applied, skipped = apply_critic_fixes(translations, [seg], issues)
    assert updated["s1"] == "Use ⟦C1⟧"
    assert applied == []
    assert len(skipped) == 1


def test_apply_critic_fixes_skips_null_suggestion():
    seg = _segment("s1", "text")
    issues = [_issue(segment_id="s1", suggested_text=None)]
    _, applied, skipped = apply_critic_fixes({"s1": "text"}, [seg], issues)
    assert applied == []
    assert len(skipped) == 1


def test_apply_critic_fixes_skips_unknown_segment_id():
    issues = [_issue(segment_id="missing", suggested_text="x")]
    _, applied, skipped = apply_critic_fixes({}, [], issues)
    assert applied == []
    assert len(skipped) == 1


def test_run_critic_calls_llm():
    raw = json.dumps({"verdict": "ok", "issues": []})
    client = _mock_client([raw])
    out = run_critic(
        client,
        source_text="RU",
        translated_text="EN",
        segments=[_segment("s1", "x")],
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
    )
    assert out.verdict == "ok"


def test_run_verify_calls_llm():
    raw = json.dumps({"verdict": "ok", "issues": []})
    client = _mock_client([raw])
    prior = [_issue(segment_id="s1", suggested_text="fixed")]
    out = run_verify(
        client,
        source_text="RU",
        translated_text="EN fixed",
        segments=[_segment("s1", "x")],
        prior_issues=prior,
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
    )
    assert out.verdict == "ok"


def test_apply_critic_fixes_skips_issue_without_segment_id():
    issues = [_issue(segment_id=None, suggested_text="cannot apply")]
    _, applied, skipped = apply_critic_fixes({}, [_segment("s1", "x")], issues)
    assert applied == []
    assert len(skipped) == 1


def test_review_with_critic_full_flow():
    critic_raw = json.dumps(
        {
            "verdict": "warnings",
            "issues": [
                {
                    "segment_id": "s1",
                    "severity": "warning",
                    "category": "terminology",
                    "comment": "fix",
                    "suggested_text": "Correct EN",
                }
            ],
        }
    )
    verify_raw = json.dumps({"verdict": "ok", "issues": []})
    client = _mock_client([critic_raw, verify_raw])
    seg = _segment("s1", "RU text")
    result = review_with_critic(
        client,
        source_text="RU body",
        translated_text="EN body",
        segments=[seg],
        translations={"s1": "Wrong EN"},
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
    )
    assert result.initial.verdict == "warnings"
    assert result.translations["s1"] == "Correct EN"
    assert len(result.applied) == 1
    assert result.unresolved is not None
    assert result.unresolved.verdict == "ok"


def test_review_with_critic_skips_verify_when_no_issues():
    critic_raw = json.dumps({"verdict": "ok", "issues": []})
    client = _mock_client([critic_raw])
    result = review_with_critic(
        client,
        source_text="RU",
        translated_text="EN",
        segments=[_segment("s1", "x")],
        translations={"s1": "EN"},
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
    )
    assert result.unresolved is None


def test_review_with_critic_no_second_pass():
    critic_raw = json.dumps(
        {
            "verdict": "warnings",
            "issues": [
                {
                    "segment_id": "s1",
                    "severity": "warning",
                    "category": "x",
                    "comment": "y",
                    "suggested_text": "EN",
                }
            ],
        }
    )
    client = _mock_client([critic_raw])
    result = review_with_critic(
        client,
        source_text="RU",
        translated_text="EN",
        segments=[_segment("s1", "RU")],
        translations={"s1": "bad"},
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
        run_second_pass=False,
    )
    assert result.unresolved is None
