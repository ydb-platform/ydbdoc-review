"""F-079: historical route conflicts preserve the live route and stay RED."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.navigation.redirects import (
    merge_en_redirects_yaml,
    parse_redirect_entries,
)
from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.types import (
    FinalTreeBlocker,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report


def test_F079_preserve_route() -> None:
    en_main = "- from: /legacy.md\n  to: /live.md\n"
    historical_ru = "- from: /new.md\n  to: /candidate.md\n"

    merged = merge_en_redirects_yaml(
        en_main,
        historical_ru,
        translate_from_paths={"/new.md"},
    )
    routes = {entry["from_path"]: entry["to_path"] for entry in parse_redirect_entries(merged)}
    assert routes["/legacy.md"] == "/live.md"
    assert routes["/new.md"] == "/candidate.md"
    # The merge is data-only: no content from the existing live target is replaced.


def test_F079_red_candidate() -> None:
    conflict = FinalTreeBlocker(
        path="ydb/docs/en/core/legacy.md",
        code="en_link_target",
        message=(
            "route conflict: existing /legacy.md -> /live.md; "
            "candidate requested /candidate.md"
        ),
    )
    result = PRTranslationResult(final_tree_blockers=[conflict])

    assert evaluate_publication_impact(result) is PublicationImpact.PUBLISH_RED
    report = build_full_report(
        result,
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )
    assert "QA RED, do not merge" in report
    assert "Candidate опубликован для ручного исправления" in report
    assert "/legacy.md" in report and "/candidate.md" in report
