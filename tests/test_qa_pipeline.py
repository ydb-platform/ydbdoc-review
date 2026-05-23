"""Verdict parsing — three-state shape."""

from ydbdoc_review.pipeline_v2 import (
    VERDICT_ACCEPT,
    VERDICT_ACCEPT_WITH_NOTES,
    VERDICT_REJECT,
    parse_verdict,
)


def test_parse_verdict_accept_only():
    md = "### Вердикт\n**ПРИНИМАТЬ**\n\n### Блокеры\n_Нет._\n"
    assert parse_verdict(md) == VERDICT_ACCEPT


def test_parse_verdict_accept_with_notes_only():
    md = "### Вердикт\n**ПРИНИМАТЬ С ОГОВОРКАМИ**\n\n### Блокеры\n_Нет._\n"
    assert parse_verdict(md) == VERDICT_ACCEPT_WITH_NOTES


def test_parse_verdict_reject_only():
    md = "### Вердикт\n**НЕ ПРИНИМАТЬ**\n\n### Блокеры\nblah\n"
    assert parse_verdict(md) == VERDICT_REJECT


def test_parse_verdict_does_not_confuse_reject_with_accept():
    md = (
        "### Вердикт\n**НЕ ПРИНИМАТЬ**\n\n"
        "### Блокеры\nESt принимать что-то странное.\n"
    )
    assert parse_verdict(md) == VERDICT_REJECT
