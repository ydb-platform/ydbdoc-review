"""Byte-identical one-pass output must not stage disk overlays."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.github.workflow import _apply_results_to_disk
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult


def test_apply_results_skips_identical_one_pass_output_without_action_special_case(
    tmp_path: Path,
):
    rel = "ydb/docs/en/core/security/authentication.md"
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text("tip-correct href\n", encoding="utf-8")

    pair = DocPair(
        ru_path="ydb/docs/ru/core/security/authentication.md",
        en_path=rel,
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_ru_to_en_once",
        source_path=pair.ru_path,
        target_path=rel,
        source_lang="ru",
        target_lang="en",
    )
    pr = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text="tip-correct href\n")]
    )
    touched = _apply_results_to_disk(str(tmp_path), pr, dry_run=False)
    assert touched.written == []
    assert path.read_text(encoding="utf-8") == "tip-correct href\n"

    pr2 = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text="real fix\n")]
    )
    touched2 = _apply_results_to_disk(str(tmp_path), pr2, dry_run=False)
    assert touched2.written == [rel]
    assert path.read_text(encoding="utf-8") == "real fix\n"
