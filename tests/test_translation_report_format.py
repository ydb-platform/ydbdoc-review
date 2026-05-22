"""Report format: per-file accept + remaining problems + pipeline."""

from ydbdoc_review.translation_qa import (
    PairQaOutcome,
    _parse_translator_file_accept,
    _parse_translator_remaining_problems,
    file_merge_verdict,
    format_pair_qa_markdown,
    format_translation_pr_summary,
)

CONFIRM_REJECT = """\
### Вердикт файла
**НЕ ПРИНИМАТЬ**

### Оставшиеся проблемы
1. Нет вкладок C++ и Rust в EN.
2. Порядок ссылок в оглавлении неверен.

### Ход проверки
- **Критик:** отсутствуют вкладки SDK.
- **Исправитель:** правки не применены: quality check.
- **Переводчик:** после проверки AFTER расхождения с RU остались.
"""

CONFIRM_ACCEPT = """\
### Вердикт файла
**ПРИНИМАТЬ**

### Оставшиеся проблемы
_Нет._

### Ход проверки
- **Критик:** существенных проблем не выявлено.
- **Исправитель:** правки не требовались.
- **Переводчик:** EN соответствует RU.
"""


def test_parse_translator_accept():
    assert _parse_translator_file_accept(CONFIRM_ACCEPT) is True
    assert _parse_translator_file_accept(CONFIRM_REJECT) is False


def test_format_pair_reject_shows_remaining():
    o = PairQaOutcome(
        ru_path="ydb/docs/ru/x.md",
        en_path="ydb/docs/en/x.md",
        target_path="ydb/docs/en/x.md",
        review_md="### Найдено критиком\n1. Нет вкладок.\n",
        repair_attempted=True,
        repair_applied=False,
        repair_skip_reason="quality check",
        confirmation_md=CONFIRM_REJECT,
        repair_error=None,
    )
    md = format_pair_qa_markdown(o)
    assert "**Принимать файл:** нет" in md
    assert "C++" in md
    assert "**Исправитель:**" in md
    assert file_merge_verdict(o.review_md, o.confirmation_md) == "reject"


def test_format_pr_summary():
    outcomes = [
        PairQaOutcome(
            "ru/a.md",
            "en/a.md",
            "en/a.md",
            "",
            False,
            False,
            None,
            CONFIRM_ACCEPT,
            None,
        ),
        PairQaOutcome(
            "ru/b.md",
            "en/b.md",
            "en/b.md",
            "",
            True,
            False,
            "skip",
            CONFIRM_REJECT,
            None,
        ),
    ]
    s = format_translation_pr_summary(source_pr_number=39667, outcomes=outcomes)
    assert "нельзя мержить" in s
    assert "**Не принимать:**" in s
    assert _parse_translator_remaining_problems(CONFIRM_REJECT).startswith("1.")
