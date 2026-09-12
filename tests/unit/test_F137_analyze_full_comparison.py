"""F-137 contracts for complete, evidence-based Analyze input."""

from __future__ import annotations

from ydbdoc_review.ops.feedback_ctx import continue_feedback_scope
from ydbdoc_review.pipeline.analyze import (
    PairContent,
    _pair_to_analyze_payload,
    analyze_payload_is_complete,
)
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.prompts import build_analyze_messages


def test_F137_complete_inputs() -> None:
    ru = "Заголовок\n" + ("текст документа\n" * 1200) + "ТЕХНИЧЕСКИЙ-КОНЕЦ-44268"
    en = "Heading\n" + ("document text\n" * 1200) + "TECHNICAL-END-44268"
    content = PairContent(
        pair=DocPair("docs/ru/a.md", "docs/en/a.md", ru_changed=True),
        ru_text=ru,
        en_text=en,
        ru_diff_vs_base="format-only\n" + "ru-diff-end",
        en_diff_vs_base="format-only\n" + "en-diff-end",
    )

    payload = _pair_to_analyze_payload(content)
    assert analyze_payload_is_complete(content)
    assert payload["source_lang"] == "ru"
    assert payload["target_lang"] == "en"
    assert payload["ru_text"] == ru
    assert payload["en_text"] == en
    assert payload["ru_diff_vs_base"] == content.ru_diff_vs_base
    assert payload["en_diff_vs_base"] == content.en_diff_vs_base

    with continue_feedback_scope("Continue: preserve the approved canonical map."):
        messages = build_analyze_messages([payload], load_glossary())
    system = messages[0]["content"]
    user = messages[1]["content"]
    assert isinstance(system, str) and "GLOSSARY:" in system
    assert "approved canonical map" in system
    assert isinstance(user, str)
    assert "ТЕХНИЧЕСКИЙ-КОНЕЦ-44268" in user
    assert "TECHNICAL-END-44268" in user
    assert "source_lang" in user and "target_lang" in user


def test_F137_evidence() -> None:
    technical_ru = "Описание\n" + ("пример\n" * 1500) + "timeout=15, rows=42\n- required item"
    technical_en = "Description\n" + ("example\n" * 1500) + "timeout=30, rows=42\n- required item"
    technical = _pair_to_analyze_payload(
        PairContent(
            pair=DocPair("ru/tech.md", "en/tech.md", ru_changed=True),
            ru_text=technical_ru,
            en_text=technical_en,
        )
    )
    assert technical["ru_text"] != technical["en_text"]
    assert "timeout=15" in technical["ru_text"]
    assert "timeout=30" in technical["en_text"]
    assert "required item" in technical["ru_text"]

    formatting = _pair_to_analyze_payload(
        PairContent(
            pair=DocPair("ru/a.md", "en/a.md", ru_changed=True),
            ru_text="Same meaning\n",
            en_text="Same meaning\n",
            ru_diff_vs_base="Same meaning\n",
            en_diff_vs_base="Same meaning  \n",
        )
    )
    assert formatting["ru_text"] == formatting["en_text"]
    assert formatting["ru_diff_vs_base"] != formatting["en_diff_vs_base"]
