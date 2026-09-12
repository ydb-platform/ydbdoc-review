"""F-078: a reachable page is kept, and losing its last route is RED."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.types import NavigationRunResult, PRTranslationResult
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
from ydbdoc_review.validation.toc_targets import check_orphan_pages_for_locale

ROOT = "ydb/docs"
TOC = f"{ROOT}/en/core/toc_p.yaml"
PAGE = f"{ROOT}/en/core/guide/a.md"
OTHER = f"{ROOT}/en/core/guide/other.md"


def test_F078_still_reachable(tmp_path) -> None:
    """Changing another link does not orphan A while the TOC still lists A."""
    assert check_orphan_pages_for_locale(
        {PAGE},
        repo_path=str(tmp_path),
        pending_toc_texts={TOC: "items:\n- name: A\n  href: guide/a.md\n"},
        pending_md_texts={PAGE: "# A\n"},
    ) == {}


def test_F078_last_path(tmp_path) -> None:
    """Removing A's last TOC path retains A and exposes a blocking RED finding."""
    orphans = check_orphan_pages_for_locale(
        {PAGE, OTHER},
        repo_path=str(tmp_path),
        pending_toc_texts={TOC: "items:\n- name: Other\n  href: guide/other.md\n"},
        pending_md_texts={PAGE: "# A\n", OTHER: "# Other\n"},
    )
    assert PAGE in orphans
    assert "orphan_toc_page:" in orphans[PAGE][0]

    report = build_full_report(
        PRTranslationResult(
            navigation_results=[
                NavigationRunResult(
                    ru_path=f"{ROOT}/ru/core/toc_p.yaml",
                    en_path=TOC,
                    kind="toc",
                    target_text="items:\n",
                    warnings=orphans[PAGE],
                    verdict="blocked",
                )
            ]
        ),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )
    assert "🔴" in report
    assert "не связана" in report
