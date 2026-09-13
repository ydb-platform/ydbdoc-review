"""Regression coverage for PR #51079 certificate Subject notation."""

from __future__ import annotations

import pytest

from ydbdoc_review.harness.render import (
    _localize_certificate_subject_notation,
    finalize_en_target,
)
from ydbdoc_review.validation.heuristics import (
    _classify_heuristic,
    check_cyrillic_in_en,
    run_file_heuristics_classified,
)

CERTIFICATE_SOURCE = "Имя=Значение,...@<domain>"
CERTIFICATE_TARGET = "Name=Value,...@<domain>"
UNRELATED_ASSIGNMENT = "ключ=значение"
HISTORICAL_EN_MARKDOWN_PATHS = (
    "ydb/docs/en/core/concepts/glossary.md",
    "ydb/docs/en/core/reference/configuration/auth_config.md",
    "ydb/docs/en/core/security/_assets/user-token-lifecycle.md",
    "ydb/docs/en/core/security/_assets/user-token.md",
    "ydb/docs/en/core/security/authentication.md",
    "ydb/docs/en/core/security/caching-authentication-results.md",
)


def test_certificate_template_is_localized_and_never_restored_to_ru():
    source = "See [SID](authorization.md#sid) in `Имя=Значение,...@<domain>`.\n"
    candidate = "See [SID](authorization.md#user) in `Name=Value,...@<domain>`.\n"
    expected = "See [SID](authorization.md#sid) in `Name=Value,...@<domain>`.\n"
    assert finalize_en_target(candidate, source) == expected
    assert finalize_en_target(expected, source) == expected


def test_injected_russian_certificate_template_is_blocking():
    messages = check_cyrillic_in_en(
        "Use `Имя=Значение,...@<domain>`.", target_lang="en"
    )
    assert messages
    assert all(_classify_heuristic(message) == "blocking" for message in messages)


def test_exact_russian_atom_localizes_multiple_occurrences():
    source = f"Use `{CERTIFICATE_SOURCE}` first, then ``{CERTIFICATE_SOURCE}`` again.\n"

    assert finalize_en_target(source, source) == (
        f"Use `{CERTIFICATE_TARGET}` first, then ``{CERTIFICATE_TARGET}`` again.\n"
    )
    assert _localize_certificate_subject_notation(
        f"Keep this wrapper: `` {CERTIFICATE_SOURCE} ``.\n"
    ) == (
        f"Keep this wrapper: `` {CERTIFICATE_TARGET} ``.\n"
    )


def test_multiline_commonmark_atom_with_exact_parsed_content_localizes():
    source = f"A `\n{CERTIFICATE_SOURCE}\n` B\n"
    expected = f"A `\n{CERTIFICATE_TARGET}\n` B\n"

    assert _localize_certificate_subject_notation(source) == expected


def test_unrelated_assignment_and_code_examples_are_untouched():
    larger_atom = f"{CERTIFICATE_SOURCE}X"
    inline_text = f"Keep `{UNRELATED_ASSIGNMENT}`, `{larger_atom}`, and `x=y`.\n"
    protected_examples = (
        f"<!-- `{CERTIFICATE_SOURCE}` -->\n"
        "```text\n"
        f"`{CERTIFICATE_SOURCE}`\n"
        "```\n"
    )

    assert finalize_en_target(inline_text, inline_text) == inline_text
    assert _localize_certificate_subject_notation(protected_examples) == protected_examples


@pytest.mark.parametrize("source_file", HISTORICAL_EN_MARKDOWN_PATHS)
def test_notation_injection_is_blocked_for_each_en_path(source_file: str):
    text = "Use `Имя=Значение,...@<domain>`.\n"

    classified = run_file_heuristics_classified(
        text,
        text,
        normalized_source_text=text,
        source_lang="ru",
        target_lang="english",
        source_file=source_file,
    )

    assert any(message.startswith("Кириллица в EN-тексте") for message in classified.blocking)
