# ruff: noqa: RUF001
"""Read-only coverage for Cyrillic content in source-owned fences."""

from textwrap import dedent

from ydbdoc_review.validation.fence_comments import (
    check_cyrillic_in_en_fence_comments,
    check_cyrillic_in_en_text_fences,
    collect_cyrillic_fence_comment_lines,
    collect_cyrillic_text_fence_lines,
)
from ydbdoc_review.validation.heuristics import _classify_heuristic

YQL_SAMPLE = dedent(
    """
    English prose.

    ```yql
    -- Секрет с токеном для подключения к YDB
    CREATE SECRET `token` WITH (value = "<token>");
    -- Чтение событий из входного топика
    SELECT * FROM input_topic;
    ```
    """
).strip()

TEXT_SAMPLE = dedent(
    """
    English prose.

    ```text
    Полная копия → Инкремент₁ → Инкремент₂
    ```
    """
).strip()


def test_collect_cyrillic_fence_comment_lines_read_only():
    items = collect_cyrillic_fence_comment_lines(YQL_SAMPLE)
    assert [item.body for item in items] == [
        "Секрет с токеном для подключения к YDB",
        "Чтение событий из входного топика",
    ]


def test_collect_cyrillic_text_fence_lines_read_only():
    items = collect_cyrillic_text_fence_lines(TEXT_SAMPLE)
    assert len(items) == 1
    assert "Полная копия" in items[0].body


def test_fence_comment_detection_is_blocking_and_does_not_mutate():
    before = YQL_SAMPLE
    findings = check_cyrillic_in_en_fence_comments(before, target_lang="en")
    assert findings
    assert all(_classify_heuristic(item) == "blocking" for item in findings)
    assert before == YQL_SAMPLE


def test_text_fence_detection_is_blocking_and_does_not_mutate():
    before = TEXT_SAMPLE
    findings = check_cyrillic_in_en_text_fences(before, target_lang="en")
    assert findings
    assert all(_classify_heuristic(item) == "blocking" for item in findings)
    assert before == TEXT_SAMPLE


def test_fence_checks_ignore_cyrillic_prose_outside_fences():
    text = "Hello привет.\n"
    assert check_cyrillic_in_en_fence_comments(text, target_lang="en") == []
    assert check_cyrillic_in_en_text_fences(text, target_lang="en") == []
