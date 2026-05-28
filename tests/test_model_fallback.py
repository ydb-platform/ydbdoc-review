from ydbdoc_review.llm import (
    _expand_model_candidates,
    _fm_model_not_found,
    translation_model_fallbacks,
    translation_verify_model_fallbacks,
)


def test_expand_chains_primary_then_fallbacks_dedup():
    chain = _expand_model_candidates(
        "qwen3.6-35b-a3b",
        ("deepseek-v3.2/latest", "qwen3.6-35b-a3b"),
    )
    assert chain[0] == "qwen3.6-35b-a3b"
    assert "deepseek-v3.2/latest" in chain
    # primary must not appear twice even if listed in fallbacks
    assert chain.count("qwen3.6-35b-a3b") == 1


def test_fm_model_not_found_detection():
    assert _fm_model_not_found(
        RuntimeError("chat.completions failed: Failed to get model")
    )


def test_verify_fallbacks_default_is_non_yandex(monkeypatch):
    monkeypatch.delenv("YDBDOC_MODEL_VERIFY_FALLBACKS", raising=False)
    fb = translation_verify_model_fallbacks()
    assert fb, "default fallbacks must not be empty"
    for slug in fb:
        assert "yandex" not in slug.lower(), (
            f"critic fallback {slug!r} must not share family with translate model"
        )


def test_translate_fallbacks_default_includes_yandex(monkeypatch):
    monkeypatch.delenv("YDBDOC_MODEL_TRANSLATE_FALLBACKS", raising=False)
    fb = translation_model_fallbacks()
    assert "yandexgpt-5.1" in fb
    chain = _expand_model_candidates("deepseek-v3.2/latest", fb)
    assert chain[0] == "deepseek-v3.2/latest"
    assert "yandexgpt-5.1" in chain
