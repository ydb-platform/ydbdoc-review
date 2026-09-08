from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.git_ops import read_text_at_commit
from ydbdoc_review.github.provenance import (
    RuAuthority,
    TranslationArtifactProvenance,
    parse_authority_evidence,
    render_authority_evidence,
)
from ydbdoc_review.ops.coverage_rebind import (
    CoverageRebindRequest,
    coverage_rebind_attestation_key,
    coverage_rebind_audit_key,
    decode_coverage_rebind_attestation,
    derive_same_fragment_coverage_rebind,
    encode_coverage_rebind_attestation,
    load_attested_coverage_evidence,
    rebind_same_fragment_coverage_evidence,
)
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore, NullTranscriptStore
from ydbdoc_review.translation.coverage import (
    CoverageEvidenceBindingMiss,
    CoveragePlan,
    CoverageUnit,
    build_coverage_evidence,
    coverage_evidence_key,
    load_coverage_evidence,
    save_coverage_evidence,
    validate_coverage_evidence,
)

REPO = "ydb-platform/ydb"
SOURCE_PR = 51079
TRANSLATION_PR = 52432
RUN_ID = "db1ddf56-e833-4aec-997c-e3bb8ab8192a"
AUTH_RU = "ydb/docs/ru/core/security/authentication.md"
AUTH_EN = "ydb/docs/en/core/security/authentication.md"
GLOSSARY_RU = "ydb/docs/ru/core/concepts/glossary.md"
GLOSSARY_EN = "ydb/docs/en/core/concepts/glossary.md"
PROTECTED_RU = "ydb/docs/ru/core/security/_assets/token.md"
PROTECTED_EN = "ydb/docs/en/core/security/_assets/token.md"
GOOD_HREF = "../reference/configuration/security_config.md#security-auth"
BAD_HREF = "../reference/configuration/auth_config.md#security-auth"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, message: str, *, allow_empty: bool = False) -> str:
    _git(root, "add", ".")
    args = ["commit", "-qm", message]
    if allow_empty:
        args.insert(1, "--allow-empty")
    _git(root, *args)
    return _git(root, "rev-parse", "HEAD")


def _auth_text(href: str, *, suffix: str = "") -> str:
    return f"# Authentication\n\n[Security settings]({href}){suffix}\n"


@dataclass(frozen=True)
class Fixture:
    repo: Path
    authority: RuAuthority
    provenance: TranslationArtifactProvenance
    evidence: object
    store: InMemoryTranscriptStore
    h0: str
    h: str
    b: str
    c: str
    k: str


