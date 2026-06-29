"""Tests for config loader: YAML default + env overrides + secrets."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from ydbdoc_review.config.loader import Config, ModelChoice, load_config


# --- ModelChoice ---


def test_model_choice_chain_dedupes():
    mc = ModelChoice(primary="a", fallbacks=["b", "a", "c"])
    assert mc.chain == ["a", "b", "c"]


def test_model_choice_chain_no_fallbacks():
    mc = ModelChoice(primary="a")
    assert mc.chain == ["a"]


# --- Default load ---


def test_load_default_no_env():
    cfg = load_config(env={})
    assert cfg.llm.provider == "yandex"
    assert cfg.llm.base_url == "https://ai.api.cloud.yandex.net/v1"
    assert cfg.llm.temperature == 0.1
    assert cfg.llm.models.translate.primary == "deepseek-v32"
    assert cfg.llm.models.critic.primary == "deepseek-v32"
    assert cfg.llm.models.analyze.primary == "yandexgpt-5-lite"


def test_default_secrets_empty():
    cfg = load_config(env={})
    assert cfg.secrets.yc_folder_id is None
    assert cfg.secrets.yc_api_key is None


def test_require_yandex_raises_when_missing():
    cfg = load_config(env={})
    with pytest.raises(RuntimeError, match="folder id"):
        cfg.secrets.require_yandex()


# --- Secret resolution ---


def test_secrets_resolved_from_new_names():
    cfg = load_config(env={
        "YDBDOC_YC_FOLDER_ID": "b1xyz",
        "YDBDOC_YC_API_KEY": "AQVN_test",
    })
    folder, key = cfg.secrets.require_yandex()
    assert folder == "b1xyz"
    assert key == "AQVN_test"


def test_secrets_resolved_from_v1_names():
    cfg = load_config(env={
        "YANDEX_CLOUD_FOLDER_DOC_REVIEW": "b1abc",
        "YANDEX_CLOUD_API_KEY_DOC_REVIEW": "AQVN_v1",
    })
    folder, key = cfg.secrets.require_yandex()
    assert folder == "b1abc"
    assert key == "AQVN_v1"


def test_secrets_resolved_from_generic_names():
    cfg = load_config(env={
        "YANDEX_CLOUD_FOLDER": "b1gen",
        "YANDEX_CLOUD_API_KEY": "AQVN_gen",
    })
    folder, key = cfg.secrets.require_yandex()
    assert folder == "b1gen"
    assert key == "AQVN_gen"


def test_secrets_resolved_from_bashrc_names():
    cfg = load_config(env={
        "YANDEX_CLOUD_FOLDER_2": "b1bash",
        "YANDEX_CLOUD_SECRET_KEY": "AQVN_bash",
    })
    folder, key = cfg.secrets.require_yandex()
    assert folder == "b1bash"
    assert key == "AQVN_bash"


def test_new_names_take_precedence_over_v1():
    cfg = load_config(env={
        "YDBDOC_YC_FOLDER_ID": "b1new",
        "YANDEX_CLOUD_FOLDER_DOC_REVIEW": "b1old",
        "YDBDOC_YC_API_KEY": "AQVN_new",
        "YANDEX_CLOUD_API_KEY_DOC_REVIEW": "AQVN_old",
    })
    folder, key = cfg.secrets.require_yandex()
    assert folder == "b1new"
    assert key == "AQVN_new"


def test_github_tokens_resolved():
    cfg = load_config(env={
        "GITHUB_TOKEN": "ghp_test",
        "YDBDOC_PUSH_PAT": "ghp_push",
    })
    assert cfg.secrets.github_token == "ghp_test"
    assert cfg.secrets.github_push_token == "ghp_push"


# --- Env overrides ---


def test_override_simple_value():
    cfg = load_config(env={"YDBDOC_LLM_TEMPERATURE": "0.5"})
    assert cfg.llm.temperature == 0.5


def test_override_int_value():
    cfg = load_config(env={"YDBDOC_LLM_MAX_TOKENS": "16000"})
    assert cfg.llm.max_tokens == 16000


def test_override_bool_value():
    cfg = load_config(env={"YDBDOC_REPORTING_INCLUDE_COST": "false"})
    assert cfg.reporting.include_cost is False


def test_override_nested_model_primary():
    cfg = load_config(env={
        "YDBDOC_LLM_MODELS_TRANSLATE_PRIMARY": "deepseek-v32",
    })
    assert cfg.llm.models.translate.primary == "deepseek-v32"
    # Fallbacks from default YAML remain.
    assert "yandexgpt-5-pro" in cfg.llm.models.translate.fallbacks


def test_override_fallbacks_list_from_csv():
    cfg = load_config(env={
        "YDBDOC_LLM_MODELS_TRANSLATE_FALLBACKS": "gpt-oss-120b, deepseek-v32",
    })
    assert cfg.llm.models.translate.fallbacks == ["gpt-oss-120b", "deepseek-v32"]


def test_override_translation_chars():
    cfg = load_config(env={
        "YDBDOC_TRANSLATION_SEGMENTS_PER_BATCH_CHARS": "2000",
    })
    assert cfg.translation.segments_per_batch_chars == 2000


def test_override_critic_feedback_retries():
    cfg = load_config(env={"YDBDOC_TRANSLATION_CRITIC_FEEDBACK_RETRIES": "2"})
    assert cfg.translation.critic_feedback_retries == 2


def test_default_critic_feedback_retries():
    cfg = load_config(env={})
    assert cfg.translation.critic_feedback_retries == 2


def test_secret_vars_not_treated_as_overrides():
    """YDBDOC_YC_* must populate secrets, not crash on validation."""
    cfg = load_config(env={
        "YDBDOC_YC_FOLDER_ID": "b1xyz",
        "YDBDOC_YC_API_KEY": "AQVN_test",
    })
    # No exception. Secrets populated; config remains default.
    assert cfg.secrets.yc_folder_id == "b1xyz"
    assert cfg.llm.temperature == 0.1


def test_unknown_section_in_env_ignored():
    """Unknown overrides like YDBDOC_FOO_BAR must not crash."""
    cfg = load_config(env={"YDBDOC_FOO_BAR": "baz"})
    assert cfg.llm.temperature == 0.1


# --- Custom YAML ---


def test_load_from_custom_yaml(tmp_path: Path):
    yaml_text = dedent("""
        llm:
          provider: yandex
          base_url: https://ai.api.cloud.yandex.net/v1
          temperature: 0.7
          models:
            analyze:
              primary: yandexgpt-5-lite
            translate:
              primary: yandexgpt-5-pro
              fallbacks: [yandexgpt-5.1]
            critic:
              primary: qwen3.6-35b-a3b
    """).strip()
    path = tmp_path / "custom.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    cfg = load_config(yaml_path=path, env={})
    assert cfg.llm.temperature == 0.7
    assert cfg.llm.models.translate.primary == "yandexgpt-5-pro"


def test_custom_yaml_then_env_override(tmp_path: Path):
    yaml_text = dedent("""
        llm:
          temperature: 0.3
          models:
            analyze:
              primary: yandexgpt-5-lite
            translate:
              primary: yandexgpt-5.1
            critic:
              primary: qwen3.6-35b-a3b
    """).strip()
    path = tmp_path / "custom.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    cfg = load_config(
        yaml_path=path,
        env={"YDBDOC_LLM_TEMPERATURE": "0.9"},
    )
    assert cfg.llm.temperature == 0.9


# --- Base URL normalization ---


def test_base_url_trailing_slash_stripped():
    cfg = load_config(env={
        "YDBDOC_LLM_BASE_URL": "https://example.com/v1/",
    })
    assert cfg.llm.base_url == "https://example.com/v1"

