"""F-115: manual target edits are editable context, not protected bytes."""

from __future__ import annotations

import json

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.skip_paths import filter_translate_changes
from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.critic import apply_critic_fixes
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.prompts import build_verify_batch_messages
from ydbdoc_review.translation.schemas import CriticIssueOut
from ydbdoc_review.validation.cli_tokens import cli_tokens_preserved
from ydbdoc_review.validation.fence_integrity import fence_content_matches_source


def test_F115_visible_edit() -> None:
    source = "Source paragraph.\n"
    manual_edit = "Human's manually edited wording.\n"
    segment = extract_segments(parse_markdown(source))[0]

    messages = build_verify_batch_messages(
        Batch(index=0, segments=[segment]),
        {segment.id: manual_edit},
        [],
        load_glossary(),
        file_path="docs/en/page.md",
        batch_count=1,
    )
    prompt = messages[1]["content"]
    assert isinstance(prompt, str)
    assert manual_edit in json.loads(
        prompt.split("```json\n", 1)[1].split("\n```", 1)[0]
    )["segments"][0]["translated_text"]

    issue = CriticIssueOut(
        segment_id=segment.id,
        severity="warning",
        category="terminology",
        comment="Use the preferred wording.",
        suggested_text="Reworked wording.\n",
    )
    updated, applied, skipped = apply_critic_fixes(
        {segment.id: manual_edit}, [segment], [issue]
    )
    assert updated[segment.id] == "Reworked wording.\n"
    assert applied == [issue]
    assert skipped == []


def test_F115_protected_contract() -> None:
    excluded = "ydb/docs/ru/core/public-materials/legacy.md"
    allowed = "ydb/docs/ru/core/allowed.md"
    assert filter_translate_changes(
        [(excluded, "modified"), (allowed, "modified")],
        ["public-materials/**"],
    ) == [(allowed, "modified")]

    assert not fence_content_matches_source(
        "endpoint: grpcs://localhost:2135\n",
        "endpoint: https://example.com\n",
        fence_info="yaml",
    )
    assert cli_tokens_preserved(
        "Run `ydb --endpoint $YDB_ENDPOINT`.\n",
        "Run `ydb --endpoint $YDB_ENDPOINT`.\n",
    )

    source_segments = extract_segments(parse_markdown("Related source only.\n"))
    batch_json = json.loads(
        build_verify_batch_messages(
            Batch(index=0, segments=source_segments),
            {source_segments[0].id: "Current target."},
            [],
            load_glossary(),
            file_path="docs/en/related.md",
            batch_count=1,
        )[1]["content"].split("```json\n", 1)[1].split("\n```", 1)[0]
    )
    assert "Unrelated source must not leak." not in json.dumps(batch_json)
