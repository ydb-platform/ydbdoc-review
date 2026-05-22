from ydbdoc_review.translate_postprocess import (
    apply_semantic_fixes_from_ru,
    fix_llm_prompt_leaks_in_cli,
)
from ydbdoc_review.translation_qa import (
    PairQaOutcome,
    pr_merge_blocked,
    translation_strict_merge_enabled,
)


def test_fix_llm_prompt_leak_in_ydb_sql():
    en = "ydb -p <profile_name> sql -s 'select 1' -- Please provide the text to translate."
    fixed = fix_llm_prompt_leaks_in_cli(en)
    assert "Please provide" not in fixed
    assert "--stats full --format json-unicode" in fixed


def test_fix_ydb_sql_flags_from_ru():
    ru = "ydb -p <profile_name> sql -s 'select 1' --stats full --format json-unicode"
    en = "ydb -p <profile_name> sql -s 'select 1' -- Please provide the text to translate."
    out = apply_semantic_fixes_from_ru(ru, en)
    assert "--stats full --format json-unicode" in out
    assert "Please provide" not in out


def test_pr_merge_blocked_soft_by_default(monkeypatch):
    monkeypatch.delenv("YDBDOC_TRANSLATION_STRICT_MERGE", raising=False)
    assert not translation_strict_merge_enabled()
    outcome = PairQaOutcome(
        ru_path="ydb/docs/ru/x.md",
        en_path="ydb/docs/en/x.md",
        target_path="ydb/docs/en/x.md",
        review_md="",
        repair_attempted=False,
        repair_applied=False,
        repair_skip_reason=None,
        confirmation_md="### Вердикт файла\n**НЕ ПРИНИМАТЬ**\n",
        repair_error=None,
    )
    assert not pr_merge_blocked([outcome])


def test_pr_merge_blocked_strict(monkeypatch):
    monkeypatch.setenv("YDBDOC_TRANSLATION_STRICT_MERGE", "1")
    outcome = PairQaOutcome(
        ru_path="ydb/docs/ru/x.md",
        en_path="ydb/docs/en/x.md",
        target_path="ydb/docs/en/x.md",
        review_md="",
        repair_attempted=False,
        repair_applied=False,
        repair_skip_reason=None,
        confirmation_md="### Вердикт файла\n**НЕ ПРИНИМАТЬ**\n",
        repair_error=None,
    )
    assert pr_merge_blocked([outcome])
