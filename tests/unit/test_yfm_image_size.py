"""Tests for Diplodoc image size syntax: ![alt](src =WxH)."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import InlineImage
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown


def round_trip(text: str) -> str:
    return render_markdown(parse_markdown(text))


def assert_stable(text: str) -> None:
    first = round_trip(text)
    second = round_trip(first)
    assert first == second, f"\nFirst:\n{first!r}\nSecond:\n{second!r}"


def test_image_with_full_size_ast():
    text = "![alt](image.png =100x200)\n"
    doc = parse_markdown(text)
    para = doc.children[0]
    img = para.children[0]
    assert isinstance(img, InlineImage)
    assert img.src == "image.png"
    assert img.width == "100"
    assert img.height == "200"


def test_image_with_width_only():
    text = "![alt](image.png =100x)\n"
    doc = parse_markdown(text)
    img = doc.children[0].children[0]
    assert isinstance(img, InlineImage)
    assert img.src == "image.png"
    assert img.width == "100"
    assert img.height is None


def test_image_with_height_only():
    text = "![alt](image.png =x200)\n"
    doc = parse_markdown(text)
    img = doc.children[0].children[0]
    assert isinstance(img, InlineImage)
    assert img.src == "image.png"
    assert img.width is None
    assert img.height == "200"


def test_image_without_size():
    text = "![alt](image.png)\n"
    doc = parse_markdown(text)
    img = doc.children[0].children[0]
    assert isinstance(img, InlineImage)
    assert img.width is None
    assert img.height is None


@pytest.mark.parametrize(
    "text",
    [
        "![alt](image.png)\n",
        "![alt](image.png =100x200)\n",
        "![alt](image.png =100x)\n",
        "![alt](image.png =x200)\n",
        '![alt](image.png =100x200 "Title")\n',
    ],
)
def test_round_trip_images(text: str):
    assert_stable(text)

