"""Immutable RU-authority selection and translation-artifact provenance."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.git_ops import (
    commit_is_ancestor,
    commit_parent_sha,
    ensure_commit,
    resolve_commit_ref,
)

AUTHORITY_WIRE_MARKER = "ydbdoc-ru-authority:v1"
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_WIRE_RE = re.compile(rf"<!--\s*{re.escape(AUTHORITY_WIRE_MARKER)}:([A-Za-z0-9_-]+)\s*-->")


class AuthorityRoute(StrEnum):
    """Producer route captured before any model or publication work."""

    MERGED = "merged"
    OPEN_SAME_REPO = "open-same-repo"
    OPEN_FORK = "open-fork"


@dataclass(frozen=True)
class RuAuthority:
    """Frozen H0/H/B/R lineage used by every source-side reader."""

    source_repo: str
    source_pr: int
    source_base_sha: str  # H0
    source_head_sha: str  # H
    baseline_sha: str  # B
    ru_sha: str  # R
    mode: RuAuthorityMode

    @property
    def ru_base_sha(self) -> str:
        return self.baseline_sha if self.ru_sha == self.baseline_sha else self.source_base_sha


@dataclass(frozen=True)
class FrozenAuthoritySelection:
    """Producer-only route data, including checkout S and prepare parent P."""

    authority: RuAuthority
    route: AuthorityRoute
    checkout_sha: str  # S
    prepare_parent_sha: str  # P


@dataclass(frozen=True)
class TranslationArtifactProvenance:
    """One immutable authority lineage bound to root candidate C."""

    authority: RuAuthority
    candidate_sha: str  # C
    coverage_version: int | None = None
    coverage_run_id: str | None = None
    coverage_digest: str | None = None


def _require_sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"authority evidence {field} must be a full lowercase commit SHA")
    return value


def _available_commit(repo_path: str, sha: str, *, role: str) -> str:
    sha = _require_sha(sha, field=role)
    if not ensure_commit(repo_path, sha):
        raise RuntimeError(f"RU authority {role} commit unavailable: {sha}")
    resolved = resolve_commit_ref(repo_path, sha)
    if resolved != sha:
        raise RuntimeError(f"RU authority {role} resolved to {resolved}, expected {sha}")
    return resolved


def freeze_ru_authority(
    repo_path: str,
    *,
    source_repo: str,
    source_pr: int,
    source_head_sha: str,
    source_base_sha: str,
    merge_commit_sha: str | None,
    merged: bool,
    fork: bool,
    baseline_sha: str,
    checkout_sha: str,
    mode: RuAuthorityMode,
) -> FrozenAuthoritySelection:
    """Freeze route-specific H0/H/B/R plus checkout S before model work."""
    baseline = _available_commit(repo_path, baseline_sha, role="B")
    checkout = _available_commit(repo_path, checkout_sha, role="S")

    if merged:
        if not merge_commit_sha:
            raise RuntimeError(f"merged source PR #{source_pr} has no landed merge commit H")
        head = _available_commit(repo_path, merge_commit_sha, role="H")
        base = commit_parent_sha(repo_path, head)
        route = AuthorityRoute.MERGED
        prepare_parent = baseline
        ru_sha = head if mode is RuAuthorityMode.SOURCE_PRESERVING else baseline
    else:
        head = _available_commit(repo_path, source_head_sha, role="H")
        if head != checkout:
            raise RuntimeError(
                f"open source PR #{source_pr} authority H {head} does not match "
                f"frozen checkout S {checkout}"
            )
        base = _available_commit(repo_path, source_base_sha, role="H0")
        route = AuthorityRoute.OPEN_FORK if fork else AuthorityRoute.OPEN_SAME_REPO
        prepare_parent = baseline if fork else checkout
        ru_sha = head

    authority = RuAuthority(
        source_repo=source_repo,
        source_pr=source_pr,
        source_base_sha=base,
        source_head_sha=head,
        baseline_sha=baseline,
        ru_sha=ru_sha,
        mode=mode,
    )
    return FrozenAuthoritySelection(
        authority=authority,
        route=route,
        checkout_sha=checkout,
        prepare_parent_sha=prepare_parent,
    )


def bind_translation_artifact(
    repo_path: str,
    selection: FrozenAuthoritySelection,
    candidate_sha: str,
) -> TranslationArtifactProvenance:
    """Bind C only after proving that its direct parent is frozen P."""
    candidate = _available_commit(repo_path, candidate_sha, role="C")
    parent = commit_parent_sha(repo_path, candidate)
    if parent != selection.prepare_parent_sha:
        raise RuntimeError(
            f"candidate C {candidate} has parent {parent}, expected frozen P "
            f"{selection.prepare_parent_sha} for {selection.route.value}"
        )
    return TranslationArtifactProvenance(selection.authority, candidate)


def authority_payload(provenance: TranslationArtifactProvenance) -> dict[str, object]:
    authority = provenance.authority
    payload: dict[str, object] = {
        "version": 1,
        "source": {
            "repo": authority.source_repo,
            "pr": authority.source_pr,
            "head_sha": authority.source_head_sha,
            "base_sha": authority.source_base_sha,
        },
        "selection": {
            "kind": authority.mode.value,
            "ru_sha": authority.ru_sha,
            "baseline_sha": authority.baseline_sha,
        },
        "candidate_sha": provenance.candidate_sha,
    }
    coverage_values = (
        provenance.coverage_version,
        provenance.coverage_run_id,
        provenance.coverage_digest,
    )
    if any(value is not None for value in coverage_values):
        if not all(value is not None for value in coverage_values):
            raise ValueError("authority coverage binding must be complete")
        if provenance.coverage_version != 1:
            raise ValueError("unsupported authority coverage version")
        if not isinstance(provenance.coverage_run_id, str) or not provenance.coverage_run_id:
            raise ValueError("authority coverage run ID is invalid")
        if (
            not isinstance(provenance.coverage_digest, str)
            or _SHA256_RE.fullmatch(provenance.coverage_digest) is None
        ):
            raise ValueError("authority coverage digest is invalid")
        payload["coverage"] = {
            "digest": provenance.coverage_digest,
            "run_id": provenance.coverage_run_id,
            "version": provenance.coverage_version,
        }
    return payload


def render_authority_evidence(provenance: TranslationArtifactProvenance) -> str:
    raw = json.dumps(authority_payload(provenance), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"<!-- {AUTHORITY_WIRE_MARKER}:{encoded} -->"


def _strict_object(value: object, *, field: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"malformed authority evidence {field} object")
    return value


def parse_authority_evidence(body: str) -> TranslationArtifactProvenance:
    """Decode exactly one self-contained v1 evidence comment."""
    matches = list(_WIRE_RE.finditer(body))
    if not matches:
        if AUTHORITY_WIRE_MARKER in body:
            raise ValueError("malformed authority evidence envelope")
        raise ValueError("authority provenance evidence missing from translation PR body")
    if len(matches) != 1:
        raise ValueError("authority provenance evidence must occur exactly once")
    encoded = matches[0].group(1)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed authority evidence payload") from exc

    if not isinstance(payload, dict):
        raise ValueError("malformed authority evidence root object")
    old_keys = {"version", "source", "selection", "candidate_sha"}
    new_keys = old_keys | {"coverage"}
    if frozenset(payload) not in {frozenset(old_keys), frozenset(new_keys)}:
        raise ValueError("malformed authority evidence root object")
    root = payload
    if root["version"] != 1:
        raise ValueError(f"unsupported authority evidence version: {root['version']!r}")
    source = _strict_object(
        root["source"],
        field="source",
        keys={"repo", "pr", "head_sha", "base_sha"},
    )
    selection = _strict_object(
        root["selection"],
        field="selection",
        keys={"kind", "ru_sha", "baseline_sha"},
    )
    if not isinstance(source["repo"], str) or not source["repo"]:
        raise ValueError("malformed authority evidence source repository")
    if isinstance(source["pr"], bool) or not isinstance(source["pr"], int) or source["pr"] <= 0:
        raise ValueError("malformed authority evidence source pull request")
    try:
        mode = RuAuthorityMode(selection["kind"])
    except (TypeError, ValueError) as exc:
        raise ValueError("malformed authority evidence selection kind") from exc

    authority = RuAuthority(
        source_repo=source["repo"],
        source_pr=source["pr"],
        source_base_sha=_require_sha(source["base_sha"], field="source.base_sha"),
        source_head_sha=_require_sha(source["head_sha"], field="source.head_sha"),
        baseline_sha=_require_sha(selection["baseline_sha"], field="selection.baseline_sha"),
        ru_sha=_require_sha(selection["ru_sha"], field="selection.ru_sha"),
        mode=mode,
    )
    coverage_version: int | None = None
    coverage_run_id: str | None = None
    coverage_digest: str | None = None
    if "coverage" in root:
        coverage = _strict_object(
            root["coverage"],
            field="coverage",
            keys={"digest", "run_id", "version"},
        )
        if coverage["version"] != 1:
            raise ValueError("unsupported authority coverage version")
        if not isinstance(coverage["run_id"], str) or not coverage["run_id"]:
            raise ValueError("malformed authority evidence coverage run ID")
        if (
            not isinstance(coverage["digest"], str)
            or _SHA256_RE.fullmatch(coverage["digest"]) is None
        ):
            raise ValueError("malformed authority evidence coverage digest")
        coverage_version = 1
        coverage_run_id = coverage["run_id"]
        coverage_digest = coverage["digest"]
    return TranslationArtifactProvenance(
        authority=authority,
        candidate_sha=_require_sha(root["candidate_sha"], field="candidate_sha"),
        coverage_version=coverage_version,
        coverage_run_id=coverage_run_id,
        coverage_digest=coverage_digest,
    )


def validate_authority_evidence(
    repo_path: str,
    provenance: TranslationArtifactProvenance,
    *,
    expected_repo: str,
    expected_source_pr: int,
    current_candidate_sha: str,
) -> TranslationArtifactProvenance:
    """Validate only frozen v1 facts; never reinterpret them through live PR metadata."""
    authority = provenance.authority
    if authority.source_repo.casefold() != expected_repo.casefold():
        raise ValueError(
            f"foreign authority source repository {authority.source_repo!r}; "
            f"expected {expected_repo!r}"
        )
    if authority.source_pr != expected_source_pr:
        raise ValueError(
            f"authority source pull request mismatch: {authority.source_pr} != {expected_source_pr}"
        )
    h0 = _available_commit(repo_path, authority.source_base_sha, role="H0")
    h = _available_commit(repo_path, authority.source_head_sha, role="H")
    b = _available_commit(repo_path, authority.baseline_sha, role="B")
    r = _available_commit(repo_path, authority.ru_sha, role="R")
    c = _available_commit(repo_path, provenance.candidate_sha, role="C")
    k = _available_commit(repo_path, current_candidate_sha, role="K")

    if authority.mode is RuAuthorityMode.SOURCE_PRESERVING and r != h:
        raise ValueError(f"source-preserving authority mismatch: R {r} != H {h}")
    if authority.mode is RuAuthorityMode.CURRENT and r not in {h, b}:
        raise ValueError(f"current authority R {r} matches neither frozen H {h} nor B {b}")
    if not commit_is_ancestor(repo_path, h0, h):
        raise ValueError(f"authority H0 {h0} is not an ancestor of H {h}")
    candidate_parent = commit_parent_sha(repo_path, c)
    if candidate_parent not in {b, h}:
        raise ValueError(
            f"candidate C {c} parent {candidate_parent} matches neither frozen B {b} nor H {h}"
        )
    if authority.mode is RuAuthorityMode.CURRENT and r == b and r != h and candidate_parent != b:
        raise ValueError(f"current landed authority C {c} is not based on frozen B {b}")
    if not commit_is_ancestor(repo_path, c, k):
        raise ValueError(f"artifact root C {c} is not an ancestor of verified checkout K {k}")
    return provenance
