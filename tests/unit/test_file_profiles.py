"""Tests for per-file harness profiles."""

from __future__ import annotations

from ydbdoc_review.translation.file_profiles import (
    GLOSSARY_PROFILE,
    DEFAULT_PROFILE,
    detect_file_profile,
    is_glossary_file,
)


def test_detect_glossary_profile():
    assert (
        detect_file_profile("ydb/docs/ru/core/concepts/glossary.md")
        == GLOSSARY_PROFILE
    )
    assert detect_file_profile("core/concepts/glossary.md") == GLOSSARY_PROFILE
    assert is_glossary_file("en/core/concepts/glossary.md")


def test_default_profile_for_other_files():
    assert (
        detect_file_profile("ydb/docs/ru/core/concepts/query_execution/index.md")
        == DEFAULT_PROFILE
    )
    assert not is_glossary_file("core/concepts/glossary-extra.md")
