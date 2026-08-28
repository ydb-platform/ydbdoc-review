#!/usr/bin/env python3
"""Bounded real-YDB preflight for the temporary PR 45949 NG cut-in."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping

LEGACY_ENDPOINT = "grpcs://ydb.serverless.yandexcloud.net:2135"
LEGACY_DATABASE = "/ru-central1/b1g7gqj2vnq67gjseuva/etns0641qf73btm7j21k"
PREFIX_RE = re.compile(r"^m0_pr45949_[0-9a-f]{16}$")
TABLE_SUFFIXES = ("command_runs", "lineages", "model_calls", "verification_results")
TEST_TIMEOUT_SECONDS = 55
CLEANUP_TIMEOUT_SECONDS = 20
MAX_CLEANUP_ROWS = 1000
SA_KEY_FIELDS = frozenset(("id", "service_account_id", "created_at", "key_algorithm", "public_key", "private_key"))


class PreflightError(RuntimeError):
    pass


def _required_secret(environment: Mapping[str, str]) -> str:
    value = environment.get("YDB_SA_KEY", "")
    if not value:
        raise PreflightError("Не задан ключ доступа к тестовой YDB. Перевод не запускался.")
    return value


def _configuration(environment: Mapping[str, str]) -> tuple[str, str]:
    return (
        environment.get("YDBDOC_YDB_ENDPOINT", "") or LEGACY_ENDPOINT,
        environment.get("YDBDOC_YDB_DATABASE", "") or LEGACY_DATABASE,
    )


def _validate_key_and_probe(key_path: Path, secret: str, endpoint: str, database: str) -> None:
    try:
        document = json.loads(secret)
    except (TypeError, json.JSONDecodeError):
        raise PreflightError("Ключ сервисного аккаунта содержит некорректный JSON. Перевод не запускался.") from None
    if not isinstance(document, dict) or any(
        not isinstance(document.get(field), str) or not document[field].strip()
        for field in SA_KEY_FIELDS
    ):
        raise PreflightError("В ключе сервисного аккаунта отсутствуют обязательные поля. Перевод не запускался.")

    import ydb

    try:
        credentials = ydb.iam.ServiceAccountCredentials.from_file(str(key_path))
    except Exception:
        raise PreflightError("SDK YDB не смог прочитать ключ сервисного аккаунта. Перевод не запускался.") from None
    driver = None
    try:
        driver = ydb.Driver(endpoint=endpoint, database=database, credentials=credentials)
        driver.wait(timeout=8, fail_fast=True)
    except Exception as error:
        unauthenticated = tuple(
            kind for kind in (getattr(ydb.issues, "Unauthenticated", None),) if isinstance(kind, type)
        )
        unauthorized = tuple(
            kind for kind in (
                getattr(ydb.issues, "Unauthorized", None), getattr(ydb.issues, "PermissionDenied", None),
            ) if isinstance(kind, type)
        )
        not_found = tuple(
            kind for kind in (
                getattr(ydb.issues, "NotFound", None), getattr(ydb.issues, "SchemeError", None),
            ) if isinstance(kind, type)
        )
        timeout = tuple(kind for kind in (getattr(ydb.issues, "Timeout", None), TimeoutError) if isinstance(kind, type))
        unavailable = tuple(kind for kind in (getattr(ydb.issues, "Unavailable", None),) if isinstance(kind, type))
        if unauthenticated and isinstance(error, unauthenticated):
            message = "Ключ сервисного аккаунта не прошёл аутентификацию в YDB. Перевод не запускался."
        elif unauthorized and isinstance(error, unauthorized):
            message = "Сервисному аккаунту не хватает прав для проверки YDB. Перевод не запускался."
        elif not_found and isinstance(error, not_found):
            message = "Указанная база YDB не найдена или недоступна по заданному пути. Перевод не запускался."
        elif timeout and isinstance(error, timeout):
            message = "Подключение к YDB не установлено за 8 секунд. Перевод не запускался."
        elif unavailable and isinstance(error, unavailable):
            message = "Сервис YDB временно недоступен. Перевод не запускался."
        else:
            message = "Не удалось проверить подключение к YDB. Перевод не запускался."
        raise PreflightError(message) from None
    finally:
        if driver is not None:
            try:
                driver.stop(timeout=3)
            except Exception:
                pass


def _stop_child(child: subprocess.Popen[bytes]) -> None:
    child.terminate()
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3)


def _run_bounded(command: list[str], environment: Mapping[str, str], timeout: int) -> int:
    child = subprocess.Popen(command, env=dict(environment))
    try:
        return child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_child(child)
        if timeout == TEST_TIMEOUT_SECONDS:
            raise PreflightError(
                "Проверка логики YDB не завершилась за 55 секунд. Перевод не запускался."
            ) from None
        raise PreflightError("Очистка тестовых таблиц YDB не завершилась вовремя. Перевод не запускался.") from None


def _parse_junit(path: Path) -> int:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        raise PreflightError("Проверка YDB не создала корректный отчёт. Перевод не запускался.") from None
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {
        name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }
    if totals["tests"] <= 0 or any(totals[name] for name in ("failures", "errors", "skipped")):
        raise PreflightError(
            "Проверка YDB завершилась не полностью или нашла ошибки. Перевод не запускался."
        )
    return totals["tests"]


def _cleanup_exact_tables(environment: Mapping[str, str]) -> None:
    prefix = environment.get("YDBDOC_REAL_YDB_TABLE_PREFIX", "")
    if not PREFIX_RE.fullmatch(prefix):
        raise PreflightError("Не удалось доказать безопасную область очистки YDB.")
    import ydb

    def confirmed_not_found(error: Exception) -> bool:
        if isinstance(error, ydb.issues.NotFound):
            return True
        if not isinstance(error, ydb.issues.SchemeError):
            return False
        message = str(error).lower()
        return any(marker in message for marker in ("does not exist", "path not found", "not found"))

    credentials = ydb.iam.ServiceAccountCredentials.from_file(environment["YDBDOC_YDB_SA_KEY_FILE"])
    driver = ydb.Driver(
        endpoint=environment["YDBDOC_YDB_ENDPOINT"],
        database=environment["YDBDOC_YDB_DATABASE"],
        credentials=credentials,
    )
    try:
        driver.wait(timeout=8, fail_fast=True)
        pool = ydb.SessionPool(driver)
        present: list[str] = []
        total = 0
        for suffix in TABLE_SUFFIXES:
            table = f"{prefix}_{suffix}"
            try:
                result = pool.retry_operation_sync(
                    lambda session, name=table: session.transaction().execute(
                        f"SELECT COUNT(*) AS n FROM `{name}`;", commit_tx=True
                    )
                )
            except (ydb.issues.NotFound, ydb.issues.SchemeError) as error:
                if confirmed_not_found(error):
                    continue
                raise
            total += int(result[0].rows[0]["n"])
            present.append(table)
        if total > MAX_CLEANUP_ROWS:
            raise PreflightError("Безопасный предел очистки YDB превышен.")
        for table in present:
            try:
                pool.retry_operation_sync(
                    lambda session, name=table: session.execute_scheme(f"DROP TABLE `{name}`;")
                )
            except (ydb.issues.NotFound, ydb.issues.SchemeError) as error:
                if not confirmed_not_found(error):
                    raise
    finally:
        driver.stop(timeout=3)


def _cleanup_child(environment: Mapping[str, str]) -> None:
    command = [sys.executable, str(Path(__file__).resolve()), "--cleanup"]
    try:
        code = _run_bounded(command, environment, CLEANUP_TIMEOUT_SECONDS)
    except PreflightError:
        raise PreflightError("Очистка тестовых таблиц YDB не завершилась вовремя. Перевод не запускался.") from None
    if code != 0:
        raise PreflightError("Не удалось удалить тестовые таблицы YDB. Перевод не запускался.")


def run(environment: Mapping[str, str]) -> int:
    secret = _required_secret(environment)
    endpoint, database = _configuration(environment)
    prefix = f"m0_pr45949_{secrets.token_hex(8)}"
    if not PREFIX_RE.fullmatch(prefix):
        raise PreflightError("Не удалось создать безопасную область проверки YDB.")
    key_fd, key_name = tempfile.mkstemp(prefix="ydbdoc-ng-sa-", suffix=".json")
    report_fd, report_name = tempfile.mkstemp(prefix="ydbdoc-ng-junit-", suffix=".xml")
    os.close(report_fd)
    key_path, report_path = Path(key_name), Path(report_name)
    child_environment = dict(environment)
    child_environment.update(
        YDBDOC_YDB_ENDPOINT=endpoint,
        YDBDOC_YDB_DATABASE=database,
        YDBDOC_YDB_SA_KEY_FILE=str(key_path),
        YDBDOC_REAL_YDB_TABLE_PREFIX=prefix,
        YDBDOC_REAL_YDB_STATE="1",
    )
    child_environment.pop("YDB_SA_KEY", None)
    child_environment.pop("YDBDOC_YDB_SA_KEY_JSON", None)
    primary_error: Exception | None = None
    cleanup_error: Exception | None = None
    try:
        os.fchmod(key_fd, 0o600)
        with os.fdopen(key_fd, "w", encoding="utf-8") as key_file:
            key_file.write(secret)
        _validate_key_and_probe(key_path, secret, endpoint, database)
        command = [
            sys.executable, "-m", "pytest", "/app/ng/tests/test_real_ydb_state.py",
            f"--junitxml={report_path}", "-q",
        ]
        if _run_bounded(command, child_environment, TEST_TIMEOUT_SECONDS) != 0:
            raise PreflightError("Проверка YDB завершилась с ошибкой. Перевод не запускался.")
        tests = _parse_junit(report_path)
    except Exception as error:
        primary_error = error
        tests = 0
    finally:
        try:
            _cleanup_child(child_environment)
        except Exception as error:
            cleanup_error = error
        key_path.unlink(missing_ok=True)
        report_path.unlink(missing_ok=True)
    if cleanup_error is not None:
        if isinstance(primary_error, PreflightError):
            raise PreflightError(f"{primary_error} {cleanup_error}") from None
        raise PreflightError(str(cleanup_error)) from None
    if primary_error is not None:
        if isinstance(primary_error, PreflightError):
            raise primary_error
        raise PreflightError("Проверка YDB сломалась. Перевод не запускался.") from None
    return tests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cleanup:
            _cleanup_exact_tables(os.environ)
            return 0
        tests = run(os.environ)
        print(f"Проверка YDB пройдена: {tests} тестов. Запускаем перевод PR 45949.")
        return 0
    except Exception as error:
        message = str(error) if isinstance(error, PreflightError) else "Проверка YDB сломалась. Перевод не запускался."
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
