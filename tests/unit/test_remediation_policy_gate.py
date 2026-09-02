from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import remediation_policy_gate as gate


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()


def _plan() -> dict[str, object]:
    return {
        "task_id": "TASK",
        "plan_version": "v1",
        "implementation_manifest": {"control_artifact_rules": {"mutable_paths": []}},
        "baseline_contract_inventory": {"required_absent": []},
        "items": [
            {
                "requirement_id": "R-001",
                "allowed_files": ["src/product.py"],
                "allowed_symbols": ["allowed"],
                "allowed_deletions": [],
            }
        ],
    }


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "product.py").write_text("def existing():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _manifest(base: str, entries: list[dict[str, object]]) -> dict[str, object]:
    return {"base_commit": base, "entries": entries}


def _v008_snapshot(base: str) -> dict[str, object]:
    statuses = ["M"] * 50 + ["D"] * 43 + ["??"] * 45
    return {
        "base_commit": base,
        "entries": [
            {
                "path": f"changed/{index:03}.md",
                "git_status": status,
                "state": {"kind": "present", "sha256": str(index)},
            }
            for index, status in enumerate(statuses)
        ],
    }


def _v008_contract(base: str) -> dict[str, object]:
    return {
        "authoritative_inputs": {"snapshot_entry_count": 138},
        "v008_snapshot_contract": {
            "base_commit": base,
            "entry_count": 138,
            "git_status_counts": {"M": 50, "D": 43, "??": 45},
        },
    }


def test_v008_snapshot_contract_accepts_exact_138_unique_entries(
    repo: tuple[Path, str],
) -> None:
    _, base = repo
    snapshot = _v008_snapshot(base)
    gate._validate_snapshot_contract(snapshot, base, _v008_contract(base))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("remove", "snapshot entry count"),
        ("wrong_base", "base_commit"),
        ("duplicate", "duplicate paths"),
    ],
)
def test_v008_snapshot_contract_refuses_non_authoritative_snapshot(
    repo: tuple[Path, str], mutation: str, message: str
) -> None:
    _, base = repo
    snapshot = _v008_snapshot(base)
    if mutation == "remove":
        snapshot["entries"] = snapshot["entries"][:114]
    elif mutation == "wrong_base":
        snapshot["base_commit"] = "not-the-base"
    else:
        snapshot["entries"][-1]["path"] = snapshot["entries"][0]["path"]

    with pytest.raises(ValueError, match=message):
        gate._validate_snapshot_contract(snapshot, base, _v008_contract(base))


def test_v008_resolve_merges_v007_and_v008_control_paths(tmp_path: Path) -> None:
    v006 = {
        "plan_version": "one-pass-remediation-v006",
        "authoritative_inputs": {"snapshot": "old", "base_commit": "old", "snapshot_entry_count": 114},
        "post_capture_control_paths": {
            "ownership_class": "control_artifact",
            "requirement_ids": ["workflow-protocol-provenance"],
            "exact_paths": ["review-request-v006.md"],
        },
    }
    v008 = {
        "plan_version": "one-pass-remediation-v008",
        "amends": "one-pass-remediation-v006",
        "authoritative_snapshot": {
            "path": "snapshot.yaml",
            "base_commit": "base",
            "entry_count": 138,
            "git_status_counts": {"M": 50, "D": 43, "??": 45},
        },
        "post_capture_control_paths_addition": {
            "exact_paths": ["review-request-v007.md", "response-v007.yaml", "response-v008.yaml"]
        },
    }
    (tmp_path / "implementation-plan-v006-amendment.yaml").write_text(
        yaml.safe_dump(v006), encoding="utf-8"
    )
    v008_path = tmp_path / "implementation-plan-v008-amendment.yaml"
    v008_path.write_text(yaml.safe_dump(v008), encoding="utf-8")

    resolved = gate._resolved_amendment(v008_path)
    assert resolved["authoritative_inputs"]["snapshot_entry_count"] == 138
    assert resolved["post_capture_control_paths"]["exact_paths"] == [
        "review-request-v006.md",
        "review-request-v007.md",
        "response-v007.yaml",
        "response-v008.yaml",
    ]


def test_capture_baseline_refuses_overwrite_and_records_untracked(
    repo: tuple[Path, str], tmp_path: Path
) -> None:
    root, base = repo
    (root / "untracked.txt").write_text("initial", encoding="utf-8")
    output = tmp_path / "baseline.yaml"
    gate.capture_baseline(_plan(), base, output)

    snapshot = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert snapshot["entries"] == [
        {
            "path": "untracked.txt",
            "git_status": "??",
            "state": {"kind": "present", "sha256": gate._sha256_bytes(b"initial")},
        }
    ]
    with pytest.raises(ValueError, match="already exists"):
        gate.capture_baseline(_plan(), base, output)


