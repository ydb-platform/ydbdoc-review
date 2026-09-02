"""Approved fail-closed coverage for the remediation Ruff gate."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import scripts.remediation_ruff_gate as gate
from scripts.remediation_ruff_gate import (
    ACTIVE,
    BASE,
    EXPECTED_BASELINE_SHA256,
    EXPECTED_CONFIG_SHA256,
    EXPECTED_RUFF_VERSION,
    EXPECTED_TOTAL_DIAGNOSTIC_COUNT,
    HISTORICAL_V020_BASELINE_SHA256,
    _load_and_validate_baseline,
    _validate_baseline_payload,
)

BASELINE = Path(".ai-workflow/tasks/TASK-51797-ONE-PASS/ruff-baseline-v025.json")
HISTORICAL_V020 = Path(".ai-workflow/tasks/TASK-51797-ONE-PASS/ruff-baseline-v020.json")


def test_active_partition_has_exact_five_paths() -> None:
    assert BASE == "9ff8edec9a26d3975306e20adca325c6eb9f77e6"
    assert len(ACTIVE) == 5


def test_historical_v020_baseline_bytes_remain_immutable() -> None:
    assert hashlib.sha256(HISTORICAL_V020.read_bytes()).hexdigest() == HISTORICAL_V020_BASELINE_SHA256


def test_genuine_baseline_has_exact_immutable_sha_and_contract() -> None:
    before = BASELINE.read_bytes()
    assert hashlib.sha256(before).hexdigest() == EXPECTED_BASELINE_SHA256
    loaded = _load_and_validate_baseline(
        BASELINE,
        actual_ruff_version=EXPECTED_RUFF_VERSION,
        current_config_sha256=EXPECTED_CONFIG_SHA256,
    )
    after = BASELINE.read_bytes()
    assert after == before
    assert loaded["ruff_version"] == EXPECTED_RUFF_VERSION
    assert loaded["config_sha256"] == EXPECTED_CONFIG_SHA256
    assert loaded["schema_version"] == 2


def _write_temp_baseline(tmp_path: Path, mutator) -> Path:
    value = json.loads(BASELINE.read_text(encoding="utf-8"))
    mutator(value)
    path = tmp_path / "forged-baseline.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _fail_if_records(*_args, **_kwargs):
    raise AssertionError("records() must not run before baseline/partition checks")


def _version_only_subprocess(*_args, **_kwargs):
    return type(
        "R", (), {"stdout": EXPECTED_RUFF_VERSION, "returncode": 0, "stderr": ""}
    )()


@pytest.mark.parametrize(
    "mutator",
    [
        lambda v: v.__setitem__("ruff_version", "tampered-version"),
        lambda v: v.__setitem__("base_untouched_diagnostic_count", -999),
        lambda v: v.__setitem__("frozen_current_diagnostic_count", -999),
        lambda v: v.__setitem__("total_baseline_diagnostic_count", -999),
        lambda v: v.pop("command"),
        lambda v: v.__setitem__("extra_field", True),
        lambda v: v.__setitem__("config_sha256", "0" * 64),
        lambda v: v.__setitem__(
            "active_python_paths", [*sorted(ACTIVE)[:-1], "x.py"]
        ),
        lambda v: v.__setitem__(
            "deleted_python_paths",
            [*v["deleted_python_paths"][1:], "gone.py"],
        ),
        lambda v: v.__setitem__(
            "base_untouched_paths",
            [*v["base_untouched_paths"][1:], "untouched-x.py"],
        ),
        lambda v: v["frozen_current_files"].__setitem__(
            0, {"path": "frozen-x.py", "sha256": "a" * 64}
        ),
        lambda v: v["frozen_current_files"].__setitem__(
            0,
            {
                "path": v["frozen_current_files"][0]["path"],
                "sha256": "b" * 64,
            },
        ),
        lambda v: v["base_untouched_diagnostics"].__setitem__(
            0,
            {
                key: v["base_untouched_diagnostics"][0][key]
                for key in v["base_untouched_diagnostics"][0]
                if key != "message"
            },
        ),
        lambda v: v["base_untouched_diagnostics"][0].__setitem__("extra", 1),
        lambda v: v["base_untouched_diagnostics"][0].__setitem__("row", "1"),
    ],
)
def test_validate_rejects_tampered_baseline_before_any_ruff_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutator
) -> None:
    forged = _write_temp_baseline(tmp_path, mutator)
    before = BASELINE.read_bytes()
    monkeypatch.setattr(gate, "records", _fail_if_records)
    monkeypatch.setattr(gate.subprocess, "run", _version_only_subprocess)
    with pytest.raises(ValueError, match=r"baseline (artifact|schema|metadata|count) drift"):
        gate.validate(BASE, ".venv/bin/ruff", forged)
    assert BASELINE.read_bytes() == before


@pytest.mark.parametrize(
    ("actual_version", "config_sha"),
    [
        ("ruff 0.0.0", EXPECTED_CONFIG_SHA256),
        (EXPECTED_RUFF_VERSION, "0" * 64),
    ],
)
def test_baseline_payload_rejects_runtime_version_and_config_drift(
    actual_version: str, config_sha: str
) -> None:
    value = json.loads(BASELINE.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="baseline metadata drift"):
        _validate_baseline_payload(
            value,
            actual_ruff_version=actual_version,
            current_config_sha256=config_sha,
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda v: v.__setitem__("schema_version", True),
        lambda v: v.__setitem__("base_untouched_diagnostic_count", True),
        lambda v: v.__setitem__(
            "active_python_paths", list(reversed(sorted(ACTIVE)))
        ),
        lambda v: v.__setitem__(
            "active_python_paths", [*sorted(ACTIVE), sorted(ACTIVE)[0]]
        ),
        lambda v: v.__setitem__("active_python_paths", ["/abs.py"]),
        lambda v: v["frozen_current_files"].__setitem__(
            0,
            {
                "path": v["frozen_current_files"][0]["path"],
                "sha256": "ZZ",
            },
        ),
        lambda v: v.__setitem__(
            "base_untouched_diagnostics",
            list(reversed(v["base_untouched_diagnostics"])),
        ),
        lambda v: v.__setitem__(
            "base_untouched_diagnostic_count",
            len(v["base_untouched_diagnostics"]) + 1,
        ),
        lambda v: v.__setitem__(
            "total_baseline_diagnostic_count",
            v["base_untouched_diagnostic_count"]
            + v["frozen_current_diagnostic_count"]
            + 1,
        ),
    ],
)
def test_baseline_payload_rejects_noncanonical_schema_and_count_arithmetic(
    mutator,
) -> None:
    value = json.loads(BASELINE.read_text(encoding="utf-8"))
    mutator(value)
    with pytest.raises(ValueError, match=r"baseline (schema|count|metadata) drift"):
        _validate_baseline_payload(
            value,
            actual_ruff_version=EXPECTED_RUFF_VERSION,
            current_config_sha256=EXPECTED_CONFIG_SHA256,
        )


def _install_exact_partitions(monkeypatch: pytest.MonkeyPatch) -> dict:
    value = json.loads(BASELINE.read_text(encoding="utf-8"))
    deleted = set(value["deleted_python_paths"])
    untouched = set(value["base_untouched_paths"])
    frozen = {item["path"] for item in value["frozen_current_files"]}
    present = set(ACTIVE) | frozen
    default_delta = present | deleted
    default_now = present | untouched
    monkeypatch.setattr(gate, "changed", lambda _base: set(default_delta))
    monkeypatch.setattr(gate, "current", lambda: set(default_now))
    monkeypatch.setattr(
        gate,
        "base_paths",
        lambda _base: set(untouched | deleted),
    )
    hashes = {item["path"]: item["sha256"] for item in value["frozen_current_files"]}
    monkeypatch.setattr(
        gate,
        "sha",
        lambda path: EXPECTED_CONFIG_SHA256
        if str(path) == "pyproject.toml"
        else hashes.get(str(path), "0" * 64),
    )
    monkeypatch.setattr(gate.subprocess, "run", _version_only_subprocess)
    return value


@pytest.mark.parametrize(
    "kind",
    ["short_delta", "missing_active", "extra_deleted", "extra_present"],
)
def test_validate_rejects_partition_cardinality_or_membership_drift_before_semantic_compare(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    value = json.loads(BASELINE.read_text(encoding="utf-8"))
    deleted = set(value["deleted_python_paths"])
    untouched = set(value["base_untouched_paths"])
    frozen = {item["path"] for item in value["frozen_current_files"]}
    present = set(ACTIVE) | frozen
    delta = present | deleted
    now = present | untouched
    base = untouched | deleted
    if kind == "short_delta":
        delta = set(list(delta)[:110])
    elif kind == "missing_active":
        now = now - {next(iter(ACTIVE))}
    elif kind == "extra_deleted":
        delta = delta | {"extra-deleted.py"}
    else:
        delta = delta | {"extra-present.py"}
        now = now | {"extra-present.py"}
    monkeypatch.setattr(gate, "changed", lambda _base: set(delta))
    monkeypatch.setattr(gate, "current", lambda: set(now))
    monkeypatch.setattr(gate, "base_paths", lambda _base: set(base))
    monkeypatch.setattr(
        gate,
        "sha",
        lambda path: EXPECTED_CONFIG_SHA256
        if str(path) == "pyproject.toml"
        else "0" * 64,
    )
    monkeypatch.setattr(gate, "records", _fail_if_records)
    monkeypatch.setattr(gate.subprocess, "run", _version_only_subprocess)
    with pytest.raises(ValueError, match="partition drift"):
        gate.validate(BASE, ".venv/bin/ruff", BASELINE)


def test_validate_rejects_frozen_byte_drift_before_semantic_compare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _install_exact_partitions(monkeypatch)
    hashes = {item["path"]: item["sha256"] for item in value["frozen_current_files"]}
    victim = next(iter(hashes))

    def sha_spy(path):
        if str(path) == "pyproject.toml":
            return EXPECTED_CONFIG_SHA256
        if str(path) == victim:
            return "c" * 64
        return hashes.get(str(path), "0" * 64)

    monkeypatch.setattr(gate, "sha", sha_spy)
    monkeypatch.setattr(gate, "records", _fail_if_records)
    with pytest.raises(ValueError, match="frozen byte drift"):
        gate.validate(BASE, ".venv/bin/ruff", BASELINE)


def _diag(path: str, row: int = 1, code: str = "X001") -> dict:
    return {
        "path": path,
        "row": row,
        "column": 1,
        "end_row": row,
        "end_column": 2,
        "code": code,
        "message": "x",
    }


@pytest.mark.parametrize("mode", ["new", "changed", "multiplied"])
def test_validate_rejects_new_moved_or_multiplied_diagnostic(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    value = _install_exact_partitions(monkeypatch)
    base_diags = copy.deepcopy(value["base_untouched_diagnostics"])
    frozen_diags = copy.deepcopy(value["frozen_current_diagnostics"])
    untouched_path = value["base_untouched_paths"][0]
    if mode == "new":
        live_base = [*base_diags, _diag(untouched_path, row=999, code="NEW001")]
        live_frozen = frozen_diags
    elif mode == "changed":
        live_base = copy.deepcopy(base_diags)
        live_base[0] = {**live_base[0], "row": live_base[0]["row"] + 1000}
        live_frozen = frozen_diags
    else:
        live_base = [*base_diags, copy.deepcopy(base_diags[0])]
        live_frozen = frozen_diags

    def records_spy(_exe, _root, paths):
        if paths == ACTIVE:
            return []
        if paths == set(value["base_untouched_paths"]):
            return live_base
        if paths == {item["path"] for item in value["frozen_current_files"]}:
            return live_frozen
        return []

    monkeypatch.setattr(gate, "records", records_spy)
    with pytest.raises(ValueError, match="diagnostic drift"):
        gate.validate(BASE, ".venv/bin/ruff", BASELINE)


def test_validate_rejects_total_over_306_even_when_partition_subsets_are_not_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _install_exact_partitions(monkeypatch)
    untouched_path = value["base_untouched_paths"][0]
    base_diags = [
        _diag(untouched_path, row=i + 1, code=f"Z{i:03d}") for i in range(250)
    ]
    frozen_diags = copy.deepcopy(value["frozen_current_diagnostics"])

    def records_spy(_exe, _root, paths):
        if paths == ACTIVE:
            return []
        if paths == set(value["base_untouched_paths"]):
            return base_diags
        if paths == {item["path"] for item in value["frozen_current_files"]}:
            return frozen_diags
        return []

    class AlwaysSubsetCounter(gate.Counter):
        def __sub__(self, other):
            return gate.Counter()

    monkeypatch.setattr(gate, "records", records_spy)
    monkeypatch.setattr(gate, "Counter", AlwaysSubsetCounter)
    assert len(base_diags) + len(frozen_diags) > EXPECTED_TOTAL_DIAGNOSTIC_COUNT
    with pytest.raises(ValueError, match="diagnostic drift"):
        gate.validate(BASE, ".venv/bin/ruff", BASELINE)


def test_validate_allows_only_diagnostic_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _install_exact_partitions(monkeypatch)
    base_diags = copy.deepcopy(value["base_untouched_diagnostics"])[1:]
    frozen_diags = copy.deepcopy(value["frozen_current_diagnostics"])[1:]

    def records_spy(_exe, _root, paths):
        if paths == ACTIVE:
            return []
        if paths == set(value["base_untouched_paths"]):
            return base_diags
        if paths == {item["path"] for item in value["frozen_current_files"]}:
            return frozen_diags
        return []

    monkeypatch.setattr(gate, "records", records_spy)
    before = BASELINE.read_bytes()
    gate.validate(BASE, ".venv/bin/ruff", BASELINE)
    assert BASELINE.read_bytes() == before
