from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


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


_DEFAULT_MODEL_CHECK = "yandexgpt/latest"
_DEFAULT_MODEL_TRANSLATE = "yandexgpt-5.1"


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


def load_config_layer() -> tuple[str, str, bool]:
    """Models and review_enabled from the first TOML that defines [models] and/or [feature]."""
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
        if isinstance(models, dict):
            mc = _toml_str(models, "check") or _DEFAULT_MODEL_CHECK
            mt = _toml_str(models, "translate") or _DEFAULT_MODEL_TRANSLATE
        review_on = True
        if isinstance(feature, dict):
            review_on = _feature_review_enabled(feature)
        return mc, mt, review_on
    return _DEFAULT_MODEL_CHECK, _DEFAULT_MODEL_TRANSLATE, True


def _parse_env_review_enabled(raw: str) -> bool:
    s = raw.strip().lower()
    if s in ("1", "true", "yes", "on", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disabled"):
        return False
    raise SystemExit(
        f"Invalid YDBDOC_REVIEW_ENABLED={raw!r}; use true/false, 1/0, yes/no, on/off."
    )


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
    review_enabled: bool
    github_token: str
    github_push_token: str
    docs_prefix: str
    max_chars_per_side_analyze: int
    prompts_dir: str

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
        )
        base_url = _first_nonempty_env(
            "YANDEX_CLOUD_BASE_URL",
            default="https://ai.api.cloud.yandex.net/v1",
        ).rstrip("/")
        file_check, file_translate, file_review = load_config_layer()
        # Env overrides ydbdoc-review.toml (for CI one-offs without editing the file).
        model_check = (
            os.environ.get("YDBDOC_MODEL_CHECK", "").strip() or file_check
        )
        model_translate = (
            os.environ.get("YDBDOC_MODEL_TRANSLATE", "").strip() or file_translate
        )
        review_env = os.environ.get("YDBDOC_REVIEW_ENABLED", "").strip()
        if review_env:
            review_enabled = _parse_env_review_enabled(review_env)
        else:
            review_enabled = file_review
        gh = os.environ.get("GITHUB_TOKEN", "").strip()
        gh_push = os.environ.get("GITHUB_PUSH_TOKEN", gh).strip()
        docs_prefix = os.environ.get("DOCS_SRC_ROOT", "ydb/docs").strip().strip("/")
        max_chars = int(os.environ.get("YDBDOC_MAX_ANALYZE_CHARS", "16000"))
        here = os.path.dirname(os.path.abspath(__file__))
        default_prompts = os.path.normpath(os.path.join(here, "..", "..", "prompts"))
        prompts_dir = os.environ.get("YDBDOC_PROMPTS_DIR", default_prompts)
        return Settings(
            yandex_folder=folder,
            yandex_api_key=api_key,
            yandex_base_url=base_url,
            model_check=model_check,
            model_translate=model_translate,
            review_enabled=review_enabled,
            github_token=gh,
            github_push_token=gh_push,
            docs_prefix=docs_prefix,
            max_chars_per_side_analyze=max_chars,
            prompts_dir=prompts_dir,
        )

    def validate_yandex(self) -> None:
        if not self.yandex_folder or not self.yandex_api_key:
            raise SystemExit(
                "Set folder (YANDEX_CLOUD_FOLDER_DOC_REVIEW or YANDEX_CLOUD_FOLDER or YC_FOLDER_ID) "
                "and API key (YANDEX_CLOUD_API_KEY_DOC_REVIEW or YANDEX_CLOUD_API_KEY or YC_API_KEY); "
                "see .env.example."
            )

    def validate_github(self) -> None:
        if not self.github_token:
            raise SystemExit("Set GITHUB_TOKEN for PR API and comments.")
