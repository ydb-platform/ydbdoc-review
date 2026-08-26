"""Partial verify realign on large files (#49957 / §6.191)."""

from __future__ import annotations

from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import RoundTripStep, _try_partial_verify_realign
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary


def test_partial_verify_realign_translates_gap_segments_only(monkeypatch):
    """Large file: one missing table row → partial realign, not full retranslate."""
    ru = (
        "| A | B |\n| --- | --- |\n"
        "| `ydb.access.grant` | GRANT |\n"
        "| **Права на основе других** | | |\n"
        "| `ydb.tables.modify` | MODIFY |\n"
    )
    en = (
        "| A | B |\n| --- | --- |\n"
        "| `ydb.access.grant` | GRANT |\n"
        "| `ydb.tables.modify` | MODIFY |\n"
    )
    segs = extract_segments(parse_markdown(ru))
    assert len(segs) > 4

    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/yql/reference/_includes/permissions_list.md",
        raw_source_text=ru,
        source_text=ru,
        existing_target_text=en,
        translated_text=en,
        segments=segs,
        source_doc=parse_markdown(ru),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    ctx = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )

    calls: list[int] = []

    def _fake_translate(pending, *_a, **_k):
        calls.append(len(pending))
        return {seg.id: "| **Rights from other rights** | | |" for seg in pending}

    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments", _fake_translate
    )
    RoundTripStep().run(state, ctx)
    assert calls, "partial realign should translate gap segments"
    assert calls[0] <= 80
    assert state.segment_alignment_error is None
    assert any("verify_realign_partial:" in w for w in state.finalize_warnings)
    assert "**Rights from other rights**" in state.translated_text


def test_partial_verify_realign_keeps_checkout_when_rebuild_drops_en_links(monkeypatch):
    """A gap repair must not erase unrelated immutable EN link atoms (#50976)."""
    ru = (
        "# Мониторинг\n\n"
        "Первая строка.\n\n"
        "Новая строка.\n\n"
        "Ещё одна новая строка.\n\n"
        "Последняя строка.\n"
    )
    en = (
        "# Monitoring\n\n"
        "First row.\n\n"
        "See [monitoring](../ydb-ui/ydb-monitoring.md) and "
        "[YDB UI](../../reference/ydb-ui/index.md).\n\n"
        "Last row.\n"
    )
    segs = extract_segments(parse_markdown(ru))
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/observability/monitoring_config.md",
        raw_source_text=ru,
        source_text=ru,
        existing_target_text=en,
        translated_text=en,
        segments=segs,
        source_doc=parse_markdown(ru),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    ctx = HarnessContext.from_options(
        MagicMock(), glossary=load_glossary(), config=cfg
    )

    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments",
        lambda pending, *_a, **_k: {seg.id: "New row." for seg in pending},
    )
    RoundTripStep().run(state, ctx)

    assert state.translated_text == en
    assert "../ydb-ui/ydb-monitoring.md" in state.translated_text
    assert "../../reference/ydb-ui/index.md" in state.translated_text
    assert state.segment_alignment_error
    assert any("verify_realign_preserved_checkout:" in w for w in state.finalize_warnings)


def _partial_state(ru: str, en: str) -> tuple[FileRunState, HarnessContext]:
    segments = extract_segments(parse_markdown(ru))
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/reference/configuration/monitoring_config.md",
        raw_source_text=ru,
        source_text=ru,
        existing_target_text=en,
        translated_text=en,
        segments=segments,
        source_doc=parse_markdown(ru),
    )
    config = load_config(
        env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}
    )
    return state, HarnessContext.from_options(
        MagicMock(), glossary=load_glossary(), config=config
    )


def test_pr_50976_62_63_like_nine_gap_rebuild_rolls_back_link_loss(monkeypatch):
    ru = "\n\n".join(f"RU paragraph {index}." for index in range(62)) + "\n"
    en_parts = [f"EN paragraph {index}." for index in range(61)]
    en_parts.append("[Monitoring](../ydb-ui/ydb-monitoring.md).")
    en_parts.append("[YDB UI](../../reference/ydb-ui/index.md).")
    en = "\n\n".join(en_parts) + "\n"
    state, ctx = _partial_state(ru, en)
    assert len(state.segments) == 62
    assert len(extract_segments(parse_markdown(en))) == 63
    seeded = {segment.id: f"Seed {index}." for index, segment in enumerate(state.segments[:53])}
    translated_pending: list[int] = []

    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.partial_align_translations_from_target",
        lambda *_a, **_k: seeded,
    )

    def fake_translate(pending, *_a, **_k):
        translated_pending.append(len(pending))
        return {segment.id: "Gap." for segment in pending}

    monkeypatch.setattr("ydbdoc_review.harness.steps.translate_segments", fake_translate)
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps._render_translated_from_source",
        lambda current, _ctx: setattr(current, "translated_text", "Candidate without links.\n"),
    )

    assert _try_partial_verify_realign(state, ctx) == "unsafe"
    assert translated_pending == [9]
    assert state.translated_text == en


