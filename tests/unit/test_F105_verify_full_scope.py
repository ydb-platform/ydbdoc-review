"""F-105: linked verify checks the expected result, not only its git diff."""

from __future__ import annotations

from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness import (
    VERIFY_PR_PROFILE,
    PRHarness,
    PRHarnessContext,
    PRRunState,
)
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.completeness import (
    VERIFY_MISSING_PAIR_SKIP_SUMMARY,
    verified_translation_pr_scope_gaps,
)
from ydbdoc_review.pipeline.pairs import (
    DocPair,
    NavigationPair,
    filter_translation_pr_verify_scope,
    merge_translation_pr_verify_scope,
)
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.translation.glossary import load_glossary


def _pair(name: str, *, deleted: bool = False) -> DocPair:
    return DocPair(
        ru_path=f"ydb/docs/ru/{name}.md",
        en_path=f"ydb/docs/en/{name}.md",
        ru_changed=True,
        ru_deleted=deleted,
    )


def _run(pair: DocPair, **kwargs: object) -> PairRunResult:
    return PairRunResult(
        plan=PairPlan(
            pair=pair,
            action="delete_en" if pair.ru_deleted else "critic_only",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary=(
                VERIFY_MISSING_PAIR_SKIP_SUMMARY
                if kwargs.get("skipped")
                else "doc_verify critic pass"
            ),
        ),
        **kwargs,
    )


def test_F105_valid_no_diff() -> None:
    existing = _pair("analyze-noop")
    newly_translated = _pair("new-translation")
    expected = [existing, newly_translated]

    full_scope = merge_translation_pr_verify_scope([], expected)
    scoped, nav = filter_translation_pr_verify_scope(
        full_scope,
        [],
        [],
        allowed_en_paths={pair.en_path for pair in expected},
        allowed_nav_en_paths=set(),
    )

    assert scoped == expected
    assert nav == []
    verified = PRTranslationResult(
        pair_results=[
            _run(existing, target_text="Existing, independently verified EN.\n"),
            _run(newly_translated, target_text="New translation with no diff.\n"),
        ]
    )
    assert verified_translation_pr_scope_gaps(expected, [], verified) == []


def test_F105_missing_kinds() -> None:
    valid_noop = _pair("valid-noop")
    missing_file = _pair("missing-file")
    missing_fragment = _pair("missing-fragment")
    incomplete = _pair("incomplete")
    deleted = _pair("deleted", deleted=True)
    translation_deleted = DocPair(
        ru_path=deleted.ru_path,
        en_path=deleted.en_path,
        en_changed=True,
        en_deleted=True,
    )
    redirect = NavigationPair(
        ru_path="ydb/docs/ru/redirects.yaml",
        en_path="ydb/docs/en/redirects.yaml",
        ru_changed=True,
    )
    expected = [valid_noop, missing_file, missing_fragment, incomplete, deleted]
    full_scope = merge_translation_pr_verify_scope([translation_deleted], expected)
    scoped, _ = filter_translation_pr_verify_scope(
        full_scope,
        [],
        [(deleted.en_path, "deleted")],
        allowed_en_paths={pair.en_path for pair in expected},
        allowed_nav_en_paths=set(),
    )
    merged_deleted = next(pair for pair in scoped if pair.en_path == deleted.en_path)
    deletion_result = PRHarness(VERIFY_PR_PROFILE).run(
        PRRunState(contents=[PairContent(pair=merged_deleted)]),
        PRHarnessContext(
            client=MagicMock(),
            glossary=load_glossary(),
            config=load_config(),
        ),
    )
    result = PRTranslationResult(
        pair_results=[
            _run(valid_noop, target_text="Current verified target.\n"),
            _run(missing_file, skipped=True),
            _run(
                missing_fragment,
                target_text="# Wrong anchor\n",
                file_result=FileTranslationResult(
                    file_path=missing_fragment.en_path,
                    final_text="# Wrong anchor\n",
                    segments_count=1,
                    verdict="blocked",
                    prompt_version="v1",
                ),
            ),
            _run(incomplete, error="semantic coverage rejected"),
            *deletion_result.pair_results,
        ],
        navigation_results=[
            NavigationRunResult(
                ru_path=redirect.ru_path,
                en_path=redirect.en_path,
                kind="redirects",
                error="required redirect was not deleted",
                verdict="blocked",
            )
        ],
        final_tree_blockers=[
            FinalTreeBlocker(
                path=missing_fragment.en_path,
                code="en_link_target",
                message="missing fragment: required-anchor",
            )
        ],
    )

    assert verified_translation_pr_scope_gaps(expected, [redirect], result) == [
        incomplete.en_path,
        missing_file.en_path,
        redirect.en_path,
    ]
    assert result.final_tree_blockers[0].message == "missing fragment: required-anchor"
