"""F-088: navigation defects are addressable publishable RED findings."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.navigation.redirects import validate_redirect_merge
from ydbdoc_review.navigation.toc import validate_toc_merge
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.navigation_merge import _navigation_verdict
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.validation.include_targets import check_missing_locale_include_targets
from ydbdoc_review.validation.toc_targets import (
    check_missing_toc_targets,
    check_orphan_pages_for_locale,
)


def test_F088_defects(tmp_path: Path) -> None:
    docs = tmp_path / "ydb" / "docs" / "en"
    (docs / "core").mkdir(parents=True)
    (docs / "page.md").write_text("Page\n", encoding="utf-8")

    toc_path = "ydb/docs/en/core/toc_p.yaml"
    invalid = validate_toc_merge(
        "items:\n- name: [broken\n  href: page.md\n",
        "items:\n- name: [broken\n  href: page.md\n",
        translate_hrefs=set(),
        en_main_yaml="",
    )
    assert any(issue.kind == "invalid_yaml" and "page.md" in issue.detail for issue in invalid)

    toc = "items:\n- name: Page\n  href: ../page.md\n- name: Missing\n  href: ../missing.md\n"
    missing_toc = check_missing_toc_targets(
        toc_path,
        toc,
        repo_path=str(tmp_path),
    )
    assert any("missing.md" in message and toc_path in message for message in missing_toc)

    include_path = "ydb/docs/en/section/page.md"
    missing_include = check_missing_locale_include_targets(
        include_path,
        "{% include [missing](./_includes/missing.md) %}\n",
        repo_path=str(tmp_path),
    )
    assert any("_includes/missing.md" in message and include_path in message for message in missing_include)

    orphan = check_orphan_pages_for_locale(
        {"ydb/docs/en/orphan.md"},
        repo_path=str(tmp_path),
        pending_md_texts={"ydb/docs/en/orphan.md": "Orphan\n"},
        pending_toc_texts={toc_path: "items:\n- name: Page\n  href: ../page.md\n"},
    )
    assert "ydb/docs/en/orphan.md" in orphan
    assert any("toc_p.yaml" in message for message in orphan["ydb/docs/en/orphan.md"])

    parent_issues = validate_toc_merge(
        "items:\n- name: Parent\n  include:\n    path: child/toc_p.yaml\n",
        "items:\n",
        translate_hrefs=set(),
        translate_include_paths={"child/toc_p.yaml"},
        en_main_yaml="items:\n- name: Parent\n  include:\n    path: child/toc_p.yaml\n",
    )
    assert any("include.path" in issue.detail and "child/toc_p.yaml" in issue.detail for issue in parent_issues)

    redirect_issues = validate_redirect_merge(
        "- from: /old\n  to: /new\n",
        "",
        translate_from_paths={"/old"},
        en_main_yaml="",
    )
    assert any("/old" in issue.detail for issue in redirect_issues)


def test_F088_publish_final() -> None:
    pair = DocPair(
        ru_path="ydb/docs/ru/page.md",
        en_path="ydb/docs/en/page.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="Translated.\n",
                file_result=FileTranslationResult(
                    file_path=pair.en_path,
                    final_text="Translated.\n",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="test",
                ),
            )
        ],
        navigation_results=[
            NavigationRunResult(
                ru_path="ydb/docs/ru/core/toc_p.yaml",
                en_path="ydb/docs/en/core/toc_p.yaml",
                kind="toc",
                warnings=["missing_toc_target: EN toc `toc_p.yaml` href `missing.md`"],
                verdict=_navigation_verdict(["missing_toc_target: missing.md"]),
            )
        ],
    )
    assert result.navigation_results[0].verdict == "blocked"
    assert evaluate_publication_impact(result) is PublicationImpact.PUBLISH_RED
