"""Tests for §6.132 differential translation analyzer and seed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.profiles import TRANSLATE_PROFILE
from ydbdoc_review.harness.runner import FileHarness
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.differential import (
    DifferentialTranslationAnalyzer,
    DifferentialTranslationConfig,
    analyze_ru_diff,
    find_corresponding_en_block,
    is_en_file_incomplete,
    is_en_file_stale,
    parse_markdown_blocks,
    prepare_differential_seed,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments


def _cfg(**kwargs: object) -> DifferentialTranslationConfig:
    return DifferentialTranslationConfig(enabled=True, **kwargs)  # type: ignore[arg-type]


def test_parse_markdown_blocks_basic() -> None:
    blocks = parse_markdown_blocks("# Title\n\nHello world.\n")
    assert len(blocks) >= 2
    assert blocks[0].kind == "heading"
    assert "Title" in blocks[0].content or blocks[0].content


def test_analyze_new_file_magnitude_one() -> None:
    analysis = analyze_ru_diff(None, "## Новый\n\nТекст.\n")
    assert analysis.change_type == "new_file"
    assert analysis.change_magnitude == 1.0
    assert analysis.added_blocks


def test_analyze_small_edit_low_magnitude() -> None:
    base = "## Title\n\nParagraph one.\n\nParagraph two.\n"
    pr = "## Title\n\nParagraph one changed.\n\nParagraph two.\n"
    analysis = analyze_ru_diff(base, pr)
    assert analysis.change_type == "modified"
    assert analysis.change_magnitude < 0.5
    assert analysis.kept_segment_ids
    assert analysis.added_segment_ids or analysis.modified_segment_ids


def test_strategy_full_when_no_en() -> None:
    a = DifferentialTranslationAnalyzer(_cfg())
    s = a.analyze_file_state(
        ru_pr_text="## X\n\nY.\n",
        en_current_text=None,
        ru_base_text="## X\n\nY.\n",
    )
    assert s.mode == "full"
    assert s.config.get("is_new_file") is True


def test_strategy_full_when_incomplete_en() -> None:
    a = DifferentialTranslationAnalyzer(_cfg(min_en_file_ratio=0.3))
    s = a.analyze_file_state(
        ru_pr_text="A" * 1000,
        en_current_text="Hi",
        ru_base_text="A" * 1000,
    )
    assert s.mode == "full"
    assert s.config.get("en_file_incomplete") is True


def test_strategy_full_when_stale() -> None:
    a = DifferentialTranslationAnalyzer(_cfg(stale_days_threshold=90))
    old = datetime.now(timezone.utc) - timedelta(days=120)
    s = a.analyze_file_state(
        ru_pr_text="## T\n\nBody.\n",
        en_current_text="## T\n\nBody.\n",
        ru_base_text="## T\n\nBody.\n",
        en_last_modified=old,
    )
    assert s.mode == "full"
    assert s.config.get("en_stale") is True


def test_strategy_full_high_magnitude() -> None:
    base = "\n\n".join(f"Paragraph {i}.\n" for i in range(10))
    pr = "\n\n".join(f"Changed {i} completely here.\n" for i in range(10))
    a = DifferentialTranslationAnalyzer(_cfg(change_magnitude_threshold=0.5))
    s = a.analyze_file_state(
        ru_pr_text=pr,
        en_current_text=pr,  # size ok
        ru_base_text=base,
    )
    assert s.mode == "full"
    assert "magnitude" in s.reason.lower() or "High" in s.reason


def test_strategy_differential_small_change() -> None:
    base = "## Title\n\nKeep me.\n\nAlso keep.\n"
    pr = "## Title\n\nKeep me.\n\nAlso keep.\n\nNew paragraph.\n"
    en = "## Title\n\nKeep me EN.\n\nAlso keep EN.\n"
    a = DifferentialTranslationAnalyzer(_cfg())
    s = a.analyze_file_state(
        ru_pr_text=pr, en_current_text=en, ru_base_text=base
    )
    assert s.mode == "differential"


def test_is_en_incomplete_and_stale_helpers() -> None:
    assert is_en_file_incomplete("x", "y" * 100, min_en_file_ratio=0.3)
    assert not is_en_file_incomplete("y" * 50, "y" * 100, min_en_file_ratio=0.3)
    assert is_en_file_stale(
        last_modified=datetime.now(timezone.utc) - timedelta(days=100),
        stale_days=90,
    )
    assert not is_en_file_stale(
        last_modified=datetime.now(timezone.utc) - timedelta(days=10),
        stale_days=90,
    )


def test_find_corresponding_en_block_positional() -> None:
    ru_base = parse_markdown_blocks("## A\n\nOne.\n")
    en = parse_markdown_blocks("## A\n\nOne EN.\n")
    assert len(ru_base) == len(en)
    found = find_corresponding_en_block(ru_base[0], ru_base, en)
    assert found is not None
    assert found.content == en[0].content


def test_prepare_seed_reuses_unchanged() -> None:
    base = "## Title\n\nStable paragraph.\n"
    pr = "## Title\n\nStable paragraph.\n\nNew RU bit.\n"
    en = "## Title\n\nStable paragraph EN.\n"
    strategy, seeded, pending = prepare_differential_seed(
        pr_segments=extract_segments(parse_markdown(pr)),
        ru_pr_text=pr,
        en_current_text=en,
        ru_base_text=base,
        config=_cfg(),
    )
    assert strategy.mode == "differential"
    assert seeded
    assert pending
    assert all(s.id not in seeded for s in pending)


def test_translate_step_differential_calls_llm_only_for_pending() -> None:
    base = "## Title\n\nStable.\n"
    pr = "## Title\n\nStable.\n\nНовый абзац.\n"
    en = "## Title\n\nStable EN.\n"

    pr_segs = extract_segments(parse_markdown(pr))
    strategy, seeded, pending = prepare_differential_seed(
        pr_segments=pr_segs,
        ru_pr_text=pr,
        en_current_text=en,
        ru_base_text=base,
        config=_cfg(),
    )
    assert strategy.mode == "differential"
    pending_ids = {s.id for s in pending}

    translated_ids: list[str] = []

    def _fake_translate(segments, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        del args, kwargs
        translated_ids.extend(s.id for s in segments)
        return {s.id: f"EN:{s.text[:20]}" for s in segments}

    import ydbdoc_review.harness.steps as steps_mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(steps_mod, "translate_segments", _fake_translate)
    try:
        cfg = load_config(
            env={
                "YDBDOC_YC_FOLDER_ID": "b1x",
                "YDBDOC_YC_API_KEY": "k",
            }
        )
        client = MagicMock()
        ctx = HarnessContext.from_options(
            client, glossary=load_glossary(), config=cfg
        )
        state = FileRunState(
            mode="translate",
            file_path="ydb/docs/ru/core/x.md",
            raw_source_text=pr,
            source_text=pr,
            existing_target_text=en,
            base_source_text=base,
        )
        FileHarness(TRANSLATE_PROFILE).run(state, ctx)
        assert state.differential_meta.get("mode") == "differential"
        assert set(translated_ids) == pending_ids
        assert set(seeded).issubset(state.translations)
        assert state.translated_text
    finally:
        monkey.undo()


def test_disabled_forces_full() -> None:
    a = DifferentialTranslationAnalyzer(
        DifferentialTranslationConfig(enabled=False)
    )
    s = a.analyze_file_state(
        ru_pr_text="## T\n\nA.\n",
        en_current_text="## T\n\nA.\n",
        ru_base_text="## T\n\nA.\n",
    )
    assert s.mode == "full"
