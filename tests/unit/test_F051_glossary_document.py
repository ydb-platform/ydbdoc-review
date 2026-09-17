"""F-051: the glossary is translated as a full, quality-checked document."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.harness.profiles import TRANSLATE_WITH_QA_PROFILE
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.file_profiles import is_glossary_file
from ydbdoc_review.translation.glossary import load_glossary


def test_F051_terms_links():
    assert is_glossary_file("ydb/docs/ru/core/concepts/glossary.md")
    source = "# Glossary\n\nSee [the definition](./missing-term.md).\n"
    pair = DocPair(
        ru_path="ydb/docs/ru/core/concepts/glossary.md",
        en_path="ydb/docs/en/core/concepts/glossary.md",
        ru_changed=True,
    )
    content = PairContent(pair=pair, ru_text=source, en_text=None)
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    ctx = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
        en_toc_reachable=frozenset(),
    )

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del state, ctx
            result = MagicMock()
            result.final_text = source
            result.link_contract_issues = ()
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        result = run_pair_plan(content, plan, ctx, {})

    assert result.target_text == source
    assert "missing-term.md" in result.target_text


def test_F051_quality_modes():
    pair = DocPair(
        ru_path="ydb/docs/ru/core/concepts/glossary.md",
        en_path="ydb/docs/en/core/concepts/glossary.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_text="# Glossary\n\nТермин.\n",  # noqa: RUF001
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
    ctx = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )
    captured: dict[str, object] = {}

    class _FakeHarness:
        def __init__(self, profile):
            captured["profile"] = profile

        def run(self, state, ctx):
            del state, ctx
            result = MagicMock()
            result.final_text = "Glossary.\n"
            result.link_contract_issues = ()
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        run_pair_plan(content, plan, ctx, {})

    assert captured["profile"] is TRANSLATE_WITH_QA_PROFILE
