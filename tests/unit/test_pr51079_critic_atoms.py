"""Actual target atoms, rather than source legends or model promises, decide language QA."""

import json

import pytest

from tests.unit.test_critic import _mock_client
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.render import render_with_translations
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import run_critic_loop
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.qa import align_translations_from_target
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.placeholder_align import normalize_target_segments_to_source
from ydbdoc_review.translation.critic import apply_critic_fixes, run_critic, run_verify
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.prompts import segments_to_critic_batch_json
from ydbdoc_review.translation.schemas import CriticIssueOut
from ydbdoc_review.validation.final_language import check_final_en_language
from ydbdoc_review.validation.markers import extract_placeholders
from ydbdoc_review.validation.placeholder_drift import exclude_skipped_issues

OK = '{"verdict":"ok","issues":[]}'
REFUSAL = "I cannot discuss this topic."
RU = "Use `Имя=Значение,...@<domain>`.\n"
EN = "Use `Name=Value,...@<domain>`.\n"


def _run(text, *, source=RU, verify=False, response=OK):
    segments = extract_segments(parse_markdown(source))
    kwargs = {"prior_issues": []} if verify else {}
    responses = response if isinstance(response, list) else [response]
    return (run_verify if verify else run_critic)(
        _mock_client(responses),
        segments=segments,
        translations={s.id: s.text for s in segments},
        glossary=load_glossary(),
        file_path="ydb/docs/ru/core/a.md",
        translated_text=text,
        **kwargs,
    )


@pytest.mark.parametrize("verify", [False, True])
def test_cyrillic_target_atom_is_blocked_even_when_model_says_ok(verify):
    response = _run(RU, verify=verify)
    issues = [i for i in response.issues if i.category == "protected_atom_language"]
    assert issues
    issue = issues[0]
    assert response.verdict == issue.severity == "blocked"
    assert issue.suggested_text is None
    assert issue.segment_id == extract_segments(parse_markdown(RU))[0].id


@pytest.mark.parametrize("verify", [False, True])
def test_source_russian_target_english_atom_is_not_a_false_positive(verify):
    assert _run(EN, verify=verify).verdict == "ok"


@pytest.mark.parametrize(
    "target",
    [
        "Use `Name=Value,...@<domain>` and `Subject`, then `Name=Value,...@<domain>`.\n",
        "Use `Имя=Значение,...@<domain>` and `Subject`, then `Name=Value,...@<domain>`.\n",
    ],
)
def test_localized_notation_aligns_among_other_and_repeated_atoms(target):
    source = "Use `Имя=Значение,...@<domain>` and `Subject`, then `Имя=Значение,...@<domain>`.\n"
    response = _run(target, source=source)
    assert not any(i.category == "protected_atom_alignment" for i in response.issues)
    assert response.verdict == ("blocked" if "Имя" in target else "ok")


@pytest.mark.parametrize("first_atom", ["Name=Value,...@<domain>", "Имя=Значение,...@<domain>"])
def test_real_alignment_and_critic_legends_share_notation_marker_addresses(first_atom):
    source = "Use `Имя=Значение,...@<domain>` and `Subject`, then `Имя=Значение,...@<domain>`.\n"
    target = f"Use `{first_atom}` and `Subject`, then `Name=Value,...@<domain>`.\n"
    segments = extract_segments(parse_markdown(source))
    translations = align_translations_from_target(segments, target)
    client = _mock_client([OK])
    response = run_critic(
        client,
        segments=segments,
        translations=translations,
        glossary=load_glossary(),
        file_path="ydb/docs/ru/core/a.md",
        translated_text=target,
    )
    user = client._client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    row = json.loads(user.split("```json\n", 1)[1].split("\n```", 1)[0])["segments"][0]
    expected_markers = {"⟦C1⟧", "⟦C2⟧", "⟦C3⟧"}
    assert set(extract_placeholders(row["source_text"])) == expected_markers
    assert set(extract_placeholders(row["translated_text"])) == expected_markers
    assert row["target_atom_map"] == {
        "⟦C1⟧": f"code:{first_atom}",
        "⟦C2⟧": "code:Subject",
        "⟦C3⟧": "code:Name=Value,...@<domain>",
    }
    assert response.verdict == ("blocked" if "Имя" in first_atom else "ok")
    target_doc = parse_markdown(target)
    target_segments = normalize_target_segments_to_source(segments, extract_segments(target_doc))
    correction = CriticIssueOut(
        segment_id=segments[0].id,
        severity="warning",
        category="grammar",
        comment="Use Apply",
        suggested_text="Apply ⟦C1⟧ and ⟦C2⟧, then ⟦C3⟧.",
    )
    fixed, applied, skipped = apply_critic_fixes(
        translations,
        target_segments,
        [correction],
        strict_placeholder_order=True,
    )
    assert applied == [correction] and skipped == []
    rendered = render_with_translations(target_doc, target_segments, fixed)
    assert rendered == target.replace("Use ", "Apply ", 1)


