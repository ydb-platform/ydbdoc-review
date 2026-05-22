from ydbdoc_review.llm import (
    _expand_model_candidates,
    _fm_model_not_found,
    translation_verify_model_fallbacks,
)


def test_expand_deepseek_candidates():
    chain = _expand_model_candidates(
        "deepseek-v4-flash", ("yandexgpt/latest",)
    )
    assert chain[0] == "deepseek-v4-flash"
    assert "yandexgpt/latest" in chain
    assert "deepseek-v4-flash/latest" in chain


def test_fm_model_not_found_detection():
    assert _fm_model_not_found(
        RuntimeError("chat.completions failed: Failed to get model")
    )


def test_verify_fallbacks_default():
    assert "yandexgpt/latest" in translation_verify_model_fallbacks()
