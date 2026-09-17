"""F-141 contracts for independent no-op and explicit verify checks."""

from __future__ import annotations

from types import SimpleNamespace

from ydbdoc_review.github.workflow import _analyzed_noop_result
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.critic import run_verify
from ydbdoc_review.translation.glossary import load_glossary


def _content(*, en_text: str | None = "Prose", en_deleted: bool = False) -> PairContent:
    return PairContent(
        pair=DocPair(
            "ru/a.md",
            "en/a.md",
            ru_changed=True,
            en_deleted=en_deleted,
        ),
        ru_text="Проза",
        en_text=en_text,
    )


def test_F141_noop_checks() -> None:
    class NoOpClient:
        def chat(self, *args, **kwargs):
            raise AssertionError("invalid pair must not receive an early no-op")

    for content in (_content(en_text=None), _content(en_deleted=True)):
        assert (
            _analyzed_noop_result(
                [content], NoOpClient(), load_glossary(), prompt_version="v1"
            )
            is None
        )


def test_F141_explicit_verify() -> None:
    calls: list[str] = []

    class RecordingClient:
        usage_tracker = SimpleNamespace(record=lambda **kwargs: None)

        def model_chain_for_role(self, role):
            assert role == "critic"
            return ["critic"]

        def chat(self, messages, **kwargs):
            calls.append(kwargs["model"])
            return SimpleNamespace(content='{"verdict": "ok", "issues": []}')

    segment = Segment(
        id="s1",
        kind=SegmentKind.PARAGRAPH,
        path=["Intro"],
        text="Текущий полный source",
        placeholders=[],
        ast_path=[0],
    )
    result = run_verify(
        RecordingClient(),
        segments=[segment],
        translations={"s1": "Current full target"},
        prior_issues=[],
        glossary=load_glossary(),
        file_path="docs/ru/a.md",
        source_text="Текущий полный source",
        translated_text="Current full target",
    )

    assert result.verdict == "ok"
    assert calls == ["critic"]
