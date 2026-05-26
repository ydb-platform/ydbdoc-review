"""Tests for RU/EN pair diff heuristics."""

from ydbdoc_review.pair_diff import (
    diff_has_content_changes,
    pair_needs_en_from_ru_only_diff,
    pair_requires_en_translation,
)

PR_CHANGED = {
    "ydb/docs/ru/core/devops/deployment-options/manual/initial-deployment.md",
}


def test_pair_requires_en_when_ru_in_pr_en_not():
    ru = "ydb/docs/ru/core/foo.md"
    en = "ydb/docs/en/core/foo.md"
    assert pair_requires_en_translation(
        ru_path=ru,
        en_path=en,
        ru_diff=None,
        en_diff=None,
        pr_changed_paths={ru},
    )


def test_pair_requires_en_when_ru_diff_only():
    ru = next(iter(PR_CHANGED))
    en = ru.replace("/ru/", "/en/", 1)
    ru_diff = "@@\n+new kafka line\n"
    assert pair_requires_en_translation(
        ru_path=ru,
        en_path=en,
        ru_diff=ru_diff,
        en_diff="",
        pr_changed_paths=PR_CHANGED,
    )


def test_pair_skips_when_both_changed_in_pr():
    ru = next(iter(PR_CHANGED))
    en = ru.replace("/ru/", "/en/", 1)
    assert not pair_requires_en_translation(
        ru_path=ru,
        en_path=en,
        ru_diff="@@\n+ru\n",
        en_diff="@@\n+en\n",
        pr_changed_paths={ru, en},
    )


def test_alias_matches_requires():
    ru = next(iter(PR_CHANGED))
    assert pair_needs_en_from_ru_only_diff(
        ru_path=ru,
        ru_diff="@@\n+line\n",
        en_diff=None,
        pr_changed_paths=PR_CHANGED,
    )


def test_diff_has_content_changes_deletions():
    assert diff_has_content_changes("@@\n-old\n")
