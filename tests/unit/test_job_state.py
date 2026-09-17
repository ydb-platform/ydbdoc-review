"""Unit tests for job continuability state (§2 / P7)."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.ops.job_state import (
    ContinuabilityState,
    clear_continuability,
    load_continuability,
    mark_continuable,
    save_continuability,
)


def test_allows_continue_requires_flag_stage_and_shas():
    assert ContinuabilityState(
        continuable=True,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "abc"},
        source_pr=7,
    ).allows_continue()

    assert not ContinuabilityState(
        continuable=False,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "abc"},
        source_pr=7,
    ).allows_continue()

    assert not ContinuabilityState(
        continuable=True,
        unfinished_stage=None,
        fixed_shas={"merge_base": "abc"},
        source_pr=7,
    ).allows_continue()

    assert not ContinuabilityState(
        continuable=True,
        unfinished_stage="verify",
        fixed_shas={},
        source_pr=7,
    ).allows_continue()


def test_mark_and_load_roundtrip(tmp_path: Path):
    path = mark_continuable(
        tmp_path,
        source_pr=42,
        unfinished_stage="verify",
        fixed_shas={"head": "aaa", "merge_base": "bbb"},
        translation_pr=99,
    )
    assert path is not None
    assert path.is_file()

    loaded = load_continuability(tmp_path, 42)
    assert loaded is not None
    assert loaded.allows_continue()
    assert loaded.translation_pr == 99
    assert loaded.fixed_shas["head"] == "aaa"


def test_clear_continuability_disallows_continue(tmp_path: Path):
    mark_continuable(
        tmp_path,
        source_pr=5,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "x"},
    )
    clear_continuability(tmp_path, 5)
    loaded = load_continuability(tmp_path, 5)
    assert loaded is not None
    assert loaded.continuable is False
    assert loaded.unfinished_stage is None
    assert not loaded.allows_continue()


def test_save_without_stage_not_continuable(tmp_path: Path):
    save_continuability(
        tmp_path,
        ContinuabilityState(
            continuable=True,
            unfinished_stage="",
            fixed_shas={"merge_base": "x"},
            source_pr=1,
        ),
    )
    loaded = load_continuability(tmp_path, 1)
    assert loaded is not None
    assert not loaded.allows_continue()
