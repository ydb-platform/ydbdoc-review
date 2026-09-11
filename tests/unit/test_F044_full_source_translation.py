"""F-044 contracts for complete, source-only file translation input."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.segmentation.chunker import chunk_segments
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.prompts import segments_to_batch_json


def _unit_config():
    return load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
        }
    )


def test_F044_coverage() -> None:
    source_parts = ["SOURCE-FIRST", "SOURCE-SECOND", "SOURCE-THIRD"]
    target_text = "OLD-TARGET-MUST-NOT-BE-A-SOURCE"
    source_segments = extract_segments(
        parse_markdown("\n\n".join(source_parts) + "\n")
    )

    batches = chunk_segments(source_segments, max_chars=len("\n\n".join(source_parts)))
    payloads = [json.loads(segments_to_batch_json(batch.segments)) for batch in batches]
    translated_input = [
        item["text"]
        for payload in payloads
        for item in payload["segments"]
    ]

    assert translated_input == source_parts
    assert len(translated_input) == len(set(translated_input))
    assert target_text not in json.dumps(payloads, ensure_ascii=False)


def test_F044_no_prose() -> None:
    technical_file = "```yaml\nkey: value\n```\n"
    empty_include = "{% include [details](_includes/details.md) %}\n"
    client = MagicMock()

    for source in (technical_file, empty_include):
        result = translate_file(
            source,
            client,
            load_glossary(),
            file_path="docs/ru/technical.md",
            config=_unit_config(),
        )
        assert result.segments_count == 0
        assert result.final_text == source
        assert result.verdict == "ok"

    client.chat.completions.create.assert_not_called()
