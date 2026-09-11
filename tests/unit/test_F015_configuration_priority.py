"""F-015 contracts for configuration precedence and non-configurable guards."""

from __future__ import annotations

from textwrap import dedent
from types import SimpleNamespace

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import QUALITY_REPAIR_ROUNDS, HarnessContext
from ydbdoc_review.ops.gates import check_acl
from ydbdoc_review.translation.glossary import load_glossary


def test_F015_precedence(tmp_path):
    project = tmp_path / "project.yaml"
    project.write_text(
        dedent(
            """
            llm:
              provider: project-provider
              timeout_s: 31
              retries:
                max_attempts: 4
              models:
                analyze:
                  primary: project-analyze
                  fallbacks: [project-analyze-fallback]
                translate:
                  primary: project-translate
                  fallbacks: [project-translate-fallback]
                critic:
                  primary: project-critic
                  fallbacks: [project-critic-fallback]
            paths:
              docs_root: project/docs
            prompts:
              glossary_path: project/glossary.yaml
            reporting:
              include_cost: false
            """
        ).strip(),
        encoding="utf-8",
    )
    cfg = load_config(
        yaml_path=project,
        env={
            "YDBDOC_LLM_PROVIDER": "env-provider",
            "YDBDOC_LLM_TIMEOUT_S": "42",
            "YDBDOC_LLM_RETRIES_MAX_ATTEMPTS": "5",
            "YDBDOC_LLM_MODELS_TRANSLATE_PRIMARY": "env-translate",
            "YDBDOC_PATHS_DOCS_ROOT": "env/docs",
            "YDBDOC_PROMPTS_GLOSSARY_PATH": "env/glossary.yaml",
            "YDBDOC_REPORTING_INCLUDE_COST": "true",
        },
    )

    assert cfg.llm.provider == "env-provider"
    assert cfg.llm.timeout_s == 42
    assert cfg.llm.retries.max_attempts == 5
    assert cfg.llm.models.translate.primary == "env-translate"
    assert cfg.llm.models.translate.fallbacks == ["project-translate-fallback"]
    assert cfg.paths.docs_root == "env/docs"
    assert cfg.prompts.glossary_path == "env/glossary.yaml"
    assert cfg.reporting.include_cost is True


def test_F015_fixed_rules() -> None:
    cfg = load_config(
        env={
            "YDBDOC_TRANSLATION_SOURCE_LANG": "de",
            "YDBDOC_TRANSLATION_TARGET_LANG": "fr",
            "YDBDOC_TRANSLATION_CRITIC_FEEDBACK_RETRIES": "0",
        }
    )
    client = SimpleNamespace(usage_tracker=SimpleNamespace(records=[]))
    ctx = HarnessContext.from_options(
        client,
        glossary=load_glossary(),
        config=cfg,
        source_lang="ru",
        target_lang="en",
        critic_feedback_retries=0,
    )

    assert (ctx.source_lang, ctx.target_lang) == ("ru", "en")
    assert ctx.critic_feedback_retries == QUALITY_REPAIR_ROUNDS == 2
    assert cfg.llm.retries.max_attempts == 3
    assert not check_acl("model-instruction", frozenset()).ok