def test_policy_accepts_declared_edit_and_rejects_outside_allowlist(
    repo: tuple[Path, str], tmp_path: Path
) -> None:
    root, base = repo
    plan = _plan()
    snapshot_path = root.parent / "snapshot.yaml"
    gate.capture_baseline(plan, base, snapshot_path)
    snapshot = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))

    (root / "src" / "product.py").write_text("def allowed():\n    return 2\n", encoding="utf-8")
    current = gate.enumerate_delta(base)
    entry = current[0]
    manifest = _manifest(
        base,
        [
            {
                "path": entry["path"],
                "git_status": entry["git_status"],
                "ownership_class": "production",
                "baseline_state": {"kind": "present", "sha256": gate._sha256_bytes(b"def existing():\n    return 1\n")},
                "final_state": entry["state"],
                "requirement_ids": ["R-001"],
            }
        ],
    )
    assert gate.validate_path_inventory(plan, snapshot, current) == []
    assert gate.validate_edit_allowlist(plan, snapshot, current) == []
    assert gate.validate_symbol_changes(plan, snapshot, current) == []
    assert gate.validate_requirement_mapping(plan, snapshot, manifest, current) == []

    (root / "outside.py").write_text("x = 1\n", encoding="utf-8")
    assert gate.validate_path_inventory(plan, snapshot, gate.enumerate_delta(base)) == [
        "path outside allowlist changed after baseline: outside.py"
    ]


def _clean_capture_amendment(base: str, output: str = "reports/manifest.yaml") -> dict[str, object]:
    return {
        "authoritative_inputs": {"snapshot_entry_count": 0, "base_commit": base},
        "required_absent": {"path": "uv.lock"},
        "output": {"manifest": output},
        "post_capture_control_paths": {
            "exact_paths": [], "ownership_class": "control_artifact",
            "requirement_ids": ["workflow-protocol-provenance"],
        },
        "manifest_top_level": {
            "values": {"protocol_version": 1, "task_id": "TASK", "plan_version": "v1", "base_commit": base},
        },
    }


def _clean_plan_for(path: str, *requirement_ids: str, allowed_deletions: list[str] | None = None) -> dict[str, object]:
    plan = _plan()
    plan["items"] = [
        {"requirement_id": requirement_id, "allowed_files": [path], "allowed_symbols": [],
         "allowed_deletions": allowed_deletions or []}
        for requirement_id in requirement_ids
    ]
    return plan


def test_clean_at_capture_tabs_path_uses_base_blob_and_exact_R006_mapping(
    repo: tuple[Path, str], tmp_path: Path
) -> None:
    root, base = repo
    tabs = root / "src" / "ydbdoc_review" / "parsing" / "yfm_plugins" / "tabs.py"
    tabs.parent.mkdir(parents=True)
    tabs.write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "tabs")
    base = _git(root, "rev-parse", "HEAD")
    tabs.write_text("changed\n", encoding="utf-8")
    path = "src/ydbdoc_review/parsing/yfm_plugins/tabs.py"
    output = root / "reports" / "manifest.yaml"
    manifest = gate.bootstrap_manifest(
        _clean_plan_for(path, "R-006"), _clean_capture_amendment(base),
        {"base_commit": base, "entries": []}, base, output,
    )
    entry = next(value for value in manifest["entries"] if value["path"] == path)
    assert entry["mapping_source"] == "clean-at-capture-exact-item"
    assert entry["requirement_ids"] == ["R-006"]
    assert entry["baseline_state"] == {"kind": "present", "sha256": gate._sha256_bytes(b"base\n")}


def test_clean_at_capture_exact_new_file_uses_absent_baseline(
    repo: tuple[Path, str]
) -> None:
    root, base = repo
    path = "src/new.py"
    (root / path).write_text("new\n", encoding="utf-8")
    manifest = gate.bootstrap_manifest(
        _clean_plan_for(path, "R-001"), _clean_capture_amendment(base),
        {"base_commit": base, "entries": []}, base, root / "reports" / "manifest.yaml",
    )
    entry = next(value for value in manifest["entries"] if value["path"] == path)
    assert entry["baseline_state"] == {"kind": "absent", "sha256": ""}


def test_clean_at_capture_multiple_exact_items_union_requirement_ids() -> None:
    mapping = gate._exact_item_allowlist_mapping(
        _clean_plan_for("src/product.py", "R-019", "R-006"), "src/product.py"
    )
    assert mapping == ("clean-at-capture-exact-item", "production", ["R-006", "R-019"])


