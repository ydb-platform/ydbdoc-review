"""RED/GREEN protect vs translate matrix (REQUIREMENTS_RU.md §5 / §9 / §13 / §14)."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.steps import ParseStep, TranslateStep
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.parsing.front_matter import (
    FrontMatterError,
    apply_front_matter_updates,
    parse_front_matter_with_spans,
)
from ydbdoc_review.parsing import front_matter as front_matter_mod
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.types import SegmentKind
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.heuristics import (
    check_cyrillic_in_en,
    check_unrestored_placeholders,
    run_file_heuristics_classified,
)


def test_green_fm_and_yfm_titles_round_trip():
    text = (
        "---\n"
        "title: Заголовок\n"
        "vcsPath: docs/ru/a.md\n"
        "description: Описание\n"
        "---\n\n"
        '{% note warning "Осторожно" %}\n\n'
        "Тело.\n\n"
        "{% endnote %}\n\n"
        '{% cut "Подробности" %}\n\n'
        "Внутри.\n\n"
        "{% endcut %}\n"
    )
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    translations: dict[str, str] = {}
    for s in segments:
        if s.kind == SegmentKind.FRONT_MATTER and s.text == "Заголовок":
            translations[s.id] = "Title"
        elif s.kind == SegmentKind.FRONT_MATTER and s.text == "Описание":
            translations[s.id] = "Description"
        elif s.kind == SegmentKind.NOTE_TITLE:
            translations[s.id] = "Be careful"
        elif s.kind == SegmentKind.CUT_TITLE:
            translations[s.id] = "Details"
        elif "Тело" in s.text:
            translations[s.id] = "Body."
        else:
            translations[s.id] = "Inside."
    out = render_markdown(reinsert_segments(doc, segments, translations))
    assert "title: Title" in out
    assert "description: Description" in out
    assert "vcsPath: docs/ru/a.md" in out
    assert '{% note warning "Be careful" %}' in out
    assert '{% cut "Details" %}' in out
    assert "{% endnote %}" in out
    assert "{% endcut %}" in out


def test_red_leftover_ru_note_cut_title_is_caught():
    """Leaving note/cut titles in Russian after translate must block publication."""
    leftover = (
        '{% note warning "Осторожно" %}\n\n'
        "Body text.\n\n"
        "{% endnote %}\n\n"
        '{% cut "Подробности" %}\n\n'
        "Inside.\n\n"
        "{% endcut %}\n"
    )
    cyr = check_cyrillic_in_en(leftover, target_lang="en")
    assert cyr, "leftover RU note/cut titles must be visible to Cyrillic gate"
    classified = run_file_heuristics_classified(
        '{% note warning "Осторожно" %}\n\nТело.\n\n{% endnote %}\n',
        leftover,
        normalized_source_text='{% note warning "Осторожно" %}\n\nТело.\n\n{% endnote %}\n',
        source_lang="ru",
        target_lang="en",
    )
    assert classified.blocking, "leftover RU titles must be a blocking heuristic"


def test_red_mutated_non_title_front_matter_fails():
    """Non-title FM keys must not change; mutation is a contract failure."""
    raw = (
        "title: Заголовок\n"
        "# keep comment\n"
        "vcsPath: ru/path.md\n"
        "description: Описание\n"
        "editable: false\n"
    )
    # Non-title keys in the updates map are ignored (not applied).
    untouched = apply_front_matter_updates(raw, {"vcsPath": "en/evil.md", "editable": "true"})
    assert untouched == raw
    assert "evil" not in untouched

    # Surgical title update preserves unselected bytes.
    good = apply_front_matter_updates(
        raw, {"title": "Title", "description": "Description", "vcsPath": "evil"}
    )
    assert "vcsPath: ru/path.md" in good
    assert "# keep comment" in good
    assert "editable: false" in good
    assert "evil" not in good

    # Integrity gate: if an unselected key value drifts, raise.
    source_fields, source_records = parse_front_matter_with_spans(raw)
    mutated = good.replace("vcsPath: ru/path.md", "vcsPath: en/evil.md")
    new_fields, new_records = parse_front_matter_with_spans(mutated)
    with pytest.raises(FrontMatterError, match="vcsPath"):
        front_matter_mod._assert_update_integrity(
            source_fields=source_fields,
            source_records=source_records,
            new_fields=new_fields,
            new_records=new_records,
            updates={"title": "Title", "description": "Description"},
        )


def test_red_unrestored_protect_markers_fail():
    text = "The cluster uses ⟦C1⟧ and [link](%E2%9F%A6U1%E2%9F%A7).\n"
    msgs = check_unrestored_placeholders(text, target_lang="en")
    assert msgs and msgs[0].startswith("unrestored_placeholder:")
    classified = run_file_heuristics_classified(
        "Кластер.\n",
        text,
        normalized_source_text="Кластер.\n",
        source_lang="ru",
        target_lang="en",
    )
    assert any(m.startswith("unrestored_placeholder:") for m in classified.blocking)


def test_translate_path_one_pass_without_differential_seed(monkeypatch):
    """doc_translate must full-pass without calling differential seed/splice."""
    base = "## Title\n\nStable paragraph.\n"
    pr = "## Title\n\nStable paragraph.\n\nНовый абзац.\n"
    en = "## Title\n\nStable paragraph EN.\n"

    translated_batches: list[list[str]] = []

    def _fake_translate(segments, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        del args, kwargs
        translated_batches.append([s.id for s in segments])
        return {s.id: f"EN:{s.text}" for s in segments}

    import ydbdoc_review.harness.steps as steps_mod

    monkeypatch.setattr(steps_mod, "translate_segments", _fake_translate)
    seed_calls = {"n": 0}
    splice_calls = {"n": 0}

    def _forbid_seed(**_kwargs):
        seed_calls["n"] += 1
        raise AssertionError("prepare_differential_seed must not run on translate")

    def _forbid_splice(*_args, **_kwargs):
        splice_calls["n"] += 1
        raise AssertionError("patch_en_with_added_translations must not run on translate")

    # TranslateStep no longer imports these; bind spies so accidental reintroduction fails.
    monkeypatch.setattr(steps_mod, "prepare_differential_seed", _forbid_seed, raising=False)
    monkeypatch.setattr(
        steps_mod, "patch_en_with_added_translations", _forbid_splice, raising=False
    )

    cfg = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "b1x",
            "YDBDOC_YC_API_KEY": "k",
            # Even if someone re-enables the flag, TranslateStep must ignore it.
            "YDBDOC_TRANSLATION_DIFFERENTIAL_ENABLED": "true",
        }
    )
    # Force config field on as well (loader env name may differ).
    cfg.translation.differential_enabled = True
    ctx = HarnessContext.from_options(
        MagicMock(), glossary=load_glossary(), config=cfg
    )
    state = FileRunState(
        mode="translate",
        file_path="ydb/docs/ru/core/x.md",
        raw_source_text=pr,
        source_text=pr,
        existing_target_text=en,
        base_source_text=base,
        base_target_text=en,
    )
    ParseStep().run(state, ctx)
    with patch("ydbdoc_review.harness.steps.finalize_en_target", side_effect=lambda text, *a, **k: text):
        with patch(
            "ydbdoc_review.harness.steps._apply_en_structural_repair",
            lambda *_a, **_k: None,
        ):
            TranslateStep().run(state, ctx)

    assert seed_calls["n"] == 0
    assert splice_calls["n"] == 0
    assert len(translated_batches) == 1
    assert set(translated_batches[0]) == {s.id for s in state.segments}
    assert state.differential_meta["mode"] == "full"
    assert state.differential_meta["seeded"] == 0
    assert state.differential_meta["low_magnitude_patch"] is False
    assert state.differential_meta["semantic_noop"] is False
    assert state.stopped_early is False
    # Old EN prose must not be the published result of a low-magnitude splice.
    assert "Stable paragraph EN" not in (state.translated_text or "")
    assert state.translated_text
