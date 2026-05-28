from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _first_nonempty_env(*names: str, default: str = "") -> str:
    for name in names:
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return default


def fm_base_url_requires_yandex_folder(base_url: str) -> bool:
    """True for Yandex Cloud FM host — needs catalog id and gpt:// URIs for models."""
    host = (urlparse(base_url.strip()).hostname or "").lower()
    return host.endswith("yandex.net")


_DEFAULT_MODEL_CHECK = "yandexgpt/latest"
_DEFAULT_MODEL_TRANSLATE = "deepseek-v4-flash/latest"


def _candidate_config_files() -> list[Path]:
    """Resolve ydbdoc-review.toml: env path, walk cwd upward, then package root."""
    paths: list[Path] = []
    seen: set[Path] = set()
    env_p = os.environ.get("YDBDOC_CONFIG", "").strip()
    if env_p:
        ep = Path(env_p).expanduser().resolve()
        if ep not in seen:
            paths.append(ep)
            seen.add(ep)
    cwd = Path.cwd().resolve()
    for _ in range(12):
        cand = (cwd / "ydbdoc-review.toml").resolve()
        if cand not in seen:
            paths.append(cand)
            seen.add(cand)
        parent = cwd.parent
        if parent == cwd:
            break
        cwd = parent
    pkg_root = Path(__file__).resolve().parents[2]
    cand = (pkg_root / "ydbdoc-review.toml").resolve()
    if cand not in seen:
        paths.append(cand)
    bundled = (Path(__file__).resolve().parent / "ydbdoc-review.toml").resolve()
    if bundled not in seen:
        paths.append(bundled)
    return paths


def _toml_str(models: dict[str, object], key: str) -> str:
    v = models.get(key)
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "none" else s


def _feature_review_enabled(feature: dict[str, object]) -> bool:
    v = feature.get("review_enabled", True)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("false", "0", "no", "off", "disabled"):
            return False
        if s in ("true", "1", "yes", "on", "enabled"):
            return True
    return True


def _feature_bool(feature: dict[str, object], key: str, *, default: bool) -> bool:
    if key not in feature:
        return default
    v = feature[key]
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("false", "0", "no", "off", "disabled"):
            return False
        if s in ("true", "1", "yes", "on", "enabled"):
            return True
    return default


@dataclass(frozen=True)
class TomlConfigLayer:
    """First-resolved ``ydbdoc-review.toml`` slice (env may still override in ``Settings.from_env``)."""

    model_check: str
    model_translate: str
    review_enabled: bool
    translation_self_check_enabled: bool
    translation_repair_enabled: bool
    """Slug for cross-model translation QA; empty → use ``model_check`` after env merge."""
    model_translation_verify: str
    glossary_path: str = ""
    project_info_path: str = ""
    translate_system_template_path: str = ""
    quality_hierarchy_path: str = ""
    en_style_guide_path: str = ""
    segment_rules_path: str = ""


def _prompt_paths_from_toml(data: dict) -> dict[str, str]:
    prompts = data.get("prompts")
    if not isinstance(prompts, dict):
        return {
            "glossary_path": "",
            "project_info_path": "",
            "translate_system_template_path": "",
            "quality_hierarchy_path": "",
            "en_style_guide_path": "",
            "segment_rules_path": "",
        }
    return {
        "glossary_path": _toml_str(prompts, "glossary"),
        "project_info_path": _toml_str(prompts, "project_info"),
        "translate_system_template_path": _toml_str(
            prompts, "translate_system_template"
        ),
        "quality_hierarchy_path": _toml_str(prompts, "quality_hierarchy"),
        "en_style_guide_path": _toml_str(prompts, "en_style_guide"),
        "segment_rules_path": _toml_str(prompts, "segment_rules"),
    }