@pytest.mark.parametrize("allowed_files", [["src/*.py"], ["src/other.py"]])
def test_clean_at_capture_rejects_wildcard_only_and_unlisted_paths(allowed_files: list[str]) -> None:
    plan = _plan()
    plan["items"][0]["allowed_files"] = allowed_files
    with pytest.raises(ValueError, match="exact item allowlist"):
        gate._exact_item_allowlist_mapping(plan, "src/product.py")


def test_clean_at_capture_deletion_requires_exact_allowed_deletion(
    repo: tuple[Path, str]
) -> None:
    root, base = repo
    (root / "src" / "product.py").unlink()
    with pytest.raises(ValueError, match="deletion is not exactly allowed"):
        gate.bootstrap_manifest(
            _clean_plan_for("src/product.py", "R-001"), _clean_capture_amendment(base),
            {"base_commit": base, "entries": []}, base, root / "reports" / "manifest.yaml",
        )


def test_clean_at_capture_exact_allowed_deletion_uses_base_blob(
    repo: tuple[Path, str]
) -> None:
    root, base = repo
    (root / "src" / "product.py").unlink()
    manifest = gate.bootstrap_manifest(
        _clean_plan_for("src/product.py", "R-001", allowed_deletions=["src/product.py"]),
        _clean_capture_amendment(base), {"base_commit": base, "entries": []}, base,
        root / "reports" / "manifest.yaml",
    )
    entry = next(value for value in manifest["entries"] if value["path"] == "src/product.py")
    assert entry["baseline_state"]["kind"] == "present"
    assert entry["final_state"]["kind"] == "deleted"


def test_clean_at_capture_base_lookup_and_non_blob_are_red(
    repo: tuple[Path, str]
) -> None:
    _, base = repo
    with pytest.raises(ValueError, match="base object lookup failed"):
        gate._git_blob_state_at_base("not-a-commit", "src/product.py")
    with pytest.raises(ValueError, match="not a blob"):
        gate._git_blob_state_at_base(base, "src")