def _fixture(tmp_path: Path) -> Fixture:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    glossary_source = "## New\n\nRequired source.\n"
    glossary_baseline = "# Existing\n\nAccepted prose.\n"
    glossary_target = "\n## New\n\nRequired translation.\n"
    _put(repo, AUTH_RU, _auth_text(BAD_HREF))
    _put(repo, AUTH_EN, _auth_text(GOOD_HREF))
    _put(repo, GLOSSARY_RU, glossary_source)
    _put(repo, GLOSSARY_EN, glossary_baseline)
    protected = "{% include [token](token.md) %}\n"
    _put(repo, PROTECTED_RU, protected)
    _put(repo, PROTECTED_EN, protected)
    for locale in ("ru", "en"):
        _put(
            repo,
            f"ydb/docs/{locale}/core/reference/configuration/security_config.md",
            "# Security config {#security-auth}\n",
        )
    h0 = _commit(repo, "source base")

    h = _commit(repo, "source PR head", allow_empty=True)
    b = _commit(repo, "frozen baseline", allow_empty=True)

    _put(repo, AUTH_EN, _auth_text(BAD_HREF))
    _put(repo, GLOSSARY_EN, glossary_baseline + glossary_target)
    c = _commit(repo, "translated candidate")
    _put(repo, AUTH_EN, _auth_text(GOOD_HREF))
    k = _commit(repo, "manual same-fragment path repair")

    authority = RuAuthority(
        source_repo=REPO,
        source_pr=SOURCE_PR,
        source_base_sha=h0,
        source_head_sha=h,
        baseline_sha=b,
        ru_sha=b,
        mode=RuAuthorityMode.CURRENT,
    )
    auth_source = _auth_text(BAD_HREF)
    auth_baseline = _auth_text(GOOD_HREF)
    auth_plan = CoveragePlan(
        source_path=AUTH_RU,
        source_hash=_hash(auth_source),
        en_hash=_hash(auth_baseline),
        units=(
            CoverageUnit(
                key="1" * 64,
                action="translate_required",
                source=auth_source,
                en_span=None,
                target=None,
                reason="full prose translation",
            ),
        ),
        required_fragments=frozenset(),
        mode="full",
    )
    glossary_plan = CoveragePlan(
        source_path=GLOSSARY_RU,
        source_hash=_hash(glossary_source),
        en_hash=_hash(glossary_baseline),
        units=(
            CoverageUnit(
                key="2" * 64,
                action="translate_required",
                source=glossary_source,
                en_span=(len(glossary_baseline), len(glossary_baseline)),
                target=None,
                reason="required missing section",
            ),
        ),
        required_fragments=frozenset(),
        mode="units",
    )
    protected_plan = CoveragePlan(
        source_path=PROTECTED_RU,
        source_hash=_hash(protected),
        en_hash=_hash(protected),
        units=(
            CoverageUnit(
                key="3" * 64,
                action="materialize_protected",
                source=protected,
                en_span=None,
                target=protected,
                reason="protected source structure",
            ),
        ),
        required_fragments=frozenset(),
        mode="full",
    )
    evidence = build_coverage_evidence(
        authority=authority,
        candidate_sha=c,
        plans={
            AUTH_EN: auth_plan,
            GLOSSARY_EN: glossary_plan,
            PROTECTED_EN: protected_plan,
        },
        baseline_en={
            AUTH_EN: auth_baseline,
            GLOSSARY_EN: glossary_baseline,
            PROTECTED_EN: protected,
        },
        candidate_files={
            AUTH_EN: _auth_text(BAD_HREF),
            GLOSSARY_EN: glossary_baseline + glossary_target,
            PROTECTED_EN: protected,
        },
    )
    store = InMemoryTranscriptStore()
    save_coverage_evidence(store, RUN_ID, evidence)
    provenance = TranslationArtifactProvenance(
        authority=authority,
        candidate_sha=c,
        coverage_version=1,
        coverage_run_id=RUN_ID,
        coverage_digest=evidence.digest,
    )
    return Fixture(repo, authority, provenance, evidence, store, h0, h, b, c, k)


class FakeGitHub:
    def __init__(self, *, body: str, head_sha: str) -> None:
        self.body = body
        self.head_sha = head_sha
        self.updated_bodies: list[str] = []

    def get_pull(self, owner: str, repo: str, pr_number: int) -> dict[str, object]:
        assert f"{owner}/{repo}" == REPO
        assert pr_number == TRANSLATION_PR
        return {
            "body": self.body,
            "head": {"sha": self.head_sha},
            "base": {"repo": {"full_name": REPO}},
        }

    def update_pull_body(self, owner: str, repo: str, pr_number: int, body: str) -> None:
        self.updated_bodies.append(body)
        self.body = body


class DriftingGitHub(FakeGitHub):
    def __init__(self, *, body: str, head_sha: str, drift: str) -> None:
        super().__init__(body=body, head_sha=head_sha)
        self.drift = drift
        self.get_calls = 0

    def get_pull(self, owner: str, repo: str, pr_number: int) -> dict[str, object]:
        self.get_calls += 1
        if self.get_calls == 2:
            if self.drift == "body":
                self.body += "\nConcurrent edit."
            else:
                self.head_sha = "f" * 40
        return super().get_pull(owner, repo, pr_number)


class FailingUpdateGitHub(FakeGitHub):
    def update_pull_body(self, owner: str, repo: str, pr_number: int, body: str) -> None:
        raise RuntimeError("simulated metadata failure")


class RacingUpdateGitHub(FakeGitHub):
    """Apply an unrelated edit just after returning the final snapshot."""

    concurrent_edit = "\nConcurrent edit after the final snapshot."

    def __init__(self, *, body: str, head_sha: str) -> None:
        super().__init__(body=body, head_sha=head_sha)
        self.get_calls = 0

    def get_pull(self, owner: str, repo: str, pr_number: int) -> dict[str, object]:
        self.get_calls += 1
        snapshot = super().get_pull(owner, repo, pr_number)
        if self.get_calls == 2:
            self.body += self.concurrent_edit
        return snapshot


