from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.file_translate import (
    translate_document_file_level,
    translate_text_with_plan,
)


def test_file_translate_single_request():
    ru = "### Заголовок {#t}\n\nПривет мир.\n"

    def fake_units(_s, units, **_k):
        return {
            u.unit_id: "### Title {#t}" if u.source_line.startswith("###")
            else "Hello world."
            for u in units
        }

    with patch(
        "ydbdoc_review.placeholder_translate.translate_line_units",
        side_effect=fake_units,
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
    assert "placeholder" in mode
    assert "Hello" in out
    assert "Привет" not in out


def test_file_translate_scoped_sections():
    padding = "\n".join(f"p{i}" for i in range(30))
    ru = f"pre\n\n### A\nru-a\n\n### B\nсекция ru-b\n{padding}\n"
    en_main = f"pre\n\n### A\nen-a\n\n### B\nen-b\n{padding}\n"
    diff = "@@ -7,1 +7,1 @@\n-секция ru-b\n+секция ru-b kafka\n"

    call_count = 0

    def fake_units(_s, units, **_k):
        nonlocal call_count
        if any("секция ru-b" in u.source_line for u in units):
            call_count += 1
            return {u.unit_id: "TRANSLATED-SECTION" for u in units}
        return {u.unit_id: u.source_line for u in units}

    with (
        patch(
            "ydbdoc_review.placeholder_translate.translate_line_units",
            side_effect=fake_units,
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
    def fake_units(_settings, units, **_kwargs):
        out = {}
        for u in units:
            if "Вступление" in u.source_line:
                out[u.unit_id] = "Intro EN."
            elif "Заключение" in u.source_line:
                out[u.unit_id] = "Outro EN."
            else:
                out[u.unit_id] = u.source_line
        return out

    with (
        patch(
            "ydbdoc_review.placeholder_translate.translate_line_units",
            side_effect=fake_units,
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

    def fake_units(_settings, units, **_kwargs):
        out = {}
        for u in units:
            if "Intro" in u.source_line:
                out[u.unit_id] = "Intro EN."
            elif "Outro" in u.source_line:
                out[u.unit_id] = "Outro EN."
            elif "Вручную" in u.source_line:
                out[u.unit_id] = "- Manually"
            elif "Запустите" in u.source_line:
                out[u.unit_id] = "Start the service."
            else:
                out[u.unit_id] = u.source_line
        return out

    with (
        patch(
            "ydbdoc_review.placeholder_translate.translate_line_units",
            side_effect=fake_units,
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


def test_placeholder_default_without_legacy_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("YDBDOC_LEGACY_ANNOTATED", raising=False)
    with patch(
        "ydbdoc_review.file_translate.translate_with_placeholders",
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
    assert "placeholder" in mode
