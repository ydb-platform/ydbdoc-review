"""F-028: history ambiguity is RED, while infrastructure failure withholds."""

from __future__ import annotations

from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.tip_newer import tip_newer_warnings_block_publish
from ydbdoc_review.pipeline.types import (
    FinalTreeBlocker,
    PRTranslationResult,
    PublicationImpact,
)


def test_F028_ambiguous_route() -> None:
    """An unresolved route is published as RED with its concrete mapping."""
    result = PRTranslationResult(
        final_tree_blockers=[
            FinalTreeBlocker(
                path="ydb/docs/en/core/new.md",
                code="en_link_target",
                message="ambiguous route: /old.md -> /new.md or /legacy.md",
            )
        ]
    )

    assert evaluate_publication_impact(result) is PublicationImpact.PUBLISH_RED
    assert "ambiguous route" in result.final_tree_blockers[0].message


def test_F028_infrastructure() -> None:
    """Unreadable S is infrastructure; content/history warnings are not blockers."""
    infrastructure = PRTranslationResult(publication_failure="cannot read frozen S")
    assert (
        evaluate_publication_impact(infrastructure)
        is PublicationImpact.WITHHOLD_INCOMPLETE
    )

    content_difference = PRTranslationResult(
        yellow_warnings=["source and EN content differ after a concurrent edit"]
    )
    assert evaluate_publication_impact(content_difference) is PublicationImpact.PUBLISH_NORMAL
    assert not tip_newer_warnings_block_publish(content_difference.yellow_warnings)
