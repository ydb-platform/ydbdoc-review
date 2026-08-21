"""§6.193: refuse low-magnitude EN patch when fence/tab structure diverges."""

from __future__ import annotations

from ydbdoc_review.harness.steps import _en_structure_safe_for_low_magnitude_patch
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


def test_refuse_patch_when_en_missing_sdk_tab_panes():
    # Real YFM shape from ydb recipes (outer tabs + fenced bodies).
    ru = """{% list tabs %}

- Go

  ```go
  fmt.Println("a")
  ```

- Rust

  ```rust
  println!("b");
  ```

{% endlist %}
"""
    en = """{% list tabs %}

- Go

  ```go
  fmt.Println("a")
  ```

{% endlist %}
"""
    assert _en_structure_safe_for_low_magnitude_patch(ru, en) is False
    assert _en_structure_safe_for_low_magnitude_patch(ru, ru) is True
