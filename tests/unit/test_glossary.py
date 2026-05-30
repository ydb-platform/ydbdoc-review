"""Tests for glossary loader and prompt formatting."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from ydbdoc_review.translation.glossary import Glossary, GlossaryEntry, load_glossary


def test_load_default_glossary():
    glossary = load_glossary()
    assert len(glossary.entries) >= 20
    assert any(e.ru == "параметризованный запрос" for e in glossary.entries)
    assert any(e.term == "YDB" and e.do_not_translate for e in glossary.entries)


def test_load_from_custom_yaml(tmp_path: Path):
    yaml_text = dedent("""
        - ru: "тест"
          en: "test"
        - term: "HTTP"
          do_not_translate: true
    """).strip()
    path = tmp_path / "glossary.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    glossary = load_glossary(path)
    assert len(glossary.entries) == 2
    assert glossary.entries[0].ru == "тест"
    assert glossary.entries[1].term == "HTTP"


def test_bilingual_entry_requires_en():
    with pytest.raises(ValueError, match="both ru and en"):
        GlossaryEntry(ru="только ru")


def test_term_entry_rejects_ru_en():
    with pytest.raises(ValueError, match="must not also set ru/en"):
        GlossaryEntry(term="X", ru="a", en="b")


def test_invalid_yaml_root_raises(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("not_a_list: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        load_glossary(path)


def test_to_prompt_yaml_contains_entries():
    glossary = Glossary(
        entries=[
            GlossaryEntry(ru="узел", en="node", aliases_ru=["узла"]),
            GlossaryEntry(term="SQL", do_not_translate=True),
        ]
    )
    text = glossary.to_prompt_yaml()
    assert "ru: узел" in text
    assert "en: node" in text
    assert "aliases_ru:" in text
    assert "term: SQL" in text
    assert "do_not_translate: true" in text


def test_to_prompt_dicts_omits_empty_optional_fields():
    glossary = Glossary(entries=[GlossaryEntry(ru="a", en="b")])
    row = glossary.to_prompt_dicts()[0]
    assert row == {"ru": "a", "en": "b"}
