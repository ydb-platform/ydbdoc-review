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


class RateLimitRetriesConfig(BaseModel):
    """Separate retry budget for HTTP 429 (overridable via env)."""

    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 6
    backoff_initial_s: float = 5.0
    backoff_factor: float = 2.0
    max_backoff_s: float = 120.0


class RetriesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    backoff_initial_s: float = 2.0
    backoff_factor: float = 2.0
    rate_limit: RateLimitRetriesConfig = Field(default_factory=RateLimitRetriesConfig)


class ConcurrencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batches_per_file: int = 3


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analyze: ModelChoice
    translate: ModelChoice
    critic: ModelChoice


class ElizaModelsConfig(BaseModel):
    """Eliza-internal model chains (ignored by ``yandex_cloud`` provider)."""

    model_config = ConfigDict(extra="forbid")
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
    eliza: ElizaModelsConfig | None = None

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class TranslationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_lang: str = "ru"
    target_lang: str = "en"
    segments_per_batch_chars: int = 4000
    critic_feedback_retries: int = 2
    # §6.132 differential translation (override via YDBDOC_TRANSLATION_*)
    differential_enabled: bool = True
    differential_stale_days: int = 90
    differential_change_magnitude: float = 0.5
    differential_min_en_ratio: float = 0.3
    continue_feedback: str = ""


class OpsConfig(BaseModel):
    """ACL, quota, transcript backend (§6.134). Override via ``YDBDOC_OPS_*``."""

    model_config = ConfigDict(extra="forbid")
    daily_budget_rub: float = 5000.0
    max_continues_per_pr: int = 3
    transcript_backend: str = "ydb"
    transcript_ttl_days: int = 14
    ydb_endpoint: str = "grpcs://ydb.serverless.yandexcloud.net:2135"
    ydb_database: str = (
        "/ru-central1/b1g7gqj2vnq67gjseuva/etns0641qf73btm7j21k"
    )
    allowed_actors: str = ""
    skip_gates: bool = False


class PromptsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "v1"
    glossary_path: str = "prompts/glossary.yaml"


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    docs_root: str = "ydb/docs"
    translation_branch_prefix: str = "ydbdoc-review/pr-"
    verify_fixup_branch_prefix: str = "ydbdoc-review/verify-"


class ReportingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_cost: bool = True
    include_token_usage: bool = True
    include_heuristics: bool = True
    include_skipped_critic: bool = True


class Secrets(BaseModel):
    """Resolved secrets. Filled from env at load time."""

    model_config = ConfigDict(extra="forbid")
    yc_folder_id: str | None = None
    yc_api_key: str | None = None
    eliza_base_url: str | None = None
    eliza_oauth_token: str | None = None
    eliza_api_root: str | None = None
    github_token: str | None = None
    github_push_token: str | None = None
    ydb_sa_key_file: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

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

    def require_github(self) -> tuple[str, str]:
        """Return (api_token, push_token) or raise if missing."""
        api = self.github_token
        push = self.github_push_token or api
        if not api:
            raise RuntimeError(
                "GitHub token not configured. Set GITHUB_TOKEN."
            )
        if not push:
            raise RuntimeError(
                "GitHub push token not configured. "
                "Set GITHUB_PUSH_TOKEN or GITHUB_TOKEN."
            )
        return api, push

    def require_eliza(self) -> tuple[str, str]:
        """Return (base_url, oauth_token) or raise if missing."""
        if not self.eliza_base_url:
            raise RuntimeError(
                "Eliza base URL not configured. Set ELIZA_BASE_URL."
            )
        if not self.eliza_oauth_token:
            raise RuntimeError(
                "Eliza OAuth token not configured. Set ELIZA_OAUTH_TOKEN."
            )
        return self.eliza_base_url, self.eliza_oauth_token

    def require_eliza_api_root(self) -> tuple[str, str]:
        """Return (api_root, oauth_token) or raise if missing."""
        root = (self.eliza_api_root or "").rstrip("/")
        if not root and self.eliza_base_url:
            # Back-compat: ELIZA_BASE_URL like https://api.eliza.yandex.net/raw/openai/v1
            # should be treated as api_root https://api.eliza.yandex.net
            try:
                from urllib.parse import urlparse

                p = urlparse(self.eliza_base_url)
                if p.scheme and p.netloc:
                    root = f"{p.scheme}://{p.netloc}"
            except Exception:
                root = ""
        if not root:
            root = "https://api.eliza.yandex.net"
        if not self.eliza_oauth_token:
            raise RuntimeError(
                "Eliza OAuth token not configured. Set ELIZA_OAUTH_TOKEN."
            )
        return root, self.eliza_oauth_token


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm: LLMConfig
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    ops: OpsConfig = Field(default_factory=OpsConfig)
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
_ELIZA_BASE_URL_ALIASES: tuple[str, ...] = ("ELIZA_BASE_URL",)
_ELIZA_OAUTH_TOKEN_ALIASES: tuple[str, ...] = ("ELIZA_OAUTH_TOKEN",)
_ELIZA_API_ROOT_ALIASES: tuple[str, ...] = ("ELIZA_API_ROOT",)


