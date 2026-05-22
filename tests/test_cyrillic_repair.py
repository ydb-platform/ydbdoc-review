"""Cyrillic leak detection for EN translations."""

from ydbdoc_review.translate_postprocess import (
    cyrillic_repair_enabled,
    en_contains_cyrillic,
)


def test_en_contains_cyrillic_prose():
    assert en_contains_cyrillic("Hello мир")
    assert not en_contains_cyrillic("Hello world")


def test_cyrillic_repair_enabled_default():
    assert cyrillic_repair_enabled()
