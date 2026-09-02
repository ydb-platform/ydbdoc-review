"""Fail-closed three-part Ruff baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath

BASE = "9ff8edec9a26d3975306e20adca325c6eb9f77e6"
COMMAND = [".venv/bin/ruff", "check", "--no-cache", "--output-format=json"]
ACTIVE = {
    "src/ydbdoc_review/pipeline/navigation_merge.py",
    "scripts/remediation_ruff_gate.py",
    "tests/unit/test_remediation_ruff_gate.py",
    "scripts/remediation_policy_gate.py",
    "tests/unit/test_remediation_policy_gate.py",
}
# Live baseline is v025. Keep ruff-baseline-v020.json byte-immutable as history.
HISTORICAL_V020_BASELINE_SHA256 = "677896e7eba1af6c884fecf42a9543b40ef70b0caf3bf7e4d98521e8e6ff6ba7"
EXPECTED_BASELINE_SHA256 = "9ef2196e1d9752422faf1bf28bafe25bbf99b371343668c5062cd0a86de22e5c"
EXPECTED_RUFF_VERSION = "ruff 0.16.5"
EXPECTED_CONFIG_SHA256 = "822a7d659b893cc498725c18df0c72060f2eeba2df89725c520d4f5ed492ec29"
EXPECTED_DELTA_COUNT, EXPECTED_PRESENT_COUNT, EXPECTED_DELETED_COUNT = 124, 89, 35
EXPECTED_ACTIVE_COUNT, EXPECTED_FROZEN_COUNT, EXPECTED_BASE_UNTOUCHED_COUNT = 5, 84, 163
EXPECTED_BASE_DIAGNOSTIC_COUNT, EXPECTED_FROZEN_DIAGNOSTIC_COUNT = 189, 102
EXPECTED_TOTAL_DIAGNOSTIC_COUNT = 291
FIELDS = {
    "schema_version",
    "base_commit",
    "ruff_version",
    "command",
    "config_sha256",
    "active_python_paths",
    "deleted_python_paths",
    "base_untouched_paths",
    "base_untouched_diagnostic_count",
    "base_untouched_diagnostics",
    "frozen_current_files",
    "frozen_current_diagnostic_count",
    "frozen_current_diagnostics",
    "total_baseline_diagnostic_count",
}
DIAG_FIELDS = ("path", "row", "column", "end_row", "end_column", "code", "message")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True).stdout


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def changed(base: str) -> set[str]:
    paths = {
        line.split("\t", 1)[1]
        for line in git("diff", "--name-status", "--no-renames", base, "--").splitlines()
        if line.split("\t", 1)[1].endswith(".py")
    }
    return paths | {
        x
        for x in git("ls-files", "--others", "--exclude-standard").splitlines()
        if x.endswith(".py")
    }


def current() -> set[str]:
    paths = set(git("ls-files").splitlines()) | set(
        git("ls-files", "--others", "--exclude-standard").splitlines()
    )
    return {x for x in paths if x.endswith(".py") and Path(x).exists()}


def base_paths(base: str) -> set[str]:
    return {x for x in git("ls-tree", "-r", "--name-only", base).splitlines() if x.endswith(".py")}


def diag_key(item: dict) -> tuple:
    return tuple(item[field] for field in DIAG_FIELDS)


def records(exe: str, root: Path, paths: set[str]) -> list[dict]:
    if not paths:
        return []
    out = subprocess.run(
        [exe, *COMMAND[1:], *sorted(paths)], cwd=root, text=True, capture_output=True
    )
    if out.returncode not in (0, 1):
        raise ValueError(out.stderr)
    values = [
        {
            "path": Path(i["filename"]).resolve().relative_to(root.resolve()).as_posix(),
            "row": i["location"]["row"],
            "column": i["location"]["column"],
            "end_row": i["end_location"]["row"],
            "end_column": i["end_location"]["column"],
            "code": i["code"],
            "message": i["message"],
        }
        for i in json.loads(out.stdout)
    ]
    return sorted(values, key=diag_key)


def archive(base: str) -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory()
    data = subprocess.run(["git", "archive", base], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-xf", "-"], input=data, cwd=temp.name, check=True)
    return temp


def valid_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and value == path.as_posix()


def path_list(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(valid_path(x) for x in value)
        or value != sorted(value)
        or len(value) != len(set(value))
    ):
        raise ValueError("baseline schema drift")
    return value


def diagnostics(value: object, allowed: set[str]) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("baseline schema drift")
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != set(DIAG_FIELDS)
            or not valid_path(item["path"])
            or item["path"] not in allowed
        ):
            raise ValueError("baseline schema drift")
        if any(
            isinstance(item[f], bool) or not isinstance(item[f], int) or item[f] <= 0
            for f in DIAG_FIELDS[1:5]
        ):
            raise ValueError("baseline schema drift")
        if any(not isinstance(item[f], str) or not item[f] for f in ("code", "message")):
            raise ValueError("baseline schema drift")
    if value != sorted(value, key=diag_key):
        raise ValueError("baseline schema drift")
    return value


def _validate_baseline_payload(
    value: object, *, actual_ruff_version: str, current_config_sha256: str
) -> dict:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("baseline schema drift")
    if isinstance(value["schema_version"], bool) or value["schema_version"] != 2:
        raise ValueError("baseline schema drift")
    if (
        value["base_commit"] != BASE
        or value["command"] != COMMAND
        or value["ruff_version"] != EXPECTED_RUFF_VERSION
        or actual_ruff_version != EXPECTED_RUFF_VERSION
        or value["config_sha256"] != EXPECTED_CONFIG_SHA256
        or current_config_sha256 != EXPECTED_CONFIG_SHA256
    ):
        raise ValueError("baseline metadata drift")
    active, deleted, untouched = (
        path_list(value[k])
        for k in ("active_python_paths", "deleted_python_paths", "base_untouched_paths")
    )
    frozen_value = value["frozen_current_files"]
    if not isinstance(frozen_value, list):
        raise ValueError("baseline schema drift")
    frozen = []
    for item in frozen_value:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not valid_path(item["path"])
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise ValueError("baseline schema drift")
        frozen.append(item["path"])
    if frozen != sorted(frozen) or len(frozen) != len(set(frozen)):
        raise ValueError("baseline schema drift")
    sets = [set(active), set(deleted), set(untouched), set(frozen)]
    if any(sets[i] & sets[j] for i in range(4) for j in range(i + 1, 4)):
        raise ValueError("baseline schema drift")
    if active != sorted(ACTIVE) or len(active) != EXPECTED_ACTIVE_COUNT:
        raise ValueError("baseline count drift")
    base_diags = diagnostics(value["base_untouched_diagnostics"], set(untouched))
    frozen_diags = diagnostics(value["frozen_current_diagnostics"], set(frozen))
    counts = (
        value["base_untouched_diagnostic_count"],
        value["frozen_current_diagnostic_count"],
        value["total_baseline_diagnostic_count"],
    )
    if any(isinstance(x, bool) or not isinstance(x, int) for x in counts):
        raise ValueError("baseline count drift")
    if (
        counts
        != (
            EXPECTED_BASE_DIAGNOSTIC_COUNT,
            EXPECTED_FROZEN_DIAGNOSTIC_COUNT,
            EXPECTED_TOTAL_DIAGNOSTIC_COUNT,
        )
        or counts[:2] != (len(base_diags), len(frozen_diags))
        or counts[0] + counts[1] != counts[2]
    ):
        raise ValueError("baseline count drift")
    return value


def _load_and_validate_baseline(
    baseline: Path, *, actual_ruff_version: str, current_config_sha256: str
) -> dict:
    data = baseline.read_bytes()
    if hashlib.sha256(data).hexdigest() != EXPECTED_BASELINE_SHA256:
        raise ValueError("baseline artifact drift")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("baseline schema drift") from error
    return _validate_baseline_payload(
        value, actual_ruff_version=actual_ruff_version, current_config_sha256=current_config_sha256
    )


def capture(base: str, ruff: str, output: Path) -> None:
    if base != BASE or output.exists():
        raise ValueError("capture precondition")
    exe = str(Path(ruff).resolve(strict=True))
    delta = changed(base)
    now = current()
    present = delta & now
    deleted = delta - now
    if (
        (len(delta), len(present), len(deleted))
        != (EXPECTED_DELTA_COUNT, EXPECTED_PRESENT_COUNT, EXPECTED_DELETED_COUNT)
        or not ACTIVE <= present
        or records(exe, Path.cwd(), ACTIVE)
    ):
        raise ValueError("v025 active partition")
    untouched, frozen = base_paths(base) - delta, present - ACTIVE
    if (len(untouched), len(frozen)) != (
        EXPECTED_BASE_UNTOUCHED_COUNT,
        EXPECTED_FROZEN_COUNT,
    ):
        raise ValueError("v025 paths")
    with archive(base) as temp:
        base_records = records(exe, Path(temp), untouched)
    frozen_records = records(exe, Path.cwd(), frozen)
    if (len(base_records), len(frozen_records)) != (
        EXPECTED_BASE_DIAGNOSTIC_COUNT,
        EXPECTED_FROZEN_DIAGNOSTIC_COUNT,
    ):
        raise ValueError("v025 diagnostics")
    value = {
        "schema_version": 2,
        "base_commit": base,
        "ruff_version": subprocess.run(
            [exe, "--version"], check=True, text=True, capture_output=True
        ).stdout.strip(),
        "command": COMMAND,
        "config_sha256": sha("pyproject.toml"),
        "active_python_paths": sorted(ACTIVE),
        "deleted_python_paths": sorted(deleted),
        "base_untouched_paths": sorted(untouched),
        "base_untouched_diagnostic_count": EXPECTED_BASE_DIAGNOSTIC_COUNT,
        "base_untouched_diagnostics": base_records,
        "frozen_current_files": [{"path": x, "sha256": sha(x)} for x in sorted(frozen)],
        "frozen_current_diagnostic_count": EXPECTED_FROZEN_DIAGNOSTIC_COUNT,
        "frozen_current_diagnostics": frozen_records,
        "total_baseline_diagnostic_count": EXPECTED_TOTAL_DIAGNOSTIC_COUNT,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def validate(base: str, ruff: str, baseline: Path) -> None:
    if base != BASE:
        raise ValueError("baseline metadata drift")
    exe = str(Path(ruff).resolve(strict=True))
    version = subprocess.run(
        [exe, "--version"], check=True, text=True, capture_output=True
    ).stdout.strip()
    value = _load_and_validate_baseline(
        baseline, actual_ruff_version=version, current_config_sha256=sha("pyproject.toml")
    )
    delta, now = changed(base), current()
    present, deleted = delta & now, delta - now
    untouched, frozen = base_paths(base) - delta, present - ACTIVE
    if (
        (len(delta), len(present), len(deleted))
        != (EXPECTED_DELTA_COUNT, EXPECTED_PRESENT_COUNT, EXPECTED_DELETED_COUNT)
        or (len(ACTIVE), len(frozen), len(untouched))
        != (EXPECTED_ACTIVE_COUNT, EXPECTED_FROZEN_COUNT, EXPECTED_BASE_UNTOUCHED_COUNT)
        or not ACTIVE <= present
        or value["active_python_paths"] != sorted(ACTIVE)
        or value["deleted_python_paths"] != sorted(deleted)
        or value["base_untouched_paths"] != sorted(untouched)
        or [x["path"] for x in value["frozen_current_files"]] != sorted(frozen)
    ):
        raise ValueError("partition drift")
    if any(sha(x["path"]) != x["sha256"] for x in value["frozen_current_files"]):
        raise ValueError("frozen byte drift")
    if records(exe, Path.cwd(), ACTIVE):
        raise ValueError("diagnostic drift")
    base_now, frozen_now = records(exe, Path.cwd(), untouched), records(exe, Path.cwd(), frozen)
    if (
        Counter(map(diag_key, base_now))
        - Counter(map(diag_key, value["base_untouched_diagnostics"]))
        or Counter(map(diag_key, frozen_now))
        - Counter(map(diag_key, value["frozen_current_diagnostics"]))
        or len(base_now) + len(frozen_now) > EXPECTED_TOTAL_DIAGNOSTIC_COUNT
    ):
        raise ValueError("diagnostic drift")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["capture-baseline", "validate"])
    parser.add_argument("--base", required=True)
    parser.add_argument("--ruff", required=True)
    parser.add_argument("--output")
    parser.add_argument("--baseline")
    args = parser.parse_args()
    capture(
        args.base, args.ruff, Path(args.output)
    ) if args.mode == "capture-baseline" else validate(args.base, args.ruff, Path(args.baseline))
    print("GREEN: remediation Ruff gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
