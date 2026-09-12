"""Parser tests for PR #44880 locale include fragments (§6.80.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ydbdoc_review.parsing.ast_types import BulletList
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "44880"


@pytest.mark.parametrize(
    "name",
    ["export-additional-params.ru.md", "import-additional-params.ru.md"],
)
def test_parse_44880_bullet_list_include_fragment(name: str):
    text = (_FIXTURES / name).read_text(encoding="utf-8")
    doc = parse_markdown(text)
    assert doc.children
    assert isinstance(doc.children[0], BulletList)
    assert len(doc.children[0].children) >= 3


@pytest.mark.parametrize(
    "name",
    ["export-additional-params.ru.md", "import-additional-params.ru.md"],
)
def test_extract_segments_44880_include_fragment(name: str):
    text = (_FIXTURES / name).read_text(encoding="utf-8")
    segments = extract_segments(parse_markdown(text))
    assert len(segments) >= 3
