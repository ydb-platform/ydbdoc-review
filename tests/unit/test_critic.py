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
    merge_critic_responses,
    merge_verdicts,
    normalize_critic_verdict_value,
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


def test_normalize_critic_verdict_aliases():
    assert normalize_critic_verdict_value("issues_found") == "warnings"
    assert normalize_critic_verdict_value("needs_fix") == "warnings"
    assert normalize_critic_verdict_value("OK") == "ok"


def test_parse_critic_response_normalizes_issues_found():
    raw = json.dumps({"verdict": "issues_found", "issues": []})
    out = parse_critic_response(raw)
    assert out.verdict == "warnings"
    assert out.issues == []


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
    seg = _segment("s1", "x")
    out = run_critic(
        client,
        segments=[seg],
        translations={"s1": "EN x"},
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
    )
    assert out.verdict == "ok"


def test_run_critic_empty_response_fallback():
    client = _mock_client(["", "", ""])
    seg = _segment("s1", "x")
    out = run_critic(
        client,
        segments=[seg],
        translations={"s1": "EN x"},
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
    )
    assert out.verdict == "warnings"
    assert out.issues == []


def test_run_critic_retries_then_parses():
    raw = json.dumps({"verdict": "ok", "issues": []})
    client = _mock_client(["", "not json", raw])
    seg = _segment("s1", "x")
    out = run_critic(
        client,
        segments=[seg],
        translations={"s1": "EN x"},
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
    )
    assert out.verdict == "ok"


def test_run_critic_merges_multiple_batches():
    long_text = "x" * 3000
    seg1 = _segment("s1", long_text)
    seg2 = _segment("s2", long_text)
    batch1 = json.dumps(
        {
            "verdict": "warnings",
            "issues": [
                {
                    "segment_id": "s1",
                    "severity": "warning",
                    "category": "terminology",
                    "comment": "a",
                    "suggested_text": None,
                }
            ],
        }
    )
    batch2 = json.dumps(
        {
            "verdict": "warnings",
            "issues": [
                {
                    "segment_id": "s2",
                    "severity": "warning",
                    "category": "terminology",
                    "comment": "b",
                    "suggested_text": None,
                }
            ],
        }
    )
    client = _mock_client([batch1, batch2])
    out = run_critic(
        client,
        segments=[seg1, seg2],
        translations={"s1": "EN1", "s2": "EN2"},
        glossary=load_glossary(),
        file_path="docs/ru/big.md",
        max_chars=4000,
    )
    assert len(out.issues) == 2
    assert {i.segment_id for i in out.issues} == {"s1", "s2"}


def test_merge_verdicts_and_responses():
    assert merge_verdicts("ok", "warnings") == "warnings"
    assert merge_verdicts("warnings", "blocked") == "blocked"
    r1 = parse_critic_response(
        json.dumps({"verdict": "ok", "issues": [{"segment_id": "s1", "severity": "warning", "category": "x", "comment": "y", "suggested_text": None}]})
    )
    r2 = parse_critic_response(json.dumps({"verdict": "ok", "issues": []}))
    merged = merge_critic_responses([r1, r2])
    assert merged.verdict == "warnings"
    assert len(merged.issues) == 1


def test_run_verify_empty_response_fallback():
    client = _mock_client(["", "", ""])
    prior = [_issue(segment_id="s1", suggested_text="fixed")]
    seg = _segment("s1", "x")
    out = run_verify(
        client,
        segments=[seg],
        translations={"s1": "EN fixed"},
        prior_issues=prior,
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
    )
    assert out.verdict == "warnings"
    assert out.issues == []


def test_run_verify_calls_llm():
    raw = json.dumps({"verdict": "ok", "issues": []})
    client = _mock_client([raw])
    prior = [_issue(segment_id="s1", suggested_text="fixed")]
    seg = _segment("s1", "x")
    out = run_verify(
        client,
        segments=[seg],
        translations={"s1": "EN fixed"},
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
