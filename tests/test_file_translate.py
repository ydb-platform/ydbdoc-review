from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.file_translate import (
    translate_document_file_level,
    translate_text_with_plan,
)


def test_file_translate_single_request():
    ru = "### Заголовок {#t}\n\nПривет мир.\n"

    def fake_masked(_s, masked, **_k):
        return (
            masked.replace("Заголовок", "Title")
            .replace("Привет мир", "Hello world")
            .replace("Привет", "Hello")
        )

    with patch(
        "ydbdoc_review.masked_translate.translate_masked_chunk",
        side_effect=fake_masked,
    ) as mocked:
        out, mode = translate_document_file_level(
            MagicMock(),
            source_path="ydb/docs/ru/core/x.md",
            source_full=ru,
            source_lang="Russian",
            target_lang="English",
            en_on_main=None,
            ru_pr_diff=None,
        )
    assert mocked.call_count >= 1
    assert "masked" in mode
    assert "Hello" in out
    assert "Привет" not in out


def test_file_translate_scoped_sections():
    padding = "\n".join(f"p{i}" for i in range(30))
    ru = f"pre\n\n### A\nru-a\n\n### B\nсекция ru-b\n{padding}\n"
    en_main = f"pre\n\n### A\nen-a\n\n### B\nen-b\n{padding}\n"
    diff = "@@ -7,1 +7,1 @@\n-секция ru-b\n+секция ru-b kafka\n"

    call_count = 0

    def fake_masked(_s, masked, **_k):
        nonlocal call_count
        if "секция ru-b" in masked:
            call_count += 1
            return "TRANSLATED-SECTION"
        return masked

    with (
        patch(
            "ydbdoc_review.masked_translate.translate_masked_chunk",
            side_effect=fake_masked,
        ),
        patch(
            "ydbdoc_review.file_translate.apply_en_postprocess_from_ru",
            side_effect=lambda _ru, en: en,
        ),
    ):
        out, mode = translate_document_file_level(
            MagicMock(),
            source_path="ydb/docs/ru/core/x.md",
            source_full=ru,
            source_lang="Russian",
            target_lang="English",
            en_on_main=en_main,
            ru_pr_diff=diff,
        )
    assert "file-plan-scoped" in mode
    assert "en-a" in out
    assert "TRANSLATED-SECTION" in out
    assert call_count == 1


def test_config_list_tabs_copied_without_llm():
    ru = (
        "Вступление RU.\n\n"
        "{% list tabs %}\n\n"
        "- mirror-3-dc-3nodes\n\n"
        "  ```yaml\n"
        "  services_enabled:\n"
        "  - legacy\n"
        "  ```\n\n"
        "- mirror-3-dc-9nodes\n\n"
        "  ```yaml\n"
        "  x: 1\n"
        "  ```\n\n"
        "{% endlist %}\n\n"
        "Заключение RU.\n"
    )
    def fake_masked(_settings, masked, **_kwargs):
        return (
            masked.replace("Вступление RU.", "Intro EN.")
            .replace("Заключение RU.", "Outro EN.")
        )

    with (
        patch(
            "ydbdoc_review.masked_translate.translate_masked_chunk",
            side_effect=fake_masked,
        ),
        patch(
            "ydbdoc_review.file_translate.apply_en_postprocess_from_ru",
            side_effect=lambda _ru, en: en,
        ),
    ):
        out, llm_calls = translate_text_with_plan(
            MagicMock(),
            source_path="ydb/docs/ru/core/x.md",
            source_text=ru,
            source_lang="Russian",
            target_lang="English",
        )
    assert llm_calls >= 1
    assert "- mirror-3-dc-3nodes" in out
    assert "- mirror-3-dc-9nodes" in out
    assert "services_enabled:" in out
    assert out.count("```yaml") == 2
    assert "Intro EN." in out
    assert "Outro EN." in out


def test_manual_list_tabs_translated_not_copied():
    ru = (
        "Intro RU.\n\n"
        "{% list tabs group=manual-systemd %}\n\n"
        "- Вручную\n\n"
        "Запустите сервис.\n\n"
        "{% endlist %}\n\n"
        "Outro RU.\n"
    )

    def fake_masked(_settings, masked, **_kwargs):
        return (
            masked.replace("Intro RU.", "Intro EN.")
            .replace("Outro RU.", "Outro EN.")
            .replace("Вручную", "Manually")
            .replace("Запустите сервис.", "Start the service.")
        )

    with (
        patch(
            "ydbdoc_review.masked_translate.translate_masked_chunk",
            side_effect=fake_masked,
        ),
        patch(
            "ydbdoc_review.file_translate.apply_en_postprocess_from_ru",
            side_effect=lambda _ru, en: en,
        ),
    ):
        out, llm_calls = translate_text_with_plan(
            MagicMock(),
            source_path="ydb/docs/ru/core/x.md",
            source_text=ru,
            source_lang="Russian",
            target_lang="English",
        )
    assert "- Manually" in out
    assert "Вручную" not in out
    assert llm_calls >= 1


def test_legacy_annotated_translate_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YDBDOC_LEGACY_ANNOTATED", "true")
    ru = "### Заголовок\n\nТекст RU.\n"
    with patch(
        "ydbdoc_review.file_translate._translate_annotated_file",
        return_value=("### Title\n\nText EN.\n", 2),
    ) as mocked:
        out, llm = translate_text_with_plan(
            MagicMock(),
            source_path="x.md",
            source_text=ru,
            source_lang="Russian",
            target_lang="English",
        )
    mocked.assert_called_once()
    assert out == "### Title\n\nText EN.\n"
    assert llm == 2


def test_legacy_annotated_document_mode_label(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YDBDOC_LEGACY_ANNOTATED", "1")
    ru = "### Заголовок {#t}\n\nПривет.\n"
    with (
        patch(
            "ydbdoc_review.file_translate._translate_annotated_file",
            return_value=("EN body", 1),
        ),
        patch(
            "ydbdoc_review.file_translate.apply_en_postprocess_from_ru",
            side_effect=lambda _ru, en: en,
        ),
    ):
        _out, mode = translate_document_file_level(
            MagicMock(),
            source_path="ydb/docs/ru/core/x.md",
            source_full=ru,
            source_lang="Russian",
            target_lang="English",
            en_on_main=None,
            ru_pr_diff=None,
        )
    assert "annotated" in mode
    assert "placeholder" not in mode


def test_masked_default_without_legacy_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("YDBDOC_LEGACY_ANNOTATED", raising=False)
    monkeypatch.delenv("YDBDOC_LEGACY_LINE_JSON", raising=False)
    with patch(
        "ydbdoc_review.file_translate.translate_with_mask",
        return_value=("EN", 1),
    ) as mocked:
        _out, mode = translate_document_file_level(
            MagicMock(),
            source_path="x.md",
            source_full="RU",
            source_lang="Russian",
            target_lang="English",
            en_on_main=None,
            ru_pr_diff=None,
        )
    mocked.assert_called_once()
    assert "masked" in mode


def test_legacy_line_json_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YDBDOC_LEGACY_LINE_JSON", "true")
    monkeypatch.delenv("YDBDOC_LEGACY_ANNOTATED", raising=False)
    with patch(
        "ydbdoc_review.file_translate.translate_with_placeholders",
        return_value=("EN", 1),
    ) as mocked:
        out, llm = translate_text_with_plan(
            MagicMock(),
            source_path="x.md",
            source_text="RU",
            source_lang="Russian",
            target_lang="English",
        )
    mocked.assert_called_once()
    assert out == "EN"
    assert llm == 1
