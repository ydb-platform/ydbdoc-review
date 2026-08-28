from __future__ import annotations

import builtins
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ng/src"))
from ydbdoc_review_ng.state import RepoIdentity, StateError, YdbConfig, YdbState


def _config():
    return YdbConfig("grpcs://example.invalid:2135", "/safe/database", "/safe/key")


def _sdk(stage=None):
    stopped, pool = [], object()
    def from_file(path):
        if stage == "credentials": raise RuntimeError("sentinel-private-key")
        return object()
    class Driver:
        def __init__(self, **kwargs):
            if stage == "driver": raise RuntimeError("sentinel-endpoint")
        def wait(self, **kwargs):
            if stage == "wait": raise RuntimeError("sentinel-database")
        def stop(self, **kwargs): stopped.append(kwargs)
    def session_pool(driver):
        if stage == "pool": raise RuntimeError("sentinel-private-key")
        return pool
    sdk = types.SimpleNamespace(
        iam=types.SimpleNamespace(ServiceAccountCredentials=types.SimpleNamespace(from_file=from_file)),
        Driver=Driver, SessionPool=session_pool,
    )
    return sdk, stopped, pool


@pytest.mark.parametrize(
    ("stage", "marker", "must_stop"),
    (("credentials", "YDB_INIT_CREDENTIALS", False), ("driver", "YDB_INIT_DRIVER", False),
     ("wait", "YDB_INIT_WAIT", True), ("pool", "YDB_INIT_POOL", True)),
)
def test_ydb_init_stage_marker_is_fixed_safe_and_stops_after_driver(stage, marker, must_stop):
    sdk, stopped, _ = _sdk(stage)
    with patch.dict(sys.modules, {"ydb": sdk}), pytest.raises(StateError) as caught:
        YdbState(_config(), RepoIdentity("owner", "repo"))
    assert str(caught.value) == marker
    assert "sentinel" not in str(caught.value)
    assert bool(stopped) is must_stop
    if must_stop:
        assert stopped == [{"timeout": 3}]


def test_ydb_init_import_marker_is_fixed_and_safe():
    original = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == "ydb": raise ImportError("sentinel-path")
        return original(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=importing), pytest.raises(StateError) as caught:
        YdbState(_config(), RepoIdentity("owner", "repo"))
    assert str(caught.value) == "YDB_INIT_IMPORT"
    assert "sentinel" not in str(caught.value)


def test_ydb_init_success_keeps_driver_and_pool():
    sdk, stopped, pool = _sdk()
    with patch.dict(sys.modules, {"ydb": sdk}):
        state = YdbState(_config(), RepoIdentity("owner", "repo"))
    assert state.pool is pool
    assert stopped == []