def _first_env(aliases: tuple[str, ...], env: dict[str, str]) -> str | None:
    for name in aliases:
        v = env.get(name)
        if v:
            return v
    return None


def _resolve_secrets(env: dict[str, str]) -> Secrets:
    from ydbdoc_review.ops.ydb_driver import resolve_sa_key_file

    return Secrets(
        yc_folder_id=_first_env(_FOLDER_ID_ENV_ALIASES, env),
        yc_api_key=_first_env(_API_KEY_ENV_ALIASES, env),
        eliza_base_url=_first_env(_ELIZA_BASE_URL_ALIASES, env),
        eliza_oauth_token=_first_env(_ELIZA_OAUTH_TOKEN_ALIASES, env),
        eliza_api_root=_first_env(_ELIZA_API_ROOT_ALIASES, env),
        github_token=_first_env(_GITHUB_TOKEN_ALIASES, env),
        github_push_token=_first_env(_GITHUB_PUSH_TOKEN_ALIASES, env),
        ydb_sa_key_file=resolve_sa_key_file(env),
        s3_access_key_id=env.get("YDBDOC_S3_ACCESS_KEY_ID") or None,
        s3_secret_access_key=env.get("YDBDOC_S3_SECRET_ACCESS_KEY") or None,
    )


# --- Env-var override mechanism ---

# Prefix all non-secret overrides with this:
_OVERRIDE_PREFIX = "YDBDOC_"
# But these prefixes belong to secrets — never treat as YAML overrides.
_SECRET_PREFIXES: tuple[str, ...] = (
    "YDBDOC_YC_",
    "YDBDOC_PUSH_",
    "YDBDOC_YDB_SA_",
    "YDBDOC_S3_ACCESS_",
    "YDBDOC_S3_SECRET_",
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
    # Flat env aliases used by GitHub Actions (not YDBDOC_OPS_* path form).
    if env.get("YDBDOC_ALLOWED_ACTORS"):
        cfg.ops.allowed_actors = env["YDBDOC_ALLOWED_ACTORS"]
    if env.get("YDBDOC_DAILY_BUDGET_RUB"):
        try:
            cfg.ops.daily_budget_rub = float(env["YDBDOC_DAILY_BUDGET_RUB"])
        except ValueError:
            pass
    if env.get("YDBDOC_TRANSCRIPT_BACKEND"):
        cfg.ops.transcript_backend = env["YDBDOC_TRANSCRIPT_BACKEND"].strip()
    skip = env.get("YDBDOC_SKIP_OPS_GATES", "").strip().lower()
    if skip in ("1", "true", "yes", "on"):
        cfg.ops.skip_gates = True
    return cfg


def _load_yaml(yaml_path: Path | None) -> dict[str, Any]:
    if yaml_path is not None:
        with yaml_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    # Load packaged default.
    pkg = resources.files("ydbdoc_review.config")
    text = (pkg / "default.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}

