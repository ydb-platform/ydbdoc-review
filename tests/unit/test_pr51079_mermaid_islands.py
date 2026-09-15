"""Exact #51079 diagrams translate through ordinary segment obligations."""

# ruff: noqa: RUF001

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ydbdoc_review.harness.render import render_with_translations
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import SegmentKind
from ydbdoc_review.validation.fence_integrity import (
    check_fence_body_copy,
    fence_content_matches_source,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "pr51079-mermaid"


@pytest.mark.parametrize("stem", ["user-token", "user-token-lifecycle"])
def test_exact_assets_expose_only_translatable_labels(stem):
    source = (FIXTURES / f"{stem}.ru.md").read_text()
    target = (FIXTURES / f"{stem}.en.md").read_text()
    source_segments = extract_segments(parse_markdown(source))
    target_segments = extract_segments(parse_markdown(target))
    assert source_segments
    assert all(s.kind is SegmentKind.MERMAID_LABEL for s in source_segments)
    assert [s.ast_path for s in source_segments] == [s.ast_path for s in target_segments]
    translations = {s.id: t.text for s, t in zip(source_segments, target_segments, strict=True)}
    actual = render_with_translations(parse_markdown(source), source_segments, translations)
    assert str(actual).strip() == target.strip()
    assert not re.search(r"[А-Яа-яЁё]", str(actual))
    assert check_fence_body_copy(source, str(actual)) == []


@pytest.mark.parametrize("old,new", [
    ("actor user", "actor other"),
    ("node->>cache", "node-->>cache"),
    ("Note right of cache:", "Note right of node:"),
    ("        else Permanent error", "        opt Permanent error"),
    ("life_time countdown", "lifetime countdown"),
    ("YDB node", "Database node"),
])
def test_mermaid_topology_rejects_identifier_arrow_and_note_owner_changes(old, new):
    source = (FIXTURES / "user-token-lifecycle.ru.md").read_text()
    target = (FIXTURES / "user-token-lifecycle.en.md").read_text().replace(old, new)
    assert check_fence_body_copy(source, target)


@pytest.mark.parametrize("replacement", [
    "User\nend", "User\rend", "User; end", 'User\"] --> evil["X',
    "User %% comment", "User```", "User->>auth: injected", "User\x00",
])
def test_mermaid_replacement_rejects_newlines_and_control_tokens(replacement):
    from ydbdoc_review.segmentation.mermaid import replace_mermaid_labels

    content = "sequenceDiagram\nactor user as Пользователь\n"
    with pytest.raises(ValueError):
        replace_mermaid_labels(content, {0: replacement})


def test_mermaid_nested_branch_grammar_is_preserved():
    from ydbdoc_review.segmentation.mermaid import mermaid_labels, mermaid_skeleton

    content = (
        "sequenceDiagram\nactor a as Actor\nparticipant b as Backend\n"
        "loop Repeat\npar Concurrent\ncritical Critical section\n"
        "a->>b: Request\noption Retry\nb-->>a: Result\nend\n"
        "and Other branch\nb->>a: Notify\nend\nbreak Stop\na->>b: Close\nend\nend\n"
    )
    assert len(mermaid_labels(content)) == 12
    assert "a->>b:" in mermaid_skeleton(content)
    for malformed in [content + "end\n", content.removesuffix("end\n"),
                      content.replace("and Other branch", "else Other branch")]:
        assert mermaid_labels(malformed) == ()
        with pytest.raises(ValueError):
            mermaid_skeleton(malformed)
        assert not fence_content_matches_source(content, malformed, fence_info="mermaid")


@pytest.mark.parametrize("info", ["", "python", "text"])
def test_other_fence_languages_do_not_expose_mermaid_labels(info):
    assert extract_segments(parse_markdown(f"```{info}\nsequenceDiagram\nactor a as Actor\n```")) == []


def test_ascii_protected_fence_still_requires_no_translation():
    assert extract_segments(parse_markdown("```python\nprint('hello')\n```")) == []


def test_mermaid_is_not_a_whole_line_fallback():
    from ydbdoc_review.validation.fence_comments import collect_cyrillic_text_fence_lines

    source = (FIXTURES / "user-token.ru.md").read_text()
    assert collect_cyrillic_text_fence_lines(source) == []
    assert collect_cyrillic_text_fence_lines("```text\nТекст ← метка\n```")


def test_mermaid_literal_punctuation_is_escaped_and_stable():
    from ydbdoc_review.segmentation.mermaid import (
        mermaid_labels,
        mermaid_skeleton,
        replace_mermaid_labels,
    )

    content = "flowchart LR\nA['Метка'] --> B[\"Другой\"]\n"
    actual = replace_mermaid_labels(content, {0: 'User\'s "end"', 1: "Other"})
    assert actual == "flowchart LR\nA['User#39;s #quot;#101;nd#quot;'] --> B[\"Other\"]\n"
    assert mermaid_skeleton(actual) == mermaid_skeleton(content)
    assert replace_mermaid_labels(actual, {s.index: s.text for s in mermaid_labels(actual)}) == actual


def test_main_flowchart_labels_comments_and_init_keep_immutable_syntax():
    from ydbdoc_review.segmentation.mermaid import mermaid_labels, mermaid_skeleton

    source = (
        '```mermaid\nflowchart LR\n%%{init: {"theme": "base"}}%%\n'
        '  A[Короткая подпись] --> B["Финиш"]\n  %% Комментарий\n```\n'
    )
    target = (
        '```mermaid\nflowchart LR\n%%{init: {"theme": "base"}}%%\n'
        '  A[A much longer label] --> B["Finished"]\n  %% Comment\n```\n'
    )
    source_doc = parse_markdown(source)
    target_doc = parse_markdown(target)
    segments = extract_segments(source_doc)
    assert [s.text for s in segments] == ["Короткая подпись", "Финиш", "Комментарий"]
    translations = dict(zip((s.id for s in segments), ["A much longer label", "Finished", "Comment"], strict=True))
    assert str(render_with_translations(source_doc, segments, translations)).strip() == target.strip()
    content = target_doc.children[0].content
    assert mermaid_skeleton(parse_markdown(source).children[0].content) == mermaid_skeleton(content)
    for old, new in [('"base"', '"dark"'), ("A[", "C["), ("-->", "---"), ("LR", "TD")]:
        assert check_fence_body_copy(source, target.replace(old, new))
    assert mermaid_labels(content.replace('{"theme": "base"}', '{invalid}')) == ()


@pytest.mark.parametrize("replacement", ["Bad] --> C[Injected", "%%{init: {}}%%", "Bad\nend"])
def test_main_flowchart_label_and_comment_reject_injected_syntax(replacement):
    from ydbdoc_review.segmentation.mermaid import replace_mermaid_labels

    content = "flowchart LR\nA[Label]\n%% Comment\n"
    for index in (0, 1):
        with pytest.raises(ValueError):
            replace_mermaid_labels(content, {index: replacement})


@pytest.mark.parametrize("token", ["YDB", "life_time"])
def test_mermaid_replacement_requires_technical_token(token):
    from ydbdoc_review.segmentation.mermaid import replace_mermaid_labels

    with pytest.raises(ValueError, match="technical token"):
        replace_mermaid_labels(f"sequenceDiagram\na->>b: Значение {token}\n", {0: "Value"})


def test_mermaid_offsets_are_original_character_spans_and_nested_paths_reinsert():
    from ydbdoc_review.segmentation.mermaid import mermaid_labels

    source = "> ```MeRmAiD\n> sequenceDiagram\n> actor a as Пользователь\n> a->>a: Запрос\n> ```\n"
    doc = parse_markdown(source)
    block = doc.children[0].children[0]
    assert [label.text for label in mermaid_labels(block.content)] == ["Пользователь", "Запрос"]
    for label in mermaid_labels(block.content):
        assert block.content[label.start:label.end] == label.text
    segments = extract_segments(doc)
    assert [segment.ast_path for segment in segments] == [
        [0, 0, "mermaid_label", 0], [0, 0, "mermaid_label", 1],
    ]
    actual = str(render_with_translations(doc, segments, {"s0001": "User", "s0002": "Request"}))
    assert "actor a as User" in actual
    assert "a->>a: Request" in actual


def test_unsupported_mermaid_grammar_remains_protected_and_warns_on_russian():
    from ydbdoc_review.harness import TRANSLATE_PROFILE, FileHarness, FileRunState, HarnessContext

    source = "```mermaid\nclassDiagram\nclass Пользователь\n```\n"
    assert extract_segments(parse_markdown(source)) == []
    client = MagicMock()
    client.chat.side_effect = AssertionError("unsupported diagram must not call the model")
    result = FileHarness(TRANSLATE_PROFILE).run(
        FileRunState(mode="translate", file_path="asset.md", raw_source_text=source,
                     source_text=source), HarnessContext.from_options(client),
    )
    assert result.verdict == "warnings"
    assert result.final_text == source


@pytest.mark.parametrize("stem", ["user-token", "user-token-lifecycle"])
def test_real_pair_translator_receives_labels_not_mermaid_lines(stem):
    from ydbdoc_review.config.loader import load_config
    from ydbdoc_review.harness import HarnessContext
    from ydbdoc_review.harness.pair import run_pair_plan
    from ydbdoc_review.llm.client import YandexLLMClient
    from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
    from ydbdoc_review.pipeline.pairs import DocPair
    from ydbdoc_review.translation.glossary import load_glossary

    source = (FIXTURES / f"{stem}.ru.md").read_text()
    target = (FIXTURES / f"{stem}.en.md").read_text()
    source_segments = extract_segments(parse_markdown(source))
    target_segments = extract_segments(parse_markdown(target))
    mapping = {s.id: t.text for s, t in zip(source_segments, target_segments, strict=True)}
    received = []
    reviewed = []

    def completion(**kwargs):
        user_message = kwargs["messages"][-1]["content"]
        batch_json = re.search(r'\{\s*"segments"\s*:', user_message)
        assert batch_json is not None
        payload, _ = json.JSONDecoder().raw_decode(user_message[batch_json.start():])
        if all("translated_text" in s for s in payload["segments"]):
            reviewed.extend(payload["segments"])
            result = {"verdict": "ok", "issues": []}
        else:
            received.extend(payload["segments"])
            result = {"segments": [{"id": s["id"], "text": mapping[s["id"]]}
                                   for s in payload["segments"]]}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    config = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    transport = MagicMock()
    transport.chat.completions.create.side_effect = completion
    client = YandexLLMClient(folder_id="b1x", api_key="k", llm=config.llm, client=transport)
    pair = DocPair(ru_path=f"ydb/docs/ru/core/security/_assets/{stem}.md",
                   en_path=f"ydb/docs/en/core/security/_assets/{stem}.md", ru_changed=True)
    plan = PairPlan(pair=pair, action="translate_to_en", source_path=pair.ru_path,
                    target_path=pair.en_path, source_lang="ru", target_lang="en")
    result = run_pair_plan(
        PairContent(pair=pair, ru_text=source, en_text=None), plan,
        HarnessContext.from_options(client, glossary=load_glossary(), config=config), {},
    )
    assert result.error is None
    assert result.target_text.strip() == target.strip()
    assert received
    assert [s["translated_text"] for s in reviewed] == [s.text for s in target_segments]
    assert [s["text"] for s in received] == [s.text for s in source_segments]
    assert all("->" not in s["text"] and "participant " not in s["text"] for s in received)


@pytest.mark.parametrize("stem", ["user-token", "user-token-lifecycle"])
def test_repeated_finalization_preserves_translated_mermaid_labels(stem):
    from ydbdoc_review.harness.render import finalize_en_target_result

    source = (FIXTURES / f"{stem}.ru.md").read_text()
    target = (FIXTURES / f"{stem}.en.md").read_text()
    for _ in range(3):
        target = str(finalize_en_target_result(target, source))
    assert target.strip() == (FIXTURES / f"{stem}.en.md").read_text().strip()
