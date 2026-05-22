"""Cyrillic leak detection for EN translations."""

from ydbdoc_review.translate_postprocess import (
    cyrillic_repair_enabled,
    en_contains_cyrillic,
    en_contains_cyrillic_prose,
)


def test_en_contains_cyrillic():
    assert en_contains_cyrillic("Hello мир")
    assert not en_contains_cyrillic("Hello world")


def test_en_contains_cyrillic_prose_ignores_fenced_code():
    assert en_contains_cyrillic_prose("Hello мир")
    assert not en_contains_cyrillic_prose("Hello world")
    md = """# Title

English paragraph.

```bash
# комментарий на русском
echo ok
```
"""
    assert not en_contains_cyrillic_prose(md)
    assert en_contains_cyrillic(md)


def test_cyrillic_repair_enabled_legacy_default(monkeypatch):
    monkeypatch.setenv("YDBDOC_PIPELINE", "legacy")
    assert cyrillic_repair_enabled()


def test_cyrillic_repair_disabled_under_pipeline_v2(monkeypatch):
    monkeypatch.delenv("YDBDOC_PIPELINE", raising=False)
    assert not cyrillic_repair_enabled()
