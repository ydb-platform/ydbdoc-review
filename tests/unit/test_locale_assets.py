"""§6.157: RU→EN locale binary asset copy."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.validation.locale_assets import (
    apply_locale_asset_copies,
    en_asset_rel_path,
    plan_locale_asset_copies,
    resolve_relative_asset,
)


def test_resolve_and_plan_asset_copy_with_rub_strip(tmp_path: Path):
    ru_md = "ydb/docs/ru/core/contributor/manage-releases.md"
    text = "![Схема](_assets/major_release_branches-rub.svg)\n"
    assert (
        resolve_relative_asset(ru_md, "_assets/major_release_branches-rub.svg")
        == "ydb/docs/ru/core/contributor/_assets/major_release_branches-rub.svg"
    )
    planned = plan_locale_asset_copies(ru_md, text)
    assert planned == [
        (
            "ydb/docs/ru/core/contributor/_assets/major_release_branches-rub.svg",
            "ydb/docs/en/core/contributor/_assets/major_release_branches.svg",
        )
    ]
    assert (
        en_asset_rel_path(
            "ydb/docs/ru/core/contributor/_assets/major_release_branches-rub.svg"
        )
        == "ydb/docs/en/core/contributor/_assets/major_release_branches.svg"
    )


def test_apply_locale_asset_copies_writes_en_file(tmp_path: Path):
    repo = tmp_path
    ru_asset = (
        repo
        / "ydb/docs/ru/core/contributor/_assets/major_release_branches.svg"
    )
    ru_asset.parent.mkdir(parents=True)
    ru_asset.write_bytes(b"<svg>ok</svg>")
    en_md = "ydb/docs/en/core/contributor/manage-releases.md"
    (repo / en_md).parent.mkdir(parents=True)
    (repo / en_md).write_text(
        "![General scheme](_assets/major_release_branches.svg)\n",
        encoding="utf-8",
    )

    pair = DocPair(
        ru_path="ydb/docs/ru/core/contributor/manage-releases.md",
        en_path=en_md,
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=en_md,
        source_lang="ru",
        target_lang="en",
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="![General scheme](_assets/major_release_branches.svg)\n",
                source_text=(
                    "![Общая схема](_assets/major_release_branches.svg)\n"
                ),
            )
        ]
    )
    written = apply_locale_asset_copies(result, repo_path=str(repo))
    dest = repo / "ydb/docs/en/core/contributor/_assets/major_release_branches.svg"
    assert written == [
        "ydb/docs/en/core/contributor/_assets/major_release_branches.svg"
    ]
    assert dest.is_file()
    assert dest.read_bytes() == b"<svg>ok</svg>"

    # Idempotent when identical
    assert apply_locale_asset_copies(result, repo_path=str(repo)) == []
