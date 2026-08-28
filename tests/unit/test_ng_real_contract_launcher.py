from __future__ import annotations

import builtins
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ng_real_contract", ROOT / "scripts/run_ng_real_contract.py")
assert SPEC and SPEC.loader
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


def test_imports_ydb_before_pytest_and_runs_only_exact_contract():
    order, calls = [], []
    original = builtins.__import__
    fake_pytest = types.SimpleNamespace(main=lambda args: calls.append(args) or 0)
    def importing(name, *args, **kwargs):
        if name == "ydb": order.append(name); return object()
        if name == "pytest": order.append(name); return fake_pytest
        return original(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=importing):
        assert LAUNCHER.main(["--junitxml=/safe/report.xml"]) == 0
    assert order == ["ydb", "pytest"]
    assert calls == [["/app/ng/tests/test_real_ydb_state.py", "--junitxml=/safe/report.xml", "-q"]]


def test_ydb_preload_failure_is_fixed_sanitized_and_never_imports_pytest(capsys):
    imported = []
    original = builtins.__import__
    def importing(name, *args, **kwargs):
        imported.append(name)
        if name == "ydb": raise ImportError("sentinel-secret endpoint database path")
        return original(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=importing):
        assert LAUNCHER.main(["--junitxml=/safe/report.xml"]) == LAUNCHER.YDB_IMPORT_FAILURE
    output = capsys.readouterr()
    assert output.err.strip() == "YDB_INIT_IMPORT"
    assert "sentinel" not in output.err
    assert "pytest" not in imported
