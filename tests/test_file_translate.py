from unittest.mock import MagicMock, patch

from ydbdoc_review.file_translate import translate_document_file_level


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
