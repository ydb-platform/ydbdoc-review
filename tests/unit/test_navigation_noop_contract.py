"""Regression tests for navigation merge no-op verdicts."""

from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.navigation_merge import merge_navigation_pair
from ydbdoc_review.pipeline.pairs import NavigationPair
from ydbdoc_review.translation.glossary import load_glossary

_TOC_TEXT = "items:\n- name: Authentication\n  href: authentication.md\n"
_PAIR = NavigationPair(
    ru_path="ydb/docs/ru/core/security/toc_p.yaml",
    en_path="ydb/docs/en/core/security/toc_p.yaml",
    ru_changed=True,
)
_PREFIX = "ydbdoc_review.pipeline.navigation_merge."


def _merge_noop_with_warnings(warnings: list[str]):
    with (
        patch(_PREFIX + "read_text", return_value=_TOC_TEXT),
        patch(
            _PREFIX + "_read_navigation_baselines",
            return_value=(_TOC_TEXT, _TOC_TEXT),
        ),
        patch(_PREFIX + "merge_en_toc_yaml", return_value=_TOC_TEXT),
        patch(
            _PREFIX + "validate_navigation_merge_warnings",
            return_value=warnings,
        ),
        patch(_PREFIX + "read_text_at_upstream_tip", return_value="page"),
    ):
        return merge_navigation_pair(
            _PAIR,
            repo_path="/tmp/noop-fixture",
            merge_base_with="frozen",
            client=MagicMock(),
            glossary=load_glossary(),
            config=load_config(env={}),
        )


def test_noop_does_not_erase_blocking_warning():
    result = _merge_noop_with_warnings(["scope_not_applied: missing caching child"])

    assert result.target_text is None
    assert result.warnings == ["scope_not_applied: missing caching child"]
    assert result.verdict == "blocked"


@pytest.mark.parametrize(
    ("warnings", "expected_verdict"),
    [
        ([], "ok"),
        (["toc_en_only_legacy: authentication.md"], "ok"),
    ],
)
def test_clean_and_soft_warning_noops_keep_canonical_verdict(
    warnings: list[str], expected_verdict: str
):
    result = _merge_noop_with_warnings(warnings)

    assert result.target_text is None
    assert result.warnings == warnings
    assert result.verdict == expected_verdict