class MarkerDriftingGitHub(FakeGitHub):
    def __init__(self, *, body: str, head_sha: str, drifted_body: str) -> None:
        super().__init__(body=body, head_sha=head_sha)
        self.drifted_body = drifted_body
        self.get_calls = 0

    def get_pull(self, owner: str, repo: str, pr_number: int) -> dict[str, object]:
        self.get_calls += 1
        if self.get_calls == 2:
            self.body = self.drifted_body
        return super().get_pull(owner, repo, pr_number)


class FailingAttestationStore(InMemoryTranscriptStore):
    fail_attestation = True

    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        if self.fail_attestation and "coverage-rebind-bindings" in object_key:
            raise RuntimeError("simulated attestation failure")
        super().put(run_id, object_key, data)


def _request(fx: Fixture, *, expected_digest: str) -> CoverageRebindRequest:
    return CoverageRebindRequest(
        source_repo=REPO,
        source_pr=SOURCE_PR,
        translation_pr=TRANSLATION_PR,
        old_run_id=RUN_ID,
        old_digest=fx.provenance.coverage_digest or "",
        old_candidate_sha=fx.c,
        new_candidate_sha=fx.k,
        expected_new_digest=expected_digest,
    )


def _attestation_key(fx: Fixture) -> str:
    return coverage_rebind_attestation_key(
        source_repo=REPO,
        source_pr=SOURCE_PR,
        translation_pr=TRANSLATION_PR,
        old_candidate_sha=fx.c,
        old_run_id=RUN_ID,
        old_coverage_digest=fx.provenance.coverage_digest or "",
        new_candidate_sha=fx.k,
    )


def test_real_git_rebind_is_exact_and_controller_stores_attestation_last(
    tmp_path: Path,
) -> None:
    fx = _fixture(tmp_path)
    with pytest.raises(ValueError, match="missing for candidate"):
        load_coverage_evidence(
            fx.store,
            RUN_ID,
            candidate_sha=fx.k,
            expected_digest=fx.provenance.coverage_digest or "",
        )

    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo),
        provenance=fx.provenance,
        old_evidence=fx.evidence,
        new_candidate_sha=fx.k,
    )
    assert proof.changed_paths == (AUTH_EN,)
    assert proof.new_evidence.candidate_sha == fx.k
    assert dict(proof.new_evidence.plans)[GLOSSARY_EN] == dict(fx.evidence.plans)[GLOSSARY_EN]
    assert dict(proof.new_evidence.candidate_file_hashes)[GLOSSARY_EN] == dict(
        fx.evidence.candidate_file_hashes
    )[GLOSSARY_EN]

    body = "RED: unresolved link remains.\n\n" + render_authority_evidence(fx.provenance)
    gh = FakeGitHub(body=body, head_sha=fx.k)
    result = rebind_same_fragment_coverage_evidence(
        repo_path=str(fx.repo),
        github=gh,
        store=fx.store,
        request=_request(fx, expected_digest=proof.new_evidence.digest),
    )

    assert result.proof == proof
    assert result.attestation_stored is True
    assert gh.updated_bodies == []
    assert gh.body == body
    rebound = parse_authority_evidence(gh.body)
    assert rebound == fx.provenance
    assert fx.store.get(RUN_ID, _attestation_key(fx)) is not None
    assert load_coverage_evidence(
        fx.store,
        RUN_ID,
        candidate_sha=fx.k,
        expected_digest=proof.new_evidence.digest,
    ) == proof.new_evidence
    assert load_attested_coverage_evidence(
        repo_path=str(fx.repo),
        store=fx.store,
        provenance=fx.provenance,
        source_repo=REPO,
        source_pr=SOURCE_PR,
        translation_pr=TRANSLATION_PR,
        new_candidate_sha=fx.k,
    ) == proof.new_evidence
    assert fx.store.get(RUN_ID, coverage_rebind_audit_key(fx.k)) is not None
    assert fx.store.get(RUN_ID, coverage_evidence_key(fx.c)) is not None


