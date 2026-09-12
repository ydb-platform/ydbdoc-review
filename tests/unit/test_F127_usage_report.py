from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.usage import LLMUsage, UsageTracker
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.reporting.builder import _usage_lines


def _cfg():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})


def test_F127_outcomes() -> None:
    tracker = UsageTracker(
        [
            LLMUsage("yandexgpt-5.1", 100, 20, 1.0, 0, True, role="analyze"),
            LLMUsage("deepseek-v4-flash", 200, 40, 1.0, 1, True, role="translate"),
            LLMUsage("gpt-oss-120b", 80, 10, 1.0, 0, True, role="critic"),
            LLMUsage("gpt-oss-20b", 50, 5, 1.0, 0, False, role="repair"),
        ]
    )
    body = "\n".join(_usage_lines(_cfg(), PRTranslationResult(), tracker))

    assert "Оценка стоимости" in body
    assert "Токены (analyze): 100 / 20" in body
    assert "Токены (перевод): 200 / 40" in body
    assert "Токены (критик): 80 / 10" in body
    assert "Токены (repair): 50 / 5" in body
    assert "analyze=`yandexgpt-5.1`" in body
    assert "repair=`gpt-oss-20b`" in body


def test_F127_privacy_detail() -> None:
    body = "\n".join(_usage_lines(_cfg(), PRTranslationResult(), UsageTracker()))

    assert "Токены (analyze): 0 / 0" in body
    assert "Оценка стоимости: ₽0.00" in body
    assert "prompt text" not in body
    assert "response text" not in body
    assert "api-token-secret" not in body
    assert "https://private.example/context" not in body
