"""YAML regression cases for FileHarness (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ydbdoc_review.harness.cases import (
    assert_harness_case,
    case_id_from_path,
    discover_harness_cases,
    load_harness_case,
    run_harness_case,
)

CASES_ROOT = Path(__file__).parent / "cases"
CASE_PATHS = discover_harness_cases(CASES_ROOT)


@pytest.mark.parametrize(
    "case_yaml",
    CASE_PATHS,
    ids=[case_id_from_path(p) for p in CASE_PATHS],
)
def test_harness_regression_case(case_yaml: Path) -> None:
    case = load_harness_case(case_yaml)
    result = run_harness_case(case)
    assert_harness_case(result)