def test_clean_at_capture_equal_baseline_and_final_is_red(
    repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, base = repo
    state = gate._git_blob_state_at_base(base, "src/product.py")
    monkeypatch.setattr(gate, "enumerate_delta", lambda _base: [
        {"path": "src/product.py", "git_status": "M", "state": state}
    ])
    with pytest.raises(ValueError, match="baseline equals final"):
        gate.bootstrap_manifest(
            _clean_plan_for("src/product.py", "R-001"), _clean_capture_amendment(base),
            {"base_commit": base, "entries": []}, base, root / "reports" / "manifest.yaml",
        )


def test_v012_requires_exact_v011_predecessor_and_self_seals_four_paths(tmp_path: Path) -> None:
    v008 = {
        "plan_version": "one-pass-remediation-v008", "amends": "one-pass-remediation-v006",
        "authoritative_snapshot": {"path": "snapshot.yaml", "base_commit": "base", "entry_count": 0, "git_status_counts": {}},
        "post_capture_control_paths_addition": {"exact_paths": []},
    }
    v006 = {"plan_version": "one-pass-remediation-v006", "authoritative_inputs": {"snapshot": "old", "base_commit": "old", "snapshot_entry_count": 0}, "post_capture_control_paths": {"exact_paths": []}}
    v009 = {"plan_version": "one-pass-remediation-v009", "amends": "one-pass-remediation-v008", "immutable_snapshot_output": {"path": "snapshot.yaml"}, "post_capture_control_paths_addition": {"exact_paths": []}}
    v011 = {"plan_version": "one-pass-remediation-v011", "amends": "one-pass-remediation-v009", "post_capture_control_paths_addition": {"exact_paths": []}}
    for name, value in (("implementation-plan-v006-amendment.yaml", v006), ("implementation-plan-v008-amendment.yaml", v008), ("implementation-plan-v009-amendment.yaml", v009), ("implementation-plan-v011-amendment.yaml", v011)):
        (tmp_path / name).write_text(yaml.safe_dump(value), encoding="utf-8")
    v012 = {"plan_version": "one-pass-remediation-v012", "amends": "one-pass-remediation-v011", "clean_at_capture_item_baseline": {"enabled": True}, "post_capture_control_paths_addition": {"exact_paths": ["a", "b", "c", "d"]}}
    path = tmp_path / "implementation-plan-v012-amendment.yaml"
    path.write_text(yaml.safe_dump(v012), encoding="utf-8")
    resolved = gate._resolved_amendment(path)
    assert resolved["clean_at_capture_item_baseline"] == {"enabled": True}
    assert resolved["post_capture_control_paths"]["exact_paths"][-4:] == ["a", "b", "c", "d"]
    v012["amends"] = "wrong"
    path.write_text(yaml.safe_dump(v012), encoding="utf-8")
    with pytest.raises(ValueError, match="v012 predecessor"):
        gate._resolved_amendment(path)


def test_v013_requires_exact_v012_predecessor_and_self_seals_four_paths(tmp_path: Path) -> None:
    v006 = {"plan_version": "one-pass-remediation-v006", "authoritative_inputs": {"snapshot": "old", "base_commit": "old", "snapshot_entry_count": 0}, "post_capture_control_paths": {"exact_paths": []}}
    v008 = {"plan_version": "one-pass-remediation-v008", "amends": "one-pass-remediation-v006", "authoritative_snapshot": {"path": "snapshot.yaml", "base_commit": "base", "entry_count": 0, "git_status_counts": {}}, "post_capture_control_paths_addition": {"exact_paths": []}}
    v009 = {"plan_version": "one-pass-remediation-v009", "amends": "one-pass-remediation-v008", "immutable_snapshot_output": {"path": "snapshot.yaml"}, "post_capture_control_paths_addition": {"exact_paths": []}}
    v011 = {"plan_version": "one-pass-remediation-v011", "amends": "one-pass-remediation-v009", "post_capture_control_paths_addition": {"exact_paths": []}}
    v012 = {"plan_version": "one-pass-remediation-v012", "amends": "one-pass-remediation-v011", "clean_at_capture_item_baseline": {"enabled": True}, "post_capture_control_paths_addition": {"exact_paths": []}}
    for name, value in (("implementation-plan-v006-amendment.yaml", v006), ("implementation-plan-v008-amendment.yaml", v008), ("implementation-plan-v009-amendment.yaml", v009), ("implementation-plan-v011-amendment.yaml", v011), ("implementation-plan-v012-amendment.yaml", v012)):
        (tmp_path / name).write_text(yaml.safe_dump(value), encoding="utf-8")
    v013 = {"plan_version": "one-pass-remediation-v013", "amends": "one-pass-remediation-v012", "post_capture_control_paths_addition": {"exact_paths": ["a", "b", "c", "d"]}}
    path = tmp_path / "implementation-plan-v013-amendment.yaml"
    path.write_text(yaml.safe_dump(v013), encoding="utf-8")
    assert gate._resolved_amendment(path)["post_capture_control_paths"]["exact_paths"][-4:] == ["a", "b", "c", "d"]
    v013["amends"] = "wrong"
    path.write_text(yaml.safe_dump(v013), encoding="utf-8")
    with pytest.raises(ValueError, match="v013 predecessor"):
        gate._resolved_amendment(path)


def test_v014_v015_and_v016_require_exact_predecessors_and_self_seal(tmp_path: Path) -> None:
    v006 = {"plan_version": "one-pass-remediation-v006", "authoritative_inputs": {"snapshot": "old", "base_commit": "old", "snapshot_entry_count": 0}, "post_capture_control_paths": {"exact_paths": []}}
    v008 = {"plan_version": "one-pass-remediation-v008", "amends": "one-pass-remediation-v006", "authoritative_snapshot": {"path": "snapshot.yaml", "base_commit": "base", "entry_count": 0, "git_status_counts": {}}, "post_capture_control_paths_addition": {"exact_paths": []}}
    v009 = {"plan_version": "one-pass-remediation-v009", "amends": "one-pass-remediation-v008", "immutable_snapshot_output": {"path": "snapshot.yaml"}, "post_capture_control_paths_addition": {"exact_paths": []}}
    v011 = {"plan_version": "one-pass-remediation-v011", "amends": "one-pass-remediation-v009", "post_capture_control_paths_addition": {"exact_paths": []}}
    v012 = {"plan_version": "one-pass-remediation-v012", "amends": "one-pass-remediation-v011", "clean_at_capture_item_baseline": {"enabled": True}, "post_capture_control_paths_addition": {"exact_paths": []}}
    v013 = {"plan_version": "one-pass-remediation-v013", "amends": "one-pass-remediation-v012", "post_capture_control_paths_addition": {"exact_paths": []}}
    for name, value in (("implementation-plan-v006-amendment.yaml", v006), ("implementation-plan-v008-amendment.yaml", v008), ("implementation-plan-v009-amendment.yaml", v009), ("implementation-plan-v011-amendment.yaml", v011), ("implementation-plan-v012-amendment.yaml", v012), ("implementation-plan-v013-amendment.yaml", v013)):
        (tmp_path / name).write_text(yaml.safe_dump(value), encoding="utf-8")
    v014 = {"plan_version": "one-pass-remediation-v014", "amends": "one-pass-remediation-v013", "test_changes": [{"path": "tests/unit/test_chunker.py", "requirement_ids": ["R-006"]}], "post_capture_control_paths_addition": {"exact_paths": ["v014-a", "v014-b", "v014-c", "v014-d"]}}
    (tmp_path / "implementation-plan-v014-amendment.yaml").write_text(yaml.safe_dump(v014), encoding="utf-8")
    v015 = {"plan_version": "one-pass-remediation-v015", "amends": "one-pass-remediation-v014", "manifest_lifecycle": {"enabled": True}, "refresh_command": "refresh", "post_capture_control_paths_addition": {"exact_paths": ["v015-a", "v015-b", "v015-c", "v015-d"]}}
    path = tmp_path / "implementation-plan-v015-amendment.yaml"
    path.write_text(yaml.safe_dump(v015), encoding="utf-8")
    resolved = gate._resolved_amendment(path)
    assert resolved["v014_legacy_test_alignment"]["tests/unit/test_chunker.py"] == (
        "v014-legacy-test-alignment", "test", ["R-006"]
    )
    assert resolved["post_capture_control_paths"]["exact_paths"][-8:] == [
        "v014-a", "v014-b", "v014-c", "v014-d", "v015-a", "v015-b", "v015-c", "v015-d"
    ]
    v015["amends"] = "wrong"
    path.write_text(yaml.safe_dump(v015), encoding="utf-8")
    with pytest.raises(ValueError, match="v015 predecessor"):
        gate._resolved_amendment(path)

    v015["amends"] = "one-pass-remediation-v014"
    path.write_text(yaml.safe_dump(v015), encoding="utf-8")
    v016 = {
        "plan_version": "one-pass-remediation-v016",
        "amends": "one-pass-remediation-v015",
        "post_capture_control_paths_addition": {
            "exact_paths": ["v016-a", "v016-b", "v016-c", "v016-d"]
        },
    }
    v016_path = tmp_path / "implementation-plan-v016-amendment.yaml"
    v016_path.write_text(yaml.safe_dump(v016), encoding="utf-8")
    resolved = gate._resolved_amendment(v016_path)
    assert resolved["post_capture_control_paths"]["exact_paths"][-4:] == [
        "v016-a", "v016-b", "v016-c", "v016-d"
    ]
    v016["amends"] = "wrong"
    v016_path.write_text(yaml.safe_dump(v016), encoding="utf-8")
    with pytest.raises(ValueError, match="v016 predecessor"):
        gate._resolved_amendment(v016_path)


def test_refresh_manifest_requires_existing_output(repo: tuple[Path, str]) -> None:
    _, base = repo
    with pytest.raises(ValueError, match="use bootstrap-manifest"):
        gate.refresh_manifest({}, {"output": {"manifest": "reports/manifest.yaml"}}, {}, base, Path("reports/manifest.yaml"))


def test_refresh_manifest_atomically_replaces_and_is_idempotent(
    repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, base = repo
    target = Path("reports/manifest.yaml")
    target.parent.mkdir()
    target.write_bytes(b"old manifest\n")
    document = {"entries": []}
    monkeypatch.setattr(gate, "build_manifest_document", lambda *_args: document)
    monkeypatch.setattr(gate, "validate_v006_manifest", lambda *_args: [])
    amendment = {"output": {"manifest": target.as_posix()}}
    gate.refresh_manifest({}, amendment, {}, base, target)
    first = target.read_bytes()
    gate.refresh_manifest({}, amendment, {}, base, target)
    assert target.read_bytes() == first
    assert yaml.safe_load(first) == document
    assert not (root / "reports" / ".manifest.yaml.tmp").exists()


def test_refresh_manifest_write_failure_preserves_existing_target(
    repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, base = repo
    target = Path("reports/manifest.yaml")
    target.parent.mkdir()
    original = b"approved manifest\n"
    target.write_bytes(original)
    monkeypatch.setattr(gate, "build_manifest_document", lambda *_args: {"entries": []})
    monkeypatch.setattr(gate, "validate_v006_manifest", lambda *_args: [])
    monkeypatch.setattr(gate, "_atomic_write_manifest", lambda *_args: (_ for _ in ()).throw(OSError("write failed")))
    with pytest.raises(OSError, match="write failed"):
        gate.refresh_manifest({}, {"output": {"manifest": target.as_posix()}}, {}, base, target)
    assert target.read_bytes() == original