def test_only_exact_missing_or_digest_mismatch_is_a_binding_miss(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    with pytest.raises(CoverageEvidenceBindingMiss, match="missing"):
        load_coverage_evidence(
            fx.store,
            RUN_ID,
            candidate_sha=fx.k,
            expected_digest=fx.evidence.digest,
        )
    with pytest.raises(CoverageEvidenceBindingMiss, match="digest mismatch"):
        load_coverage_evidence(
            fx.store,
            RUN_ID,
            candidate_sha=fx.c,
            expected_digest="0" * 64,
        )

    fx.store.put(RUN_ID, coverage_evidence_key(fx.c), b"forged-object")
    with pytest.raises(ValueError, match="corrupt") as error:
        load_coverage_evidence(
            fx.store,
            RUN_ID,
            candidate_sha=fx.c,
            expected_digest=fx.evidence.digest,
        )
    assert not isinstance(error.value, CoverageEvidenceBindingMiss)


def test_rebound_units_evidence_still_requires_semantic_validation(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo),
        provenance=fx.provenance,
        old_evidence=fx.evidence,
        new_candidate_sha=fx.k,
    )
    calls: list[str] = []

    with pytest.raises(ValueError, match="semantic coverage rejected"):
        validate_coverage_evidence(
            proof.new_evidence,
            authority=fx.authority,
            read_source=lambda path: read_text_at_commit(str(fx.repo), fx.b, path),
            read_baseline_en=lambda path: read_text_at_commit(str(fx.repo), fx.b, path),
            read_candidate=lambda path: read_text_at_commit(str(fx.repo), fx.k, path),
            semantic_validator=lambda path, *_args: calls.append(path) is None and False,
        )
    assert calls == [GLOSSARY_EN]


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (_auth_text(GOOD_HREF, suffix=" extra prose"), "byte-exact"),
        (_auth_text("../reference/configuration/other.md#security-auth"), "byte-exact"),
        (_auth_text("../reference/configuration/security_config.md#other"), "byte-exact"),
        ("# Authentication\n\n[Changed label](" + GOOD_HREF + ")\n", "byte-exact"),
        ("# Authentication\n\nDeleted link.\n", "byte-exact"),
        ("# Authentication\n\n`config: changed`\n", "byte-exact"),
    ],
)
def test_arbitrary_manual_edits_are_rejected_before_writes(
    tmp_path: Path, replacement: str, message: str
) -> None:
    fx = _fixture(tmp_path)
    _git(fx.repo, "reset", "--hard", fx.c)
    _put(fx.repo, AUTH_EN, replacement)
    bad_k = _commit(fx.repo, "unsupported manual edit")

    with pytest.raises(ValueError, match=message):
        derive_same_fragment_coverage_rebind(
            repo_path=str(fx.repo),
            provenance=fx.provenance,
            old_evidence=fx.evidence,
            new_candidate_sha=bad_k,
        )
    assert fx.store.list_keys(RUN_ID) == [coverage_evidence_key(fx.c)]


@pytest.mark.parametrize("target", ["units", "protected"])
def test_units_or_protected_only_changed_path_is_rejected(tmp_path: Path, target: str) -> None:
    fx = _fixture(tmp_path)
    _git(fx.repo, "reset", "--hard", fx.c)
    path = GLOSSARY_EN if target == "units" else PROTECTED_EN
    _put(fx.repo, path, (fx.repo / path).read_text() + "manual\n")
    bad_k = _commit(fx.repo, "changed non-full path")
    with pytest.raises(ValueError, match="full prose"):
        derive_same_fragment_coverage_rebind(
            repo_path=str(fx.repo),
            provenance=fx.provenance,
            old_evidence=fx.evidence,
            new_candidate_sha=bad_k,
        )


