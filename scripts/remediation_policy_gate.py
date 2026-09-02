"""Fail-closed policy gate for the one-pass remediation worktree.

The remediation is intentionally performed on a dirty worktree.  This tool
records that starting delta once, then distinguishes its frozen baseline from
later executor edits.  It is not a translation-pipeline component.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml


def _run_git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, text=True, capture_output=True
    ).stdout


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _base_bytes(base: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{base}:{path}"], check=True, capture_output=True
    ).stdout


def _path_state(base: str, path: str, git_status: str) -> dict[str, str]:
    file_path = Path(path)
    if file_path.exists():
        return {"kind": "present", "sha256": _sha256_bytes(file_path.read_bytes())}
    if git_status.startswith("D"):
        return {"kind": "deleted", "sha256": _sha256_bytes(_base_bytes(base, path))}
    return {"kind": "absent", "sha256": ""}


def load_plan(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"plan {path} must be a YAML mapping")
    return value


def load_snapshot(path: Path) -> dict[str, Any]:
    """Load, but never rewrite, the immutable remediation snapshot."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise ValueError(f"snapshot {path} must contain an entries list")
    return value


def _resolved_amendment(path: Path) -> dict[str, Any]:
    """Resolve the reviewed v008 count-only amendment onto the v006 contract.

    v008 deliberately contains only the delta from v006.  Its ``amends`` field
    fixes the predecessor file name, so this is a deterministic composition,
    not an executor-selected policy.
    """
    amendment = load_plan(path)
    if amendment.get("plan_version") == "one-pass-remediation-v025":
        predecessor = path.with_name("implementation-plan-v024-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v024":
            raise ValueError("v025 predecessor")
        resolved = copy.deepcopy(_resolved_amendment(predecessor))
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        extensions = {
            item["path"]: (item["mapping_source"], item["ownership_class"], item["requirement_ids"])
            for item in amendment["R016_extension"]["exact_mappings"]
        }
        merged = dict(resolved.get("v021_exact_mappings", {}))
        merged.update(extensions)
        resolved["v021_exact_mappings"] = merged
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v024":
        predecessor = path.with_name("implementation-plan-v023-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v023":
            raise ValueError("v024 predecessor")
        resolved = copy.deepcopy(_resolved_amendment(predecessor))
        resolved["post_capture_control_paths"]["exact_paths"] += list(amendment["post_capture_control_paths_addition"]["exact_paths"])
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v023":
        predecessor = path.with_name("implementation-plan-v022-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v022":
            raise ValueError("v023 predecessor")
        resolved = copy.deepcopy(_resolved_amendment(predecessor))
        resolved["post_capture_control_paths"]["exact_paths"] += list(amendment["post_capture_control_paths_addition"]["exact_paths"])
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v022":
        predecessor = path.with_name("implementation-plan-v021-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v021":
            raise ValueError("v022 predecessor")
        resolved = copy.deepcopy(_resolved_amendment(predecessor))
        resolved["post_capture_control_paths"]["exact_paths"] += list(amendment["post_capture_control_paths_addition"]["exact_paths"])
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v021":
        predecessor = path.with_name("implementation-plan-v020-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v020":
            raise ValueError("v021 predecessor")
        resolved = copy.deepcopy(_resolved_amendment(predecessor))
        resolved["post_capture_control_paths"]["exact_paths"] += list(amendment["post_capture_control_paths_addition"]["exact_paths"])
        resolved["v021_exact_mappings"] = {
            item["path"]: (item["mapping_source"], item["ownership_class"], item["requirement_ids"])
            for item in amendment["R004_extension"]["exact_mappings"]
        }
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v020":
        predecessor = path.with_name("implementation-plan-v019-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v019":
            raise ValueError("v020 predecessor")
        resolved = copy.deepcopy(_resolved_amendment(predecessor))
        resolved["post_capture_control_paths"]["exact_paths"] += list(amendment["post_capture_control_paths_addition"]["exact_paths"])
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v019":
        predecessor = path.with_name("implementation-plan-v018-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v018":
            raise ValueError("v019 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path:
            raise ValueError("v019 requires a resolved v018 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        resolved["v019_exact_deletions"] = list(
            amendment["exact_deletion_extension"]["exact_paths"]
        )
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v018":
        predecessor = path.with_name("implementation-plan-v017-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v017":
            raise ValueError("v018 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path:
            raise ValueError("v018 requires a resolved v017 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v017":
        predecessor = path.with_name("implementation-plan-v016-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v016":
            raise ValueError("v017 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path:
            raise ValueError("v017 requires a resolved v016 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v016":
        predecessor = path.with_name("implementation-plan-v015-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v015":
            raise ValueError("v016 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path or resolved.get("v015_refresh_command") is None:
            raise ValueError("v016 requires a resolved v015 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v015":
        predecessor = path.with_name("implementation-plan-v014-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v014":
            raise ValueError("v015 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path or resolved.get("v014_legacy_test_alignment") is None:
            raise ValueError("v015 requires a resolved v014 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        resolved["manifest_lifecycle"] = copy.deepcopy(amendment["manifest_lifecycle"])
        resolved["v015_refresh_command"] = amendment["refresh_command"]
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v014":
        predecessor = path.with_name("implementation-plan-v013-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v013":
            raise ValueError("v014 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path or resolved.get("clean_at_capture_item_baseline") is None:
            raise ValueError("v014 requires a resolved v013 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        resolved["v014_legacy_test_alignment"] = {
            change["path"]: (
                "v014-legacy-test-alignment",
                "test",
                list(change["requirement_ids"]),
            )
            for change in amendment["test_changes"]
        }
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v013":
        predecessor = path.with_name("implementation-plan-v012-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v012":
            raise ValueError("v013 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path or resolved.get("clean_at_capture_item_baseline") is None:
            raise ValueError("v013 requires a resolved v012 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v012":
        predecessor = path.with_name("implementation-plan-v011-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v011":
            raise ValueError("v012 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path or resolved.get("immutable_snapshot_output") is None:
            raise ValueError("v012 requires a resolved v011 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        resolved["clean_at_capture_item_baseline"] = copy.deepcopy(
            amendment["clean_at_capture_item_baseline"]
        )
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v011":
        predecessor = path.with_name("implementation-plan-v009-amendment.yaml")
        if amendment.get("amends") != "one-pass-remediation-v009":
            raise ValueError("v011 predecessor does not match its approved amends value")
        resolved = _resolved_amendment(predecessor)
        if predecessor == path or resolved.get("immutable_snapshot_output") is None:
            raise ValueError("v011 requires a resolved v009 predecessor")
        resolved = copy.deepcopy(resolved)
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        return resolved
    if amendment.get("plan_version") == "one-pass-remediation-v009":
        predecessor = path.with_name("implementation-plan-v008-amendment.yaml")
        base = _resolved_amendment(predecessor)
        if amendment.get("amends") != "one-pass-remediation-v008":
            raise ValueError("v009 predecessor does not match its approved amends value")
        resolved = copy.deepcopy(base)
        resolved["immutable_snapshot_output"] = amendment["immutable_snapshot_output"]
        resolved["post_capture_control_paths"]["exact_paths"] += list(
            amendment["post_capture_control_paths_addition"]["exact_paths"]
        )
        return resolved
    if amendment.get("plan_version") != "one-pass-remediation-v008":
        return amendment

    predecessor = path.with_name("implementation-plan-v006-amendment.yaml")
    base = load_plan(predecessor)
    if base.get("plan_version") != amendment.get("amends"):
        raise ValueError("v008 predecessor does not match its approved amends value")
    resolved = copy.deepcopy(base)
    authoritative = amendment["authoritative_snapshot"]
    resolved["authoritative_inputs"]["snapshot"] = authoritative["path"]
    resolved["authoritative_inputs"]["base_commit"] = authoritative["base_commit"]
    resolved["authoritative_inputs"]["snapshot_entry_count"] = authoritative["entry_count"]
    resolved["post_capture_control_paths"]["exact_paths"] = (
        list(resolved["post_capture_control_paths"]["exact_paths"])
        + list(amendment["post_capture_control_paths_addition"]["exact_paths"])
    )
    resolved["v008_snapshot_contract"] = authoritative
    return resolved


def _validate_snapshot_file_bytes(snapshot_bytes: bytes, amendment: dict[str, Any]) -> None:
    contract = amendment.get("v008_snapshot_contract")
    if contract is not None and _sha256_bytes(snapshot_bytes) != contract["sha256"]:
        raise ValueError("snapshot SHA-256 does not match v008")


def _validate_snapshot_contract(
    snapshot: dict[str, Any], base: str, amendment: dict[str, Any]
) -> None:
    """Fail closed on all reviewed snapshot identity facts before any mapping."""
    if snapshot.get("base_commit") != base:
        raise ValueError("snapshot base_commit does not match --base")
    entries = snapshot.get("entries", [])
    expected_count = amendment["authoritative_inputs"]["snapshot_entry_count"]
    if len(entries) != expected_count:
        raise ValueError("snapshot entry count does not match approved amendment")
    paths = [entry.get("path") for entry in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("snapshot has duplicate paths")
    contract = amendment.get("v008_snapshot_contract")
    if contract is not None:
        if len(entries) != contract["entry_count"]:
            raise ValueError("snapshot entry count does not match v008")
        status_counts: dict[str, int] = {}
        for entry in entries:
            status = entry.get("git_status")
            status_counts[status] = status_counts.get(status, 0) + 1
        if status_counts != contract["git_status_counts"]:
            raise ValueError("snapshot git-status counts do not match v008")


def enumerate_delta(base: str) -> list[dict[str, Any]]:
    """Return one deterministic entry for every tracked or untracked delta path."""
    statuses: dict[str, str] = {}
    for line in _run_git("diff", "--name-status", "--no-renames", base, "--").splitlines():
        status, path = line.split("\t", 1)
        statuses[path] = status
    for path in _run_git("ls-files", "--others", "--exclude-standard").splitlines():
        statuses.setdefault(path, "??")
    return [
        {"path": path, "git_status": status, "state": _path_state(base, path, status)}
        for path, status in sorted(statuses.items())
    ]


def capture_baseline(plan: dict[str, Any], base: str, output: Path) -> None:
    if output.exists():
        raise ValueError(f"baseline snapshot already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "protocol_version": 1,
        "task_id": plan["task_id"],
        "plan_version": plan["plan_version"],
        "base_commit": base,
        "entries": enumerate_delta(base),
    }
    output.write_text(yaml.safe_dump(snapshot, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise ValueError(f"manifest {path} must contain an entries list")
    return value


def normalized_manifest_self_sha256(manifest: dict[str, Any], manifest_path: str) -> str:
    """Hash a manifest with only its own final SHA field blanked.

    The v006 amendment defines this normalization to avoid a mathematically
    impossible literal hash of a file that contains that same hash.
    """
    normalized = copy.deepcopy(manifest)
    matching = [entry for entry in normalized["entries"] if entry["path"] == manifest_path]
    if len(matching) != 1:
        raise ValueError("manifest must contain its self entry exactly once")
    matching[0]["final_state"]["sha256"] = ""
    serialized = yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True)
    return _sha256_bytes(serialized.encode("utf-8"))


def _inventory_mapping(plan: dict[str, Any], path: str) -> tuple[str, str, list[str]] | None:
    """Return the one v005 inventory mapping for *path*, or fail closed."""
    inventory = plan.get("baseline_contract_inventory", {})
    matches: list[tuple[str, str, list[str]]] = []
    for group_name, ownership_class in (
        ("frozen_modified_production", "production"),
        ("approved_deleted_production", "production"),
        ("frozen_modified_tests", "test"),
        ("approved_deleted_tests_and_fixtures", "test"),
    ):
        group = inventory.get(group_name, {})
        if isinstance(group, dict) and path in set(group.get("exact_paths", [])):
            requirements = group.get("requirement_ids", [])
            matches.append((f"baseline-inventory:{group_name}", ownership_class, sorted(set(requirements))))
        if isinstance(group, list):
            for item in group:
                if isinstance(item, dict) and item.get("path") == path:
                    matches.append(
                        (
                            f"baseline-inventory:{group_name}",
                            ownership_class,
                            sorted(set(item.get("requirement_ids", []))),
                        )
                    )
    if len(matches) > 1:
        raise ValueError(f"RED: conflicting baseline inventory mappings: {path}")
    return matches[0] if matches else None


def _v005_control_mapping(plan: dict[str, Any], path: str) -> tuple[str, str, list[str]] | None:
    inventory = plan.get("baseline_contract_inventory", {})
    matches: list[tuple[str, str, list[str]]] = []
    for group_name in ("immutable_control_artifacts", "planned_analyst_publication_before_capture"):
        group = inventory.get(group_name, {})
        if path in set(group.get("exact_paths", [])):
            matches.append(
                (
                    f"v005-control:{group_name}",
                    "control_artifact",
                    sorted(set(group.get("requirement_ids", ["workflow-protocol-provenance"]))),
                )
            )
    if len(matches) > 1:
        raise ValueError(f"RED: conflicting v005 control mappings: {path}")
    return matches[0] if matches else None


def _exact_item_allowlist_mapping(
    plan: dict[str, Any], path: str
) -> tuple[str, str, list[str]]:
    """Map a clean-at-capture path only through exact literal item entries."""
    matching_items = [
        item
        for item in plan.get("items", [])
        if path in {
            allowed_path
            for allowed_path in item.get("allowed_files", [])
            if not any(character in allowed_path for character in "*?[")
        }
    ]
    if not matching_items:
        raise ValueError(f"RED: clean-at-capture path lacks exact item allowlist: {path}")
    if path.startswith("tests/"):
        ownership = "test"
    elif path.startswith("scripts/"):
        ownership = "script"
    elif path.startswith("src/ydbdoc_review/config/"):
        ownership = "configuration"
    elif path.startswith("src/"):
        ownership = "production"
    else:
        raise ValueError(f"RED: clean-at-capture path has unmapped ownership class: {path}")
    requirements = sorted({item["requirement_id"] for item in matching_items})
    return "clean-at-capture-exact-item", ownership, requirements


def _clean_at_capture_mapping(
    plan: dict[str, Any], amendment: dict[str, Any], path: str
) -> tuple[str, str, list[str]]:
    exact = amendment.get("v021_exact_mappings", {}).get(path)
    if exact is not None:
        return exact
    exact_amendment_mappings = amendment.get("v014_legacy_test_alignment", {})
    mapping = exact_amendment_mappings.get(path)
    if mapping is not None:
        return mapping
    return _exact_item_allowlist_mapping(plan, path)


def _git_blob_state_at_base(base: str, path: str) -> dict[str, str]:
    """Read one exact base-tree item without consulting the working tree."""
    try:
        result = subprocess.run(
            ["git", "ls-tree", "-z", base, "--", path],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError(f"RED: base object lookup failed: {path}") from error
    entries = [entry for entry in result.stdout.split(b"\0") if entry]
    if not entries:
        return {"kind": "absent", "sha256": ""}
    if len(entries) != 1:
        raise ValueError(f"RED: ambiguous base object: {path}")
    metadata, separator, object_path = entries[0].partition(b"\t")
    if not separator or object_path.decode("utf-8", errors="strict") != path:
        raise ValueError(f"RED: ambiguous base object: {path}")
    fields = metadata.split()
    if len(fields) != 3:
        raise ValueError(f"RED: ambiguous base object: {path}")
    _, object_type, object_id = fields
    if object_type != b"blob":
        raise ValueError(f"RED: base object is not a blob: {path}")
    try:
        raw_blob = subprocess.run(
            ["git", "cat-file", "blob", object_id.decode("ascii")],
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise ValueError(f"RED: base object lookup failed: {path}") from error
    return {"kind": "present", "sha256": _sha256_bytes(raw_blob)}


def _exact_allowed_deletion(
    plan: dict[str, Any], path: str, amendment: dict[str, Any] | None = None
) -> bool:
    base_allowed = any(
        path == allowed_path
        for item in plan.get("items", [])
        for allowed_path in item.get("allowed_deletions", [])
    )
    amendment_allowed = any(
        path == allowed_path
        for item in (amendment or {}).get("items", [])
        for allowed_path in item.get("exact_deletions", [])
    )
    return base_allowed or amendment_allowed or path in (amendment or {}).get("v019_exact_deletions", [])


def classify_manifest_entry(
    plan: dict[str, Any], amendment: dict[str, Any], path: str
) -> tuple[str, str, list[str]]:
    """Classify a path using only the reviewed v006 precedence table."""
    manifest_path = amendment["output"]["manifest"]
    if path == manifest_path:
        return "manifest-self", "implementation_report", ["R-004"]
    snapshot_output = amendment.get("immutable_snapshot_output")
    if snapshot_output is not None and path == snapshot_output["path"]:
        return (
            snapshot_output["mapping_source"],
            snapshot_output["ownership_class"],
            list(snapshot_output["requirement_ids"]),
        )
    post_capture = amendment["post_capture_control_paths"]
    if path in set(post_capture["exact_paths"]):
        return (
            "post-capture-control",
            post_capture["ownership_class"],
            sorted(set(post_capture["requirement_ids"])),
        )
    control = _v005_control_mapping(plan, path)
    if control is not None:
        return control
    mutable = set(plan["implementation_manifest"]["control_artifact_rules"]["mutable_paths"])
    snapshot_path = amendment["authoritative_inputs"]["snapshot"]
    if path in mutable - {snapshot_path, manifest_path}:
        return "implementation-report", "implementation_report", ["R-004"]
    baseline = _inventory_mapping(plan, path)
    if baseline is not None:
        return baseline
    matching_items = [
        item
        for item in plan.get("items", [])
        if any(_matches(path, pattern) for pattern in item.get("allowed_files", []))
    ]
    if not matching_items:
        raise ValueError(f"RED: unmapped manifest path: {path}")
    if path.startswith("tests/"):
        ownership = "test"
    elif path.startswith("scripts/"):
        ownership = "script"
    elif path.startswith("src/ydbdoc_review/config/"):
        ownership = "configuration"
    elif path.startswith("src/"):
        ownership = "production"
    elif path.startswith(".ai-workflow/"):
        ownership = "implementation_report"
    else:
        raise ValueError(f"RED: unmapped ownership class: {path}")
    requirements = sorted({item["requirement_id"] for item in matching_items})
    return "+".join(requirements), ownership, requirements


def _manifest_entry(
    *, path: str, git_status: str, baseline_state: dict[str, str], final_state: dict[str, str],
    ownership_class: str, requirement_ids: list[str], mapping_source: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "git_status": git_status,
        "ownership_class": ownership_class,
        "baseline_state": {"kind": baseline_state["kind"], "sha256": baseline_state["sha256"]},
        "final_state": {"kind": final_state["kind"], "sha256": final_state["sha256"]},
        "requirement_ids": requirement_ids,
        "mapping_source": mapping_source,
    }


def build_manifest_document(
    plan: dict[str, Any], amendment: dict[str, Any], snapshot: dict[str, Any], base: str, output: Path
) -> dict[str, Any]:
    """Build the complete, validated-in-memory manifest replacement."""
    _validate_snapshot_contract(snapshot, base, amendment)
    required_absent = amendment["required_absent"]["path"]
    if Path(required_absent).exists():
        raise ValueError(f"required-absent path is present: {required_absent}")
    current = {entry["path"]: entry for entry in enumerate_delta(base)}
    manifest_path = amendment["output"]["manifest"]
    if manifest_path in current:
        if not output.exists():
            raise ValueError(f"manifest path unexpectedly already in delta: {manifest_path}")
    else:
        current[manifest_path] = {
            "path": manifest_path,
            "git_status": "??",
            "state": {"kind": "present", "sha256": ""},
        }
    snapshot_by_path = {entry["path"]: entry for entry in snapshot["entries"]}
    entries: list[dict[str, Any]] = []
    post_capture = set(amendment["post_capture_control_paths"]["exact_paths"]) | {manifest_path}
    snapshot_output = amendment.get("immutable_snapshot_output")
    if snapshot_output is not None:
        post_capture.add(snapshot_output["path"])
    for path in sorted(current):
        baseline = snapshot_by_path.get(path)
        if snapshot_output is not None and path == snapshot_output["path"]:
            baseline = None
        if baseline is None:
            if path in post_capture:
                source, ownership, requirement_ids = classify_manifest_entry(plan, amendment, path)
                baseline_state = {"kind": "absent", "sha256": ""}
            else:
                source, ownership, requirement_ids = _clean_at_capture_mapping(
                    plan, amendment, path
                )
                baseline_state = _git_blob_state_at_base(base, path)
                if current[path]["git_status"].startswith("D") and (
                    not _exact_allowed_deletion(plan, path, amendment)
                    or baseline_state["kind"] != "present"
                    or current[path]["state"]["kind"] != "deleted"
                ):
                    raise ValueError(f"RED: clean-at-capture deletion is not exactly allowed: {path}")
                if _state_key(baseline_state) == _state_key(current[path]["state"]):
                    raise ValueError(f"RED: clean-at-capture baseline equals final state: {path}")
        else:
            source, ownership, requirement_ids = classify_manifest_entry(plan, amendment, path)
            baseline_state = baseline["state"]
        entries.append(
            _manifest_entry(
                path=path,
                git_status=current[path]["git_status"],
                baseline_state=baseline_state,
                final_state=current[path]["state"],
                ownership_class=ownership,
                requirement_ids=requirement_ids,
                mapping_source=source,
            )
        )
    manifest = {
        "protocol_version": amendment["manifest_top_level"]["values"]["protocol_version"],
        "task_id": amendment["manifest_top_level"]["values"]["task_id"],
        "plan_version": amendment["manifest_top_level"]["values"]["plan_version"],
        "base_commit": amendment["manifest_top_level"]["values"]["base_commit"],
        "entries": entries,
    }
    self_entry = next(entry for entry in entries if entry["path"] == manifest_path)
    self_entry["final_state"]["sha256"] = normalized_manifest_self_sha256(manifest, manifest_path)
    return manifest


def bootstrap_manifest(
    plan: dict[str, Any], amendment: dict[str, Any], snapshot: dict[str, Any], base: str, output: Path
) -> dict[str, Any]:
    if output.exists():
        raise ValueError(f"manifest already exists: {output}")
    manifest = build_manifest_document(plan, amendment, snapshot, base, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return manifest


def _atomic_write_manifest(output: Path, document: bytes) -> None:
    """Durably replace only *output* through a sibling temporary file."""
    temporary = output.with_name(f".{output.name}.tmp")
    if temporary.exists():
        raise ValueError(f"manifest temporary path already exists: {temporary}")
    try:
        with temporary.open("xb") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def refresh_manifest(
    plan: dict[str, Any], amendment: dict[str, Any], snapshot: dict[str, Any], base: str, output: Path
) -> dict[str, Any]:
    """Atomically regenerate a complete manifest from the current delta."""
    expected_output = amendment["output"]["manifest"]
    if output.as_posix() != expected_output:
        raise ValueError(f"refresh target is not approved manifest path: {output}")
    if not output.exists():
        raise ValueError(f"manifest does not exist; use bootstrap-manifest: {output}")
    manifest = build_manifest_document(plan, amendment, snapshot, base, output)
    current = enumerate_delta(base)
    errors = validate_v006_manifest(plan, amendment, snapshot, manifest, current)
    if errors:
        raise ValueError("RED: prewrite manifest validation failed: " + "; ".join(errors))
    replacement = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True).encode("utf-8")
    _atomic_write_manifest(output, replacement)
    written = load_manifest(output)
    errors = validate_v006_manifest(plan, amendment, snapshot, written, enumerate_delta(base))
    if errors:
        raise ValueError("RED: postwrite manifest validation failed: " + "; ".join(errors))
    return written


def _state_key(state: dict[str, str]) -> tuple[str, str]:
    return state.get("kind", ""), state.get("sha256", "")


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern)


def _requirements_for_path(plan: dict[str, Any], path: str) -> set[str]:
    return {
        item["requirement_id"]
        for item in plan.get("items", [])
        if any(_matches(path, pattern) for pattern in item.get("allowed_files", []))
    }


def _all_control_paths(plan: dict[str, Any]) -> set[str]:
    inventory = plan.get("baseline_contract_inventory", {})
    paths: set[str] = set(inventory.get("immutable_control_artifacts", {}).get("exact_paths", []))
    paths.update(
        inventory.get("planned_analyst_publication_before_capture", {}).get("exact_paths", [])
    )
    return paths


def validate_path_inventory(
    plan: dict[str, Any], snapshot: dict[str, Any], current: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    snapshot_by_path = {entry["path"]: entry for entry in snapshot.get("entries", [])}
    for entry in current:
        path = entry["path"]
        baseline = snapshot_by_path.get(path)
        if baseline and _state_key(baseline["state"]) == _state_key(entry["state"]):
            continue
        if not _requirements_for_path(plan, path):
            errors.append(f"path outside allowlist changed after baseline: {path}")
    for required in plan.get("baseline_contract_inventory", {}).get("required_absent", []):
        if Path(required["path"]).exists():
            errors.append(f"required-absent path is present: {required['path']}")
    return errors


def validate_edit_allowlist(
    plan: dict[str, Any], snapshot: dict[str, Any], current: list[dict[str, Any]]
) -> list[str]:
    snapshot_by_path = {entry["path"]: entry for entry in snapshot.get("entries", [])}
    errors: list[str] = []
    for entry in current:
        baseline = snapshot_by_path.get(entry["path"])
        if baseline and _state_key(baseline["state"]) == _state_key(entry["state"]):
            continue
        if not _requirements_for_path(plan, entry["path"]):
            errors.append(f"unapproved executor edit: {entry['path']}")
    return errors


def validate_control_hashes(
    plan: dict[str, Any], snapshot: dict[str, Any], current: list[dict[str, Any]]
) -> list[str]:
    mutable = set(plan["implementation_manifest"]["control_artifact_rules"]["mutable_paths"])
    current_by_path = {entry["path"]: entry for entry in current}
    snapshot_by_path = {entry["path"]: entry for entry in snapshot.get("entries", [])}
    errors: list[str] = []
    for path in _all_control_paths(plan):
        if path in mutable:
            continue
        baseline = snapshot_by_path.get(path)
        present = current_by_path.get(path)
        if baseline is None or present is None:
            errors.append(f"control artifact missing from snapshot or delta: {path}")
        elif _state_key(baseline["state"]) != _state_key(present["state"]):
            errors.append(f"immutable control artifact changed: {path}")
    return errors


def validate_requirement_mapping(
    plan: dict[str, Any], snapshot: dict[str, Any], manifest: dict[str, Any], current: list[dict[str, Any]]
) -> list[str]:
    del plan, snapshot
    errors: list[str] = []
    entries = manifest["entries"]
    manifest_by_path = {entry.get("path"): entry for entry in entries}
    current_by_path = {entry["path"]: entry for entry in current}
    if len(manifest_by_path) != len(entries):
        errors.append("manifest has duplicate paths")
    if set(manifest_by_path) != set(current_by_path):
        errors.append("manifest paths do not exactly match worktree delta")
    required = {"path", "git_status", "ownership_class", "baseline_state", "final_state", "requirement_ids"}
    for path, current_entry in current_by_path.items():
        entry = manifest_by_path.get(path, {})
        missing = sorted(required - set(entry))
        if missing:
            errors.append(f"manifest entry missing {missing}: {path}")
            continue
        if entry["git_status"] != current_entry["git_status"]:
            errors.append(f"manifest git status mismatch: {path}")
        if _state_key(entry["final_state"]) != _state_key(current_entry["state"]):
            errors.append(f"manifest final state mismatch: {path}")
        if not entry["requirement_ids"]:
            errors.append(f"manifest requirement_ids empty: {path}")
    return errors


def validate_v006_manifest(
    plan: dict[str, Any], amendment: dict[str, Any], snapshot: dict[str, Any], manifest: dict[str, Any],
    current: list[dict[str, Any]],
) -> list[str]:
    """Validate every v006 schema, mapping, state and self-hash invariant."""
    errors: list[str] = []
    expected_top = amendment["manifest_top_level"]
    if list(manifest) != expected_top["exact_field_order"]:
        errors.append("manifest top-level field order does not match v006")
    for key, expected in expected_top["values"].items():
        if manifest.get(key) != expected:
            errors.append(f"manifest top-level value mismatch: {key}")
    entries = manifest.get("entries", [])
    paths = [entry.get("path") for entry in entries]
    if paths != sorted(paths):
        errors.append("manifest paths are not bytewise ascending")
    if len(paths) != len(set(paths)):
        errors.append("manifest has duplicate paths")
    current_by_path = {entry["path"]: entry for entry in current}
    manifest_path = amendment["output"]["manifest"]
    if set(paths) != set(current_by_path):
        errors.append("manifest paths do not exactly match worktree delta")
    snapshot_by_path = {entry["path"]: entry for entry in snapshot["entries"]}
    required_fields = amendment["entry_schema"]["exact_field_order"]
    state_fields = amendment["entry_schema"]["state_exact_field_order"]
    for entry in entries:
        path = entry.get("path", "")
        if list(entry) != required_fields:
            errors.append(f"manifest entry field order mismatch: {path}")
            continue
        for state_name in ("baseline_state", "final_state"):
            if not isinstance(entry.get(state_name), dict) or list(entry[state_name]) != state_fields:
                errors.append(f"manifest state field order mismatch: {path}:{state_name}")
        if entry["ownership_class"] not in amendment["entry_schema"]["allowed_ownership_classes"]:
            errors.append(f"manifest ownership class invalid: {path}")
        if entry["requirement_ids"] != sorted(set(entry["requirement_ids"])) or not entry["requirement_ids"]:
            errors.append(f"manifest requirement_ids invalid: {path}")
        snapshot_output = amendment.get("immutable_snapshot_output")
        baseline = snapshot_by_path.get(path)
        if snapshot_output is not None and path == snapshot_output["path"]:
            baseline = None
        try:
            if baseline is None:
                post_capture = set(amendment["post_capture_control_paths"]["exact_paths"]) | {
                    manifest_path
                }
                if snapshot_output is not None:
                    post_capture.add(snapshot_output["path"])
                if path in post_capture:
                    source, ownership, requirements = classify_manifest_entry(plan, amendment, path)
                    expected_baseline = {"kind": "absent", "sha256": ""}
                else:
                    source, ownership, requirements = _clean_at_capture_mapping(
                        plan, amendment, path
                    )
                    current_state = current_by_path.get(path, {}).get("state", {})
                    base_state = _git_blob_state_at_base(
                        amendment["authoritative_inputs"]["base_commit"], path
                    )
                    if current_by_path.get(path, {}).get("git_status", "").startswith("D") and (
                        not _exact_allowed_deletion(plan, path, amendment)
                        or base_state["kind"] != "present"
                        or current_state.get("kind") != "deleted"
                    ):
                        raise ValueError(f"RED: clean-at-capture deletion is not exactly allowed: {path}")
                    expected_baseline = _git_blob_state_at_base(amendment["authoritative_inputs"]["base_commit"], path)
                    current_state = current_by_path.get(path, {}).get("state")
                    if current_state is not None and _state_key(expected_baseline) == _state_key(current_state):
                        raise ValueError(f"RED: clean-at-capture baseline equals final state: {path}")
            else:
                source, ownership, requirements = classify_manifest_entry(plan, amendment, path)
                expected_baseline = baseline["state"]
        except (subprocess.CalledProcessError, UnicodeDecodeError, ValueError) as error:
            errors.append(str(error))
            continue
        if (entry["mapping_source"], entry["ownership_class"], entry["requirement_ids"]) != (
            source,
            ownership,
            requirements,
        ):
            errors.append(f"manifest derived mapping mismatch: {path}")
        if _state_key(entry["baseline_state"]) != _state_key(expected_baseline):
            errors.append(f"manifest baseline state mismatch: {path}")
        current_entry = current_by_path.get(path)
        if current_entry is None:
            continue
        if entry["git_status"] != current_entry["git_status"]:
            errors.append(f"manifest git status mismatch: {path}")
        if path != manifest_path and _state_key(entry["final_state"]) != _state_key(current_entry["state"]):
            errors.append(f"manifest final state mismatch: {path}")
        if snapshot_output is not None and path == snapshot_output["path"]:
            expected = snapshot_output["final_state"]
            if _state_key(entry["final_state"]) != _state_key(expected):
                errors.append("immutable snapshot output final state mismatch")
    self_entries = [entry for entry in entries if entry.get("path") == manifest_path]
    if len(self_entries) == 1:
        self_entry = self_entries[0]
        if self_entry.get("git_status") != "??" or self_entry.get("final_state", {}).get("kind") != "present":
            errors.append("manifest self entry state is invalid")
        if self_entry.get("final_state", {}).get("sha256") != normalized_manifest_self_sha256(manifest, manifest_path):
            errors.append("manifest normalized self hash mismatch")
    else:
        errors.append("manifest self entry missing or duplicated")
    return errors


def validate_symbol_changes(plan: dict[str, Any], snapshot: dict[str, Any], current: list[dict[str, Any]]) -> list[str]:
    """Reject newly declared production symbols not named by the applicable item."""
    snapshot_by_path = {entry["path"]: entry for entry in snapshot.get("entries", [])}
    errors: list[str] = []
    for entry in current:
        path = entry["path"]
        if not path.startswith("src/") or not path.endswith(".py"):
            continue
        baseline = snapshot_by_path.get(path)
        if baseline and _state_key(baseline["state"]) == _state_key(entry["state"]):
            continue
        allowed = {
            symbol
            for item in plan.get("items", [])
            if any(_matches(path, pattern) for pattern in item.get("allowed_files", []))
            for symbol in item.get("allowed_symbols", [])
        }
        if not allowed:
            errors.append(f"production source edit has no declared symbols: {path}")
            continue
        diff = _run_git("diff", "--unified=0", snapshot["base_commit"], "--", path)
        for line in diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            stripped = line[1:].lstrip()
            if stripped.startswith(("def ", "class ")):
                name = stripped.split("(", 1)[0].split(":", 1)[0].split()[1]
                if name not in allowed:
                    errors.append(f"undeclared production symbol {name} in {path}")
    return errors


def validate_deletions(plan: dict[str, Any], current: list[dict[str, Any]]) -> list[str]:
    allowed = {
        path
        for item in plan.get("items", [])
        for path in item.get("allowed_deletions", [])
        if "::" not in path
    }
    baseline = plan.get("baseline_contract_inventory", {})
    allowed.update(baseline.get("approved_deleted_production", {}).get("exact_paths", []))
    allowed.update(baseline.get("approved_deleted_tests_and_fixtures", {}).get("exact_paths", []))
    return [
        f"undeclared deletion: {entry['path']}"
        for entry in current
        if entry["git_status"].startswith("D") and entry["path"] not in allowed
    ]


def _validate(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    snapshot_bytes = args.snapshot.read_bytes()
    snapshot = load_snapshot(args.snapshot)
    current = enumerate_delta(args.base)
    manifest = load_manifest(args.manifest)
    amendment = _resolved_amendment(args.amendment) if args.amendment else None
    if amendment is not None:
        _validate_snapshot_file_bytes(snapshot_bytes, amendment)
        _validate_snapshot_contract(snapshot, args.base, amendment)
        errors = validate_v006_manifest(plan, amendment, snapshot, manifest, current)
        required_absent = amendment["required_absent"]["path"]
        if Path(required_absent).exists():
            errors.append(f"required-absent path is present: {required_absent}")
        if errors:
            for error in errors:
                print(f"RED: {error}")
            return 1
        if args.snapshot.read_bytes() != snapshot_bytes:
            raise ValueError("immutable snapshot bytes changed during validation")
        print("GREEN: remediation policy gate")
        return 0
    validators = (
        validate_path_inventory,
        validate_edit_allowlist,
        validate_control_hashes,
        validate_requirement_mapping,
        validate_symbol_changes,
        validate_deletions,
    )
    errors: list[str] = []
    for validator in validators:
        if validator is validate_requirement_mapping:
            errors.extend(validator(plan, snapshot, manifest, current))
        elif validator is validate_deletions:
            errors.extend(validator(plan, current))
        else:
            errors.extend(validator(plan, snapshot, current))
    if errors:
        for error in errors:
            print(f"RED: {error}")
        return 1
    print("GREEN: remediation policy gate")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--plan", type=Path, required=True)
    capture.add_argument("--base", required=True)
    capture.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--snapshot", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--base", required=True)
    validate.add_argument("--amendment", type=Path)
    bootstrap = commands.add_parser("bootstrap-manifest")
    bootstrap.add_argument("--plan", type=Path, required=True)
    bootstrap.add_argument("--amendment", type=Path, required=True)
    bootstrap.add_argument("--snapshot", type=Path, required=True)
    bootstrap.add_argument("--base", required=True)
    bootstrap.add_argument("--output", type=Path, required=True)
    refresh = commands.add_parser("refresh-manifest")
    refresh.add_argument("--plan", type=Path, required=True)
    refresh.add_argument("--amendment", type=Path, required=True)
    refresh.add_argument("--snapshot", type=Path, required=True)
    refresh.add_argument("--base", required=True)
    refresh.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        capture_baseline(load_plan(args.plan), args.base, args.output)
        print(f"GREEN: captured {args.output}")
        return 0
    if args.command == "bootstrap-manifest":
        snapshot_bytes = args.snapshot.read_bytes()
        amendment = _resolved_amendment(args.amendment)
        _validate_snapshot_file_bytes(snapshot_bytes, amendment)
        bootstrap_manifest(
            load_plan(args.plan),
            amendment,
            load_snapshot(args.snapshot),
            args.base,
            args.output,
        )
        if args.snapshot.read_bytes() != snapshot_bytes:
            raise ValueError("immutable snapshot bytes changed during bootstrap")
        print(f"GREEN: bootstrapped {args.output}")
        return 0
    if args.command == "refresh-manifest":
        snapshot_bytes = args.snapshot.read_bytes()
        amendment = _resolved_amendment(args.amendment)
        _validate_snapshot_file_bytes(snapshot_bytes, amendment)
        refreshed = refresh_manifest(
            load_plan(args.plan),
            amendment,
            load_snapshot(args.snapshot),
            args.base,
            args.output,
        )
        if args.snapshot.read_bytes() != snapshot_bytes:
            raise ValueError("immutable snapshot bytes changed during refresh")
        if refreshed != load_manifest(args.output):
            raise ValueError("manifest changed after refresh write")
        print(f"GREEN: refreshed {args.output}")
        return 0
    return _validate(args)


if __name__ == "__main__":
    raise SystemExit(main())
