import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)
from ydbdoc_review.translation.one_pass import (
    OnePassTranslationError,
    assert_no_protect_token,
    translate_ru_to_en_once,
)

MANIFEST = TranslationJobManifest(TranslationModelPolicy(
    translate=ModelPair("translate-primary", "translate-fallback"),
    critic=ModelPair("critic-primary", "critic-fallback"),
    repair=ModelPair("repair-primary", "repair-fallback"),
))


class TranslatingClient:
    def chat_once(self, messages, *, explicit_model, role, **kwargs):
        if role == "critic":
            return SimpleNamespace(content=json.dumps({"findings": []}))
        payload = json.loads(messages[-1]["content"])
        translated = []
        for segment in payload["segments"]:
            text = segment["text"].replace("Заголовок", "Heading")
            text = text.replace("Смотрите", "See").replace("страницу", "page")
            text = text.replace("Текст", "Text")
            text = text.replace("локально", "locally").replace(" и ", " and ")
            text = text.replace("Внимание", "Attention").replace(
                "Подробнее", "Details"
            )
            translated.append({"id": segment["id"], "text": text})
        return SimpleNamespace(content=json.dumps({"segments": translated}))


def test_source_owned_atoms_round_trip_without_exposure_to_model():
    source = (
        "## Заголовок {#точный-якорь}\n\n"
        "Смотрите [страницу `cmd`](../node.md?q=1#frag) и [локально](#точный-якорь).\n\n"
        "Текст `inline --flag`.\n\n"
        "```yaml title=пример\nключ: значение\n```\n\n"
        "{% include [часть](../_includes/example.md) %}\n"
        "{% note warning \"Внимание\" %}\n\nТекст.\n\n{% endnote %}\n"
        "{% cut \"Подробнее\" %}\n\nТекст.\n\n{% endcut %}\n"
    )
    client = TranslatingClient()
    result = translate_ru_to_en_once(
        source, client, file_path="ydb/docs/ru/a/page.md", manifest=MANIFEST
    )

    assert "{#heading}" in result.text
    assert "[page `cmd`](../node.md?q=1#frag)" in result.text
    assert "(#heading)" in result.text
    assert "`inline --flag`" in result.text
    assert "```yaml title=пример\nключ: значение\n```" in result.text
    assert "{% include [часть](../_includes/example.md) %}" in result.text
    assert '{% note warning "Attention" %}' in result.text
    assert '{% cut "Details" %}' in result.text
    assert "⟦" not in result.text


@pytest.mark.parametrize(
    "leak",
    [
        "⟦U1⟧",
        "&#10214;U1&#10215;",
        quote("⟦U1⟧"),
    ],
)
def test_protect_token_leaks_are_blocking(leak):
    with pytest.raises(OnePassTranslationError, match="unrestored_protect_token"):
        assert_no_protect_token(f"before {leak} after")


def test_parser_owned_link_wrappers_map_only_absolute_ru_locale_path():
    source = "Смотрите [страницу](/ru/core/a.md?x=1#top) и [локально](../ru/a.md).\n"
    result = translate_ru_to_en_once(
        source, TranslatingClient(), file_path="ydb/docs/ru/a.md", manifest=MANIFEST
    )
    assert "[page](/en/core/a.md?x=1#top)" in result.text
    assert "[locally](../ru/a.md)" in result.text


def test_strike_and_image_wrappers_are_restored_once():
    source = "~~Текст *Текст*~~ и ![Заголовок](image.png =100x200 \"title\").\n"
    result = translate_ru_to_en_once(
        source, TranslatingClient(), file_path="ydb/docs/ru/a.md", manifest=MANIFEST
    )
    assert "~~Text *Text*~~" in result.text
    assert "![Heading](image.png =100x200 \"title\")" in result.text
