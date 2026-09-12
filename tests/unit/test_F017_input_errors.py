"""F-017 contracts for input, infrastructure, and budget failures."""

from __future__ import annotations

import pytest
from typer import BadParameter

from ydbdoc_review.cli import job
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.pr import pick_verify_ru_text, source_pr_content_ref_from_pull
from ydbdoc_review.ops.gates import check_daily_quota


def test_F017_invalid_input(tmp_path) -> None:
    with pytest.raises(BadParameter, match="mode must be translate, verify, or continue"):
        job(mode="unknown", repo="owner/repo", pr=1, repo_path=tmp_path)

    with pytest.raises(ValueError, match="no head sha"):
        source_pr_content_ref_from_pull(
            {"head": {"repo": {"owner": {"login": "owner"}, "name": "repo"}}},
            "owner",
            "repo",
            1,
        )

    with pytest.raises(RuntimeError, match="Yandex Cloud folder id"):
        load_config(env={}).secrets.require_yandex()

def test_F017_budget_values() -> None:
    zero = load_config(env={"YDBDOC_DAILY_BUDGET_RUB": "0"})
    assert zero.ops.daily_budget_rub == 0
    assert not check_daily_quota(spent_rub=0, budget_rub=zero.ops.daily_budget_rub).ok

    with pytest.raises(ValueError, match="non-negative"):
        load_config(env={"YDBDOC_DAILY_BUDGET_RUB": "-1"})
    with pytest.raises(ValueError):
        load_config(env={"YDBDOC_DAILY_BUDGET_RUB": "not-a-number"})
    with pytest.raises(ValueError, match="non-negative"):
        check_daily_quota(spent_rub=0, budget_rub=-1)


def test_F017_en_only() -> None:
    assert pick_verify_ru_text(en_text="Hello.\n") is None
