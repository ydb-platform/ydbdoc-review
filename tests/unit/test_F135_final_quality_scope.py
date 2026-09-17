from ydbdoc_review.pipeline.types import FinalTreeBlocker, PRTranslationResult
from ydbdoc_review.reporting.builder import _qa_status


def test_F135_scope_green() -> None:
    clean = PRTranslationResult()
    blocked = PRTranslationResult(completeness_gaps=["docs/en/a.md"])
    tree_blocked = PRTranslationResult(
        final_tree_blockers=[FinalTreeBlocker("docs/en/a.md", "en_link_target", "missing")]
    )

    assert "нет обработанных" in _qa_status(clean)[1]
    assert _qa_status(blocked)[0] == "🔴"
    assert _qa_status(tree_blocked)[0] == "🔴"


def test_F135_irrelevant_data() -> None:
    result = PRTranslationResult(yellow_warnings=["external orphan outside scope"])

    quality = _qa_status(result)
    assert quality[0] != "🔴"
    assert "external orphan" not in quality[1]
    assert "служебного протокола" not in quality[1]
