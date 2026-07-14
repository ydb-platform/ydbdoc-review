"""Tests for markdown report builder."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    ManualAction,
    NavigationRunResult,
    PRTranslationResult,
    PairRunResult,
)
from ydbdoc_review.reporting.locations import ReportLinkContext
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_commit_message,
    build_full_report,
    build_source_pr_comment,
)
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def _cfg():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})


def _sample_result(*, new_file: bool = False) -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    summary = "EN missing — generate from RU" if new_file else "RU changed, EN unchanged"
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary=summary,
    )
    unresolved = CriticIssueOut(
        segment_id="s0042",
        severity="warning",
        category="terminology",
        comment='использовано "string table" вместо "row table"',
        suggested_text="row table",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello",
        segments_count=12,
        verdict="warnings",
        prompt_version="v1",
        critic_applied=[
            CriticIssueOut(
                segment_id="s0001",
                severity="warning",
                category="formatting",
                comment="fixed spacing",
                suggested_text="fixed",
            )
        ],
        critic_unresolved=CriticResponse(verdict="warnings", issues=[unresolved]),
        heuristic_blocking=["Кириллица в EN-тексте (строка ~5): «командой YQL»"],
        manual_actions=[
            ManualAction(
                segment_id="s0124",
                location="table:row1:col2",
                message=(
                    "Таблица не переведена автоматически (table:row1:col2, `s0124`); "
                    "оставлена на русском. Переведите вручную."
                ),
            )
        ],
        segment_locations={"s0042": "Overview", "s0124": "table:row1:col2"},
        segment_lines={"s0124": (355, 358)},
        segment_excerpts={
            "s0042": "Overview paragraph text",
            "s0124": "Cell text to search",
        },
        segment_source_excerpts={
            "s0042": "Обзорный абзац",
            "s0124": "Текст ячейки",
        },
    )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(plan=plan, target_text="Hello", file_result=fr),
        ]
    )


def test_build_source_pr_comment_new_and_updated():
    cfg = _cfg()
    body = build_source_pr_comment(
        _sample_result(new_file=True),
        translation_pr_number=99,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=134),
        config=cfg,
    )
    assert "1 (1 новых)" in body
    assert "Translation PR | #99" in body
    assert "doc_verify" in body
    assert "Статус QA" not in body


def test_build_source_pr_comment_bilingual_skip():
    from ydbdoc_review.pipeline.analyze import BILINGUAL_SKIP_SUMMARY, PairPlan
    from ydbdoc_review.pipeline.pairs import DocPair

    cfg = _cfg()
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
        en_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="skip",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary=BILINGUAL_SKIP_SUMMARY,
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, skipped=True)],
    )
    body = build_source_pr_comment(
        result,
        translation_pr_number=None,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=5),
        config=cfg,
    )
    assert "перевод не требуется" in body
    assert "§6.76" in body
    assert "Translation PR не создаётся" in body


def test_build_source_pr_comment_completeness_gaps_no_translation_pr():
    cfg = _cfg()
    result = _sample_result(new_file=True)
    result.completeness_gaps = [
        "ydb/docs/en/core/reference/ydb-cli/export-import/_includes/export-additional-params.md",
    ]
    body = build_source_pr_comment(
        result,
        translation_pr_number=None,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=384),
        config=cfg,
    )
    assert "translation PR **не создан**" in body
    assert "completeness gate" in body
    assert "export-additional-params.md" in body
    assert "перевод готов" not in body
    assert "Translation PR | — |" in body


def test_build_full_report_reviewer_focused():
    cfg = _cfg()
    body = build_full_report(
        _sample_result(),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=90),
        config=cfg,
        link=ReportLinkContext(github_repo="ydb-platform/ydb", ref="ydbdoc-review/pr-1"),
    )
    assert "Рекомендация:" in body
    assert "Что исправить" in body
    assert "Overview" in body and "`s0042`" in body
    assert "string table" in body
    assert "Оригинал (RU):" in body
    assert "Перевели:" in body
    assert "Проблема:" in body
    assert "**Совет:** row table" in body
    assert "Обзорный абзац" in body
    assert "таблица, строка 1, столбец 2" in body
    assert "Таблица не переведена автоматически" in body
    assert "355" in body
    assert "Переведите вручную" in body
    assert "fixed spacing" not in body
    assert "Сегментов переведено" not in body
    assert "Critic fixes auto-applied" not in body
    assert "Glossary used" not in body
    assert "Generated by" in body


def test_build_full_report_shows_rub_cost():
    from ydbdoc_review.llm.usage import LLMUsage, UsageTracker

    cfg = _cfg()
    tracker = UsageTracker()
    tracker.add(
        LLMUsage("deepseek-v32", 5_000, 2_000, 100.0, 0, True, role="critic")
    )
    body = build_full_report(
        _sample_result(),
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=cfg,
        usage=tracker,
    )
    assert "Стоимость и токены" in body
    assert "Оценка стоимости" in body
    assert "~₽" in body
    assert "Токены (всего)" in body
    assert "Токены (критик)" in body


def test_build_full_report_includes_cost_section():
    from ydbdoc_review.llm.usage import LLMUsage, UsageTracker

    cfg = _cfg()
    tracker = UsageTracker()
    tracker.add(
        LLMUsage(
            "deepseek-v32", 10_000, 5_000, 100.0, 0, True, role="translate"
        )
    )
    body = build_full_report(
        _sample_result(),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=cfg,
        usage=tracker,
    )
    assert "Стоимость и токены" in body
    assert "Оценка стоимости" in body
    assert "перевод=`" in body


def test_build_full_report_shows_na_for_unpriced_model():
    from ydbdoc_review.llm.usage import LLMUsage, UsageTracker

    cfg = _cfg()
    tracker = UsageTracker()
    tracker.add(
        LLMUsage(
            "eliza-unknown-model",
            10_000,
            5_000,
            100.0,
            0,
            True,
            role="translate",
        )
    )
    body = build_full_report(
        _sample_result(),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=cfg,
        usage=tracker,
    )
    assert "Оценка стоимости: n/a (модель не в прайсе: `eliza-unknown-model`)" in body


def test_build_full_report_shows_eliza_model_cost():
    from ydbdoc_review.llm.usage import LLMUsage, UsageTracker

    cfg = _cfg()
    tracker = UsageTracker()
    tracker.add(
        LLMUsage(
            "deepseek-v4-flash",
            10_000,
            5_000,
            100.0,
            0,
            True,
            role="translate",
        )
    )
    tracker.add(
        LLMUsage(
            "gpt-oss-120b",
            2_000,
            1_000,
            100.0,
            0,
            True,
            role="critic",
        )
    )
    body = build_full_report(
        _sample_result(),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=cfg,
        usage=tracker,
    )
    assert "Оценка стоимости: ~₽" in body
    assert "Оценка стоимости: n/a" not in body
    assert "перевод=`deepseek-v4-flash`" in body
    assert "критик=`gpt-oss-120b`" in body


def test_build_full_report_uses_ci_version_label(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTION_REF", "v0.1.0")
    monkeypatch.setenv("YDBDOC_GIT_SHA", "a1e8e92f3b82678b2c625232b1b4d1e74b5f4136")
    body = build_full_report(
        _sample_result(),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=_cfg(),
    )
    assert "Generated by ydbdoc-review v0.1.0 @ a1e8e92" in body


def test_build_full_report_error_and_deleted_rows():
    cfg = _cfg()
    pair = DocPair(
        ru_path="ydb/docs/ru/x.md",
        en_path="ydb/docs/en/x.md",
        ru_deleted=True,
        ru_changed=True,
    )
    del_plan = PairPlan(
        pair=pair,
        action="delete_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    err_pair = DocPair(
        ru_path="ydb/docs/ru/e.md",
        en_path="ydb/docs/en/e.md",
        ru_changed=True,
    )
    err_plan = PairPlan(
        pair=err_pair,
        action="translate_to_en",
        source_path=err_pair.ru_path,
        target_path=err_pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(plan=del_plan, deleted=True),
            PairRunResult(plan=err_plan, error="API down"),
        ]
    )
    body = build_full_report(
        result,
        meta=ReportMeta(mode="doc_verify", report_number=2, elapsed_s=5),
        config=cfg,
    )
    assert "Ошибки pipeline" in body
    assert "API down" in body


def test_full_report_shows_alignment_error():
    cfg = _cfg()
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="EN",
        segments_count=2,
        verdict="blocked",
        segment_alignment_error="segment count mismatch: source 2 vs target 1",
        prompt_version="v1",
    )
    body = build_full_report(
        PRTranslationResult(pair_results=[PairRunResult(plan=plan, file_result=fr)]),
        meta=ReportMeta(
            mode="doc_verify",
            report_number=1,
            elapsed_s=1,
            checkout_ref="abc123def456",
        ),
        config=cfg,
    )
    assert "Checkout: `abc123def456`" in body
    assert "(alignment)" in body
    assert "не мержить" in body or "требует правок" in body


def test_full_report_includes_info_section():
    cfg = _cfg()
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="EN",
        segments_count=1,
        verdict="ok",
        heuristic_info=["ru_source (исправьте в RU PR): typo"],
        prompt_version="v1",
    )
    body = build_full_report(
        PRTranslationResult(pair_results=[PairRunResult(plan=plan, file_result=fr)]),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=cfg,
    )
    assert "Справка (не блокирует merge EN)" in body
    assert "ru_source" in body


def test_merge_recommendation_red_when_navigation_blocked():
    cfg = _cfg()
    nav = NavigationRunResult(
        ru_path="ydb/docs/ru/a/toc_i.yaml",
        en_path="ydb/docs/en/a/toc_i.yaml",
        kind="toc",
        target_text="items:\n",
        warnings=["scope_not_applied: href 'compact.md' was in translate scope but missing from EN toc"],
        verdict="blocked",
    )
    body = build_full_report(
        PRTranslationResult(navigation_results=[nav]),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=cfg,
    )
    assert "не мержить" in body
    assert "🔴" in body


def test_merge_recommendation_green_when_critic_warnings_but_no_open_issues():
    """Regression: verdict warnings + empty issue list must not yield yellow header."""
    cfg = _cfg()
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello",
        segments_count=1,
        verdict="warnings",
        critic_initial=CriticResponse(verdict="warnings", issues=[]),
        prompt_version="v1",
    )
    body = build_full_report(
        PRTranslationResult(pair_results=[PairRunResult(plan=plan, file_result=fr)]),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=cfg,
    )
    assert "можно мержить" in body
    assert "требует правок" not in body


def test_build_full_report_all_ok():
    cfg = _cfg()
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    from ydbdoc_review.llm.usage import LLMUsage, UsageTracker

    tracker = UsageTracker()
    tracker.add(
        LLMUsage("deepseek-v32", 7_000, 4_000, 100.0, 0, True, role="translate")
    )
    body = build_full_report(
        PRTranslationResult(pair_results=[PairRunResult(plan=plan, file_result=fr)]),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=cfg,
        usage=tracker,
    )
    assert "можно мержить" in body
    assert "открытых замечаний нет" in body
    assert "Стоимость и токены" in body
    assert "Оценка стоимости" in body


def test_build_commit_message():
    cfg = _cfg()
    msg = build_commit_message(12, _sample_result(), config=cfg)
    assert "PR #12" in msg
    verify_msg = build_commit_message(12, _sample_result(), config=cfg, verify=True)
    assert "doc_verify" in verify_msg


def test_full_report_skipped_critic_in_collapsed_section():
    """Skipped apply-safe fixes must not inflate the main issue list."""
    cfg = _cfg()
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    skipped = CriticIssueOut(
        segment_id="s0013",
        severity="blocked",
        category="placeholder corruption",
        comment="order change rejected by pipeline",
        suggested_text="would break EN",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
        critic_skipped=[skipped],
        critic_unresolved=CriticResponse(verdict="ok", issues=[]),
    )
    body = build_full_report(
        PRTranslationResult(pair_results=[PairRunResult(plan=plan, file_result=fr)]),
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=cfg,
    )
    assert "можно мержить" in body or "открытых замечаний нет" in body
    assert "Автоисправление не применено" in body
    assert "order change rejected" in body
    assert body.index("Автоисправление") > body.find("Без замечаний") or "можно мержить" in body


def test_full_report_dedupes_skipped_from_main_critic_list():
    """§6.57: verify echo of skipped issues must not appear in main list."""
    cfg = _cfg()
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    skipped = CriticIssueOut(
        segment_id="s0013",
        severity="blocked",
        category="placeholder corruption",
        comment="order change rejected by pipeline",
        suggested_text="would break EN",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello",
        segments_count=1,
        verdict="blocked",
        prompt_version="v1",
        critic_skipped=[skipped],
        critic_unresolved=CriticResponse(verdict="blocked", issues=[skipped]),
        segment_locations={"s0013": "Overview"},
    )
    body = build_full_report(
        PRTranslationResult(pair_results=[PairRunResult(plan=plan, file_result=fr)]),
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=cfg,
    )
    assert body.count("order change rejected by pipeline") == 1
    assert "Автоисправление не применено" in body


def test_excerpt_found_in_file_rejects_broken_preview():
    from ydbdoc_review.reporting.locations import excerpt_found_in_file

    final = "when a query (e.g., `SELECT a FROM t`) is executed"
    assert not excerpt_found_in_file("when a query (e.g., ) is executed", final)
    assert excerpt_found_in_file("when a query (e.g., `SELECT a FROM t`)", final)
