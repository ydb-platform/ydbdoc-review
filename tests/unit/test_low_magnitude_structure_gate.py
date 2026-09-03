"""Low-magnitude EN splice is retired on translate (REQUIREMENTS_RU.md §5 / §13)."""

from __future__ import annotations

import ydbdoc_review.harness.steps as steps
from ydbdoc_review.config.loader import TranslationConfig
from ydbdoc_review.segmentation.extractor import (
    DEFAULT_TAB_TITLE_WHITELIST,
    _is_whitelisted_tab_title,
)
from ydbdoc_review.validation.heuristics import _classify_heuristic


def test_verify_realign_partial_is_info_not_blocking():
    assert (
        _classify_heuristic("verify_realign_partial: translated 4 gap segment(s) from RU")
        == "info"
    )


def test_alternative_tab_titles_whitelisted():
    assert _is_whitelisted_tab_title("Python (alternative)", DEFAULT_TAB_TITLE_WHITELIST)
    assert _is_whitelisted_tab_title(
        "Python (альтернативный)", DEFAULT_TAB_TITLE_WHITELIST
    )


def test_translate_step_no_longer_exports_low_magnitude_structure_gate():
    """Structure-safe splice gate is gone with differential EN stitch on translate."""
    assert not hasattr(steps, "_en_structure_safe_for_low_magnitude_patch")
    assert not hasattr(steps, "prepare_differential_seed")
    assert not hasattr(steps, "patch_en_with_added_translations")
    assert TranslationConfig().differential_enabled is False
