"""Refusal leaves real prose review incomplete, including mixed critic batches."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from tests.unit.test_critic import _mock_client
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness import TRANSLATE_WITH_QA_PROFILE, VERIFY_PROFILE, FileHarness
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.critic_verdict import compute_critic_verdict
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import refresh_publication_impact
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult, PublicationImpact
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.schemas import CriticResponse

REFUSAL = "Я не могу обсуждать эту тему."
PROSE = "Use the documented authentication method.\n"
RU = "ydb/docs/ru/core/auth.md"
EN = "ydb/docs/en/core/auth.md"


def _context(responses, **options):
    return HarnessContext.from_options(
        _mock_client(responses),
        glossary=load_glossary(),
        allow_verify_realign=False,
        docs_text_reader=lambda _: "# Auth\n\n## TLS {#tls}\n\n## StartTls {#starttls}\n",
        **options,
    )


def _run(text=PROSE, responses=None, *, source=None, **options):
    state = FileRunState(
        mode="verify", file_path=RU, raw_source_text=source or text,
        source_text=source or text, existing_target_text=text,
    )
    ctx = _context([REFUSAL] if responses is None else responses, **options)
    return FileHarness(VERIFY_PROFILE).run(state, ctx), ctx


def _plan():
    return PairPlan(
        pair=DocPair(ru_path=RU, en_path=EN), action="critic_only",
        source_path=RU, target_path=EN, source_lang="ru", target_lang="en",
    )


@pytest.mark.parametrize("mixed_batches", [False, True], ids=["five-calls", "seven-calls"])
@pytest.mark.parametrize("later_review", ["refusal", "failure", "empty-verdict"])
def test_unchanged_feedback_retry_incomplete_review_retains_blocker_through_translate_qa(
    mixed_batches, later_review,
):
    source = "Используйте описанный способ аутентификации.\n"
    translation = json.dumps({"segments": [{
        "id": "s0001", "text": PROSE.strip(),
    }]})
    blocker = {
        "segment_id": "s0001", "severity": "blocked", "category": "meaning",
        "comment": "Missing authentication constraint", "suggested_text": None,
    }
    blocked = json.dumps({"verdict": "blocked", "issues": [blocker]})
    review_responses = {
        "refusal": [REFUSAL],
        "failure": ["", "", ""],
        "empty-verdict": ['{"verdict":"warnings","issues":[]}'],
    }[later_review]
    if mixed_batches:
        source += "\nНе отключайте аутентификацию.\n"  # noqa: RUF001
        second_translation = json.dumps({"segments": [{
            "id": "s0002", "text": "Keep authentication enabled.",
        }]})
        responses = [
            translation, second_translation, blocked, REFUSAL,
            translation, *review_responses, '{"verdict":"ok","issues":[]}',
        ]
    else:
        responses = [translation, blocked, blocked, translation, *review_responses]
    state = FileRunState(
        mode="translate", file_path=RU, raw_source_text=source, source_text=source,
    )
    ctx = _context(responses, max_chars=1, max_parallel_batches=1)

    result = FileHarness(TRANSLATE_WITH_QA_PROFILE).run(state, ctx)

    assert ctx.client._client.chat.completions.create.call_count == len(responses)
    assert result.final_text == PROSE + (
        "\nKeep authentication enabled.\n" if mixed_batches else ""
    )
    assert not result.heuristic_blocking
    assert result.verdict == result.critic_unresolved.verdict == "blocked"
    assert [
        issue.model_dump() for issue in result.critic_unresolved.issues
        if issue.category == "meaning"
    ] == [blocker]
    pr = PRTranslationResult(pair_results=[PairRunResult(
        plan=PairPlan(
            pair=DocPair(ru_path=RU, en_path=EN), action="translate_to_en",
            source_path=RU, target_path=EN, source_lang="ru", target_lang="en",
        ),
        file_result=result, target_text=result.final_text,
    )])
    assert refresh_publication_impact(pr) == PublicationImpact.WITHHOLD_UNSAFE
    original_result = deepcopy(result)
    for include_skipped in (True, False):
        config = load_config(env={})
        config.reporting.include_skipped_critic = include_skipped
        body = build_full_report(
            pr, meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
            config=config,
        )
        assert "Статус QA (K): 🔴 RED" in body
        assert body.count(blocker["comment"]) == 1
        assert body.count("Модель отказала проверять файл") == int(
            mixed_batches or later_review == "refusal"
        )
    assert result == original_result


def test_unchanged_feedback_retry_empty_batch_with_warning_sibling_retains_blocker():
    source = (
        "Используйте описанный способ аутентификации.\n\n"
        "Не отключайте аутентификацию.\n"  # noqa: RUF001
    )
    first_translation = json.dumps({"segments": [{"id": "s0001", "text": PROSE.strip()}]})
    second_translation = json.dumps({"segments": [{
        "id": "s0002", "text": "Keep authentication enabled.",
    }]})
    blocker = {
        "segment_id": "s0001", "severity": "blocked", "category": "meaning",
        "comment": "Missing authentication constraint", "suggested_text": None,
    }
    blocked = json.dumps({"verdict": "blocked", "issues": [blocker]})
    empty_batch = '{"verdict":"warnings","issues":[]}'
    sibling_warning = json.dumps({"verdict": "warnings", "issues": [{
        "segment_id": "s0002", "severity": "warning", "category": "terminology",
        "comment": "Check the authentication term", "suggested_text": None,
    }]})
    responses = [
        first_translation, second_translation, blocked, REFUSAL,
        first_translation, empty_batch, sibling_warning,
        # The broken implementation attempts verify and another feedback round.
        # Supply valid responses so the regression exposes lost meaning, not a
        # fake-client exhaustion failure.
        empty_batch, sibling_warning, second_translation,
        empty_batch, sibling_warning, empty_batch, sibling_warning,
    ]
    state = FileRunState(
        mode="translate", file_path=RU, raw_source_text=source, source_text=source,
    )
    ctx = _context(responses, max_chars=1, max_parallel_batches=1)

    result = FileHarness(TRANSLATE_WITH_QA_PROFILE).run(state, ctx)

    assert result.final_text == PROSE + "\nKeep authentication enabled.\n"
    assert not result.heuristic_blocking
    assert result.verdict == result.critic_unresolved.verdict == "blocked"
    assert [
        issue.model_dump() for issue in result.critic_unresolved.issues
        if issue.category == "meaning"
    ] == [blocker]
    assert ctx.client._client.chat.completions.create.call_count == 7
    pr = PRTranslationResult(pair_results=[PairRunResult(
        plan=PairPlan(
            pair=DocPair(ru_path=RU, en_path=EN), action="translate_to_en",
            source_path=RU, target_path=EN, source_lang="ru", target_lang="en",
        ),
        file_result=result, target_text=result.final_text,
    )])
    assert refresh_publication_impact(pr) == PublicationImpact.WITHHOLD_UNSAFE
    original_result = deepcopy(result)
    for include_skipped in (True, False):
        config = load_config(env={})
        config.reporting.include_skipped_critic = include_skipped
        body = build_full_report(
            pr, meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
            config=config,
        )
        assert "Статус QA (K): 🔴 RED" in body
        assert body.count(blocker["comment"]) == 1
        assert body.count("Check the authentication term") == 1
    assert result == original_result


def test_feedback_retry_repaired_atom_is_not_restored_after_critic_refusal():
    from ydbdoc_review.translation.critic_atoms import (
        protected_atom_language_issues,
        target_atom_maps,
    )

    source = "Используйте `Имя=Значение`.\n"
    translation = '{"segments":[{"id":"s0001","text":"Use ⟦C1⟧."}]}'
    unchanged_atom = '{"spans":[{"id":"p1","text":"Имя=Значение"}]}'
    repaired_atom = '{"spans":[{"id":"p1","text":"Name=Value"}]}'
    state = FileRunState(
        mode="translate", file_path=RU, raw_source_text=source, source_text=source,
    )
    ctx = _context([
        translation, unchanged_atom, REFUSAL,
        translation, repaired_atom, REFUSAL,
    ])

    result = FileHarness(TRANSLATE_WITH_QA_PROFILE).run(state, ctx)

    assert result.final_text == "Use `Name=Value`.\n"
    assert state.translations == {"s0001": "Use ⟦C1⟧."}
    assert protected_atom_language_issues(
        state.segments,
        target_atoms=target_atom_maps(state.segments, result.final_text),
    ) == []
    assert not result.heuristic_blocking
    assert result.verdict == result.critic_unresolved.verdict == "warnings"
    assert [i.category for i in result.critic_unresolved.issues] == ["critic_model_refusal"]
    assert ctx.client._client.chat.completions.create.call_count == 6
    pr = PRTranslationResult(pair_results=[PairRunResult(
        plan=PairPlan(
            pair=DocPair(ru_path=RU, en_path=EN), action="translate_to_en",
            source_path=RU, target_path=EN, source_lang="ru", target_lang="en",
        ),
        file_result=result, target_text=result.final_text,
    )])
    assert refresh_publication_impact(pr) == PublicationImpact.PUBLISH_NORMAL
    body = build_full_report(
        pr, meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=load_config(env={}),
    )
    assert "Статус QA (K): 🟡 YELLOW" in body
    assert "Untranslated human-language content in protected code atom" not in body
    assert body.count("Модель отказала проверять файл") == 1


@pytest.mark.parametrize("mixed_batches", [False, True])
def test_changed_feedback_retry_clean_review_clears_prior_blocker_through_translate_qa(
    mixed_batches,
):
    source = "Используйте описанный способ аутентификации.\n"
    translation = json.dumps({"segments": [{"id": "s0001", "text": PROSE.strip()}]})
    repaired_text = "Use the documented authentication method securely."
    repaired = json.dumps({"segments": [{"id": "s0001", "text": repaired_text}]})
    blocked = json.dumps({"verdict": "blocked", "issues": [{
        "segment_id": "s0001", "severity": "blocked", "category": "meaning",
        "comment": "Missing authentication constraint", "suggested_text": None,
    }]})
    clean = '{"verdict":"ok","issues":[]}'
    if mixed_batches:
        source += "\nНе отключайте аутентификацию.\n"  # noqa: RUF001
        second_translation = json.dumps({"segments": [{
            "id": "s0002", "text": "Keep authentication enabled.",
        }]})
        responses = [translation, second_translation, blocked, REFUSAL, repaired, clean, clean]
    else:
        responses = [translation, blocked, blocked, repaired, clean]
    state = FileRunState(
        mode="translate", file_path=RU, raw_source_text=source, source_text=source,
    )
    ctx = _context(responses, max_chars=1, max_parallel_batches=1)

    result = FileHarness(TRANSLATE_WITH_QA_PROFILE).run(state, ctx)

    assert ctx.client._client.chat.completions.create.call_count == len(responses)
    assert result.final_text == repaired_text + "\n" + (
        "\nKeep authentication enabled.\n" if mixed_batches else ""
    )
    assert result.verdict == ("warnings" if mixed_batches else "ok")
    assert result.critic_unresolved.verdict == "ok"
    assert not result.critic_unresolved.issues
    assert not result.critic_skipped
    pr = PRTranslationResult(pair_results=[PairRunResult(
        plan=PairPlan(
            pair=DocPair(ru_path=RU, en_path=EN), action="translate_to_en",
            source_path=RU, target_path=EN, source_lang="ru", target_lang="en",
        ),
        file_result=result, target_text=result.final_text,
    )])
    assert refresh_publication_impact(pr) == PublicationImpact.PUBLISH_NORMAL
    body = build_full_report(
        pr, meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=load_config(env={}),
    )
    assert "Missing authentication constraint" not in body


@pytest.mark.parametrize("text", [
    "Use the ldaps schema.\n",
    "See [ section `use_tls` ](auth.md#tls).\n",
    "Connect [ using the `StartTls` request ](auth.md#starttls).\n",
    PROSE,
])
def test_refusal_never_certifies_prose_as_green(text):
    result, _ = _run(text)
    assert result.verdict == "warnings"
    assert not result.heuristic_blocking
    assert any(i.category == "critic_model_refusal" for i in result.critic_unresolved.issues)


@pytest.mark.parametrize("refusal_first", [True, False])
def test_refusal_and_blocked_sibling_batch_preserves_red(refusal_first):
    blocked = json.dumps({"verdict": "blocked", "issues": [{
        "severity": "blocked", "category": "meaning", "comment": "Missing constraint",
    }]})
    responses = [REFUSAL, blocked] if refusal_first else [blocked, REFUSAL]
    result, ctx = _run(PROSE + "\nKeep authentication enabled.\n", responses, max_chars=1)
    assert result.verdict == "blocked"
    assert {i.category for i in result.critic_unresolved.issues} == {
        "meaning", "critic_model_refusal",
    }
    assert ctx.client._client.chat.completions.create.call_count == 2


@pytest.mark.parametrize("severity", ["warning", "blocked"])
def test_second_pass_refusal_remains_yellow(severity):
    warning = json.dumps({"verdict": "blocked" if severity == "blocked" else "warnings", "issues": [{
        "segment_id": "s0001", "severity": severity, "category": "grammar",
        "comment": "Use Apply", "suggested_text": PROSE.replace("Use", "Apply").strip(),
    }]})
    result, ctx = _run(responses=[warning, REFUSAL], source="Use documented authentication method.\n")
    assert result.verdict == "warnings"
    assert any(i.category == "critic_model_refusal" for i in result.critic_unresolved.issues)
    assert any("critic_model_refusal:" in m for m in result.heuristic_warnings)
    assert {i.category for i in result.critic_applied} == {"grammar"}
    assert {i.category for i in result.critic_unresolved.issues} == {"critic_model_refusal"}
    assert ctx.client._client.chat.completions.create.call_count == 2


@pytest.mark.parametrize("include_skipped", [None, True, False])
@pytest.mark.parametrize("blocked_passes", [(), (2,), (1, 2)])
def test_third_pass_refusal_preserves_pending_without_resurrecting_applied(
    include_skipped, blocked_passes,
):
    responses = []
    pending_comments = []
    for pass_number, verb in enumerate(("Apply", "Specify"), start=1):
        issues = [{
            "segment_id": "s0001", "severity": "blocked", "category": "grammar",
            "comment": f"Use {verb}",
            "suggested_text": f"{verb} the documented authentication method.",
        }]
        if pass_number in blocked_passes:
            comment = f"Missing authentication constraint from pass {pass_number}"
            pending_comments.append(comment)
            issues.append({
                "segment_id": "s0001", "severity": "blocked", "category": "meaning",
                "comment": comment, "suggested_text": None,
            })
        responses.append(json.dumps({"verdict": "blocked", "issues": issues}))
    result, ctx = _run(
        responses=[*responses, REFUSAL], source="Use documented authentication method.\n",
    )
    expected = "blocked" if blocked_passes else "warnings"
    assert result.verdict == result.critic_unresolved.verdict == expected
    assert result.final_text == "Specify the documented authentication method.\n"
    assert ctx.client._client.chat.completions.create.call_count == 3
    assert [issue.comment for issue in result.critic_applied] == ["Use Apply", "Use Specify"]
    assert [issue.comment for issue in result.critic_skipped] == pending_comments
    assert sorted(
        issue.comment for issue in result.critic_unresolved.issues if issue.category == "meaning"
    ) == pending_comments
    assert all(issue.category != "grammar" for issue in result.critic_unresolved.issues)
    assert any(message.startswith("critic_model_refusal:") for message in result.heuristic_warnings)
    pr = PRTranslationResult(pair_results=[PairRunResult(
        plan=_plan(), file_result=result, target_text=result.final_text,
    )])
    assert refresh_publication_impact(pr) == (
        PublicationImpact.WITHHOLD_UNSAFE if blocked_passes else PublicationImpact.PUBLISH_NORMAL
    )
    config = load_config(env={})
    if include_skipped is not None:
        config.reporting.include_skipped_critic = include_skipped
    original_result = deepcopy(result)
    body = build_full_report(
        pr, meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1), config=config,
    )
    assert ("Статус QA (K): 🔴 RED" if blocked_passes else "Статус QA (K): 🟡 YELLOW") in body
    for comment in pending_comments:
        assert body.count(comment) == 1
    assert "Use Apply" not in body and "Use Specify" not in body
    assert body.count("Модель отказала проверять файл") == 1
    assert result == original_result


def test_third_pass_refusal_preserves_protected_atom_and_meaning_blockers():
    text = "Use `Имя=Значение`.\n"
    unchanged = '{"spans":[{"id":"p1","text":"Имя=Значение"}]}'
    responses = []
    for verb in ("Apply", "Specify"):
        responses.extend([unchanged, json.dumps({"verdict": "blocked", "issues": [
            {"segment_id": "s0001", "severity": "warning", "category": "grammar",
             "comment": f"Use {verb}", "suggested_text": f"{verb} ⟦C1⟧."},
            {"segment_id": "s0001", "severity": "blocked", "category": "meaning",
             "comment": "Missing authentication constraint", "suggested_text": None},
        ]})])
    result, _ = _run(
        text, responses=[*responses, unchanged, REFUSAL, unchanged],
        source="Configure `Имя=Значение`.\n",
    )
    assert len(result.critic_applied) == 2
    assert result.verdict == "blocked"
    assert {issue.category for issue in result.critic_unresolved.issues} == {
        "meaning", "protected_atom_language", "critic_model_refusal",
    }
    pr = PRTranslationResult(pair_results=[PairRunResult(
        plan=_plan(), file_result=result, target_text=result.final_text,
    )])
    assert refresh_publication_impact(pr) == PublicationImpact.WITHHOLD_UNSAFE
    original_result = deepcopy(result)
    for include_skipped in (True, False):
        config = load_config(env={})
        config.reporting.include_skipped_critic = include_skipped
        body = build_full_report(
            pr, meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1), config=config,
        )
        assert body.count("Untranslated human-language content in protected code atom") == 1
        assert body.count("Missing authentication constraint") == 1
        assert body.count("Модель отказала проверять файл") == 1
    assert result == original_result


def test_third_pass_refusal_does_not_restore_a_skipped_finding_repaired_on_second_pass():
    first = {"verdict": "blocked", "issues": [
        {"segment_id": "s0001", "severity": "warning", "category": "grammar",
         "comment": "Use Apply", "suggested_text": "Apply the documented authentication method."},
        {"segment_id": "s0001", "severity": "blocked", "category": "meaning",
         "comment": "Missing authentication constraint", "suggested_text": None},
        {"segment_id": "s0001", "severity": "blocked", "category": "meaning",
         "comment": "Missing a separate authentication constraint", "suggested_text": None},
    ]}
    second = {"verdict": "blocked", "issues": [
        {"segment_id": "s0001", "severity": "blocked", "category": "meaning",
         "comment": "Missing authentication constraint",
         "suggested_text": "Apply the documented authentication method securely."},
    ]}
    result, _ = _run(
        responses=[json.dumps(first), json.dumps(second), REFUSAL],
        source="Use documented authentication method.\n",
    )
    assert result.final_text == "Apply the documented authentication method securely.\n"
    assert [issue.comment for issue in result.critic_applied] == [
        "Use Apply", "Missing authentication constraint",
    ]
    assert [issue.comment for issue in result.critic_skipped] == [
        "Missing authentication constraint", "Missing a separate authentication constraint",
    ]
    assert [
        issue.comment for issue in result.critic_unresolved.issues if issue.category == "meaning"
    ] == ["Missing a separate authentication constraint"]
    assert result.verdict == "blocked"
    original_result = deepcopy(result)
    for include_skipped in (True, False):
        config = load_config(env={})
        config.reporting.include_skipped_critic = include_skipped
        body = build_full_report(
            PRTranslationResult(pair_results=[PairRunResult(
                plan=_plan(), file_result=result, target_text=result.final_text,
            )]),
            meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1), config=config,
        )
        assert "Missing authentication constraint" not in body
        assert body.count("Missing a separate authentication constraint") == 1
        assert body.count("Модель отказала проверять файл") == 1
    assert result == original_result


def test_third_pass_refusal_keeps_a_finding_reconfirmed_after_an_applied_fix():
    first = {"verdict": "blocked", "issues": [
        {"segment_id": "s0001", "severity": "blocked", "category": "grammar",
         "comment": "Check authentication wording",
         "suggested_text": "Apply the documented authentication method."},
    ]}
    second = {"verdict": "blocked", "issues": [
        {"segment_id": "s0001", "severity": "blocked", "category": "grammar",
         "comment": "Check authentication wording", "suggested_text": None},
        {"segment_id": "s0001", "severity": "warning", "category": "terminology",
         "comment": "Use Specify", "suggested_text": "Specify the documented authentication method."},
    ]}
    result, _ = _run(
        responses=[json.dumps(first), json.dumps(second), REFUSAL],
        source="Use documented authentication method.\n",
    )
    assert result.final_text == "Specify the documented authentication method.\n"
    assert result.verdict == "blocked"
    assert [
        issue.comment for issue in result.critic_unresolved.issues if issue.category == "grammar"
    ] == ["Check authentication wording"]


@pytest.mark.parametrize("suggestion", [None, "Unsafe new ⟦C99⟧."])
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("include_skipped", [None, True, False])
def test_second_pass_refusal_keeps_unrepaired_first_pass_blocker(suggestion, mixed, include_skipped):
    issues = [{
        "segment_id": "s0001", "severity": "blocked", "category": "meaning",
        "comment": "Missing authentication constraint", "suggested_text": suggestion,
    }]
    if mixed:
        issues.extend([
            {"segment_id": "s0001", "severity": "warning", "category": "terminology",
             "comment": "Check preferred term", "suggested_text": None},
            {"segment_id": "s0001", "severity": "warning", "category": "grammar",
             "comment": "Use Apply", "suggested_text": "Apply the documented authentication method."},
        ])
    result, ctx = _run(
        responses=[json.dumps({"verdict": "blocked", "issues": issues}), REFUSAL],
        source="Use documented authentication method.\n",
    )
    assert result.verdict == result.critic_unresolved.verdict == "blocked"
    assert not result.heuristic_blocking
    remaining = {i.category for i in result.critic_unresolved.issues}
    assert remaining == ({"meaning", "terminology", "critic_model_refusal"}
                         if mixed else {"meaning", "critic_model_refusal"})
    assert {i.category for i in result.critic_skipped} == (
        {"meaning", "terminology"} if mixed else {"meaning"}
    )
    assert {i.category for i in result.critic_applied} == ({"grammar"} if mixed else set())
    assert ctx.client._client.chat.completions.create.call_count == 2
    pr = PRTranslationResult(pair_results=[PairRunResult(
        plan=_plan(), file_result=result, target_text=result.final_text,
    )])
    assert refresh_publication_impact(pr) == PublicationImpact.WITHHOLD_UNSAFE
    config = load_config(env={})
    if include_skipped is not None:
        config.reporting.include_skipped_critic = include_skipped
    original_result = deepcopy(result)
    body = build_full_report(
        pr, meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=config,
    )
    assert "Статус QA (K): 🔴 RED" in body
    assert body.count("Missing authentication constraint") == 1
    assert body.count("Check preferred term") == int(mixed)
    assert "Use Apply" not in body
    assert body.count("Модель отказала проверять файл") == 1
    assert result == original_result
    assert "ручная проверка" in body
    assert "можно мержить" not in body


@pytest.mark.parametrize("include_skipped", [None, True, False])
def test_second_pass_refusal_keeps_meaning_and_protected_atom_blockers(include_skipped):
    text = "Use `Имя=Значение`.\n"
    first = json.dumps({"verdict": "blocked", "issues": [{
        "segment_id": "s0001", "severity": "blocked", "category": "meaning",
        "comment": "Missing authentication constraint", "suggested_text": None,
    }]})
    # Both real finalization passes request atom repair; the model leaves it unchanged.
    unchanged = '{"spans":[{"id":"p1","text":"Имя=Значение"}]}'
    result, _ = _run(
        text, responses=[unchanged, first, unchanged, REFUSAL, unchanged],
        source="Apply `Имя=Значение`.\n",
    )
    assert result.verdict == "blocked"
    assert {i.category for i in result.critic_unresolved.issues} >= {
        "meaning", "protected_atom_language", "critic_model_refusal",
    }
    atoms = [i for i in result.critic_unresolved.issues if i.category == "protected_atom_language"]
    assert len(atoms) == 1
    assert atoms[0].severity == "blocked" and atoms[0].suggested_text is None
    config = load_config(env={})
    if include_skipped is not None:
        config.reporting.include_skipped_critic = include_skipped
    pr = PRTranslationResult(pair_results=[PairRunResult(
        plan=_plan(), file_result=result, target_text=result.final_text,
    )])
    body = build_full_report(
        pr, meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=config,
    )
    assert body.count("Untranslated human-language content in protected code atom") == 1
    assert body.count("Missing authentication constraint") == 1
    assert body.count("Модель отказала проверять файл") == 1
    assert "Статус QA (K): 🔴 RED" in body
    assert refresh_publication_impact(pr) == PublicationImpact.WITHHOLD_UNSAFE


def test_second_pass_refusal_preserves_blocked_sibling():
    warning = json.dumps({"verdict": "warnings", "issues": [{
        "severity": "warning", "category": "grammar", "comment": "Check language",
    }]})
    blocked = json.dumps({"verdict": "blocked", "issues": [{
        "severity": "blocked", "category": "meaning", "comment": "Missing constraint",
    }]})
    result, ctx = _run(
        PROSE + "\nKeep authentication enabled.\n",
        [warning, '{"verdict":"ok","issues":[]}', REFUSAL, blocked],
        max_chars=1,
    )
    assert result.verdict == "blocked"
    assert {i.category for i in result.critic_unresolved.issues} == {
        "critic_model_refusal", "meaning", "grammar",
    }
    assert ctx.client._client.chat.completions.create.call_count == 4


@pytest.mark.parametrize("second_pass", [False, True])
@pytest.mark.parametrize("verdict", ["warnings", "blocked"])
def test_refusal_compatibility_empty_issues_through_harness(verdict, second_pass):
    response = json.dumps({"verdict": verdict, "issues": []})
    warning = json.dumps({"verdict": "warnings", "issues": [{
        "severity": "warning", "category": "grammar", "comment": "Check language",
    }]})
    result, _ = _run(responses=[warning, response] if second_pass else [response])
    assert result.verdict == verdict


def test_execution_failure_remains_red():
    result, _ = _run(responses=["", "", ""])
    assert result.verdict == "blocked"
    assert {i.category for i in result.critic_unresolved.issues} == {"critic_execution_failed"}


def test_refusal_warning_survives_pair_post_repair_qa():
    from ydbdoc_review.harness import pair

    # Canonical finalization removes trailing spaces; verify restores exact PR bytes.
    text = PROSE.rstrip("\n") + "  \n"
    plan = _plan()
    content = PairContent(pair=plan.pair, ru_text=text, en_text=text)
    with patch.object(pair, "run_file_heuristics_classified", wraps=pair.run_file_heuristics_classified) as qa:
        run = run_pair_plan(content, plan, _context([REFUSAL]), {})
    assert qa.call_count == 1
    assert run.target_text == text
    assert run.file_result.verdict == "warnings"
    assert any(i.category == "critic_model_refusal" for i in run.file_result.critic_unresolved.issues)


@pytest.mark.parametrize("include_skipped", [None, True, False])
def test_report_does_not_say_can_merge_for_refused_prose(include_skipped):
    result, _ = _run()
    config = load_config(env={})
    if include_skipped is not None:
        config.reporting.include_skipped_critic = include_skipped
    body = build_full_report(
        PRTranslationResult(pair_results=[PairRunResult(plan=_plan(), file_result=result)]),
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=config,
    )
    assert "можно мержить" not in body
    assert "🟡" in body
    assert "ручная проверка" in body
    assert "языка и стиля не завершена" in body
    assert body.count("Модель отказала проверять файл") == 1


@pytest.mark.parametrize("include_skipped", [None, True, False])
def test_refusal_report_keeps_distinct_findings_with_similar_prose(include_skipped):
    from dataclasses import replace

    from ydbdoc_review.translation.schemas import CriticIssueOut

    result, _ = _run()
    distinct_critic = CriticIssueOut(
        severity="warning", category="meaning",
        comment="Language/style review incomplete for another reason.",
    )
    distinct_heuristic = "other_review: language/style review incomplete; manual review required"
    distinct_skipped = distinct_critic.model_copy(update={
        "comment": "Language/style review incomplete for a separate skipped reason.",
    })
    result = replace(
        result,
        critic_unresolved=CriticResponse(
            verdict="warnings", issues=[*result.critic_unresolved.issues, distinct_critic],
        ),
        heuristic_warnings=[*result.heuristic_warnings, distinct_heuristic],
        critic_skipped=[distinct_critic, distinct_skipped],
    )
    original_result = deepcopy(result)
    config = load_config(env={})
    if include_skipped is not None:
        config.reporting.include_skipped_critic = include_skipped
    body = build_full_report(
        PRTranslationResult(pair_results=[PairRunResult(plan=_plan(), file_result=result)]),
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=config,
    )
    assert body.count("Модель отказала проверять файл") == 1
    assert body.count(distinct_critic.comment) == 1
    assert body.count(distinct_heuristic) == 1
    assert body.count(distinct_skipped.comment) == int(include_skipped is not False)
    assert result == original_result


def test_ascii_zero_segment_file_makes_no_critic_request():
    result, ctx = _run("```yaml\nkey: value\n```\n", responses=[])
    assert result.segments_count == 0
    assert result.verdict == "ok"
    ctx.client._client.chat.completions.create.assert_not_called()


@pytest.mark.parametrize("verdict", ["warnings", "blocked"])
@pytest.mark.parametrize("resolved", [True, False])
def test_refusal_compatibility_verdict_survives_empty_issues(verdict, resolved):
    response = CriticResponse(verdict=verdict, issues=[])
    assert compute_critic_verdict(
        initial=response, unresolved=response if resolved else None,
    ) == verdict