def test_extra_path_mode_change_and_non_child_are_rejected(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    _git(fx.repo, "reset", "--hard", fx.c)
    _put(fx.repo, AUTH_EN, _auth_text(GOOD_HREF))
    _put(fx.repo, "unrelated.txt", "extra\n")
    extra_k = _commit(fx.repo, "path fix plus extra file")
    with pytest.raises(ValueError, match="modified evidence files"):
        derive_same_fragment_coverage_rebind(
            repo_path=str(fx.repo), provenance=fx.provenance,
            old_evidence=fx.evidence, new_candidate_sha=extra_k,
        )

    _git(fx.repo, "reset", "--hard", fx.c)
    _put(fx.repo, AUTH_EN, _auth_text(GOOD_HREF))
    (fx.repo / AUTH_EN).chmod(0o755)
    mode_k = _commit(fx.repo, "path fix plus mode change")
    with pytest.raises(ValueError, match="regular non-executable"):
        derive_same_fragment_coverage_rebind(
            repo_path=str(fx.repo), provenance=fx.provenance,
            old_evidence=fx.evidence, new_candidate_sha=mode_k,
        )

    with pytest.raises(ValueError, match="direct child"):
        derive_same_fragment_coverage_rebind(
            repo_path=str(fx.repo), provenance=fx.provenance,
            old_evidence=fx.evidence, new_candidate_sha=fx.b,
        )


def test_wrong_binding_digest_and_expected_digest_fail_closed(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    body = render_authority_evidence(fx.provenance)
    gh = FakeGitHub(body=body, head_sha=fx.k)
    with pytest.raises(ValueError, match="old coverage binding"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(fx.repo), github=gh, store=fx.store,
            request=replace(_request(fx, expected_digest="0" * 64), old_digest="f" * 64),
        )
    assert gh.updated_bodies == []


@pytest.mark.parametrize("corruption", ["candidate", "source", "baseline", "authority"])
def test_old_evidence_snapshot_corruption_is_rejected(
    tmp_path: Path, corruption: str
) -> None:
    fx = _fixture(tmp_path)
    evidence = fx.evidence
    if corruption == "candidate":
        hashes = dict(evidence.candidate_file_hashes)
        hashes[AUTH_EN] = "f" * 64
        evidence = replace(evidence, candidate_file_hashes=tuple(sorted(hashes.items())))
    elif corruption == "source":
        plans = dict(evidence.plans)
        plans[AUTH_EN] = replace(plans[AUTH_EN], source_hash="f" * 64)
        evidence = replace(evidence, plans=tuple(sorted(plans.items())))
    elif corruption == "baseline":
        hashes = dict(evidence.baseline_en_hashes)
        hashes[AUTH_EN] = "f" * 64
        evidence = replace(evidence, baseline_en_hashes=tuple(sorted(hashes.items())))
    else:
        evidence = replace(
            evidence,
            authority=replace(evidence.authority, source_pr=SOURCE_PR + 1),
        )
    with pytest.raises(ValueError, match="coverage rebind"):
        derive_same_fragment_coverage_rebind(
            repo_path=str(fx.repo), provenance=fx.provenance,
            old_evidence=evidence, new_candidate_sha=fx.k,
        )


def test_concurrent_head_drift_after_persistence_prevents_attestation(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    gh = DriftingGitHub(
        body=render_authority_evidence(fx.provenance), head_sha=fx.k, drift="head"
    )
    with pytest.raises(ValueError, match="head drifted"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(fx.repo), github=gh, store=fx.store,
            request=_request(fx, expected_digest=proof.new_evidence.digest),
        )
    assert gh.updated_bodies == []
    assert fx.store.get(RUN_ID, _attestation_key(fx)) is None
    assert fx.store.get(RUN_ID, coverage_evidence_key(fx.k)) is not None
    assert fx.store.get(RUN_ID, coverage_rebind_audit_key(fx.k)) is not None


def test_concurrent_body_edit_exactly_before_update_is_not_overwritten(
    tmp_path: Path,
) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    initial_body = render_authority_evidence(fx.provenance)
    gh = RacingUpdateGitHub(body=initial_body, head_sha=fx.k)

    result = rebind_same_fragment_coverage_evidence(
        repo_path=str(fx.repo), github=gh, store=fx.store,
        request=_request(fx, expected_digest=proof.new_evidence.digest),
    )

    assert result.attestation_stored is True
    assert gh.body == initial_body + gh.concurrent_edit
    assert gh.updated_bodies == []
    assert fx.store.get(RUN_ID, _attestation_key(fx)) is not None
    assert fx.store.get(RUN_ID, coverage_evidence_key(fx.k)) is not None
    assert fx.store.get(RUN_ID, coverage_rebind_audit_key(fx.k)) is not None


def test_concurrent_authority_marker_change_never_stores_attestation(
    tmp_path: Path,
) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    gh = MarkerDriftingGitHub(
        body=render_authority_evidence(fx.provenance),
        head_sha=fx.k,
        drifted_body=render_authority_evidence(
            replace(fx.provenance, coverage_digest="0" * 64)
        ),
    )

    with pytest.raises(ValueError, match="binding drifted"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(fx.repo), github=gh, store=fx.store,
            request=_request(fx, expected_digest=proof.new_evidence.digest),
        )

    assert gh.updated_bodies == []
    assert fx.store.get(RUN_ID, _attestation_key(fx)) is None


def test_audit_conflict_and_metadata_failure_are_safe_to_retry(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    body = render_authority_evidence(fx.provenance)
    request = _request(fx, expected_digest=proof.new_evidence.digest)
    fx.store.put(RUN_ID, coverage_rebind_audit_key(fx.k), b"conflict")
    gh = FakeGitHub(body=body, head_sha=fx.k)
    with pytest.raises(ValueError, match="audit immutable conflict"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(fx.repo), github=gh, store=fx.store, request=request
        )
    assert gh.updated_bodies == []

    retry = _fixture(tmp_path / "retry")
    retry_proof = derive_same_fragment_coverage_rebind(
        repo_path=str(retry.repo), provenance=retry.provenance,
        old_evidence=retry.evidence, new_candidate_sha=retry.k,
    )
    retry_request = _request(retry, expected_digest=retry_proof.new_evidence.digest)
    retry_gh = FailingUpdateGitHub(
        body=render_authority_evidence(retry.provenance), head_sha=retry.k
    )
    retry_store = FailingAttestationStore()
    for object_key in retry.store.list_keys(RUN_ID):
        data = retry.store.get(RUN_ID, object_key)
        assert data is not None
        retry_store.put(RUN_ID, object_key, data)
    with pytest.raises(ValueError, match="attestation storage failed"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(retry.repo), github=retry_gh, store=retry_store,
            request=retry_request,
        )
    assert retry_store.get(RUN_ID, coverage_evidence_key(retry.k)) is not None
    assert retry_store.get(RUN_ID, coverage_rebind_audit_key(retry.k)) is not None
    assert retry_store.get(RUN_ID, _attestation_key(retry)) is None
    assert retry_gh.updated_bodies == []
    retry_store.fail_attestation = False
    result = rebind_same_fragment_coverage_evidence(
        repo_path=str(retry.repo), github=retry_gh, store=retry_store,
        request=retry_request,
    )
    assert result.attestation_stored is True

    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    with pytest.raises(ValueError, match="new coverage digest"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(fx.repo), github=gh, store=fx.store,
            request=_request(fx, expected_digest="0" * 64),
        )
    assert proof.new_evidence.digest != "0" * 64
    assert gh.updated_bodies == []


def test_head_or_body_drift_and_null_store_never_update_envelope(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    body = render_authority_evidence(fx.provenance)

    gh = FakeGitHub(body=body, head_sha="f" * 40)
    with pytest.raises(ValueError, match="remote head"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(fx.repo), github=gh, store=fx.store,
            request=_request(fx, expected_digest=proof.new_evidence.digest),
        )
    assert gh.updated_bodies == []

    gh = FakeGitHub(body=body, head_sha=fx.k)
    with pytest.raises(ValueError, match="coverage evidence missing"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(fx.repo), github=gh, store=NullTranscriptStore(),
            request=_request(fx, expected_digest=proof.new_evidence.digest),
        )
    assert gh.updated_bodies == []


def test_idempotent_retry_and_conflicting_new_object(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    body = render_authority_evidence(fx.provenance)
    gh = FakeGitHub(body=body, head_sha=fx.k)
    request = _request(fx, expected_digest=proof.new_evidence.digest)
    first = rebind_same_fragment_coverage_evidence(
        repo_path=str(fx.repo), github=gh, store=fx.store, request=request
    )
    second = rebind_same_fragment_coverage_evidence(
        repo_path=str(fx.repo), github=gh, store=fx.store, request=request
    )
    assert first.attestation_stored is True
    assert second.attestation_stored is False
    assert second.proof == first.proof

    conflict = _fixture(tmp_path / "other")
    conflict.store.put(RUN_ID, coverage_evidence_key(conflict.k), b"conflict")
    conflict_gh = FakeGitHub(
        body=render_authority_evidence(conflict.provenance), head_sha=conflict.k
    )
    conflict_proof = derive_same_fragment_coverage_rebind(
        repo_path=str(conflict.repo), provenance=conflict.provenance,
        old_evidence=conflict.evidence, new_candidate_sha=conflict.k,
    )
    with pytest.raises(ValueError, match="immutable conflict"):
        rebind_same_fragment_coverage_evidence(
            repo_path=str(conflict.repo), github=conflict_gh, store=conflict.store,
            request=_request(conflict, expected_digest=conflict_proof.new_evidence.digest),
        )
    assert conflict_gh.updated_bodies == []


@pytest.mark.parametrize(
    "mismatch",
    ["repo", "source_pr", "translation_pr", "root", "run", "old_digest", "head"],
)
def test_attestation_complete_identity_mismatch_is_rejected(
    tmp_path: Path, mismatch: str
) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    rebind_same_fragment_coverage_evidence(
        repo_path=str(fx.repo),
        github=FakeGitHub(
            body=render_authority_evidence(fx.provenance), head_sha=fx.k
        ),
        store=fx.store,
        request=_request(fx, expected_digest=proof.new_evidence.digest),
    )
    provenance = fx.provenance
    source_repo = REPO
    source_pr = SOURCE_PR
    translation_pr = TRANSLATION_PR
    candidate = fx.k
    if mismatch == "repo":
        source_repo = "other/repo"
    elif mismatch == "source_pr":
        source_pr += 1
    elif mismatch == "translation_pr":
        translation_pr += 1
    elif mismatch == "root":
        provenance = replace(provenance, candidate_sha=fx.h)
    elif mismatch == "run":
        provenance = replace(provenance, coverage_run_id="other-run")
    elif mismatch == "old_digest":
        provenance = replace(provenance, coverage_digest="0" * 64)
    else:
        candidate = fx.h

    with pytest.raises(ValueError, match="coverage rebind"):
        load_attested_coverage_evidence(
            repo_path=str(fx.repo),
            store=fx.store,
            provenance=provenance,
            source_repo=source_repo,
            source_pr=source_pr,
            translation_pr=translation_pr,
            new_candidate_sha=candidate,
        )


def _rewrite_attestation_field(
    data: bytes, field: str, value: object
) -> bytes:
    payload = json.loads(data)
    payload[field] = value
    core = {key: item for key, item in payload.items() if key != "self_digest"}
    payload["self_digest"] = hashlib.sha256(
        json.dumps(
            core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def test_valid_self_digest_never_substitutes_for_replayed_proof(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    rebind_same_fragment_coverage_evidence(
        repo_path=str(fx.repo),
        github=FakeGitHub(
            body=render_authority_evidence(fx.provenance), head_sha=fx.k
        ),
        store=fx.store,
        request=_request(fx, expected_digest=proof.new_evidence.digest),
    )
    key = _attestation_key(fx)
    data = fx.store.get(RUN_ID, key)
    assert data is not None
    attestation = decode_coverage_rebind_attestation(data)
    assert encode_coverage_rebind_attestation(attestation) == data
    tampered = _rewrite_attestation_field(data, "proof_audit_digest", "0" * 64)
    decode_coverage_rebind_attestation(tampered)
    fx.store.put(RUN_ID, key, tampered)

    with pytest.raises(ValueError, match="proof digest mismatch"):
        load_attested_coverage_evidence(
            repo_path=str(fx.repo), store=fx.store, provenance=fx.provenance,
            source_repo=REPO, source_pr=SOURCE_PR,
            translation_pr=TRANSLATION_PR, new_candidate_sha=fx.k,
        )


@pytest.mark.parametrize("corruption", ["partial", "unknown", "version", "rule"])
def test_malformed_or_unsupported_attestation_is_rejected(
    tmp_path: Path, corruption: str
) -> None:
    fx = _fixture(tmp_path)
    proof = derive_same_fragment_coverage_rebind(
        repo_path=str(fx.repo), provenance=fx.provenance,
        old_evidence=fx.evidence, new_candidate_sha=fx.k,
    )
    rebind_same_fragment_coverage_evidence(
        repo_path=str(fx.repo),
        github=FakeGitHub(
            body=render_authority_evidence(fx.provenance), head_sha=fx.k
        ),
        store=fx.store,
        request=_request(fx, expected_digest=proof.new_evidence.digest),
    )
    key = _attestation_key(fx)
    data = fx.store.get(RUN_ID, key)
    assert data is not None
    if corruption == "partial":
        bad = data[: len(data) // 2]
    elif corruption == "unknown":
        payload = json.loads(data)
        payload["extra"] = True
        bad = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    elif corruption == "version":
        bad = _rewrite_attestation_field(data, "version", 2)
    else:
        bad = _rewrite_attestation_field(data, "rule", "arbitrary_repair")
    fx.store.put(RUN_ID, key, bad)

    with pytest.raises(ValueError, match="attestation"):
        load_attested_coverage_evidence(
            repo_path=str(fx.repo), store=fx.store, provenance=fx.provenance,
            source_repo=REPO, source_pr=SOURCE_PR,
            translation_pr=TRANSLATION_PR, new_candidate_sha=fx.k,
        )
