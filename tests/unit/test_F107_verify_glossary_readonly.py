"""F-107: standalone glossary verify keeps the historical read-only profile."""
# ruff: noqa: RUF001

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github import workflow
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
from ydbdoc_review.translation.glossary import Glossary, GlossaryEntry

_RU_PATH = "ydb/docs/ru/core/concepts/glossary.md"
_EN_PATH = "ydb/docs/en/core/concepts/glossary.md"


def _config():
    return load_config(
        env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "secret"}
    )


def _glossary() -> Glossary:
    return Glossary(entries=[GlossaryEntry(ru="таблетка", en="tablet")])


def _run_glossary_verify(ru: str, en: str):
    pair = DocPair(
        ru_path=_RU_PATH,
        en_path=_EN_PATH,
        ru_changed=True,
        en_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=_RU_PATH,
        target_path=_EN_PATH,
        source_lang="ru",
        target_lang="en",
    )
    client = MagicMock()
    client.usage_tracker = UsageTracker()
    ctx = HarnessContext.from_options(
        client,
        glossary=_glossary(),
        config=_config(),
    )
    run = run_pair_plan(
        PairContent(pair=pair, ru_text=ru, en_text=en),
        plan,
        ctx,
        {},
    )
    return run, client


def test_F107_readonly(tmp_path):
    ru = "# Глоссарий\n\n## Таблетка {#tablet}\n\nОписание.\n"
    en = "# Glossary\n\nLegacy glossary layout.\n"

    forbidden = [
        "ydbdoc_review.harness.steps._apply_en_structural_repair",
        "ydbdoc_review.harness.steps._try_partial_verify_realign",
        "ydbdoc_review.harness.steps.run_critic_loop",
        "ydbdoc_review.harness.steps.finalize_en_target",
        "ydbdoc_review.harness.steps.translate_segments",
        "ydbdoc_review.harness.pair.repair_en_structure_from_ru",
    ]
    guards = [patch(name, side_effect=AssertionError(name)) for name in forbidden]
    for guard in guards:
        guard.start()
    try:
        run, client = _run_glossary_verify(ru, en)
    finally:
        for guard in reversed(guards):
            guard.stop()

    assert run.target_text == en
    assert run.file_result is not None
    assert run.file_result.final_text == en
    assert run.file_result.critic_initial is None
    assert run.file_result.critic_unresolved is None
    assert any(
        message.startswith("glossary_verify_critic_skipped:")
        and "coverage is informational" in message
        and "doc_translate" in message
        and "doc_continue" in message
        for message in run.file_result.heuristic_info
    )
    client.chat.completions.create.assert_not_called()

    target = tmp_path / _EN_PATH
    target.parent.mkdir(parents=True)
    target.write_text(en, encoding="utf-8")
    touched = workflow._apply_results_to_disk(
        str(tmp_path), PRTranslationResult(pair_results=[run]), dry_run=False
    )
    assert touched.written == []
    assert touched.deleted == []
    assert target.read_text(encoding="utf-8") == en

    semantic_check = getattr(workflow, "_verify_coverage_semantically", None)
    assert callable(semantic_check)
    coverage_critic = MagicMock()
    assert semantic_check(
        coverage_critic,
        MagicMock(),
        _glossary(),
        _config(),
        _EN_PATH,
        None,
        [],
        {},
    )
    coverage_critic.assert_not_called()


def test_F107_real_defects():
    ru = (
        "# Глоссарий\n\n"
        "## Таблетка {#tablet}\n\n"
        "Описание со [ссылкой](expected.md).\n"
    )
    en = "# Glossary\n\nтаблетка with a [wrong link](wrong.md).\n"

    run, client = _run_glossary_verify(ru, en)

    assert run.target_text == en
    assert run.file_result is not None
    assert run.file_result.verdict == "blocked"
    findings = run.file_result.heuristic_blocking
    assert any(message.startswith("glossary_violation:") for message in findings)
    assert any(
        message.startswith(("md_link_parity:", "href_parity:"))
        for message in findings
    )
    assert any(
        message.startswith(("heading_parity:", "anchor_parity:"))
        for message in findings
    )
    client.chat.completions.create.assert_not_called()

    report = build_full_report(
        PRTranslationResult(pair_results=[run]),
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=0.1),
        config=_config(),
    )
    assert "Статус QA (K): 🔴 RED" in report
    assert "glossary_violation" in report
    assert "doc_translate" in report
    assert "doc_continue" in report
