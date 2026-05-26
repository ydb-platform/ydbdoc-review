from unittest.mock import MagicMock, patch

from ydbdoc_review.file_translate import (
    translate_document_file_level,
    translate_text_with_plan,
)


def test_file_translate_single_request():
    ru = "### Title {#t}\n\nHello world.\n"
    with patch(
        "ydbdoc_review.file_translate._translate_one_chunk",
        return_value="### Title {#t}\n\nHello world EN.\n",
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
    assert "file-plan" in mode
    assert "EN" in out or "Hello" in out


def test_file_translate_scoped_sections():
    padding = "\n".join(f"p{i}" for i in range(30))
    ru = f"pre\n\n### A\nru-a\n\n### B\nru-b\n{padding}\n"
    en_main = f"pre\n\n### A\nen-a\n\n### B\nen-b\n{padding}\n"
    diff = "@@ -7,1 +7,1 @@\n-ru-b\n+ru-b kafka\n"

    call_count = 0

    def fake_chunk(*_a, **_k):
        nonlocal call_count
        call_count += 1
        return "TRANSLATED-SECTION"

    with (
        patch(
            "ydbdoc_review.file_translate._translate_one_chunk",
            side_effect=fake_chunk,
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


def test_list_tabs_blocks_copied_without_llm():
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
    chunk_calls: list[str] = []

    def fake_chunk(_settings, *, chunk, **_kwargs):
        chunk_calls.append(chunk.source_text)
        if "Вступление" in chunk.source_text:
            return "Intro EN.\n\n"
        return "Outro EN.\n"

    with (
        patch(
            "ydbdoc_review.file_translate._translate_one_chunk",
            side_effect=fake_chunk,
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
    assert llm_calls == 2
    assert "- mirror-3-dc-3nodes" in out
    assert "- mirror-3-dc-9nodes" in out
    assert "services_enabled:" in out
    assert "\n      - legacy\n" not in out
    assert out.count("```yaml") == 2
    assert "Intro EN." in out
    assert "Outro EN." in out
    assert all("{% list tabs" not in src for src in chunk_calls)
