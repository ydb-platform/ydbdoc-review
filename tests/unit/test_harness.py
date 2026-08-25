"""Tests for per-file harness (translate / verify profiles)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness import (
    FileHarness,
    FileRunState,
    HarnessContext,
    TRANSLATE_PROFILE,
    TRANSLATE_WITH_QA_PROFILE,
    VERIFY_PROFILE,
)
from ydbdoc_review.harness.steps import ParseStep, TranslateStep
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary


def _completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _mock_client(responses: list[str]) -> YandexLLMClient:
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.side_effect = [_completion(r) for r in responses]
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    return YandexLLMClient(
        folder_id="b1x",
        api_key="k",
        llm=cfg.llm,
        client=mock_openai,
    )


def _translate_json(segments, mapping: dict[str, str]) -> str:
    payload = {
        "segments": [{"id": seg.id, "text": mapping.get(seg.id, seg.text)} for seg in segments]
    }
    return json.dumps(payload, ensure_ascii=False)


def test_profiles_translate_only_verify_has_qa():
    translate_names = [s.name for s in TRANSLATE_PROFILE.steps]
    verify_names = [s.name for s in VERIFY_PROFILE.steps]
    assert translate_names == ["parse", "translate"]
    assert verify_names[0] == "parse"
    assert verify_names[1] == "load_target"
    assert verify_names.count("finalize_en") == 2
    assert verify_names.index("finalize_en") < verify_names.index("critic_loop")
    assert verify_names.index("finalize_en") < verify_names.index("heuristics")
    assert verify_names.index("heuristics") < verify_names.index("verdict")
    assert verify_names.index("verdict") < verify_names.index("report_artifacts")
    shared_qa = [
        "round_trip",
        "finalize_en",
        "critic_loop",
        "finalize_en",
        "heuristics",
        "verdict",
        "report_artifacts",
    ]
    assert verify_names[2:] == shared_qa
    with_qa_names = [s.name for s in TRANSLATE_WITH_QA_PROFILE.steps]
    assert with_qa_names[:2] == ["parse", "translate"]
    assert "critic_feedback_retry" in with_qa_names
    assert "finalize_en" not in with_qa_names


def test_verify_profile_translates_yql_trailing_cyrillic_comments():
    """doc_verify must fix RU ``--`` comments even with Fixed segments: 0 (§6.136).

    Verdict uses post-finalize text (§6.138): incoming Cyrillic is auto-fixed,
    so the report is 🟢 when ``final_text`` is clean English.
    """
    from textwrap import dedent

    from ydbdoc_review.validation.fence_comments import (
        check_cyrillic_in_en_fence_comments,
        collect_cyrillic_fence_comment_lines,
    )

    ru = dedent(
        """
        Intro with enough words so heuristics do not complain about length here.

        ```yql
        select
            Query,          -- Запрос
            WmPoolId        -- Идентификатор пула
        from `.sys/query_sessions`
        ```
        """
    ).strip()
    en = dedent(
        """
        Intro with enough words so heuristics do not complain about length here.

        ```yql
        select
            Query,          -- Запрос
            WmPoolId        -- Идентификатор пула
        from `.sys/query_sessions`
        ```
        """
    ).strip()
    critic_ok = json.dumps({"verdict": "ok", "issues": []})
    items = collect_cyrillic_fence_comment_lines(en)
    assert len(items) >= 2
    fence_json = json.dumps(
        {
            "comments": [
                {
                    "id": f"b{it.block_index}-l{it.line_index}",
                    "text": ("Query" if "Запрос" in it.body else "Pool ID"),
                }
                for it in items
            ]
        },
        ensure_ascii=False,
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    glossary = load_glossary()
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/dev/a.md",
        raw_source_text=ru,
        source_text=ru,
        existing_target_text=en,
    )
    result = FileHarness(VERIFY_PROFILE).run(
        state,
        HarnessContext.from_options(
            _mock_client([fence_json, critic_ok]),
            glossary=glossary,
            config=cfg,
        ),
    )
    assert result.verdict == "ok"
    assert not any(
        w.startswith("cyrillic_in_fence:") or w.startswith("cyrillic_in_code_fence:")
        for w in result.heuristic_warnings
    )
    assert not any(
        w.startswith("cyrillic_in_fence:") or w.startswith("cyrillic_in_code_fence:")
        for w in result.heuristic_blocking
    )
    assert "Запрос" not in result.final_text
    assert "Идентификатор" not in result.final_text
    assert check_cyrillic_in_en_fence_comments(result.final_text, target_lang="en") == []


def test_parse_step_empty_file_stops_early():
    state = FileRunState(
        mode="translate",
        file_path="empty.md",
        raw_source_text="",
        source_text="",
    )
    ctx = HarnessContext.from_options(
        _mock_client([]),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"}),
    )
    ParseStep().run(state, ctx)
    assert state.stopped_early is True
    assert state.segments == []


def test_finalize_runs_despite_stale_alignment_error():
    from ydbdoc_review.harness.steps import FinalizeEnStep

    exact = "See [SID](authorization.md#sid).\n"
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/security/index.md",
        raw_source_text=exact,
        source_text=exact,
        translated_text="See [SID](authorization.md#user).\n",
        fence_reference_text=exact,
        segment_alignment_error="stale critic alignment",
    )
    ctx = HarnessContext.from_options(
        _mock_client([]),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"}),
    )

    FinalizeEnStep().run(state, ctx)

    assert state.translated_text == exact


def test_harness_translate_matches_translate_file():
    source = "Привет.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id
    responses = [
        _translate_json(segments, {seg_id: "Hello.\n"}),
    ]
    glossary = load_glossary()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})

    state = FileRunState(
        mode="translate",
        file_path="docs/ru/a.md",
        raw_source_text=source,
        source_text=source,
    )
    harness_result = FileHarness(TRANSLATE_PROFILE).run(
        state, HarnessContext.from_options(_mock_client(responses), glossary=glossary, config=cfg)
    )

    file_result = translate_file(
        source,
        _mock_client(responses),
        glossary,
        file_path="docs/ru/a.md",
        config=cfg,
    )

    assert harness_result.verdict == file_result.verdict
    assert harness_result.final_text == file_result.final_text
    assert harness_result.segments_count == file_result.segments_count


def test_translate_step_skipped_in_verify_profile():
    source = "Привет.\n"
    state = FileRunState(
        mode="verify",
        file_path="docs/ru/a.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text="Hello.\n",
    )
    ctx = HarnessContext.from_options(
        _mock_client([]),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"}),
    )
    with patch("ydbdoc_review.harness.steps.translate_segments") as mock_tr:
        TranslateStep().run(state, ctx)
        mock_tr.assert_not_called()


def test_translate_semantic_noop_preserves_existing_en_link_exactly():
    base = (
        "{% note warning %}\n\n"
        "Секреты необходимо [создавать](../../create-secret.md). \n\n"
        "{% endnote %}\n"
    )
    source = base.replace(". \n", ".\n")
    existing = (
        "{% note warning %}\n\n"
        "Secrets must be [created](../../create-secret.md).\n\n"
        "{% endnote %}\n"
    )
    state = FileRunState(
        mode="translate",
        file_path="ydb/docs/ru/core/limitation-dump-secrets.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text=existing,
        # Production #49933 currently resolves the same normalized base text;
        # the low-magnitude analysis must still preserve EN exactly.
        base_source_text=source,
    )
    ctx = HarnessContext.from_options(
        _mock_client([]),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"}),
    )
    ParseStep().run(state, ctx)

    with patch("ydbdoc_review.harness.steps.finalize_en_target") as finalize:
        TranslateStep().run(state, ctx)

    assert state.stopped_early is True
    assert state.differential_meta["semantic_noop"] is True
    assert state.translated_text == existing
    finalize.assert_not_called()
