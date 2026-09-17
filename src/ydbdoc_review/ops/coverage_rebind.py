"""Fail-closed coverage evidence rebinding for one proven path-only repair."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ydbdoc_review.github.git_ops import read_text_at_commit, resolve_commit_ref
from ydbdoc_review.github.provenance import (
    TranslationArtifactProvenance,
    parse_authority_evidence,
    validate_authority_evidence,
)
from ydbdoc_review.ops.transcripts import TranscriptStore
from ydbdoc_review.translation.coverage import (
    CoverageEvidence,
    CoveragePlan,
    build_coverage_evidence,
    load_coverage_evidence,
    save_coverage_evidence,
)
from ydbdoc_review.validation.href_parity import (
    check_href_parity,
    reconcile_final_en_same_fragment_paths,
)

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_AUDIT_RULE = "reconcile_final_en_same_fragment_paths"
_AUDIT_VERSION = 1
_ATTESTATION_VERSION = 1


class CoverageRebindGitHub(Protocol):
    def get_pull(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CoverageRebindRequest:
    """All identities required for a single bounded metadata recovery."""

    source_repo: str
    source_pr: int
    translation_pr: int
    old_run_id: str
    old_digest: str
    old_candidate_sha: str
    new_candidate_sha: str
    expected_new_digest: str


@dataclass(frozen=True)
class CoverageRebindProof:
    """Deterministic proof and the fresh candidate-bound evidence it derives."""

    old_evidence: CoverageEvidence
    new_evidence: CoverageEvidence
    changed_paths: tuple[str, ...]
    old_file_hashes: tuple[tuple[str, str], ...]
    new_file_hashes: tuple[tuple[str, str], ...]
    audit_data: bytes


@dataclass(frozen=True)
class CoverageRebindResult:
    proof: CoverageRebindProof
    attestation_stored: bool


@dataclass(frozen=True)
class CoverageRebindAttestation:
    """Exact trusted-store authorization for one proven C-to-K repair."""

    version: int
    source_repo: str
    source_pr: int
    translation_pr: int
    old_candidate_sha: str
    old_run_id: str
    old_coverage_digest: str
    new_candidate_sha: str
    new_coverage_digest: str
    rule: str
    rule_version: int
    proof_audit_digest: str
    self_digest: str


@dataclass(frozen=True)
class _RawDelta:
    old_mode: str
    new_mode: str
    old_object: str
    new_object: str
    status: str
    path: str


def _require_sha(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"coverage rebind {field} must be a full lowercase commit SHA")
    return value


def _require_digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"coverage rebind {field} must be a lowercase SHA-256 digest")
    return value


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_bytes(repo_path: str, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=False,
    )
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or b"").decode(
            "utf-8", errors="replace"
        ).strip()
        raise ValueError(
            f"coverage rebind git {' '.join(args)} failed: {error or '(no output)'}"
        )
    return proc.stdout


def _direct_parent(repo_path: str, new_candidate_sha: str) -> str:
    raw = _git_bytes(repo_path, "rev-list", "--parents", "-n", "1", new_candidate_sha)
    try:
        fields = raw.decode("ascii", errors="strict").strip().split()
    except UnicodeDecodeError as exc:
        raise ValueError("coverage rebind commit graph is not ASCII") from exc
    if len(fields) != 2 or fields[0] != new_candidate_sha:
        raise ValueError("coverage rebind K must be a single-parent direct child of C")
    return fields[1]


def _raw_tree_delta(
    repo_path: str, old_candidate_sha: str, new_candidate_sha: str
) -> tuple[_RawDelta, ...]:
    raw = _git_bytes(
        repo_path,
        "diff-tree",
        "--no-commit-id",
        "--raw",
        "-r",
        "-z",
        "--no-abbrev",
        "--no-renames",
        old_candidate_sha,
        new_candidate_sha,
    )
    if not raw:
        raise ValueError("coverage rebind candidate tree delta is empty")
    if not raw.endswith(b"\0"):
        raise ValueError("coverage rebind raw tree delta is malformed")
    fields = raw[:-1].split(b"\0")
    if len(fields) % 2:
        raise ValueError("coverage rebind raw tree delta is incomplete")
    changes: list[_RawDelta] = []
    for index in range(0, len(fields), 2):
        try:
            metadata = fields[index].decode("ascii", errors="strict")
            path = fields[index + 1].decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("coverage rebind tree delta contains unreadable paths") from exc
        tokens = metadata.split()
        if len(tokens) != 5 or not tokens[0].startswith(":"):
            raise ValueError("coverage rebind raw tree delta record is malformed")
        old_mode = tokens[0][1:]
        new_mode, old_object, new_object, status = tokens[1:]
        changes.append(
            _RawDelta(old_mode, new_mode, old_object, new_object, status, path)
        )
    return tuple(changes)


def _read_required(repo_path: str, commit_sha: str, path: str, *, role: str) -> str:
    try:
        text = read_text_at_commit(repo_path, commit_sha, path)
    except RuntimeError as exc:
        raise ValueError(f"coverage rebind {role} file is unreadable: {path}") from exc
    if text is None:
        raise ValueError(f"coverage rebind {role} file is missing: {path}")
    return text


def _validate_old_evidence_snapshots(
    repo_path: str,
    provenance: TranslationArtifactProvenance,
    old_evidence: CoverageEvidence,
) -> tuple[dict[str, CoveragePlan], dict[str, str | None], dict[str, str]]:
    if old_evidence.candidate_sha != provenance.candidate_sha:
        raise ValueError("coverage rebind old evidence candidate mismatch")
    if old_evidence.authority != provenance.authority:
        raise ValueError("coverage rebind old evidence authority mismatch")
    plans = dict(old_evidence.plans)
    baseline_hashes = dict(old_evidence.baseline_en_hashes)
    candidate_hashes = dict(old_evidence.candidate_file_hashes)
    if (
        len(plans) != len(old_evidence.plans)
        or set(plans) != set(baseline_hashes)
        or set(plans) != set(candidate_hashes)
    ):
        raise ValueError("coverage rebind old evidence path set is incomplete")

    authority = provenance.authority
    baseline_en: dict[str, str | None] = {}
    candidate_files: dict[str, str] = {}
    for target_path, plan in plans.items():
        source_text = _read_required(
            repo_path, authority.ru_sha, plan.source_path, role="source"
        )
        if _hash_text(source_text) != plan.source_hash:
            raise ValueError(f"coverage rebind source hash mismatch: {plan.source_path}")
        baseline_text = read_text_at_commit(
            repo_path, authority.baseline_sha, target_path
        )
        candidate_text = _read_required(
            repo_path, provenance.candidate_sha, target_path, role="old candidate"
        )
        baseline_en[target_path] = baseline_text
        candidate_files[target_path] = candidate_text

    rebuilt = build_coverage_evidence(
        authority=authority,
        candidate_sha=provenance.candidate_sha,
        plans=plans,
        baseline_en=baseline_en,
        candidate_files=candidate_files,
    )
    if rebuilt != old_evidence:
        raise ValueError("coverage rebind old evidence snapshot hashes do not match")
    return plans, baseline_en, candidate_files


def coverage_rebind_audit_key(new_candidate_sha: str) -> str:
    sha = _require_sha(new_candidate_sha, field="new candidate SHA")
    return f"translation/v1/coverage-repairs/{sha}.json"


def _attestation_identity(
    *,
    source_repo: str,
    source_pr: int,
    translation_pr: int,
    old_candidate_sha: str,
    old_run_id: str,
    old_coverage_digest: str,
    new_candidate_sha: str,
) -> dict[str, object]:
    if type(source_repo) is not str:
        raise ValueError("coverage rebind source repository is invalid")
    owner, separator, repo = source_repo.partition("/")
    if not separator or not owner or not repo or "/" in repo:
        raise ValueError("coverage rebind source repository is invalid")
    if (
        type(source_pr) is not int
        or type(translation_pr) is not int
        or source_pr <= 0
        or translation_pr <= 0
    ):
        raise ValueError("coverage rebind pull request identity is invalid")
    if type(old_run_id) is not str or not old_run_id:
        raise ValueError("coverage rebind old run ID is invalid")
    return {
        "source_repo": source_repo.casefold(),
        "source_pr": source_pr,
        "translation_pr": translation_pr,
        "old_candidate_sha": _require_sha(
            old_candidate_sha, field="old candidate SHA"
        ),
        "old_run_id": old_run_id,
        "old_coverage_digest": _require_digest(
            old_coverage_digest, field="old digest"
        ),
        "new_candidate_sha": _require_sha(
            new_candidate_sha, field="new candidate SHA"
        ),
        "rule": _AUDIT_RULE,
        "rule_version": _AUDIT_VERSION,
    }


def coverage_rebind_attestation_key(
    *,
    source_repo: str,
    source_pr: int,
    translation_pr: int,
    old_candidate_sha: str,
    old_run_id: str,
    old_coverage_digest: str,
    new_candidate_sha: str,
) -> str:
    """Address a receipt by its complete immutable authorization tuple."""
    identity = _attestation_identity(
        source_repo=source_repo,
        source_pr=source_pr,
        translation_pr=translation_pr,
        old_candidate_sha=old_candidate_sha,
        old_run_id=old_run_id,
        old_coverage_digest=old_coverage_digest,
        new_candidate_sha=new_candidate_sha,
    )
    encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"translation/v1/coverage-rebind-bindings/{digest}.json"


def _attestation_core(attestation: CoverageRebindAttestation) -> dict[str, object]:
    return {
        "version": attestation.version,
        "source_repo": attestation.source_repo,
        "source_pr": attestation.source_pr,
        "translation_pr": attestation.translation_pr,
        "old_candidate_sha": attestation.old_candidate_sha,
        "old_run_id": attestation.old_run_id,
        "old_coverage_digest": attestation.old_coverage_digest,
        "new_candidate_sha": attestation.new_candidate_sha,
        "new_coverage_digest": attestation.new_coverage_digest,
        "rule": attestation.rule,
        "rule_version": attestation.rule_version,
        "proof_audit_digest": attestation.proof_audit_digest,
    }


def _digest_attestation_core(core: dict[str, object]) -> str:
    encoded = json.dumps(
        core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_attestation(
    *, request: CoverageRebindRequest, proof: CoverageRebindProof
) -> CoverageRebindAttestation:
    identity = _attestation_identity(
        source_repo=request.source_repo,
        source_pr=request.source_pr,
        translation_pr=request.translation_pr,
        old_candidate_sha=request.old_candidate_sha,
        old_run_id=request.old_run_id,
        old_coverage_digest=request.old_digest,
        new_candidate_sha=request.new_candidate_sha,
    )
    without_digest = CoverageRebindAttestation(
        version=_ATTESTATION_VERSION,
        source_repo=str(identity["source_repo"]),
        source_pr=request.source_pr,
        translation_pr=request.translation_pr,
        old_candidate_sha=request.old_candidate_sha,
        old_run_id=request.old_run_id,
        old_coverage_digest=request.old_digest,
        new_candidate_sha=request.new_candidate_sha,
        new_coverage_digest=proof.new_evidence.digest,
        rule=_AUDIT_RULE,
        rule_version=_AUDIT_VERSION,
        proof_audit_digest=hashlib.sha256(proof.audit_data).hexdigest(),
        self_digest="",
    )
    return replace(
        without_digest,
        self_digest=_digest_attestation_core(_attestation_core(without_digest)),
    )


def encode_coverage_rebind_attestation(
    attestation: CoverageRebindAttestation,
) -> bytes:
    core = _attestation_core(attestation)
    if attestation.self_digest != _digest_attestation_core(core):
        raise ValueError("coverage rebind attestation self-digest mismatch")
    return json.dumps(
        {**core, "self_digest": attestation.self_digest},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def decode_coverage_rebind_attestation(data: bytes) -> CoverageRebindAttestation:
    """Decode only canonical, complete v1 attestation bytes."""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("coverage rebind attestation is malformed") from exc
    expected_fields = {
        "version",
        "source_repo",
        "source_pr",
        "translation_pr",
        "old_candidate_sha",
        "old_run_id",
        "old_coverage_digest",
        "new_candidate_sha",
        "new_coverage_digest",
        "rule",
        "rule_version",
        "proof_audit_digest",
        "self_digest",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise ValueError("coverage rebind attestation fields are invalid")
    if (
        type(raw["version"]) is not int
        or type(raw["source_pr"]) is not int
        or type(raw["translation_pr"]) is not int
        or type(raw["rule_version"]) is not int
        or any(type(raw[field]) is not str for field in expected_fields - {
            "version", "source_pr", "translation_pr", "rule_version"
        })
    ):
        raise ValueError("coverage rebind attestation field types are invalid")
    attestation = CoverageRebindAttestation(**raw)
    _attestation_identity(
        source_repo=attestation.source_repo,
        source_pr=attestation.source_pr,
        translation_pr=attestation.translation_pr,
        old_candidate_sha=attestation.old_candidate_sha,
        old_run_id=attestation.old_run_id,
        old_coverage_digest=attestation.old_coverage_digest,
        new_candidate_sha=attestation.new_candidate_sha,
    )
    _require_digest(attestation.new_coverage_digest, field="new digest")
    _require_digest(attestation.proof_audit_digest, field="proof audit digest")
    _require_digest(attestation.self_digest, field="attestation self digest")
    if attestation.version != _ATTESTATION_VERSION:
        raise ValueError("coverage rebind attestation version is unsupported")
    if (
        attestation.rule != _AUDIT_RULE
        or attestation.rule_version != _AUDIT_VERSION
    ):
        raise ValueError("coverage rebind attestation rule is unsupported")
    if encode_coverage_rebind_attestation(attestation) != data:
        raise ValueError("coverage rebind attestation encoding is not canonical")
    return attestation


def _build_audit_data(
    provenance: TranslationArtifactProvenance,
    old_evidence: CoverageEvidence,
    new_evidence: CoverageEvidence,
    changed_paths: tuple[str, ...],
    old_hashes: tuple[tuple[str, str], ...],
    new_hashes: tuple[tuple[str, str], ...],
) -> bytes:
    core: dict[str, object] = {
        "rule": _AUDIT_RULE,
        "version": _AUDIT_VERSION,
        "source_repo": provenance.authority.source_repo,
        "source_pr": provenance.authority.source_pr,
        "old_candidate_sha": old_evidence.candidate_sha,
        "new_candidate_sha": new_evidence.candidate_sha,
        "old_coverage_digest": old_evidence.digest,
        "new_coverage_digest": new_evidence.digest,
        "changed_paths": list(changed_paths),
        "old_file_hashes": dict(old_hashes),
        "new_file_hashes": dict(new_hashes),
    }
    encoded_core = json.dumps(
        core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    payload = {**core, "audit_digest": hashlib.sha256(encoded_core).hexdigest()}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def derive_same_fragment_coverage_rebind(
    *,
    repo_path: str,
    provenance: TranslationArtifactProvenance,
    old_evidence: CoverageEvidence,
    new_candidate_sha: str,
) -> CoverageRebindProof:
    """Prove one direct-child path repair and derive fresh immutable evidence.

    This function performs no writes. It accepts only a complete Git tree delta
    reproduced byte-for-byte by the existing four-snapshot repair rule.
    """
    old_candidate_sha = _require_sha(
        provenance.candidate_sha, field="old candidate SHA"
    )
    new_candidate_sha = _require_sha(new_candidate_sha, field="new candidate SHA")
    if resolve_commit_ref(repo_path, old_candidate_sha) != old_candidate_sha:
        raise ValueError("coverage rebind old candidate did not resolve exactly")
    if resolve_commit_ref(repo_path, new_candidate_sha) != new_candidate_sha:
        raise ValueError("coverage rebind new candidate did not resolve exactly")
    if _direct_parent(repo_path, new_candidate_sha) != old_candidate_sha:
        raise ValueError("coverage rebind K must be a single-parent direct child of C")

    plans, baseline_en, old_candidate_files = _validate_old_evidence_snapshots(
        repo_path, provenance, old_evidence
    )
    changes = _raw_tree_delta(repo_path, old_candidate_sha, new_candidate_sha)
    evidence_paths = set(plans)
    changed_paths: list[str] = []
    old_hashes: list[tuple[str, str]] = []
    new_hashes: list[tuple[str, str]] = []

    authority = provenance.authority

    def read_source_ru(path: str) -> str | None:
        return read_text_at_commit(repo_path, authority.ru_sha, path)

    def read_final_en(path: str) -> str | None:
        return read_text_at_commit(repo_path, new_candidate_sha, path)

    def read_contract_path(path: str) -> str | None:
        commit = authority.ru_sha if "/docs/ru/" in path else new_candidate_sha
        return read_text_at_commit(repo_path, commit, path)

    for change in changes:
        if change.status != "M" or change.path not in evidence_paths:
            raise ValueError(
                "coverage rebind accepts only modified evidence files"
            )
        if change.old_mode != "100644" or change.new_mode != "100644":
            raise ValueError(
                "coverage rebind files must remain regular non-executable blobs"
            )
        for object_id in (change.old_object, change.new_object):
            object_type = _git_bytes(repo_path, "cat-file", "-t", object_id).strip()
            if object_type != b"blob":
                raise ValueError("coverage rebind tree delta contains a non-blob object")
        if "/docs/en/" not in change.path or not change.path.endswith(".md"):
            raise ValueError("coverage rebind accepts only EN Markdown files")
        plan = plans[change.path]
        if (
            plan.mode != "full"
            or not plan.units
            or any(unit.action != "translate_required" for unit in plan.units)
        ):
            raise ValueError(
                "coverage rebind changed paths must have a full prose coverage plan"
            )
        if plan.source_path != change.path.replace("/docs/en/", "/docs/ru/", 1):
            raise ValueError("coverage rebind source/target path identity mismatch")

        old_text = old_candidate_files[change.path]
        new_text = _read_required(
            repo_path, new_candidate_sha, change.path, role="new candidate"
        )
        ru_base = _read_required(
            repo_path, authority.source_base_sha, plan.source_path, role="source base"
        )
        ru_current = _read_required(
            repo_path, authority.ru_sha, plan.source_path, role="source"
        )
        en_tip = _read_required(
            repo_path, authority.baseline_sha, change.path, role="baseline EN"
        )
        link_issues = check_href_parity(
            ru_current,
            old_text,
            en_page_path=change.path,
            docs_text_reader=read_contract_path,
            en_baseline_text=en_tip,
            source_baseline_text=ru_base,
        )
        if link_issues:
            raise ValueError(
                f"coverage rebind old link contract is not clean: {change.path}"
            )
        replayed = reconcile_final_en_same_fragment_paths(
            ru_base,
            ru_current,
            en_tip,
            old_text,
            ru_page_path=plan.source_path,
            en_page_path=change.path,
            read_source_ru=read_source_ru,
            read_final_en=read_final_en,
        )
        if replayed == old_text or replayed != new_text:
            raise ValueError(
                f"coverage rebind repair is not byte-exact for {change.path}"
            )
        changed_paths.append(change.path)
        old_hashes.append((change.path, _hash_text(old_text)))
        new_hashes.append((change.path, _hash_text(new_text)))

    candidate_files = {
        path: _read_required(
            repo_path, new_candidate_sha, path, role="new candidate"
        )
        for path in plans
    }
    new_evidence = build_coverage_evidence(
        authority=authority,
        candidate_sha=new_candidate_sha,
        plans=plans,
        baseline_en=baseline_en,
        candidate_files=candidate_files,
    )
    changed = tuple(sorted(changed_paths))
    old_file_hashes = tuple(sorted(old_hashes))
    new_file_hashes = tuple(sorted(new_hashes))
    return CoverageRebindProof(
        old_evidence=old_evidence,
        new_evidence=new_evidence,
        changed_paths=changed,
        old_file_hashes=old_file_hashes,
        new_file_hashes=new_file_hashes,
        audit_data=_build_audit_data(
            provenance,
            old_evidence,
            new_evidence,
            changed,
            old_file_hashes,
            new_file_hashes,
        ),
    )


def _pull_snapshot(
    github: CoverageRebindGitHub,
    *,
    source_repo: str,
    translation_pr: int,
) -> tuple[str, str]:
    owner, separator, repo = source_repo.partition("/")
    if not separator or not owner or not repo or "/" in repo:
        raise ValueError("coverage rebind source repository is invalid")
    pull = github.get_pull(owner, repo, translation_pr)
    body = pull.get("body")
    head = pull.get("head")
    base = pull.get("base")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    base_repo = base.get("repo") if isinstance(base, dict) else None
    full_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
    if type(body) is not str:
        raise ValueError("coverage rebind translation PR body is missing")
    if full_name != source_repo:
        raise ValueError("coverage rebind translation PR repository mismatch")
    return body, _require_sha(head_sha, field="remote head SHA")


def _save_audit(
    store: TranscriptStore, run_id: str, new_candidate_sha: str, data: bytes
) -> None:
    key = coverage_rebind_audit_key(new_candidate_sha)
    try:
        current = store.get(run_id, key)
        if current is not None and current != data:
            raise ValueError("coverage rebind audit immutable conflict")
        if current is None:
            store.put(run_id, key, data)
        if store.get(run_id, key) != data:
            raise ValueError("coverage rebind audit storage verification failed")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"coverage rebind audit storage failed: {exc}") from exc


def _save_attestation(
    store: TranscriptStore,
    run_id: str,
    key: str,
    attestation: CoverageRebindAttestation,
) -> bool:
    data = encode_coverage_rebind_attestation(attestation)
    try:
        current = store.get(run_id, key)
        if current is not None and current != data:
            raise ValueError("coverage rebind attestation immutable conflict")
        stored = current is None
        if stored:
            store.put(run_id, key, data)
        if store.get(run_id, key) != data:
            raise ValueError("coverage rebind attestation storage verification failed")
        return stored
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"coverage rebind attestation storage failed: {exc}") from exc


def load_attested_coverage_evidence(
    *,
    repo_path: str,
    store: TranscriptStore,
    provenance: TranslationArtifactProvenance,
    source_repo: str,
    source_pr: int,
    translation_pr: int,
    new_candidate_sha: str,
) -> CoverageEvidence:
    """Re-prove an exact trusted-store receipt, then strictly load K evidence."""
    if provenance.coverage_version != 1:
        raise ValueError("coverage rebind original coverage version is invalid")
    old_run_id = provenance.coverage_run_id
    old_digest = provenance.coverage_digest
    if old_run_id is None or old_digest is None:
        raise ValueError("coverage rebind original coverage binding is missing")
    old_candidate_sha = _require_sha(
        provenance.candidate_sha, field="old candidate SHA"
    )
    new_candidate_sha = _require_sha(new_candidate_sha, field="new candidate SHA")
    if old_candidate_sha == new_candidate_sha:
        raise ValueError("coverage rebind attestation is not an exact-binding fallback")
    if provenance.authority.source_repo.casefold() != source_repo.casefold():
        raise ValueError("coverage rebind attestation source repository mismatch")
    if provenance.authority.source_pr != source_pr:
        raise ValueError("coverage rebind attestation source pull request mismatch")
    key = coverage_rebind_attestation_key(
        source_repo=source_repo,
        source_pr=source_pr,
        translation_pr=translation_pr,
        old_candidate_sha=old_candidate_sha,
        old_run_id=old_run_id,
        old_coverage_digest=old_digest,
        new_candidate_sha=new_candidate_sha,
    )
    try:
        data = store.get(old_run_id, key)
    except Exception as exc:
        raise ValueError(f"coverage rebind attestation load failed: {exc}") from exc
    if data is None:
        raise ValueError("coverage rebind attestation is missing for exact candidate")
    attestation = decode_coverage_rebind_attestation(data)
    expected_identity = _attestation_identity(
        source_repo=source_repo,
        source_pr=source_pr,
        translation_pr=translation_pr,
        old_candidate_sha=old_candidate_sha,
        old_run_id=old_run_id,
        old_coverage_digest=old_digest,
        new_candidate_sha=new_candidate_sha,
    )
    actual_identity = {
        "source_repo": attestation.source_repo,
        "source_pr": attestation.source_pr,
        "translation_pr": attestation.translation_pr,
        "old_candidate_sha": attestation.old_candidate_sha,
        "old_run_id": attestation.old_run_id,
        "old_coverage_digest": attestation.old_coverage_digest,
        "new_candidate_sha": attestation.new_candidate_sha,
        "rule": attestation.rule,
        "rule_version": attestation.rule_version,
    }
    if actual_identity != expected_identity:
        raise ValueError("coverage rebind attestation identity mismatch")
    validate_authority_evidence(
        repo_path,
        provenance,
        expected_repo=source_repo,
        expected_source_pr=source_pr,
        current_candidate_sha=new_candidate_sha,
    )
    old_evidence = load_coverage_evidence(
        store,
        old_run_id,
        candidate_sha=old_candidate_sha,
        expected_digest=old_digest,
    )
    proof = derive_same_fragment_coverage_rebind(
        repo_path=repo_path,
        provenance=provenance,
        old_evidence=old_evidence,
        new_candidate_sha=new_candidate_sha,
    )
    proof_digest = hashlib.sha256(proof.audit_data).hexdigest()
    if proof_digest != attestation.proof_audit_digest:
        raise ValueError("coverage rebind attestation proof digest mismatch")
    if proof.new_evidence.digest != attestation.new_coverage_digest:
        raise ValueError("coverage rebind attestation new coverage digest mismatch")
    audit_key = coverage_rebind_audit_key(new_candidate_sha)
    try:
        audit_data = store.get(old_run_id, audit_key)
    except Exception as exc:
        raise ValueError(f"coverage rebind audit load failed: {exc}") from exc
    if audit_data != proof.audit_data:
        raise ValueError("coverage rebind attestation audit proof mismatch")
    evidence = load_coverage_evidence(
        store,
        old_run_id,
        candidate_sha=new_candidate_sha,
        expected_digest=proof.new_evidence.digest,
    )
    if evidence != proof.new_evidence:
        raise ValueError("coverage rebind strict K evidence differs from proof")
    return evidence


def rebind_same_fragment_coverage_evidence(
    *,
    repo_path: str,
    github: CoverageRebindGitHub,
    store: TranscriptStore,
    request: CoverageRebindRequest,
) -> CoverageRebindResult:
    """Persist proven K evidence and activate it with an exact attestation."""
    old_candidate_sha = _require_sha(
        request.old_candidate_sha, field="old candidate SHA"
    )
    new_candidate_sha = _require_sha(
        request.new_candidate_sha, field="new candidate SHA"
    )
    old_digest = _require_digest(request.old_digest, field="old digest")
    expected_new_digest = _require_digest(
        request.expected_new_digest, field="expected new digest"
    )
    if type(request.old_run_id) is not str or not request.old_run_id:
        raise ValueError("coverage rebind old run ID is invalid")
    if request.source_pr <= 0 or request.translation_pr <= 0:
        raise ValueError("coverage rebind pull request identity is invalid")

    initial_body, initial_head = _pull_snapshot(
        github,
        source_repo=request.source_repo,
        translation_pr=request.translation_pr,
    )
    if initial_head != new_candidate_sha:
        raise ValueError("coverage rebind remote head does not match expected K")
    provenance = parse_authority_evidence(initial_body)
    if provenance.candidate_sha != old_candidate_sha:
        raise ValueError("coverage rebind artifact root does not match old candidate C")
    if provenance.authority.source_repo.casefold() != request.source_repo.casefold():
        raise ValueError("coverage rebind source repository identity mismatch")
    if provenance.authority.source_pr != request.source_pr:
        raise ValueError("coverage rebind source pull request identity mismatch")
    if provenance.coverage_run_id != request.old_run_id or provenance.coverage_version != 1:
        raise ValueError("coverage rebind old coverage binding is invalid")
    if provenance.coverage_digest != old_digest:
        raise ValueError("coverage rebind old coverage binding is invalid")
    validate_authority_evidence(
        repo_path,
        provenance,
        expected_repo=request.source_repo,
        expected_source_pr=request.source_pr,
        current_candidate_sha=new_candidate_sha,
    )
    old_evidence = load_coverage_evidence(
        store,
        request.old_run_id,
        candidate_sha=old_candidate_sha,
        expected_digest=old_digest,
    )
    proof = derive_same_fragment_coverage_rebind(
        repo_path=repo_path,
        provenance=replace(provenance, coverage_digest=old_digest),
        old_evidence=old_evidence,
        new_candidate_sha=new_candidate_sha,
    )
    if proof.new_evidence.digest != expected_new_digest:
        raise ValueError(
            "coverage rebind new coverage digest does not match the expected digest"
        )

    save_coverage_evidence(store, request.old_run_id, proof.new_evidence)
    _save_audit(store, request.old_run_id, new_candidate_sha, proof.audit_data)

    latest_body, latest_head = _pull_snapshot(
        github,
        source_repo=request.source_repo,
        translation_pr=request.translation_pr,
    )
    if latest_head != new_candidate_sha:
        raise ValueError("coverage rebind remote head drifted before metadata update")
    latest = parse_authority_evidence(latest_body)
    expected_old = replace(provenance, coverage_digest=old_digest)
    if latest != expected_old:
        raise ValueError("coverage rebind authority binding drifted before attestation")
    attestation = _build_attestation(request=request, proof=proof)
    key = coverage_rebind_attestation_key(
        source_repo=request.source_repo,
        source_pr=request.source_pr,
        translation_pr=request.translation_pr,
        old_candidate_sha=old_candidate_sha,
        old_run_id=request.old_run_id,
        old_coverage_digest=old_digest,
        new_candidate_sha=new_candidate_sha,
    )
    stored = _save_attestation(store, request.old_run_id, key, attestation)
    return CoverageRebindResult(proof=proof, attestation_stored=stored)