def test_critic_json_contains_separate_source_and_target_atom_maps():
    from ydbdoc_review.translation.critic_atoms import target_atom_maps

    segments = extract_segments(parse_markdown(RU))
    maps = target_atom_maps(segments, EN)
    payload = json.loads(
        segments_to_critic_batch_json(
            segments,
            {},
            target_atom_maps=maps,
        )
    )["segments"][0]
    assert payload["atom_map"] == {"⟦C1⟧": "code:Имя=Значение,...@<domain>"}
    assert payload["target_atom_map"] == {"⟦C1⟧": "code:Name=Value,...@<domain>"}


@pytest.mark.parametrize("text", ["", "# Title\n", EN + "\nExtra paragraph.\n", "Use text.\n"])
def test_atom_map_alignment_failure_is_blocked(text):
    response = _run(text)
    assert response.verdict == "blocked"
    assert any(i.category == "protected_atom_alignment" for i in response.issues)


def test_blocked_atom_issue_survives_skipped_fix_filter():
    issue = CriticIssueOut(
        segment_id="s1",
        severity="blocked",
        category="protected_atom_language",
        comment="opaque atom",
    )
    assert exclude_skipped_issues([issue], [issue]) == [issue]


def test_model_cannot_replace_opaque_atom_with_suggested_text():
    segments = extract_segments(parse_markdown(RU))
    issue = CriticIssueOut(
        segment_id=segments[0].id,
        severity="blocked",
        category="protected_atom_language",
        comment="opaque atom",
        suggested_text="Use a different ⟦C1⟧.",
    )
    translations = {segments[0].id: segments[0].text}
    fixed, applied, skipped = apply_critic_fixes(translations, segments, [issue])
    assert fixed == translations
    assert applied == []
    assert skipped == [issue]


def test_reverify_uses_post_fix_target_atoms():
    doc = parse_markdown(RU)
    segments = extract_segments(doc)
    client = _mock_client([OK, '{"spans":[{"id":"p1","text":"Name=Value,...@<domain>"}]}', OK])
    state = FileRunState(
        mode="translate",
        file_path="ydb/docs/ru/core/a.md",
        raw_source_text=RU,
        source_text=RU,
        source_doc=doc,
        segments=segments,
        translations={segments[0].id: segments[0].text},
        translated_text=RU,
        render_base_doc=doc,
        render_base_segments=segments,
        fence_reference_text=RU,
    )
    run_critic_loop(state, HarnessContext.from_options(client, glossary=load_glossary()))
    assert state.critic_initial.verdict == "blocked"
    assert state.translated_text == EN
    assert state.critic_unresolved.verdict == "ok"
    messages = client._client.chat.completions.create.call_args_list
    assert len(messages) == 3
    assert '"target_atom_map"' in messages[-1].kwargs["messages"][1]["content"]
    assert "code:Name=Value,...@<domain>" in messages[-1].kwargs["messages"][1]["content"]


def test_refusal_cannot_clear_deterministic_atom_blocker():
    response = _run(RU, response=[REFUSAL] * 3)
    assert response.verdict == "blocked"
    assert any(i.category == "protected_atom_language" for i in response.issues)


def test_third_verify_pass_preserves_actual_english_target_atoms():
    source = "Используйте `Имя=Значение,...@<domain>`.\n"
    source_doc = parse_markdown(source)
    segments = extract_segments(source_doc)
    target_doc = parse_markdown(EN)
    target_segments = normalize_target_segments_to_source(segments, extract_segments(target_doc))
    responses = [
        json.dumps(
            {
                "verdict": "warnings",
                "issues": [
                    {
                        "segment_id": segments[0].id,
                        "severity": "warning",
                        "category": "grammar",
                        "comment": f"Use {verb}",
                        "suggested_text": f"{verb} ⟦C1⟧.",
                    }
                ],
            }
        )
        for verb in ("Apply", "Specify")
    ]
    client = _mock_client([*responses, OK])
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/a.md",
        raw_source_text=source,
        source_text=source,
        source_doc=source_doc,
        segments=segments,
        translations=align_translations_from_target(segments, EN),
        translated_text=EN,
        render_base_doc=target_doc,
        render_base_segments=target_segments,
        fence_reference_text=EN,
    )

    run_critic_loop(state, HarnessContext.from_options(client, glossary=load_glossary()))

    assert state.translated_text == "Specify `Name=Value,...@<domain>`.\n"
    assert state.segment_alignment_error is None
    assert len(state.critic_applied) == 2
    assert check_final_en_language(state.translated_text) == []
    requests = client._client.chat.completions.create.call_args_list
    assert len(requests) == 3
    for request, verb in zip(requests, ("Use", "Apply", "Specify"), strict=True):
        user = request.kwargs["messages"][1]["content"]
        row = json.loads(user.split("```json\n", 1)[1].split("\n```", 1)[0])["segments"][0]
        assert row["translated_text"] == f"{verb} ⟦C1⟧."
        assert row["target_atom_map"] == {"⟦C1⟧": "code:Name=Value,...@<domain>"}
    assert state.critic_unresolved.verdict == "ok"
    assert not any(
        issue.category == "protected_atom_language" for issue in state.critic_unresolved.issues
    )


