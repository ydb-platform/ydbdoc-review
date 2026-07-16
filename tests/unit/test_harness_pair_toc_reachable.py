"""Regression: en_toc_reachable must reach finalize_en_target (§6.112 / #46846)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary


def test_run_pair_plan_forwards_en_toc_reachable_to_harness():
    """Bug #46846: strip never ran because pair rebuilt HarnessContext without reachable."""
    pair = DocPair(
        ru_path="ydb/docs/ru/core/dev/streaming-query/index.md",
        en_path="ydb/docs/en/core/dev/streaming-query/index.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_text="# RU\n\nSee [Watermarks](watermarks.md).\n",
        en_text=None,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="test",
    )
    reachable = frozenset(
        {
            "ydb/docs/en/core/dev/streaming-query/index.md",
            "ydb/docs/en/core/dev/streaming-query/patterns.md",
        }
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
        en_toc_reachable=reachable,
    )

    captured: dict[str, object] = {}

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            captured["en_toc_reachable"] = ctx.en_toc_reachable
            result = MagicMock()
            result.final_text = "ok"
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        run_pair_plan(content, plan, parent, {})

    assert captured["en_toc_reachable"] is reachable
