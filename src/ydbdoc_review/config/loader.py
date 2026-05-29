"""Configuration loader: YAML default + env-var overrides + secret resolution."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


# --- Schema ---


class ModelChoice(BaseModel):
    """A primary model with optional fallbacks for one role."""

    model_config = ConfigDict(extra="forbid")
    primary: str
    fallbacks: list[str] = Field(default_factory=list)

    @property
    def chain(self) -> list[str]:
        """Full ordered list of models to try (primary then fallbacks)."""
        seen: set[str] = set()
        out: list[str] = []
        for m in [self.primary, *self.fallbacks]:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out


class RetriesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    backoff_initial_s: float = 2.0
    backoff_factor: float = 2.0


class ConcurrencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batches_per_file: int = 3


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analyze: ModelChoice
    translate: ModelChoice
    critic: ModelChoice


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "yandex"
    base_url: str = "https://ai.api.cloud.yandex.net/v1"
    temperature: float = 0.1
    max_tokens: int = 8000
    timeout_s: int = 120
    retries: RetriesConfig = Field(default_factory=RetriesConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    models: ModelsConfig

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class TranslationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_lang: str = "ru"
    target_lang: str = "en"
    segments_per_batch_chars: int = 4000


class PromptsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "v1"
    glossary_path: str = "prompts/glossary.yaml"


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    docs_root: str = "ydb/docs"
    translation_branch_prefix: str = "ydbdoc-review/pr-"


class ReportingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_cost: bool = True
    include_token_usage: bool = True
    include_heuristics: bool = True


class Secrets(BaseModel):
    """Resolved secrets. Filled from env at load time."""

    model_config = ConfigDict(extra="forbid")
    yc_folder_id: str | None = None
    yc_api_key: str | None = None
    github_token: str | None = None
    github_push_token: str | None = None

    def require_yandex(self) -> tuple[str, str]:
        """Return (folder_id, api_key) or raise if missing."""
        if not self.yc_folder_id:
            raise RuntimeError(
                "Yandex Cloud folder id not configured. "
                "Set YDBDOC_YC_FOLDER_ID (or one of the v1 aliases)."
            )
        if not self.yc_api_key:
            raise RuntimeError(
                "Yandex Cloud API key not configured. "
                "Set YDBDOC_YC_API_KEY (or one of the v1 aliases)."
            )
        return self.yc_folder_id, self.yc_api_key


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm: LLMConfig
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    # Resolved at load time; not part of the YAML.
    secrets: Secrets = Field(default_factory=Secrets)


# --- Secret env aliases ---


_FOLDER_ID_ENV_ALIASES: tuple[str, ...] = (
    "YDBDOC_YC_FOLDER_ID",
    "YANDEX_CLOUD_FOLDER_DOC_REVIEW",
    "YANDEX_CLOUD_FOLDER",
    "YANDEX_CLOUD_FOLDER_2",
    "YC_FOLDER_ID",
)
_API_KEY_ENV_ALIASES: tuple[str, ...] = (
    "YDBDOC_YC_API_KEY",
    "YANDEX_CLOUD_API_KEY_DOC_REVIEW",
    "YANDEX_CLOUD_API_KEY",
    "YANDEX_CLOUD_SECRET_KEY",
    "YC_API_KEY",
)
_GITHUB_TOKEN_ALIASES: tuple[str, ...] = ("GITHUB_TOKEN",)
_GITHUB_PUSH_TOKEN_ALIASES: tuple[str, ...] = (
    "GITHUB_PUSH_TOKEN",
    "YDBDOC_PUSH_PAT",
)


def _first_env(aliases: tuple[str, ...], env: dict[str, str]) -> str | None:
    for name in aliases:
        v = env.get(name)
        if v:
            return v
    return None


def _resolve_secrets(env: dict[str, str]) -> Secrets:
    return Secrets(
        yc_folder_id=_first_env(_FOLDER_ID_ENV_ALIASES, env),
        yc_api_key=_first_env(_API_KEY_ENV_ALIASES, env),
        github_token=_first_env(_GITHUB_TOKEN_ALIASES, env),
        github_push_token=_first_env(_GITHUB_PUSH_TOKEN_ALIASES, env),
    )


# --- Env-var override mechanism ---

# Prefix all non-secret overrides with this:
_OVERRIDE_PREFIX = "YDBDOC_"
# But these prefixes belong to secrets — never treat as YAML overrides.
_SECRET_PREFIXES: tuple[str, ...] = (
    "YDBDOC_YC_",
    "YDBDOC_PUSH_",
)


def _apply_env_overrides(
    data: dict[str, Any], env: dict[str, str]
) -> dict[str, Any]:
    """Apply YDBDOC_<SECTION>_<KEY> overrides onto the YAML dict.

    Naming convention (underscores in env = dots + snake_case in YAML):
        YDBDOC_LLM_TEMPERATURE → llm.temperature
        YDBDOC_LLM_MAX_TOKENS → llm.max_tokens
        YDBDOC_LLM_MODELS_TRANSLATE_PRIMARY → llm.models.translate.primary

    Secret-related vars (YDBDOC_YC_*, YDBDOC_PUSH_*) are skipped — they go
    through ``_resolve_secrets`` instead. Unknown paths are ignored.
    """
    for key, raw_value in env.items():
        if not key.startswith(_OVERRIDE_PREFIX):
            continue
        if any(key.startswith(p) for p in _SECRET_PREFIXES):
            continue

        parts = key[len(_OVERRIDE_PREFIX) :].lower().split("_")
        if not parts or not parts[0]:
            continue

        path = _resolve_config_path(data, parts)
        if path is None:
            continue
        _set_config_path(data, path, _coerce_value(raw_value))
    return data


def _resolve_config_path(
    node: dict[str, Any], parts: list[str]
) -> list[str] | None:
    """Map env suffix segments onto nested YAML keys (greedy snake_case join)."""
    if not parts:
        return []
    for end in range(len(parts), 0, -1):
        key = "_".join(parts[:end])
        if key not in node:
            continue
        rest = parts[end:]
        if not rest:
            return [key]
        child = node[key]
        if not isinstance(child, dict):
            return None
        sub = _resolve_config_path(child, rest)
        if sub is not None:
            return [key, *sub]
    return None


def _set_config_path(
    target: dict[str, Any], path: list[str], value: Any
) -> None:
    """Set ``value`` at ``path`` inside ``target`` (must exist from YAML)."""
    cursor: Any = target
    for key in path[:-1]:
        if not isinstance(cursor, dict):
            return
        cursor = cursor[key]
    last = path[-1]
    if not isinstance(cursor, dict):
        return
    if isinstance(cursor.get(last), list) and isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    cursor[last] = value


def _coerce_value(raw: str) -> Any:
    """Best-effort type coercion for env strings.

    Recognizes: 'true'/'false', integers, floats, otherwise returns str.
    """
    low = raw.strip().lower()
    if low in ("true", "yes", "1", "on"):
        return True
    if low in ("false", "no", "0", "off"):
        return False
    # Integer?
    try:
        return int(raw)
    except ValueError:
        pass
    # Float?
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


# --- Public loader ---


def load_config(
    *,
    yaml_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> Config:
    """Load configuration from YAML and apply env overrides.

    Args:
        yaml_path: Path to a YAML file. If None, loads the packaged default.
        env: Environment mapping to read overrides and secrets from.
             If None, uses os.environ.
    """
    if env is None:
        env = dict(os.environ)

    data = _load_yaml(yaml_path)
    data = _apply_env_overrides(data, env)

    cfg = Config.model_validate(data)
    cfg.secrets = _resolve_secrets(env)
    return cfg


def _load_yaml(yaml_path: Path | None) -> dict[str, Any]:
    if yaml_path is not None:
        with yaml_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    # Load packaged default.
    pkg = resources.files("ydbdoc_review.config")
    text = (pkg / "default.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}

