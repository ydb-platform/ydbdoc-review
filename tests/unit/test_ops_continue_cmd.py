"""Tests for /ydbdoc continue parsing."""

from ydbdoc_review.ops.continue_cmd import (
    find_latest_continue_instruction,
    parse_continue_instruction,
)


def test_parse_continue_simple():
    assert (
        parse_continue_instruction("/ydbdoc continue use EN wiki")
        == "use EN wiki"
    )


def test_parse_continue_multiline():
    body = "/ydbdoc continue fix glossary\nsecond line"
    assert parse_continue_instruction(body) == "fix glossary\nsecond line"


def test_parse_continue_rejects_other():
    assert parse_continue_instruction("please continue") is None
    assert parse_continue_instruction("/ydbdoc continue") is None


def test_find_latest_continue():
    comments = [
        {"body": "/ydbdoc continue first", "created_at": "2026-01-01T00:00:00Z"},
        {"body": "noise", "created_at": "2026-01-02T00:00:00Z"},
        {"body": "/ydbdoc continue second", "created_at": "2026-01-03T00:00:00Z"},
    ]
    assert find_latest_continue_instruction(comments) == "second"