def load_config_layer() -> TomlConfigLayer:
    """Models and feature flags from the first TOML that defines [models] and/or [feature]."""
    for path in _candidate_config_files():
        if not path.is_file():
            continue
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except OSError:
            continue
        except tomllib.TOMLDecodeError:
            raise SystemExit(f"Invalid TOML in config file: {path}") from None
        models = data.get("models")
        feature = data.get("feature")
        if not isinstance(models, dict) and not isinstance(feature, dict):
            continue
        mc = _DEFAULT_MODEL_CHECK
        mt = _DEFAULT_MODEL_TRANSLATE
        verify = ""
        if isinstance(models, dict):
            mc = _toml_str(models, "check") or _DEFAULT_MODEL_CHECK
            mt = _toml_str(models, "translate") or _DEFAULT_MODEL_TRANSLATE
            verify = _toml_str(models, "translation_verify")
        review_on = True
        self_check = False
        repair = True
        if isinstance(feature, dict):
            review_on = _feature_review_enabled(feature)
            self_check = _feature_bool(
                feature, "translation_self_check", default=False
            )
            repair = _feature_bool(
                feature, "translation_repair", default=self_check
            )
        return TomlConfigLayer(
            model_check=mc,
            model_translate=mt,
            review_enabled=review_on,
            translation_self_check_enabled=self_check,
            translation_repair_enabled=repair,
            model_translation_verify=verify,
            **_prompt_paths_from_toml(data),
        )
    return TomlConfigLayer(
        model_check=_DEFAULT_MODEL_CHECK,
        model_translate=_DEFAULT_MODEL_TRANSLATE,
        review_enabled=True,
        translation_self_check_enabled=False,
        translation_repair_enabled=False,
        model_translation_verify="",
    )


