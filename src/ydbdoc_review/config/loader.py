# ruff: noqa: RUF001 -- Russian diagnostics and test text are intentional.
"""Required Actions configuration and the shared access check (no effects)."""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


class SettingsError(ValueError):
    """Invalid operator configuration; messages never include supplied values."""


class AccessDenied(PermissionError):
    """The initiating GitHub actor is not allowed to run any product mode."""


@dataclass(frozen=True)
class Settings:
    max_dependency_files: int
    max_source_characters: int
    allowed_actors: frozenset[str]
    daily_budget_rub: Decimal
    ydb_endpoint: str
    ydb_database: str
    ydb_sa_key: str = field(repr=False)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read the four required Actions variables, without fallback product limits.

    This function performs no I/O and creates no clients or credential files.
    Budget admission belongs immediately before the first paid call, not here.
    """
    env = os.environ if env is None else env

    def required(name: str) -> str:
        value = env.get(name, "").strip()
        if not value:
            raise SettingsError(
                f"Не задана {name}. Настройте Actions variables/secret "
                "репозитория ydb-platform/ydb."
            )
        return value

    def integer(name: str) -> int:
        value = required(name)
        if not re.fullmatch(r"[0-9]+", value):
            raise SettingsError(f"{name}: требуется целое неотрицательное число.")
        try:
            return int(value)
        except ValueError:
            raise SettingsError(f"{name}: слишком большое целое число.") from None

    dependencies = integer("YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE")
    characters = integer("YDBDOC_MAX_SOURCE_CHARACTERS")
    actors = frozenset(
        actor.strip().casefold()
        for actor in required("YDBDOC_ALLOWED_ACTORS").split(",")
        if actor.strip()
    )
    if not actors:
        raise SettingsError("YDBDOC_ALLOWED_ACTORS: укажите разрешённые GitHub-логины.")
    try:
        budget = Decimal(required("YDBDOC_DAILY_BUDGET_RUB"))
    except InvalidOperation:
        raise SettingsError("YDBDOC_DAILY_BUDGET_RUB: требуется число рублей.") from None
    if not budget.is_finite() or budget < 0:
        raise SettingsError("YDBDOC_DAILY_BUDGET_RUB: требуется конечное неотрицательное число.")
    endpoint = env.get("YDBDOC_YDB_ENDPOINT", "grpcs://ydb.serverless.yandexcloud.net:2135").strip()
    database = env.get("YDBDOC_YDB_DATABASE", "/ru-central1/b1g7gqj2vnq67gjseuva/etns0641qf73btm7j21k").strip()
    if not endpoint.startswith("grpcs://") or not endpoint.removeprefix("grpcs://"):
        raise SettingsError("YDBDOC_YDB_ENDPOINT: требуется адрес grpcs://.")
    if not database.startswith("/") or database == "/":
        raise SettingsError("YDBDOC_YDB_DATABASE: требуется абсолютный путь базы YDB.")
    return Settings(dependencies, characters, actors, budget, endpoint, database,
                    required("YDB_SA_KEY"))


def require_actor(settings: Settings, actor: str | None) -> None:
    """Mandatory ACL shared by translate, verify and continue; no budget or I/O."""
    if not actor or actor.strip().casefold() not in settings.allowed_actors:
        raise AccessDenied(
            "Запуск запрещён: пользователь не входит в YDBDOC_ALLOWED_ACTORS. "
            "Обратитесь к владельцам репозитория ydb-platform/ydb для получения доступа."
        )
