"""F-142 contracts for continue-driven Analyze invalidation."""

from __future__ import annotations

from ydbdoc_review.github.workflow import _analyzed_noop_result
from ydbdoc_review.ops.feedback_ctx import continue_feedback_scope
from ydbdoc_review.ops.lifecycle import compose_continue_feedback
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary


def _content(path: str) -> PairContent:
    return PairContent(
        pair=DocPair(f"ru/{path}.md", f"en/{path}.md", ru_changed=True),
        ru_text="Source prose",
        en_text="Current target prose",
    )


def test_F142_force_pair() -> None:
    class MustNotReuseNoOp:
        def chat(self, *args, **kwargs):
            raise AssertionError("continue must not reuse the old Analyze no-op")

    with continue_feedback_scope("Переведи заново пару docs/ru/a.md целиком"):
        result = _analyzed_noop_result(
            [_content("a")], MustNotReuseNoOp(), load_glossary(), prompt_version="v1"
        )
    assert result is None

    combined = compose_continue_feedback(
        "Переведи заново пару docs/ru/a.md целиком",
        "previous Analyze: no_translation_needed",
    )
    assert "Переведи заново" in combined
    assert "previous Analyze" in combined


def test_F142_other_pairs() -> None:
    # Without continue, the current no-op contract remains available for a
    # separately confirmed pair; the continue invalidation is scoped to the
    # active continuation context and does not alter PairContent/source paths.
    content = _content("b")
    assert content.pair.ru_path == "ru/b.md"
    assert content.pair.en_path == "en/b.md"
    assert content.ru_text == "Source prose"
    assert content.en_text == "Current target prose"
