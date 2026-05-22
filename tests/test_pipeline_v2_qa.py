"""Pipeline v2 QA must not run legacy deterministic_prepare / cyrillic storms."""

from types import SimpleNamespace
from unittest.mock import patch

from ydbdoc_review.translation_qa import PairQaOutcome, run_pair_qa_repair


def _settings():
    return SimpleNamespace(
        model_translate="yandexgpt-5.1",
        model_translation_verify="yandexgpt/latest",
    )


def test_run_pair_qa_repair_uses_v2_without_deterministic_prepare(monkeypatch):
    monkeypatch.delenv("YDBDOC_PIPELINE", raising=False)

    def boom(*_a, **_k):
        raise AssertionError("legacy deterministic_prepare_en must not run under v2")

    with patch(
        "ydbdoc_review.translation_qa.deterministic_prepare_en",
        side_effect=boom,
    ):
        with patch(
            "ydbdoc_review.pipeline_v2.verify_translation_pair",
            return_value="### Блокеры\n_Блокеров нет._\n",
        ) as critic:
            with patch(
                "ydbdoc_review.pipeline_v2.confirm_repair_pair",
                return_value="### Вердикт файла\n**ПРИНИМАТЬ**\n\n### Оставшиеся проблемы\n_Нет._\n",
            ) as translator:
                out, outcome = run_pair_qa_repair(
                    _settings(),  # type: ignore[arg-type]
                    ru_path="ydb/docs/ru/x.md",
                    en_path="ydb/docs/en/x.md",
                    target_path="ydb/docs/en/x.md",
                    source_text="# RU\n",
                    translated_text="# EN\n",
                    source_lang="Russian",
                    target_lang="English",
                    repair_enabled=True,
                )

    assert out.startswith("# EN")
    assert isinstance(outcome, PairQaOutcome)
    critic.assert_called_once()
    translator.assert_called_once()
