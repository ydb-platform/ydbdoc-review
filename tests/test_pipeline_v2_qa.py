"""Pipeline v2 QA: compare → optional fix-diff → optional re-validate → heuristics."""

from types import SimpleNamespace
from unittest.mock import patch

from ydbdoc_review.pipeline_v2 import (
    PairQaOutcome,
    VERDICT_ACCEPT,
    VERDICT_ACCEPT_WITH_NOTES,
    VERDICT_REJECT,
    _apply_translated_fence_comments,
    apply_fix_diff,
    final_verdict,
    format_pair_qa_markdown,
    parse_verdict,
    run_pair_qa,
)


def _settings():
    return SimpleNamespace(
        model_translate="yandexgpt-5.1",
        model_translation_verify="qwen3.6-35b-a3b",
        prompts_dir="prompts",
    )


REVIEW_ACCEPT = """\
### Вердикт
**ПРИНИМАТЬ**

### Блокеры
_Нет._

### Оговорки
_Нет._

### Кратко
Перевод корректен.
"""

REVIEW_REJECT = """\
### Вердикт
**НЕ ПРИНИМАТЬ**

### Блокеры

- Раздел: Установка
- Тип: Пропуск
- Что не так: отсутствует параграф про SDK.
- Где в SOURCE: «Установите SDK через pip».
- Где в TRANSLATION: отсутствует

### Оговорки
_Нет._

### Кратко
EN пропускает один параграф.
"""


def test_parse_verdict_accept():
    assert parse_verdict(REVIEW_ACCEPT) == VERDICT_ACCEPT


def test_parse_verdict_reject():
    assert parse_verdict(REVIEW_REJECT) == VERDICT_REJECT


def test_parse_verdict_with_notes():
    md = "### Вердикт\n**ПРИНИМАТЬ С ОГОВОРКАМИ**\n\n### Блокеры\n_Нет._\n"
    assert parse_verdict(md) == VERDICT_ACCEPT_WITH_NOTES


def test_apply_fix_diff_exact_match():
    text = "Install SDK via pip.\nThen run example.\n"
    result = apply_fix_diff(
        text,
        [{"find": "Then run example.", "replace": "Then run the example."}],
    )
    assert result.applied == 1
    assert result.new_text == "Install SDK via pip.\nThen run the example.\n"
    assert not result.skipped


def test_apply_fix_diff_skip_when_not_found():
    text = "Install SDK via pip.\n"
    result = apply_fix_diff(
        text, [{"find": "does not exist", "replace": "x", "reason": "blocker 1"}]
    )
    assert result.applied == 0
    assert result.new_text == text
    assert any("не найден" in s for s in result.skipped)


def test_apply_fix_diff_skip_when_ambiguous():
    text = "foo\nfoo\n"
    result = apply_fix_diff(text, [{"find": "foo", "replace": "bar"}])
    assert result.applied == 0
    assert any("уникальный" in s or "встречается" in s for s in result.skipped)


def test_run_pair_qa_accept_skips_fix(monkeypatch):
    monkeypatch.setattr(
        "ydbdoc_review.pipeline_v2.verify_translation_pair",
        lambda *a, **k: REVIEW_ACCEPT,
    )
    monkeypatch.setattr(
        "ydbdoc_review.pipeline_v2.run_heuristics", lambda *a, **k: []
    )
    fix_called = {"called": False}

    def fail_fix(*a, **k):
        fix_called["called"] = True
        return {"fixes": []}

    monkeypatch.setattr("ydbdoc_review.pipeline_v2.fix_translation_pair", fail_fix)

    out, outcome = run_pair_qa(
        _settings(),
        ru_path="ydb/docs/ru/x.md",
        en_path="ydb/docs/en/x.md",
        source_text="# RU\n",
        translated_text="# EN\n",
    )
    assert out == "# EN\n"
    assert not outcome.repair_attempted
    assert not fix_called["called"]
    assert final_verdict(outcome) == VERDICT_ACCEPT