def test_partial_verify_realign_rolls_back_explicit_anchor_loss(monkeypatch):
    state, ctx = _partial_state(
        "# Раздел {#stable}\n\nНовый абзац.\n",
        "# Section {#stable}\n",
    )
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.partial_align_translations_from_target",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments",
        lambda pending, *_a, **_k: {segment.id: "Translated." for segment in pending},
    )
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps._render_translated_from_source",
        lambda current, _ctx: setattr(current, "translated_text", "# Section\n\nTranslated.\n"),
    )

    assert _try_partial_verify_realign(state, ctx) == "unsafe"
    assert state.translated_text == "# Section {#stable}\n"


def test_partial_verify_realign_counts_duplicate_hrefs(monkeypatch):
    href = "../ydb-ui/ydb-monitoring.md"
    original = f"[One]({href}) and [Two]({href}).\n"
    state, ctx = _partial_state("Первый.\n\nВторой.\n", original)
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.partial_align_translations_from_target",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments",
        lambda pending, *_a, **_k: {segment.id: "Translated." for segment in pending},
    )
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps._render_translated_from_source",
        lambda current, _ctx: setattr(current, "translated_text", f"[One]({href}).\n"),
    )

    assert _try_partial_verify_realign(state, ctx) == "unsafe"
    assert state.translated_text == original


def test_partial_verify_realign_accepts_candidate_preserving_atoms(monkeypatch):
    original = "# Section {#stable}\n\n[Monitoring](../monitoring.md).\n"
    state, ctx = _partial_state("Раздел.\n\nНовый абзац.\n", original)
    candidate = original + "\nTranslated addition.\n"
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.partial_align_translations_from_target",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments",
        lambda pending, *_a, **_k: {segment.id: "Translated." for segment in pending},
    )
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps._render_translated_from_source",
        lambda current, _ctx: setattr(current, "translated_text", candidate),
    )

    assert _try_partial_verify_realign(state, ctx) == "applied"
    assert state.translated_text == candidate


def test_round_trip_verify_restores_missing_heading_anchor_without_llm(monkeypatch):
    """#49957 example-dotnet.md: missing {#csharp-app} is repaired before critic."""
    ru = "# Приложение на C# {#csharp-app}\n\nТело страницы.\n"
    en = "# Example app in C# (.NET)\n\nPage body.\n"
    segs = extract_segments(parse_markdown(ru))
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/dev/example-app/_includes/example-dotnet.md",
        raw_source_text=ru,
        source_text=ru,
        existing_target_text=en,
        translated_text=en,
        segments=segs,
        source_doc=parse_markdown(ru),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    ctx = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
        target_lang="en",
        source_lang="ru",
    )

    def _boom(*_a, **_k):
        raise AssertionError("anchor restore must not call translate_segments")

    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments", _boom
    )
    RoundTripStep().run(state, ctx)
    assert "{#csharp-app}" in state.translated_text
    assert state.segment_alignment_error is None
    assert any("structural_repair:" in w for w in state.finalize_warnings)


def test_partial_verify_realign_skips_when_too_many_pending(monkeypatch):
    """Gap set > 80: do not LLM-translate; leave alignment blocker (§6.185)."""
    ru_parts = [f"## H{i} {{#h{i}}}\n\nPara {i}.\n" for i in range(90)]
    ru = "# Title\n\n" + "\n".join(ru_parts)
    en = "# Title\n\nDifferent structure only.\n"
    segs = extract_segments(parse_markdown(ru))
    assert len(segs) > 80

    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/dev/large-guide.md",
        raw_source_text=ru,
        source_text=ru,
        existing_target_text=en,
        translated_text=en,
        segments=segs,
        source_doc=parse_markdown(ru),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    ctx = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("pending > 80 must not call translate_segments")

    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments", _boom
    )
    RoundTripStep().run(state, ctx)
    assert called["n"] == 0
    assert state.segment_alignment_error
    assert any("verify_realign_skipped" in w for w in state.finalize_warnings)