def _parse_env_bool(raw: str, *, var_name: str) -> bool:
    s = raw.strip().lower()
    if s in ("1", "true", "yes", "on", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disabled"):
        return False
    raise SystemExit(
        f"Invalid {var_name}={raw!r}; use true/false, 1/0, yes/no, on/off."
    )


def _parse_env_review_enabled(raw: str) -> bool:
    return _parse_env_bool(raw, var_name="YDBDOC_REVIEW_ENABLED")


def resolved_config_path() -> Path | None:
    """First config file that defines [models] and/or [feature]."""
    for path in _candidate_config_files():
        if not path.is_file():
            continue
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(data.get("models"), dict) or isinstance(data.get("feature"), dict):
            return path
    return None


def resolved_models_config_path() -> Path | None:
    """First config file that has a [models] table (for list-models hint)."""
    for path in _candidate_config_files():
        if not path.is_file():
            continue
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(data.get("models"), dict):
            return path
    return None


@dataclass(frozen=True)
class Settings:
    yandex_folder: str
    yandex_api_key: str
    yandex_base_url: str
    model_check: str
    model_translate: str
    """Second model for post-translation cross-check (debug); defaults to ``model_check``."""
    model_translation_verify: str
    translation_self_check_enabled: bool
    translation_repair_enabled: bool
    review_enabled: bool
    github_token: str
    github_push_token: str
    docs_prefix: str
    prompts_dir: str
    glossary_path: str
    project_info_path: str
    translate_system_template_path: str
    quality_hierarchy_path: str
    en_style_guide_path: str
    segment_rules_path: str

    @staticmethod
    def from_env() -> "Settings":
        _load_dotenv()
        folder = _first_nonempty_env(
            "YANDEX_CLOUD_FOLDER_DOC_REVIEW",
            "YANDEX_CLOUD_FOLDER",
            "YC_FOLDER_ID",
        )
        api_key = _first_nonempty_env(
            "YANDEX_CLOUD_API_KEY_DOC_REVIEW",
            "YANDEX_CLOUD_API_KEY",
            "YC_API_KEY",
            "OPENAI_API_KEY",
            "YDBDOC_LLM_API_KEY",
        )
        base_url = _first_nonempty_env(
            "YANDEX_CLOUD_BASE_URL",
            "OPENAI_BASE_URL",
            "YDBDOC_LLM_BASE_URL",
            default="https://ai.api.cloud.yandex.net/v1",
        ).rstrip("/")
        toml = load_config_layer()
        # Env overrides ydbdoc-review.toml (for CI one-offs without editing the file).
        model_check = os.environ.get("YDBDOC_MODEL_CHECK", "").strip() or toml.model_check
        model_translate = (
            os.environ.get("YDBDOC_MODEL_TRANSLATE", "").strip() or toml.model_translate
        )
        model_translation_verify = (
            os.environ.get("YDBDOC_MODEL_TRANSLATION_VERIFY", "").strip()
            or toml.model_translation_verify.strip()
            or model_check
        )
        self_check_env = os.environ.get("YDBDOC_TRANSLATION_SELF_CHECK")
        if self_check_env is not None and str(self_check_env).strip() != "":
            translation_self_check_enabled = _parse_env_bool(
                str(self_check_env).strip(),
                var_name="YDBDOC_TRANSLATION_SELF_CHECK",
            )
        else:
            translation_self_check_enabled = toml.translation_self_check_enabled
        repair_env = os.environ.get("YDBDOC_TRANSLATION_REPAIR")
        if repair_env is not None and str(repair_env).strip() != "":
            translation_repair_enabled = _parse_env_bool(
                str(repair_env).strip(),
                var_name="YDBDOC_TRANSLATION_REPAIR",
            )
        else:
            translation_repair_enabled = toml.translation_repair_enabled
        review_env = os.environ.get("YDBDOC_REVIEW_ENABLED", "").strip()
        if review_env:
            review_enabled = _parse_env_review_enabled(review_env)
        else:
            review_enabled = toml.review_enabled
        gh = os.environ.get("GITHUB_TOKEN", "").strip()
        # PAT for git push: GITHUB_PUSH_TOKEN (workflow env) or YDBDOC_PUSH_PAT (secret name as env).
        gh_push = (
            os.environ.get("GITHUB_PUSH_TOKEN", "").strip()
            or os.environ.get("YDBDOC_PUSH_PAT", "").strip()
            or gh
        )
        docs_prefix = os.environ.get("DOCS_SRC_ROOT", "ydb/docs").strip().strip("/")
        here = os.path.dirname(os.path.abspath(__file__))
        default_prompts = os.path.normpath(os.path.join(here, "..", "..", "prompts"))
        prompts_dir = os.environ.get("YDBDOC_PROMPTS_DIR", default_prompts)
        glossary_path = (
            os.environ.get("YDBDOC_GLOSSARY_PATH", "").strip()
            or toml.glossary_path
        )
        project_info_path = (
            os.environ.get("YDBDOC_PROJECT_INFO_PATH", "").strip()
            or toml.project_info_path
        )
        translate_system_template_path = (
            os.environ.get("YDBDOC_TRANSLATE_SYSTEM_TEMPLATE_PATH", "").strip()
            or toml.translate_system_template_path
        )
        quality_hierarchy_path = (
            os.environ.get("YDBDOC_QUALITY_HIERARCHY_PATH", "").strip()
            or toml.quality_hierarchy_path
        )
        en_style_guide_path = (
            os.environ.get("YDBDOC_EN_STYLE_GUIDE_PATH", "").strip()
            or toml.en_style_guide_path
        )
        segment_rules_path = (
            os.environ.get("YDBDOC_SEGMENT_RULES_PATH", "").strip()
            or toml.segment_rules_path
        )
        return Settings(
            yandex_folder=folder,
            yandex_api_key=api_key,
            yandex_base_url=base_url,
            model_check=model_check,
            model_translate=model_translate,
            model_translation_verify=model_translation_verify,
            translation_self_check_enabled=translation_self_check_enabled,
            translation_repair_enabled=translation_repair_enabled,
            review_enabled=review_enabled,
            github_token=gh,
            github_push_token=gh_push,
            docs_prefix=docs_prefix,
            prompts_dir=prompts_dir,
            glossary_path=glossary_path,
            project_info_path=project_info_path,
            translate_system_template_path=translate_system_template_path,
            quality_hierarchy_path=quality_hierarchy_path,
            en_style_guide_path=en_style_guide_path,
            segment_rules_path=segment_rules_path,
        )

    def validate_yandex(self) -> None:
        if not self.yandex_api_key:
            raise SystemExit(
                "Set an API key: YANDEX_CLOUD_API_KEY_DOC_REVIEW / YANDEX_CLOUD_API_KEY / "
                "YC_API_KEY, or OPENAI_API_KEY / YDBDOC_LLM_API_KEY for other OpenAI-compatible hosts; "
                "see .env.example."
            )
        if fm_base_url_requires_yandex_folder(self.yandex_base_url) and not self.yandex_folder:
            raise SystemExit(
                "Yandex Foundation Models require a cloud folder id: "
                "YANDEX_CLOUD_FOLDER_DOC_REVIEW or YANDEX_CLOUD_FOLDER or YC_FOLDER_ID."
            )

    def validate_github(self) -> None:
        if not self.github_token:
            raise SystemExit("Set GITHUB_TOKEN for PR API and comments.")
