from ydbdoc_review.translation_qa import (
    PairQaOutcome,
    critic_api_failed_outcome,
    doc_translate_should_fail_ci,
    format_translation_pr_summary,
    pr_merge_verdict_unavailable,
    qa_critic_unavailable_all,
    synthetic_qa_outcomes_for_api_failure,
)


def _critic_fail_outcome(en: str = "en/a.md") -> PairQaOutcome:
    return synthetic_qa_outcomes_for_api_failure([("ru/a.md", en)], "Failed to get model")[0]


def test_critic_api_failure_does_not_block_ci():
    outcomes = [_critic_fail_outcome()]
    assert critic_api_failed_outcome(outcomes[0])
    assert qa_critic_unavailable_all(outcomes)
    assert not doc_translate_should_fail_ci(outcomes)
    assert pr_merge_verdict_unavailable(outcomes) == []


def test_translator_error_still_blocks_when_critic_ok():
    outcomes = [
        PairQaOutcome(
            "ru/a.md",
            "en/a.md",
            "en/a.md",
            "### Найдено критиком\n\n_Существенных проблем не выявлено._\n",
            False,
            False,
            None,
            "_Ошибка вердикта переводчика:_ `token limit`",
            "token limit",
        )
    ]
    assert doc_translate_should_fail_ci(outcomes)
    assert "en/a.md" in pr_merge_verdict_unavailable(outcomes)


def test_summary_when_critic_unavailable():
    outcomes = [_critic_fail_outcome("en/x.md")]
    s = format_translation_pr_summary(source_pr_number=1, outcomes=outcomes)
    assert "критик не отработал" in s.lower()
    assert "можно мержить" in s.lower()
