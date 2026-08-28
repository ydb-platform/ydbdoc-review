from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "ng/src", ROOT / "ng/tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
SPEC = importlib.util.spec_from_file_location(
    "ng_real_ydb_contract_fixture", ROOT / "ng/tests/test_real_ydb_state.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.RealYdbStateContract
CONTRACT.__test__ = False
REPOSITORY = "acceptance/r0123456789abcdef"


class FakeDriver:
    def __init__(self):
        self.stop_calls = []

    def stop(self, *, timeout):
        self.stop_calls.append(timeout)


class FakeState:
    instances = []
    ensure_error = None
    cleanup_error_at = None

    def __init__(self, config, repository):
        self.config = config
        self.repository = repository
        self.driver = FakeDriver()
        self.ensure_calls = 0
        self.cleanup_calls = []
        type(self).instances.append(self)

    def ensure_schema(self):
        self.ensure_calls += 1
        if self.ensure_error is not None:
            raise self.ensure_error

    def cleanup_test_rows(self, *, maximum_rows):
        self.cleanup_calls.append(maximum_rows)
        if self.cleanup_error_at == len(self.cleanup_calls):
            raise RuntimeError("cleanup failed")


@pytest.fixture(autouse=True)
def reset_contract_fixture():
    FakeState.instances.clear()
    FakeState.ensure_error = None
    FakeState.cleanup_error_at = None
    CONTRACT.__dict__.get("state") and delattr(CONTRACT, "state")
    yield
    if "state" in CONTRACT.__dict__:
        delattr(CONTRACT, "state")


def lifecycle():
    environment = {"YDBDOC_REAL_YDB_REPOSITORY": REPOSITORY}
    return patch.multiple(
        MODULE,
        YdbState=FakeState,
        real_ydb_test_config_from_env=lambda value: "config",
    ), patch.dict(os.environ, environment, clear=True)


def start_class():
    module_patch, environment_patch = lifecycle()
    module_patch.start()
    environment_patch.start()
    try:
        CONTRACT.setUpClass()
    except BaseException:
        environment_patch.stop()
        module_patch.stop()
        raise
    return module_patch, environment_patch, FakeState.instances[0]


def stop_patches(patches):
    module_patch, environment_patch, _ = patches
    environment_patch.stop()
    module_patch.stop()


def test_ensure_runs_once_and_all_tests_share_one_state_and_driver():
    patches = start_class()
    try:
        _, _, state = patches
        first = CONTRACT(methodName="test_a_schema_exact_four_tables_and_nonce_columns")
        second = CONTRACT(methodName="test_b_receipt_explicit_winner_duplicate_and_conflict")
        first.setUp()
        second.setUp()
        assert first.state is second.state is state
        assert len(FakeState.instances) == 1
        assert state.ensure_calls == 1
        assert first.state.driver is second.state.driver
    finally:
        stop_patches(patches)


def test_per_test_cleanup_runs_before_and_after_without_schema_or_stop():
    patches = start_class()
    try:
        _, _, state = patches
        case = CONTRACT(methodName="test_a_schema_exact_four_tables_and_nonce_columns")
        case.setUp()
        case.tearDown()
        assert state.cleanup_calls == [1000, 1000]
        assert state.ensure_calls == 1
        assert state.driver.stop_calls == []
    finally:
        stop_patches(patches)


def test_cleanup_uses_exact_repository_and_maximum_1000():
    patches = start_class()
    try:
        _, _, state = patches
        case = CONTRACT(methodName="test_a_schema_exact_four_tables_and_nonce_columns")
        case.setUp()
        case.tearDown()
        assert state.repository.canonical == REPOSITORY
        assert state.cleanup_calls == [1000, 1000]
    finally:
        stop_patches(patches)


def test_final_cleanup_and_driver_stop_run_once():
    patches = start_class()
    try:
        _, _, state = patches
        CONTRACT.tearDownClass()
        assert state.cleanup_calls == [1000]
        assert state.driver.stop_calls == [5]
    finally:
        stop_patches(patches)


def test_final_cleanup_error_still_stops_driver_once():
    patches = start_class()
    try:
        _, _, state = patches
        FakeState.cleanup_error_at = 1
        with pytest.raises(RuntimeError, match="cleanup failed"):
            CONTRACT.tearDownClass()
        assert state.driver.stop_calls == [5]
    finally:
        stop_patches(patches)


def test_ensure_error_stops_driver_and_prevents_test_setup():
    FakeState.ensure_error = RuntimeError("ensure failed")
    module_patch, environment_patch = lifecycle()
    with module_patch, environment_patch, pytest.raises(RuntimeError, match="ensure failed"):
        CONTRACT.setUpClass()
    state = FakeState.instances[0]
    assert state.ensure_calls == 1
    assert state.cleanup_calls == []
    assert state.driver.stop_calls == [5]


def test_real_contract_still_contains_exactly_16_tests():
    assert len(unittest.defaultTestLoader.getTestCaseNames(CONTRACT)) == 16