def test_run_pair_qa_reject_applies_fix_and_revalidates(monkeypatch):
    monkeypatch.setattr(
        "ydbdoc_review.pipeline_v2.verify_translation_pair",
        lambda *a, **k: REVIEW_REJECT,
    )
    monkeypatch.setattr(
        "ydbdoc_review.pipeline_v2.run_heuristics", lambda *a, **k: []
    )
    monkeypatch.setattr(
        "ydbdoc_review.pipeline_v2.fix_translation_pair",
        lambda *a, **k: {
            "fixes": [
                {
                    "find": "Other content.",
                    "replace": "Install SDK via pip.\n\nOther content.",
                    "reason": "blocker 1",
                }
            ]
        },
    )
    confirm_called = {"called": False}

    def fake_confirm(*a, **k):
        confirm_called["called"] = True
        return "### Вердикт\n**ПРИНИМАТЬ**\n\n### Блокеры\n_Нет._\n"

    monkeypatch.setattr(
        "ydbdoc_review.pipeline_v2.revalidate_translation_pair", fake_confirm
    )

    out, outcome = run_pair_qa(
        _settings(),
        ru_path="ydb/docs/ru/x.md",
        en_path="ydb/docs/en/x.md",
        source_text="...",
        translated_text="Other content.\n",
    )
    assert outcome.repair_attempted
    assert outcome.repair_applied
    assert "Install SDK via pip." in out
    assert confirm_called["called"]
    assert final_verdict(outcome) == VERDICT_ACCEPT


def test_run_pair_qa_critic_error_does_not_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("FM down")

    monkeypatch.setattr("ydbdoc_review.pipeline_v2.verify_translation_pair", boom)
    monkeypatch.setattr(
        "ydbdoc_review.pipeline_v2.run_heuristics", lambda *a, **k: []
    )

    out, outcome = run_pair_qa(
        _settings(),
        ru_path="ydb/docs/ru/x.md",
        en_path="ydb/docs/en/x.md",
        source_text="# RU\n",
        translated_text="# EN\n",
    )
    assert out == "# EN\n"
    assert "FM down" in (outcome.repair_error or "")
    assert outcome.repair_skip_reason == "api_error"


def test_final_verdict_caps_accept_when_fixes_skipped():
    outcome = PairQaOutcome(
        ru_path="ru.md",
        en_path="en.md",
        target_path="en.md",
        review_md=REVIEW_REJECT,
        repair_attempted=True,
        repair_applied=True,
        repair_skip_reason=None,
        confirmation_md="### Вердикт\n**ПРИНИМАТЬ**\n\n### Блокеры\n_Нет._\n",
        repair_error=None,
        fix_skipped_notes=["`find` не найден в EN"],
    )
    assert final_verdict(outcome) == VERDICT_ACCEPT_WITH_NOTES


def test_apply_translated_fence_comments_preserves_inline_code():
    fence = (
        '```yql\n'
        'SELECT\n'
        '  Decimal("1.23", 5, 2), -- до 5 десятичных знаков\n'
        '```'
    )
    out = _apply_translated_fence_comments(
        fence,
        [{"line": 2, "marker": "--", "text": "up to 5 decimal places"}],
    )
    assert 'Decimal("1.23", 5, 2), -- up to 5 decimal places' in out
    assert out.count("Decimal") == 1


def test_format_pair_qa_hides_skipped_fixes_after_clean_revalidate():
    outcome = PairQaOutcome(
        ru_path="ru.md",
        en_path="en.md",
        target_path="en.md",
        review_md=REVIEW_REJECT,
        repair_attempted=True,
        repair_applied=True,
        repair_skip_reason=None,
        confirmation_md="### Вердикт\n**ПРИНИМАТЬ**\n\n### Блокеры\n_Нет._\n",
        repair_error=None,
        fix_skipped_notes=["`find` не найден в EN: «foo»"],
        findings=[],
    )
    md = format_pair_qa_markdown(outcome)
    assert "Пропущенные fixes" not in md
    assert "(есть пропущенные)" not in md


def test_format_pair_qa_shows_skipped_fixes_when_revalidate_not_accept():
    outcome = PairQaOutcome(
        ru_path="ru.md",
        en_path="en.md",
        target_path="en.md",
        review_md=REVIEW_REJECT,
        repair_attempted=True,
        repair_applied=True,
        repair_skip_reason=None,
        confirmation_md="### Вердикт\n**НЕ ПРИНИМАТЬ**\n\n### Блокеры\nx\n",
        repair_error=None,
        fix_skipped_notes=["`find` не найден в EN: «foo»"],
        findings=[],
    )
    md = format_pair_qa_markdown(outcome)
    assert "Пропущенные fixes" in md
