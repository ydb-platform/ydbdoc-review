"""F-062: preserve unresolved link destinations and RED publication."""

from ydbdoc_review.navigation.link_deps import canonical_md_dependency_path
from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.types import (
    FinalTreeBlocker,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.validation.href_parity import check_href_parity


def test_F062_no_erasure():
    href = "../reference/missing.md#exact-fragment"
    result = PRTranslationResult(
        final_tree_blockers=[
            FinalTreeBlocker(
                path="ydb/docs/en/core/index.md",
                code="en_link_target",
                message=f"target: {href}\nmissing file",
            )
        ]
    )

    assert href in result.final_tree_blockers[0].message
    assert evaluate_publication_impact(result) == PublicationImpact.PUBLISH_RED


def test_F062_raw_paths():
    redirects = "common:\n  - from: /old.md\n    to: /final.md\n"
    assert (
        canonical_md_dependency_path(
            "ydb/docs/ru/core/old.md",
            redirects_yaml=redirects,
        )
        == "ydb/docs/ru/core/final.md"
    )
    assert check_href_parity("[old](old.md#section)\n", "[старый](old.md#section)\n") == []
    assert check_href_parity(
        "[old](old.md#section)\n", "[старый](other.md#section)\n"
    )
