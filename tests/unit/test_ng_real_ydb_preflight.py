from __future__ import annotations

import importlib.util
import os
import stat
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ng_preflight", ROOT / "scripts/run_ng_real_ydb_preflight.py")
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def _junit(path: Path, *, tests=1, failures=0, errors=0, skipped=0):
    ET.ElementTree(ET.Element("testsuite", tests=str(tests), failures=str(failures), errors=str(errors), skipped=str(skipped))).write(path)


def test_configuration_uses_env_or_exact_legacy_defaults():
    assert RUNNER._configuration({}) == (RUNNER.LEGACY_ENDPOINT, RUNNER.LEGACY_DATABASE)
    assert RUNNER._configuration({"YDBDOC_YDB_ENDPOINT": "e", "YDBDOC_YDB_DATABASE": "d"}) == ("e", "d")


@pytest.mark.parametrize("field", ["tests", "failures", "errors", "skipped"])
def test_junit_requires_nonempty_all_green(tmp_path, field):
    values = {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
    values[field] = 0 if field == "tests" else 1
    report = tmp_path / "report.xml"
    _junit(report, **values)
    with pytest.raises(RUNNER.PreflightError):
        RUNNER._parse_junit(report)


def test_run_uses_strict_scope_0600_deletes_files_and_always_cleans():
    observed = {}

    def bounded(command, environment, timeout):
        if "--cleanup" in command:
            observed["cleanup"] = (tuple(command), timeout)
            observed["cleanup_environment"] = dict(environment)
            return 0
        key = Path(environment["YDBDOC_YDB_SA_KEY_FILE"])
        observed["mode"] = stat.S_IMODE(key.stat().st_mode)
        observed["key"] = key
        observed["prefix"] = environment["YDBDOC_REAL_YDB_TABLE_PREFIX"]
        observed["test_environment"] = dict(environment)
        report = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        observed["report"] = report
        _junit(report, tests=3)
        return 0

    with patch.object(RUNNER, "_run_bounded", side_effect=bounded):
        assert RUNNER.run({"YDB_SA_KEY": "private-json", "YDBDOC_YDB_SA_KEY_JSON": "second-raw-copy"}) == 3
    assert observed["mode"] == 0o600
    assert RUNNER.PREFIX_RE.fullmatch(observed["prefix"])
    assert observed["cleanup"][1] == RUNNER.CLEANUP_TIMEOUT_SECONDS
    for child_environment in (observed["test_environment"], observed["cleanup_environment"]):
        assert "YDB_SA_KEY" not in child_environment
        assert "YDBDOC_YDB_SA_KEY_JSON" not in child_environment
        assert child_environment["YDBDOC_YDB_SA_KEY_FILE"]
    assert not observed["key"].exists()
    assert not observed["report"].exists()


def test_child_timeout_terminates_waits_kills_and_waits_again():
    class Child:
        def __init__(self): self.actions, self.waits = [], 0
        def wait(self, timeout):
            self.actions.append(("wait", timeout))
            self.waits += 1
            if self.waits < 3:
                raise __import__("subprocess").TimeoutExpired("x", timeout)
            return -9
        def terminate(self): self.actions.append(("terminate",))
        def kill(self): self.actions.append(("kill",))
    child = Child()
    with patch.object(RUNNER.subprocess, "Popen", return_value=child):
        with pytest.raises(RUNNER.PreflightError, match="55 секунд"):
            RUNNER._run_bounded(["child"], {}, 55)
    assert child.actions == [("wait", 55), ("terminate",), ("wait", 3), ("kill",), ("wait", 3)]


def test_cleanup_failure_is_red_even_after_green_test():
    def bounded(command, environment, timeout):
        if "--cleanup" in command:
            return 1
        report = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        _junit(report)
        return 0
    with patch.object(RUNNER, "_run_bounded", side_effect=bounded):
        with pytest.raises(RUNNER.PreflightError, match="удалить тестовые таблицы"):
            RUNNER.run({"YDB_SA_KEY": "private-json"})


def test_cleanup_names_are_closed_and_no_listing_or_teardown_exists():
    source = (ROOT / "scripts/run_ng_real_ydb_preflight.py").read_text()
    assert RUNNER.TABLE_SUFFIXES == ("command_runs", "lineages", "model_calls", "verification_results")
    assert "teardown_test_schema" not in source
    assert "scheme_client.list" not in source
    assert "DROP TABLE" in source


def test_cleanup_partial_schema_ignores_confirmed_scheme_not_found_and_drops_existing():
    calls = []
    class NotFound(Exception): pass
    class SchemeError(Exception): pass
    class Rows: rows = [{"n": 1}]
    class Tx:
        def execute(self, query, commit_tx=False):
            calls.append(("count", query))
            if "command_runs" in query: raise SchemeError("Path not found")
            if "lineages" in query: raise SchemeError("table does not exist")
            return [Rows()]
    class Session:
        def transaction(self): return Tx()
        def execute_scheme(self, query): calls.append(("drop", query))
    class Pool:
        def __init__(self, driver): pass
        def retry_operation_sync(self, operation): return operation(Session())
    class Driver:
        def __init__(self, **kwargs): pass
        def wait(self, **kwargs): pass
        def stop(self, **kwargs): pass
    fake = types.SimpleNamespace(
        iam=types.SimpleNamespace(ServiceAccountCredentials=types.SimpleNamespace(from_file=lambda path: object())),
        Driver=Driver, SessionPool=Pool, issues=types.SimpleNamespace(NotFound=NotFound, SchemeError=SchemeError),
    )
    environment = {
        "YDBDOC_REAL_YDB_TABLE_PREFIX": "m0_pr45949_0123456789abcdef",
        "YDBDOC_YDB_SA_KEY_FILE": "/safe/key", "YDBDOC_YDB_ENDPOINT": "endpoint", "YDBDOC_YDB_DATABASE": "database",
    }
    with patch.dict(sys.modules, {"ydb": fake}):
        RUNNER._cleanup_exact_tables(environment)
    expected = {f"m0_pr45949_0123456789abcdef_{suffix}" for suffix in RUNNER.TABLE_SUFFIXES}
    touched = {name for _, query in calls for name in expected if f"`{name}`" in query}
    assert touched == expected
    assert len([call for call in calls if call[0] == "count"]) == 4
    assert len([call for call in calls if call[0] == "drop"]) == 2


def test_cleanup_unknown_scheme_error_is_red():
    class NotFound(Exception): pass
    class SchemeError(Exception): pass
    class Tx:
        def execute(self, query, commit_tx=False): raise SchemeError("permission denied")
    class Session:
        def transaction(self): return Tx()
    class Pool:
        def __init__(self, driver): pass
        def retry_operation_sync(self, operation): return operation(Session())
    class Driver:
        def __init__(self, **kwargs): pass
        def wait(self, **kwargs): pass
        def stop(self, **kwargs): pass
    fake = types.SimpleNamespace(
        iam=types.SimpleNamespace(ServiceAccountCredentials=types.SimpleNamespace(from_file=lambda path: object())),
        Driver=Driver, SessionPool=Pool, issues=types.SimpleNamespace(NotFound=NotFound, SchemeError=SchemeError),
    )
    environment = {"YDBDOC_REAL_YDB_TABLE_PREFIX":"m0_pr45949_0123456789abcdef","YDBDOC_YDB_SA_KEY_FILE":"/safe/key","YDBDOC_YDB_ENDPOINT":"endpoint","YDBDOC_YDB_DATABASE":"database"}
    with patch.dict(sys.modules, {"ydb": fake}), pytest.raises(SchemeError, match="permission denied"):
        RUNNER._cleanup_exact_tables(environment)