def test_missing_explicit_map_never_falls_back_to_source():
    from ydbdoc_review.translation.critic_atoms import protected_atom_language_issues

    segments = extract_segments(parse_markdown(EN))
    assert any(
        i.category == "protected_atom_alignment"
        for i in protected_atom_language_issues(segments, target_atoms={})
    )
    assert protected_atom_language_issues(segments) == []


def test_target_atom_markers_align_after_word_order_change():
    from ydbdoc_review.translation.critic_atoms import target_atom_maps

    segments = extract_segments(parse_markdown("Use `alpha` then `beta`.\n"))
    maps = target_atom_maps(segments, "Use `beta` before `alpha`.\n")
    assert maps == {segments[0].id: {"⟦C2⟧": "code:beta", "⟦C1⟧": "code:alpha"}}


def test_runtime_nulls_unsafe_model_suggestion_for_opaque_atom():
    response = _run(
        RU,
        response=json.dumps(
            {
                "verdict": "ok",
                "issues": [
                    {
                        "segment_id": "s0001",
                        "severity": "warning",
                        "category": "protected_atom_language",
                        "comment": "Translate atom",
                        "suggested_text": "Use `Name=Value`.",
                    }
                ],
            }
        ),
    )
    assert response.verdict == "blocked"
    assert all(i.suggested_text is None and i.severity == "blocked" for i in response.issues)


def test_harness_refusal_keeps_protected_atom_blocked():
    doc = parse_markdown(RU)
    segments = extract_segments(doc)
    state = FileRunState(
        mode="translate",
        file_path="ydb/docs/ru/core/a.md",
        raw_source_text=RU,
        source_text=RU,
        source_doc=doc,
        segments=segments,
        translations={segments[0].id: segments[0].text},
        translated_text=RU,
    )
    client = _mock_client([REFUSAL] * 3)
    run_critic_loop(state, HarnessContext.from_options(client, glossary=load_glossary()))
    assert state.critic_unresolved.verdict == "blocked"
    assert any(i.category == "protected_atom_language" for i in state.critic_unresolved.issues)


def test_opaque_issue_survives_real_apply_and_reverify_loop():
    source = "Use `Имя=Значение`.\n"
    doc = parse_markdown(source)
    segments = extract_segments(doc)
    state = FileRunState(
        mode="translate",
        file_path="ydb/docs/ru/core/a.md",
        raw_source_text=source,
        source_text=source,
        source_doc=doc,
        segments=segments,
        translations={segments[0].id: segments[0].text},
        translated_text=source,
        render_base_doc=doc,
        render_base_segments=segments,
        fence_reference_text=source,
    )
    # The external repair fails to translate the atom; the real finalizer retains it.
    client = _mock_client([OK, '{"spans":[{"id":"p1","text":"Имя=Значение"}]}', OK])
    run_critic_loop(state, HarnessContext.from_options(client, glossary=load_glossary()))
    assert state.translated_text == source
    assert state.critic_skipped
    assert state.critic_unresolved.verdict == "blocked"
    assert any(i.category == "protected_atom_language" for i in state.critic_unresolved.issues)


def test_resplit_batches_keep_target_atom_evidence_and_deterministic_blocker():
    source = "Use `Имя=Значение`.\n\nAnother paragraph.\n"
    segments = extract_segments(parse_markdown(source))
    client = _mock_client(["", "", "", OK, OK])
    response = run_critic(
        client,
        segments=segments,
        translations={s.id: s.text for s in segments},
        glossary=load_glossary(),
        file_path="ydb/docs/ru/core/a.md",
        translated_text=source,
    )
    assert response.verdict == "blocked"
    assert any(i.category == "protected_atom_language" for i in response.issues)
    halves = client._client.chat.completions.create.call_args_list[-2:]
    assert all('"target_atom_map"' in c.kwargs["messages"][1]["content"] for c in halves)
