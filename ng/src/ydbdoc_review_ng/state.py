from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

UTC = timezone.utc
MOSCOW = ZoneInfo("Europe/Moscow")
RETENTION = timedelta(days=14)
LEASE_TTL = timedelta(hours=2)
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TABLE_PREFIX_RE = re.compile(r"^m0_[a-z0-9_]{12,80}$")
TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
COMMANDS = {"DOC_TRANSLATE", "DOC_CONTINUE", "DOC_VERIFY"}
MODEL_ROLES = {"CLASSIFIER", "TRANSLATOR_A", "CRITIC_B", "REPAIR_B", "CRITIC_A", "REPAIR_A", "FINAL_CRITIC_B"}
PROVIDER_OUTCOMES = {"SUCCESS", "TIMEOUT", "PROVIDER_ERROR", "MALFORMED", "RECOVERED_SUCCESS"}
VERDICTS = {"PASS", "PASS_WITH_WARNINGS", "BLOCKED", "QUALITY_RED", "RED", "YELLOW", "GREEN"}


class StateError(RuntimeError):
    """Sanitized state error. Adapter exceptions never expose configuration."""


class ClaimStatus(str, Enum):
    CREATED = "CREATED"
    EXISTING_SAME = "EXISTING_SAME"
    WON = "WON"
    LOST = "LOST"
    CONFLICT = "CONFLICT"
    INCONCLUSIVE = "INCONCLUSIVE"


class ModelState(str, Enum):
    RESERVED = "RESERVED"
    RESULT_RECORDED = "RESULT_RECORDED"
    UNKNOWN_BILLED = "UNKNOWN_BILLED"
    RECONCILED_NOT_BILLED = "RECONCILED_NOT_BILLED"


class RowKind(str, Enum):
    RUN = "RUN"
    LOCK = "LOCK"
    CALL = "CALL"
    ROTATION = "ROTATION"


EFFECT_KINDS = {
    "CLOSE_OLD_DRAFT", "DELETE_OLD_BRANCH", "UPDATE_BRANCH",
    "CREATE_DRAFT", "PUSH_BRANCH", "POST_OR_UPDATE_COMMENT",
}
EFFECT_STATES = {"PLANNED", "INTENT_RECORDED", "CONFIRMED"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("clock must return an aware UTC timestamp")
    return value.astimezone(UTC)


def _nonce(value: str) -> str:
    if not NONCE_RE.fullmatch(value):
        raise ValueError("claimant nonce must be bounded opaque ASCII")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not TEXT_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _git_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not GIT_SHA_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _timestamp(value: object, field: str) -> str:
    _text(value, field)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"invalid {field}") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0) or not str(value).endswith("Z"):
        raise ValueError(f"invalid {field}")
    return str(value)


def _nonnegative(value: int | Decimal | None, field: str) -> None:
    if value is not None and (isinstance(value, bool) or value < 0):
        raise ValueError(f"invalid {field}")


def _closed_mapping(value: object, fields: set[str], field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"invalid {field}")
    return value


def new_claim_nonce() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class RepoIdentity:
    owner: str
    name: str

    def __post_init__(self) -> None:
        owner, name = self.owner.strip().lower(), self.name.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]+", owner) or not re.fullmatch(r"[a-z0-9_.-]+", name):
            raise ValueError("invalid repository identity")
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "name", name)

    @property
    def canonical(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class ClaimResult:
    status: ClaimStatus
    won: bool
    current_state: str
    current_owner: str | None = None
    mutation_nonce: str | None = None


@dataclass(frozen=True)
class MutationResult:
    changed: bool
    current_state: str


@dataclass(frozen=True)
class TransitionResult:
    changed: bool
    previous_state: str
    current_state: str
    reason: str = ""


@dataclass(frozen=True)
class CommandReceipt:
    receipt_identity: str
    github_run_id: str
    github_run_attempt: int
    github_event_name: str
    github_event_action: str
    label_timeline_event_id: int
    payload_sha256: str
    command: str
    actor: str
    source_pr: int

    def __post_init__(self) -> None:
        if self.github_run_attempt < 1 or self.label_timeline_event_id < 1:
            raise ValueError("invalid receipt identity")
        for field in ("receipt_identity", "github_run_id", "github_event_name", "github_event_action", "actor"):
            _text(getattr(self, field), field)
        if self.command not in COMMANDS:
            raise ValueError("invalid command")
        if not SHA_RE.fullmatch(self.payload_sha256) or self.source_pr < 1:
            raise ValueError("invalid command receipt")


@dataclass(frozen=True)
class LeaseOwner:
    owner_id: str
    mutation_nonce: str

    def __post_init__(self) -> None:
        if not self.owner_id:
            raise ValueError("owner is required")
        _nonce(self.mutation_nonce)


@dataclass(frozen=True)
class EffectCheckpoint:
    ordinal: int
    kind: str
    state: str
    target_identity: str
    payload_sha256: str
    intent_recorded_at: str | None = None
    confirmation_external_id: str | None = None
    confirmed_at: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("invalid effect ordinal")
        _text(self.target_identity, "effect target")
        _sha(self.payload_sha256, "effect digest")


def validate_effects(version: str, effects: Sequence[EffectCheckpoint]) -> None:
    if version != "command-effects/v1":
        raise ValueError("unknown effects schema")
    for expected, effect in enumerate(effects):
        if effect.ordinal != expected or effect.kind not in EFFECT_KINDS or effect.state not in EFFECT_STATES:
            raise ValueError("invalid ordered effect checkpoint")
        if not effect.target_identity or not SHA_RE.fullmatch(effect.payload_sha256):
            raise ValueError("invalid effect identity")
        prefixes = {
            "CLOSE_OLD_DRAFT": "pr:", "DELETE_OLD_BRANCH": "branch:", "UPDATE_BRANCH": "branch:",
            "CREATE_DRAFT": "draft:", "PUSH_BRANCH": "branch:", "POST_OR_UPDATE_COMMENT": "comment:",
        }
        if not effect.target_identity.startswith(prefixes[effect.kind]):
            raise ValueError("invalid effect target")
        if effect.state == "PLANNED" and any((effect.intent_recorded_at, effect.confirmation_external_id, effect.confirmed_at)):
            raise ValueError("planned effect has evidence")
        if effect.state == "INTENT_RECORDED" and (not effect.intent_recorded_at or effect.confirmed_at):
            raise ValueError("invalid intent evidence")
        if effect.state == "CONFIRMED" and not all((effect.intent_recorded_at, effect.confirmation_external_id, effect.confirmed_at)):
            raise ValueError("invalid confirmation evidence")
        if effect.intent_recorded_at is not None:
            _timestamp(effect.intent_recorded_at, "intent timestamp")
        if effect.confirmed_at is not None:
            _timestamp(effect.confirmed_at, "confirmation timestamp")
        if effect.confirmation_external_id is not None:
            _text(effect.confirmation_external_id, "confirmation external id")


def _validate_decision(value: object) -> None:
    if not isinstance(value, Mapping) or not set(value).issubset({"kind", "scope", "value"}) or "kind" not in value:
        raise ValueError("invalid typed decision")
    for key, item in value.items():
        _text(item, f"decision {key}")


@dataclass(frozen=True)
class NewLineage:
    lineage_id: str
    source_pr: int
    draft_pr: int | None
    branch: str
    state: str
    merge_commit_sha: str
    base_sha: str
    head_sha: str
    manifest_schema_version: str
    manifest_sha256: str
    manifest_payload: Mapping[str, object]
    main_commit_sha: str
    main_tree_sha: str
    decisions_schema_version: str
    typed_decisions: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        for field in ("lineage_id", "branch", "state"):
            _text(getattr(self, field), field)
        if self.source_pr < 1 or (self.draft_pr is not None and self.draft_pr < 1):
            raise ValueError("invalid lineage PR identity")
        for field in ("merge_commit_sha", "base_sha", "head_sha", "main_commit_sha", "main_tree_sha"):
            _git_sha(getattr(self, field), field)
        if self.manifest_schema_version != "manifest/v1" or self.decisions_schema_version != "decisions/v1":
            raise ValueError("invalid lineage schema version")
        _sha(self.manifest_sha256, "manifest digest")
        manifest = _closed_mapping(self.manifest_payload, {"files"}, "manifest")
        if not isinstance(manifest["files"], list):
            raise ValueError("invalid manifest files")
        for decision in self.typed_decisions:
            _validate_decision(decision)


@dataclass(frozen=True)
class AcceptedContinue:
    decision: Mapping[str, object]
    resulting_continue_count: int

    def __post_init__(self) -> None:
        if self.resulting_continue_count < 1 or self.resulting_continue_count > 3:
            raise ValueError("invalid accepted continue count")
        _validate_decision(self.decision)


@dataclass(frozen=True)
class LineageRecord:
    value: NewLineage
    continue_count: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ModelCallIdentity:
    idempotency_identity: str

    def __post_init__(self) -> None:
        _text(self.idempotency_identity, "model call identity")


@dataclass(frozen=True)
class ModelCallReservation:
    identity: ModelCallIdentity
    reservation_nonce: str
    lineage_id: str
    run_receipt_identity: str
    provider: str
    model: str
    role: str
    verification_pass: int
    attempt: int

    def __post_init__(self) -> None:
        _nonce(self.reservation_nonce)
        for field in ("lineage_id", "run_receipt_identity", "provider", "model"):
            _text(getattr(self, field), field)
        if self.role not in MODEL_ROLES or self.verification_pass < 0 or self.attempt < 1:
            raise ValueError("invalid model reservation")


@dataclass(frozen=True)
class RecordedModelResult:
    provider_outcome: str
    provider_request_id: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    actual_cost_rub: Decimal | None = None
    reconciliation_kind: str | None = None
    reconciliation_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.provider_outcome not in PROVIDER_OUTCOMES:
            raise ValueError("invalid provider outcome")
        if self.provider_request_id is not None:
            _text(self.provider_request_id, "provider request id")
        for field in ("input_tokens", "output_tokens", "total_tokens", "actual_cost_rub"):
            _nonnegative(getattr(self, field), field)
        known = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.total_tokens is not None and self.input_tokens is not None and self.output_tokens is not None and self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("inconsistent total tokens")
        if self.reconciliation_kind is not None or self.reconciliation_evidence_sha256 is not None:
            raise ValueError("reconciliation metadata belongs to reconciliation")


@dataclass(frozen=True)
class UnknownModelOutcome:
    provider_outcome: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.provider_outcome != "UNKNOWN":
            raise ValueError("invalid unknown model outcome")


@dataclass(frozen=True)
class ModelReconciliation:
    kind: str
    evidence_sha256: str
    result: RecordedModelResult | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"RESULT_RECORDED", "RECONCILED_NOT_BILLED"} or not SHA_RE.fullmatch(self.evidence_sha256):
            raise ValueError("invalid reconciliation")
        if (self.kind == "RESULT_RECORDED") != (self.result is not None):
            raise ValueError("reconciliation/result mismatch")


@dataclass(frozen=True)
class ModelCallRecord:
    reservation: ModelCallReservation
    state: ModelState
    reserved_at: datetime
    reservation_moscow_day: date
    finished_at: datetime | None = None
    finished_moscow_day: date | None = None
    result: RecordedModelResult | None = None
    reconciled_at: datetime | None = None
    reconciliation_kind: str | None = None
    reconciliation_evidence_sha256: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class RotationRecord:
    role: str
    cursor: int
    rotation_nonce: str
    updated_at: datetime


@dataclass(frozen=True)
class RotationClaim:
    role: str
    expected_cursor: int
    next_cursor: int
    rotation_nonce: str

    def __post_init__(self) -> None:
        _nonce(self.rotation_nonce)
        if self.role not in MODEL_ROLES:
            raise ValueError("invalid rotation role")
        if self.expected_cursor < 0 or self.next_cursor < 0:
            raise ValueError("invalid rotation cursor")


@dataclass(frozen=True)
class VerificationResult:
    verification_id: str
    case_id: str
    case_sha256: str
    lineage_id: str
    run_receipt_identity: str
    main_commit_sha: str
    main_tree_sha: str
    source_manifest_sha256: str
    source_content_sha256: str
    target_content_sha256: str | None
    candidate_content_sha256: str
    original_verdict: str
    critic_results: tuple[Mapping[str, object], ...]
    deterministic_findings: tuple[Mapping[str, object], ...]
    repair_evidence: tuple[Mapping[str, object], ...]
    interpreted_issues: tuple[Mapping[str, object], ...]
    final_verdict: str
    verification_schema_version: str = "verification-result/v1"

    def __post_init__(self) -> None:
        if self.verification_schema_version != "verification-result/v1" or len(self.repair_evidence) > 2:
            raise ValueError("invalid verification shape")
        for field in ("verification_id", "case_id", "lineage_id", "run_receipt_identity"):
            _text(getattr(self, field), field)
        for field in ("main_commit_sha", "main_tree_sha"):
            _git_sha(getattr(self, field), field)
        for field in ("case_sha256", "source_manifest_sha256", "source_content_sha256", "candidate_content_sha256"):
            _sha(getattr(self, field), field)
        if self.target_content_sha256 is not None:
            _sha(self.target_content_sha256, "target content digest")
        if self.original_verdict not in VERDICTS or self.final_verdict not in VERDICTS:
            raise ValueError("invalid verification verdict")
        required_critic = {"provider", "model", "role", "pass", "attempt", "call_identity", "provider_outcome", "result_sha256", "verdict"}
        for item in self.critic_results:
            critic = _closed_mapping(item, required_critic, "critic result")
            for field in ("provider", "model", "call_identity"):
                _text(critic[field], field)
            if critic["role"] not in MODEL_ROLES or critic["provider_outcome"] not in PROVIDER_OUTCOMES or critic["verdict"] not in VERDICTS:
                raise ValueError("invalid critic enum")
            if not isinstance(critic["pass"], int) or isinstance(critic["pass"], bool) or critic["pass"] < 1 or not isinstance(critic["attempt"], int) or isinstance(critic["attempt"], bool) or critic["attempt"] < 1:
                raise ValueError("invalid critic ordinal")
            _sha(critic["result_sha256"], "critic result digest")
        finding_fields = {"file", "line", "rule", "severity", "message", "evidence_sha256"}
        for item in self.deterministic_findings:
            finding = _closed_mapping(item, finding_fields, "deterministic finding")
            for field in ("file", "rule", "message"):
                _text(finding[field], field)
            if not isinstance(finding["line"], int) or isinstance(finding["line"], bool) or finding["line"] < 1 or finding["severity"] not in {"RED", "YELLOW"}:
                raise ValueError("invalid deterministic finding value")
            _sha(finding["evidence_sha256"], "finding evidence digest")
        repair_fields = {"provider", "model", "role", "attempt", "call_identity", "input_findings_sha256", "proposed_candidate_sha256", "outcome"}
        for item in self.repair_evidence:
            repair = _closed_mapping(item, repair_fields, "repair evidence")
            for field in ("provider", "model", "call_identity"):
                _text(repair[field], field)
            if repair["role"] not in MODEL_ROLES or repair["outcome"] not in PROVIDER_OUTCOMES or not isinstance(repair["attempt"], int) or isinstance(repair["attempt"], bool) or repair["attempt"] not in {1, 2}:
                raise ValueError("invalid repair evidence value")
            _sha(repair["input_findings_sha256"], "repair input digest")
            _sha(repair["proposed_candidate_sha256"], "repair candidate digest")
        issue_fields = {"file", "line", "severity", "message", "evidence_sha256"}
        for item in self.interpreted_issues:
            issue = _closed_mapping(item, issue_fields, "interpreted issue")
            for field in ("file", "message"):
                _text(issue[field], field)
            if not isinstance(issue["line"], int) or isinstance(issue["line"], bool) or issue["line"] < 1 or issue["severity"] not in {"RED", "YELLOW"}:
                raise ValueError("invalid interpreted issue value")
            _sha(issue["evidence_sha256"], "issue evidence digest")


@dataclass(frozen=True)
class VerificationRecord:
    value: VerificationResult
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class Clock(Protocol):
    def __call__(self) -> datetime: ...


class StatePort(Protocol):
    def receive_command(self, receipt: CommandReceipt) -> ClaimResult: ...
    def acquire_source_lease(self, source_pr: int, owner: LeaseOwner) -> ClaimResult: ...
    def release_source_lease(self, source_pr: int, owner: LeaseOwner) -> MutationResult: ...
    def create_lineage(self, lineage: NewLineage) -> ClaimResult: ...
    def get_lineage(self, lineage_id: str) -> LineageRecord | None: ...
    def record_accepted_continue(self, lineage_id: str, accepted: AcceptedContinue) -> MutationResult: ...
    def put_effect_checkpoints(self, receipt_identity: str, effects: tuple[EffectCheckpoint, ...]) -> ClaimResult: ...
    def get_effect_checkpoints(self, receipt_identity: str) -> tuple[EffectCheckpoint, ...] | None: ...
    def reserve_model_call(self, reservation: ModelCallReservation) -> ClaimResult: ...
    def record_model_result(self, identity: ModelCallIdentity, result: RecordedModelResult) -> TransitionResult: ...
    def mark_model_unknown(self, identity: ModelCallIdentity, unknown: UnknownModelOutcome) -> TransitionResult: ...
    def reconcile_model_call(self, identity: ModelCallIdentity, reconciliation: ModelReconciliation) -> TransitionResult: ...
    def get_model_call(self, identity: ModelCallIdentity) -> ModelCallRecord | None: ...
    def actual_spend_current_moscow_day(self) -> Decimal: ...
    def has_unknown_for_current_moscow_day(self) -> bool: ...
    def get_rotation(self, role: str) -> RotationRecord | None: ...
    def advance_rotation(self, claim: RotationClaim) -> ClaimResult: ...
    def put_verification_result(self, result: VerificationResult) -> ClaimResult: ...
    def get_verification_result(self, verification_id: str) -> VerificationRecord | None: ...


@dataclass
class _Run:
    receipt: CommandReceipt
    created_at: datetime
    expires_at: datetime
    effects: tuple[EffectCheckpoint, ...] | None = None


@dataclass
class _Lease:
    owner: LeaseOwner
    lease_until: datetime


def _same_model_reservation(left: ModelCallReservation, right: ModelCallReservation) -> bool:
    return (
        left.identity, left.lineage_id, left.run_receipt_identity, left.provider,
        left.model, left.role, left.verification_pass, left.attempt,
    ) == (
        right.identity, right.lineage_id, right.run_receipt_identity, right.provider,
        right.model, right.role, right.verification_pass, right.attempt,
    )


def _effects_json(effects: Sequence[EffectCheckpoint]) -> str:
    validate_effects("command-effects/v1", effects)
    return json.dumps([effect.__dict__ for effect in effects], sort_keys=True, separators=(",", ":"))


def _effects_from_json(value: object) -> tuple[EffectCheckpoint, ...]:
    decoded = _json_value(value)
    if not isinstance(decoded, list):
        raise ValueError("invalid stored effects")
    try:
        effects = tuple(EffectCheckpoint(**item) for item in decoded if isinstance(item, Mapping))
    except TypeError:
        raise ValueError("invalid stored effects") from None
    if len(effects) != len(decoded):
        raise ValueError("invalid stored effects")
    validate_effects("command-effects/v1", effects)
    return effects


def _valid_effect_transition(before: Sequence[EffectCheckpoint], after: Sequence[EffectCheckpoint]) -> bool:
    if len(before) != len(after):
        return False
    next_state = {"PLANNED": "INTENT_RECORDED", "INTENT_RECORDED": "CONFIRMED"}
    for old, new in zip(before, after, strict=True):
        if old == new:
            continue
        if (old.ordinal, old.kind, old.target_identity, old.payload_sha256) != (
            new.ordinal, new.kind, new.target_identity, new.payload_sha256,
        ):
            return False
        if next_state.get(old.state) != new.state:
            return False
    return True


class DryState(StatePort):
    """Thread-safe reference implementation used by the shared contract suite."""

    def __init__(self, repository: RepoIdentity, clock: Clock):
        self.repository = repository
        self._clock = clock
        self._lock = threading.RLock()
        self._runs: dict[str, _Run] = {}
        self._leases: dict[int, _Lease] = {}
        self._lineages: dict[str, LineageRecord] = {}
        self._calls: dict[str, ModelCallRecord] = {}
        self._rotations: dict[str, RotationRecord] = {}
        self._verifications: dict[str, VerificationRecord] = {}

    def _now(self) -> datetime:
        return _utc(self._clock())

    def receive_command(self, receipt: CommandReceipt) -> ClaimResult:
        with self._lock:
            existing = self._runs.get(receipt.receipt_identity)
            if existing and existing.expires_at <= self._now():
                del self._runs[receipt.receipt_identity]
                existing = None
            if existing:
                same = existing.receipt == receipt
                return ClaimResult(ClaimStatus.EXISTING_SAME if same else ClaimStatus.CONFLICT, False, "RECEIVED")
            now = self._now()
            self._runs[receipt.receipt_identity] = _Run(receipt, now, now + RETENTION)
            return ClaimResult(ClaimStatus.CREATED, True, "RECEIVED")

    def acquire_source_lease(self, source_pr: int, owner: LeaseOwner) -> ClaimResult:
        with self._lock:
            now = self._now()
            current = self._leases.get(source_pr)
            if current and current.lease_until > now:
                return ClaimResult(ClaimStatus.LOST, False, "LOCKED", current.owner.owner_id, current.owner.mutation_nonce)
            self._leases[source_pr] = _Lease(owner, now + LEASE_TTL)
            return ClaimResult(ClaimStatus.WON, True, "LOCKED", owner.owner_id, owner.mutation_nonce)

    def release_source_lease(self, source_pr: int, owner: LeaseOwner) -> MutationResult:
        with self._lock:
            current = self._leases.get(source_pr)
            if current and current.owner == owner:
                del self._leases[source_pr]
                return MutationResult(True, "RELEASED")
            return MutationResult(False, "LOCKED" if current else "ABSENT")

    def create_lineage(self, lineage: NewLineage) -> ClaimResult:
        with self._lock:
            existing = self._lineages.get(lineage.lineage_id)
            if existing:
                return ClaimResult(ClaimStatus.EXISTING_SAME if existing.value == lineage else ClaimStatus.CONFLICT, False, existing.value.state)
            now = self._now()
            self._lineages[lineage.lineage_id] = LineageRecord(copy.deepcopy(lineage), 0, now, now, now + RETENTION)
            return ClaimResult(ClaimStatus.CREATED, True, lineage.state)

    def get_lineage(self, lineage_id: str) -> LineageRecord | None:
        with self._lock:
            value = self._lineages.get(lineage_id)
            return copy.deepcopy(value) if value and value.expires_at > self._now() else None

    def record_accepted_continue(self, lineage_id: str, accepted: AcceptedContinue) -> MutationResult:
        with self._lock:
            current = self._lineages.get(lineage_id)
            if not current or current.expires_at <= self._now() or accepted.resulting_continue_count != current.continue_count + 1:
                return MutationResult(False, "ABSENT_OR_CONFLICT")
            now = self._now()
            decisions = (*current.value.typed_decisions, copy.deepcopy(accepted.decision))
            value = NewLineage(**{**current.value.__dict__, "typed_decisions": decisions})
            self._lineages[lineage_id] = LineageRecord(value, accepted.resulting_continue_count, current.created_at, now, now + RETENTION)
            return MutationResult(True, value.state)

    def put_effect_checkpoints(self, receipt_identity: str, effects: tuple[EffectCheckpoint, ...]) -> ClaimResult:
        validate_effects("command-effects/v1", effects)
        with self._lock:
            run = self._runs.get(receipt_identity)
            if not run or run.expires_at <= self._now():
                return ClaimResult(ClaimStatus.CONFLICT, False, "ABSENT")
            updating = run.effects is not None
            if updating:
                if run.effects == effects:
                    return ClaimResult(ClaimStatus.EXISTING_SAME, False, "EFFECTS_RECORDED")
                if not _valid_effect_transition(run.effects, effects):
                    return ClaimResult(ClaimStatus.CONFLICT, False, "EFFECTS_RECORDED")
            run.effects = copy.deepcopy(effects)
            return ClaimResult(ClaimStatus.WON if updating else ClaimStatus.CREATED, True, "EFFECTS_RECORDED")

    def get_effect_checkpoints(self, receipt_identity: str) -> tuple[EffectCheckpoint, ...] | None:
        with self._lock:
            run = self._runs.get(receipt_identity)
            if not run or run.expires_at <= self._now() or run.effects is None:
                return None
            validate_effects("command-effects/v1", run.effects)
            return copy.deepcopy(run.effects)

    def reserve_model_call(self, reservation: ModelCallReservation) -> ClaimResult:
        with self._lock:
            key = reservation.identity.idempotency_identity
            existing = self._calls.get(key)
            if existing:
                same = _same_model_reservation(existing.reservation, reservation)
                return ClaimResult(ClaimStatus.EXISTING_SAME if same else ClaimStatus.CONFLICT, False, existing.state.value, mutation_nonce=existing.reservation.reservation_nonce)
            now = self._now()
            self._calls[key] = ModelCallRecord(reservation, ModelState.RESERVED, now, now.astimezone(MOSCOW).date(), expires_at=now + RETENTION)
            return ClaimResult(ClaimStatus.CREATED, True, ModelState.RESERVED.value, mutation_nonce=reservation.reservation_nonce)

    def _transition(self, identity: ModelCallIdentity, allowed: set[ModelState], target: ModelState, result: RecordedModelResult | None = None, reconciliation: ModelReconciliation | None = None) -> TransitionResult:
        key = identity.idempotency_identity
        current = self._calls.get(key)
        if not current:
            return TransitionResult(False, "ABSENT", "ABSENT", "missing")
        if current.state not in allowed:
            return TransitionResult(False, current.state.value, current.state.value, "invalid_transition")
        now = self._now()
        finished = now if target == ModelState.RESULT_RECORDED else None
        finished_day = now.astimezone(MOSCOW).date() if finished else None
        self._calls[key] = ModelCallRecord(
            current.reservation, target, current.reserved_at, current.reservation_moscow_day,
            finished, finished_day, copy.deepcopy(result), now if reconciliation else None,
            reconciliation.kind if reconciliation else None,
            reconciliation.evidence_sha256 if reconciliation else None,
            current.expires_at,
        )
        return TransitionResult(True, current.state.value, target.value)

    def record_model_result(self, identity: ModelCallIdentity, result: RecordedModelResult) -> TransitionResult:
        with self._lock:
            return self._transition(identity, {ModelState.RESERVED}, ModelState.RESULT_RECORDED, result)

    def mark_model_unknown(self, identity: ModelCallIdentity, unknown: UnknownModelOutcome) -> TransitionResult:
        with self._lock:
            return self._transition(identity, {ModelState.RESERVED}, ModelState.UNKNOWN_BILLED)

    def reconcile_model_call(self, identity: ModelCallIdentity, reconciliation: ModelReconciliation) -> TransitionResult:
        with self._lock:
            target = ModelState.RESULT_RECORDED if reconciliation.kind == "RESULT_RECORDED" else ModelState.RECONCILED_NOT_BILLED
            return self._transition(identity, {ModelState.UNKNOWN_BILLED}, target, reconciliation.result, reconciliation)

    def get_model_call(self, identity: ModelCallIdentity) -> ModelCallRecord | None:
        with self._lock:
            value = self._calls.get(identity.idempotency_identity)
            return copy.deepcopy(value) if value and value.expires_at and value.expires_at > self._now() else None

    def actual_spend_current_moscow_day(self) -> Decimal:
        with self._lock:
            day = self._now().astimezone(MOSCOW).date()
            return sum((call.result.actual_cost_rub for call in self._calls.values() if call.state == ModelState.RESULT_RECORDED and call.finished_moscow_day == day and call.result and call.result.actual_cost_rub is not None), Decimal("0"))

    def has_unknown_for_current_moscow_day(self) -> bool:
        with self._lock:
            day = self._now().astimezone(MOSCOW).date()
            return any(call.state == ModelState.UNKNOWN_BILLED and call.reservation_moscow_day == day for call in self._calls.values())

    def get_rotation(self, role: str) -> RotationRecord | None:
        with self._lock:
            return copy.deepcopy(self._rotations.get(role))

    def advance_rotation(self, claim: RotationClaim) -> ClaimResult:
        with self._lock:
            current = self._rotations.get(claim.role)
            cursor = current.cursor if current else 0
            if current and cursor == claim.next_cursor and current.rotation_nonce == claim.rotation_nonce:
                return ClaimResult(ClaimStatus.EXISTING_SAME, True, str(cursor), mutation_nonce=current.rotation_nonce)
            if cursor != claim.expected_cursor:
                return ClaimResult(ClaimStatus.LOST, False, str(cursor), mutation_nonce=current.rotation_nonce if current else None)
            now = self._now()
            self._rotations[claim.role] = RotationRecord(claim.role, claim.next_cursor, claim.rotation_nonce, now)
            return ClaimResult(ClaimStatus.WON, True, str(claim.next_cursor), mutation_nonce=claim.rotation_nonce)

    def put_verification_result(self, result: VerificationResult) -> ClaimResult:
        with self._lock:
            existing = self._verifications.get(result.verification_id)
            if existing:
                return ClaimResult(ClaimStatus.EXISTING_SAME if existing.value == result else ClaimStatus.CONFLICT, False, existing.value.final_verdict)
            now = self._now()
            self._verifications[result.verification_id] = VerificationRecord(copy.deepcopy(result), now, now, now + RETENTION)
            return ClaimResult(ClaimStatus.CREATED, True, result.final_verdict)

    def get_verification_result(self, verification_id: str) -> VerificationRecord | None:
        with self._lock:
            value = self._verifications.get(verification_id)
            return copy.deepcopy(value) if value and value.expires_at > self._now() else None


@dataclass(frozen=True)
class YdbConfig:
    endpoint: str
    database: str
    sa_key_file: str

    def __post_init__(self) -> None:
        for field in ("endpoint", "database", "sa_key_file"):
            _text(getattr(self, field), field)
        if not self.database.startswith("/"):
            raise ValueError("invalid YDB database")


@dataclass(frozen=True)
class RealYdbTestConfig(YdbConfig):
    table_prefix: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not TABLE_PREFIX_RE.fullmatch(self.table_prefix):
            raise ValueError("invalid acceptance table prefix")


def real_ydb_test_config_from_env(environment: Mapping[str, str]) -> RealYdbTestConfig | None:
    names = ("YDBDOC_YDB_ENDPOINT", "YDBDOC_YDB_DATABASE", "YDBDOC_YDB_SA_KEY_FILE", "YDBDOC_REAL_YDB_TABLE_PREFIX")
    values = tuple(environment.get(name, "") for name in names)
    if not any(values):
        return None
    if not all(values):
        raise ValueError("incomplete real YDB acceptance configuration")
    return RealYdbTestConfig(*values)


class YdbState(StatePort):
    """YDB adapter using only serializable read/write SDK transactions."""

    TABLES = ("command_runs", "lineages", "model_calls", "verification_results")

    def __init__(self, config: YdbConfig | RealYdbTestConfig, repository: RepoIdentity):
        self.config, self.repository = config, repository
        self._schema_initialized = False
        try:
            import ydb
        except Exception:
            raise StateError("YDB_INIT_IMPORT") from None
        try:
            credentials = ydb.iam.ServiceAccountCredentials.from_file(config.sa_key_file)
        except Exception:
            raise StateError("YDB_INIT_CREDENTIALS") from None
        try:
            driver = ydb.Driver(endpoint=config.endpoint, database=config.database, credentials=credentials)
        except Exception:
            raise StateError("YDB_INIT_DRIVER") from None
        try:
            driver.wait(timeout=15, fail_fast=True)
        except Exception:
            try:
                driver.stop(timeout=3)
            except Exception:
                pass
            raise StateError("YDB_INIT_WAIT") from None
        try:
            pool = ydb.SessionPool(driver)
        except Exception:
            try:
                driver.stop(timeout=3)
            except Exception:
                pass
            raise StateError("YDB_INIT_POOL") from None
        self.driver, self.pool = driver, pool

    def _table(self, name: str) -> str:
        prefix = self.config.table_prefix if isinstance(self.config, RealYdbTestConfig) else ""
        return f"{prefix}_{name}" if prefix else name

    def ensure_schema(self) -> None:
        if self._schema_initialized:
            return
        # Exact columns are asserted by tests; production creates only these four tables.
        prefix = self.config.table_prefix if isinstance(self.config, RealYdbTestConfig) else ""
        statements = schema_statements(prefix)
        try:
            for statement in statements:
                self.pool.retry_operation_sync(lambda session, sql=statement: session.execute_scheme(sql))
            self._schema_initialized = True
        except Exception:
            raise StateError("YDB schema initialization failed") from None

    def teardown_test_schema(self, maximum_rows: int = 1000) -> None:
        if not isinstance(self.config, RealYdbTestConfig) or not TABLE_PREFIX_RE.fullmatch(self.config.table_prefix):
            raise StateError("acceptance cleanup scope is invalid")
        if isinstance(maximum_rows, bool) or maximum_rows < 0:
            raise StateError("acceptance cleanup bound is invalid")
        tables = tuple(self._table(name) for name in self.TABLES)
        def count(tx):
            total = 0
            for table in tables:
                result = tx.execute(f"SELECT COUNT(*) AS n FROM `{table}`;")
                total += int(result[0].rows[0]["n"])
            tx.commit()
            return total
        if self._serializable(count) > maximum_rows:
            raise StateError("acceptance cleanup bound exceeded")
        try:
            for table in tables:
                self.pool.retry_operation_sync(lambda session, name=table: session.execute_scheme(f"DROP TABLE `{name}`;"))
        except Exception:
            raise StateError("acceptance cleanup failed") from None

    def _serializable(self, operation):
        import ydb
        def closure(session):
            tx = session.transaction(ydb.SerializableReadWrite())
            return operation(tx)
        try:
            return self.pool.retry_operation_sync(closure)
        except Exception:
            raise StateError("YDB serializable transaction failed") from None

    # The adapter methods below intentionally use SELECT, conditional DML and a
    # final SELECT in one transaction. No external effect is invoked by closure.
    def receive_command(self, receipt: CommandReceipt) -> ClaimResult:
        table = self._table("command_runs")
        key = f"run:{receipt.receipt_identity}"
        def op(tx):
            params = {"$repo": self.repository.canonical, "$key": key}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT payload_sha256,receipt_identity,github_run_id,github_run_attempt,github_event_name,github_event_action,label_timeline_event_id,command,actor,source_pr,expires_at>CurrentUtcTimestamp() AS alive FROM `{table}` WHERE repository=$repo AND record_key=$key AND row_kind='RUN';", params)
            rows = before[0].rows if before else []
            if rows and not rows[0]["alive"]:
                tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DELETE FROM `{table}` WHERE repository=$repo AND record_key=$key AND row_kind='RUN' AND expires_at<=CurrentUtcTimestamp();", params)
                rows = []
            if rows:
                same = (
                    rows[0]["payload_sha256"], rows[0]["receipt_identity"], rows[0]["github_run_id"],
                    rows[0]["github_run_attempt"], rows[0]["github_event_name"], rows[0]["github_event_action"],
                    rows[0]["label_timeline_event_id"], rows[0]["command"], rows[0]["actor"], rows[0]["source_pr"],
                ) == (
                    receipt.payload_sha256, receipt.receipt_identity, receipt.github_run_id,
                    receipt.github_run_attempt, receipt.github_event_name, receipt.github_event_action,
                    receipt.label_timeline_event_id, receipt.command, receipt.actor, receipt.source_pr,
                )
                tx.commit()
                return ClaimResult(ClaimStatus.EXISTING_SAME if same else ClaimStatus.CONFLICT, False, "RECEIVED")
            insert = {**params, "$receipt": receipt.receipt_identity, "$payload": receipt.payload_sha256, "$run": receipt.github_run_id, "$attempt": receipt.github_run_attempt, "$event": receipt.github_event_name, "$action": receipt.github_event_action, "$timeline": receipt.label_timeline_event_id, "$command": receipt.command, "$actor": receipt.actor, "$pr": receipt.source_pr}
            tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $receipt AS Utf8; DECLARE $payload AS Utf8; DECLARE $run AS Utf8; DECLARE $attempt AS Uint32; DECLARE $event AS Utf8; DECLARE $action AS Utf8; DECLARE $timeline AS Uint64; DECLARE $command AS Utf8; DECLARE $actor AS Utf8; DECLARE $pr AS Uint64; $now=CurrentUtcTimestamp(); INSERT INTO `{table}` (repository,record_key,row_kind,receipt_identity,github_run_id,github_run_attempt,github_event_name,github_event_action,label_timeline_event_id,payload_sha256,command,actor,source_pr,phase,created_at,updated_at,expires_at) VALUES ($repo,$key,'RUN',$receipt,$run,$attempt,$event,$action,$timeline,$payload,$command,$actor,$pr,'RECEIVED',$now,$now,$now+Interval('P14D'));", insert)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT receipt_identity FROM `{table}` WHERE repository=$repo AND record_key=$key;", params, commit_tx=True)
            won = bool(actual and actual[0].rows and actual[0].rows[0]["receipt_identity"] == receipt.receipt_identity)
            return ClaimResult(ClaimStatus.CREATED if won else ClaimStatus.INCONCLUSIVE, won, "RECEIVED")
        try:
            return self._serializable(op)
        except StateError:
            return self._reconcile_receipt_once(receipt)

    def _reconcile_receipt_once(self, receipt: CommandReceipt) -> ClaimResult:
        table, key = self._table("command_runs"), f"run:{receipt.receipt_identity}"
        def read(session):
            result = session.transaction().execute(
                f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT payload_sha256,receipt_identity,github_run_id,github_run_attempt,github_event_name,github_event_action,label_timeline_event_id,command,actor,source_pr,phase FROM `{table}` WHERE repository=$repo AND record_key=$key AND row_kind='RUN';",
                {"$repo": self.repository.canonical, "$key": key}, commit_tx=True,
            )
            return result[0].rows[0] if result and result[0].rows else None
        try:
            row = self.pool.retry_operation_sync(read)
        except Exception:
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "UNKNOWN")
        if not row:
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "ABSENT")
        same = (
            row.get("payload_sha256"), row.get("receipt_identity"), row.get("github_run_id"),
            row.get("github_run_attempt"), row.get("github_event_name"), row.get("github_event_action"),
            row.get("label_timeline_event_id"), row.get("command"), row.get("actor"), row.get("source_pr"),
        ) == (
            receipt.payload_sha256, receipt.receipt_identity, receipt.github_run_id,
            receipt.github_run_attempt, receipt.github_event_name, receipt.github_event_action,
            receipt.label_timeline_event_id, receipt.command, receipt.actor, receipt.source_pr,
        )
        return ClaimResult(ClaimStatus.EXISTING_SAME if same else ClaimStatus.CONFLICT, False, str(row.get("phase", "RECEIVED")))

    def _reconcile_model_reservation_once(self, reservation: ModelCallReservation) -> ClaimResult:
        """One bounded read after an ambiguous transaction outcome; never dispatches."""
        table, key_value = self._table("model_calls"), f"call:{reservation.identity.idempotency_identity}"
        def read(session):
            result = session.transaction().execute(
                f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT state,reservation_nonce,idempotency_identity,lineage_id,run_receipt_identity,provider,model,role,verification_pass,attempt FROM `{table}` WHERE repository=$repo AND record_key=$key;",
                {"$repo": self.repository.canonical, "$key": key_value}, commit_tx=True,
            )
            return result[0].rows[0] if result and result[0].rows else None
        try:
            row = self.pool.retry_operation_sync(read)
        except Exception:
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "UNKNOWN")
        if not row:
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "ABSENT")
        try:
            stored = ModelCallReservation(ModelCallIdentity(row["idempotency_identity"]), row["reservation_nonce"], row["lineage_id"], row["run_receipt_identity"], row["provider"], row["model"], row["role"], row["verification_pass"], row["attempt"])
        except (KeyError, TypeError, ValueError):
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "INVALID")
        if not _same_model_reservation(stored, reservation):
            return ClaimResult(ClaimStatus.CONFLICT, False, str(row["state"]), mutation_nonce=stored.reservation_nonce)
        if stored.reservation_nonce == reservation.reservation_nonce and row["state"] == ModelState.RESERVED.value:
            return ClaimResult(ClaimStatus.CREATED, True, str(row["state"]), mutation_nonce=stored.reservation_nonce)
        return ClaimResult(ClaimStatus.EXISTING_SAME, False, str(row["state"]), mutation_nonce=stored.reservation_nonce)

    def acquire_source_lease(self, source_pr: int, owner: LeaseOwner) -> ClaimResult:
        return self._lease_tx(source_pr, owner, release=False)

    def release_source_lease(self, source_pr: int, owner: LeaseOwner) -> MutationResult:
        result = self._lease_tx(source_pr, owner, release=True)
        return MutationResult(result.won, result.current_state)

    def _lease_tx(self, source_pr: int, owner: LeaseOwner, release: bool) -> ClaimResult:
        table, key = self._table("command_runs"), f"lock:{self.repository.canonical}#{source_pr}"
        def op(tx):
            params = {"$repo": self.repository.canonical, "$key": key, "$owner": owner.owner_id, "$nonce": owner.mutation_nonce}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT lock_owner,mutation_nonce,lease_until,lease_until<=CurrentUtcTimestamp() AS expired FROM `{table}` WHERE repository=$repo AND record_key=$key;", {"$repo": self.repository.canonical, "$key": key})
            rows = before[0].rows if before else []
            if release:
                owned = bool(rows and rows[0]["lock_owner"] == owner.owner_id and rows[0]["mutation_nonce"] == owner.mutation_nonce)
                tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $owner AS Utf8; DECLARE $nonce AS Utf8; DELETE FROM `{table}` WHERE repository=$repo AND record_key=$key AND lock_owner=$owner AND mutation_nonce=$nonce;", params)
                actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT lock_owner,mutation_nonce FROM `{table}` WHERE repository=$repo AND record_key=$key;", {"$repo": self.repository.canonical, "$key": key}, commit_tx=True)
                changed = owned and (not actual or not actual[0].rows)
                return ClaimResult(ClaimStatus.WON if changed else ClaimStatus.LOST, changed, "RELEASED" if changed else "LOCKED")
            if rows and not rows[0]["expired"]:
                tx.commit()
                return ClaimResult(ClaimStatus.LOST, False, "LOCKED", rows[0]["lock_owner"], rows[0]["mutation_nonce"])
            tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $owner AS Utf8; DECLARE $nonce AS Utf8; $now=CurrentUtcTimestamp(); UPSERT INTO `{table}` (repository,record_key,row_kind,source_pr,phase,lock_owner,mutation_nonce,lease_until,created_at,updated_at) VALUES ($repo,$key,'LOCK',{source_pr}u,'LOCKED',$owner,$nonce,$now+Interval('PT2H'),$now,$now);", params)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT lock_owner,mutation_nonce FROM `{table}` WHERE repository=$repo AND record_key=$key;", {"$repo": self.repository.canonical, "$key": key}, commit_tx=True)
            won = bool(actual and actual[0].rows and actual[0].rows[0]["mutation_nonce"] == owner.mutation_nonce)
            return ClaimResult(ClaimStatus.WON if won else ClaimStatus.LOST, won, "LOCKED", owner.owner_id if won else None, owner.mutation_nonce if won else None)
        try:
            return self._serializable(op)
        except StateError:
            return self._reconcile_lease_once(source_pr, owner, release)

    def _reconcile_lease_once(self, source_pr: int, owner: LeaseOwner, release: bool) -> ClaimResult:
        table, key = self._table("command_runs"), f"lock:{self.repository.canonical}#{source_pr}"
        def read(session):
            result = session.transaction().execute(
                f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT lock_owner,mutation_nonce FROM `{table}` WHERE repository=$repo AND record_key=$key AND row_kind='LOCK';",
                {"$repo": self.repository.canonical, "$key": key}, commit_tx=True,
            )
            return result[0].rows[0] if result and result[0].rows else None
        try:
            row = self.pool.retry_operation_sync(read)
        except Exception:
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "UNKNOWN")
        if release:
            if not row:
                return ClaimResult(ClaimStatus.WON, True, "RELEASED")
            return ClaimResult(ClaimStatus.LOST, False, "LOCKED", row.get("lock_owner"), row.get("mutation_nonce"))
        if not row:
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "ABSENT")
        exact = row.get("lock_owner") == owner.owner_id and row.get("mutation_nonce") == owner.mutation_nonce
        return ClaimResult(ClaimStatus.WON if exact else ClaimStatus.LOST, exact, "LOCKED", row.get("lock_owner"), row.get("mutation_nonce"))

    def create_lineage(self, lineage: NewLineage) -> ClaimResult:
        table = self._table("lineages")
        manifest = json.dumps(lineage.manifest_payload, sort_keys=True, separators=(",", ":"))
        decisions = json.dumps(lineage.typed_decisions, sort_keys=True, separators=(",", ":"))
        def op(tx):
            key = {"$repo": self.repository.canonical, "$id": lineage.lineage_id}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; SELECT * FROM `{table}` WHERE repository=$repo AND lineage_id=$id;", key)
            rows = before[0].rows if before else []
            if rows:
                tx.commit()
                same = _lineage_from_row(rows[0]).value == lineage
                return ClaimResult(ClaimStatus.EXISTING_SAME if same else ClaimStatus.CONFLICT, False, rows[0]["state"])
            params = {**key, "$pr": lineage.source_pr, "$draft": lineage.draft_pr, "$branch": lineage.branch, "$state": lineage.state, "$merge": lineage.merge_commit_sha, "$base": lineage.base_sha, "$head": lineage.head_sha, "$msv": lineage.manifest_schema_version, "$msha": lineage.manifest_sha256, "$manifest": manifest, "$main": lineage.main_commit_sha, "$tree": lineage.main_tree_sha, "$dsv": lineage.decisions_schema_version, "$decisions": decisions}
            tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; DECLARE $pr AS Uint64; DECLARE $draft AS Uint64?; DECLARE $branch AS Utf8; DECLARE $state AS Utf8; DECLARE $merge AS Utf8; DECLARE $base AS Utf8; DECLARE $head AS Utf8; DECLARE $msv AS Utf8; DECLARE $msha AS Utf8; DECLARE $manifest AS Json; DECLARE $main AS Utf8; DECLARE $tree AS Utf8; DECLARE $dsv AS Utf8; DECLARE $decisions AS Json; $now=CurrentUtcTimestamp(); INSERT INTO `{table}` (repository,lineage_id,source_pr,draft_pr,branch,state,merge_commit_sha,base_sha,head_sha,manifest_schema_version,manifest_sha256,manifest_payload,main_commit_sha,main_tree_sha,snapshot_captured_at,decisions_schema_version,typed_decisions,continue_count,created_at,updated_at,expires_at) VALUES ($repo,$id,$pr,$draft,$branch,$state,$merge,$base,$head,$msv,$msha,$manifest,$main,$tree,$now,$dsv,$decisions,0u,$now,$now,$now+Interval('P14D'));", params)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; SELECT lineage_id FROM `{table}` WHERE repository=$repo AND lineage_id=$id;", key, commit_tx=True)
            won = bool(actual and actual[0].rows)
            return ClaimResult(ClaimStatus.CREATED if won else ClaimStatus.INCONCLUSIVE, won, lineage.state)
        return self._serializable(op)

    def get_lineage(self, lineage_id: str) -> LineageRecord | None:
        table = self._table("lineages")
        def op(tx):
            result = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; SELECT * FROM `{table}` WHERE repository=$repo AND lineage_id=$id AND expires_at>CurrentUtcTimestamp();", {"$repo": self.repository.canonical, "$id": lineage_id}, commit_tx=True)
            return result[0].rows[0] if result and result[0].rows else None
        row = self._serializable(op)
        if not row: return None
        return _lineage_from_row(row)

    def record_accepted_continue(self, lineage_id: str, accepted: AcceptedContinue) -> MutationResult:
        table = self._table("lineages")
        def op(tx):
            key = {"$repo": self.repository.canonical, "$id": lineage_id}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; SELECT typed_decisions,continue_count,state,expires_at>CurrentUtcTimestamp() AS alive FROM `{table}` WHERE repository=$repo AND lineage_id=$id;", key)
            rows = before[0].rows if before else []
            if not rows or not rows[0]["alive"] or rows[0]["continue_count"] + 1 != accepted.resulting_continue_count:
                tx.commit(); return MutationResult(False, "ABSENT_OR_CONFLICT")
            existing_decisions = _json_value(rows[0]["typed_decisions"])
            if not isinstance(existing_decisions, list):
                raise ValueError("invalid stored decisions")
            for decision in existing_decisions:
                _validate_decision(decision)
            decisions = [*existing_decisions, accepted.decision]
            params = {**key, "$count": accepted.resulting_continue_count, "$decisions": json.dumps(decisions, sort_keys=True, separators=(",", ":"))}
            tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; DECLARE $count AS Uint32; DECLARE $decisions AS Json; $now=CurrentUtcTimestamp(); UPDATE `{table}` SET typed_decisions=$decisions,continue_count=$count,updated_at=$now,expires_at=$now+Interval('P14D') WHERE repository=$repo AND lineage_id=$id;", params)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; SELECT continue_count,state FROM `{table}` WHERE repository=$repo AND lineage_id=$id;", key, commit_tx=True)
            changed = bool(actual and actual[0].rows and actual[0].rows[0]["continue_count"] == accepted.resulting_continue_count)
            return MutationResult(changed, actual[0].rows[0]["state"] if changed else "CONFLICT")
        return self._serializable(op)

    def put_effect_checkpoints(self, receipt_identity: str, effects: tuple[EffectCheckpoint, ...]) -> ClaimResult:
        payload = _effects_json(effects)
        table, key_value = self._table("command_runs"), f"run:{receipt_identity}"
        def op(tx):
            key = {"$repo": self.repository.canonical, "$key": key_value}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT effects_schema_version,effect_checkpoints,expires_at>CurrentUtcTimestamp() AS alive FROM `{table}` WHERE repository=$repo AND record_key=$key AND row_kind='RUN';", key)
            rows = before[0].rows if before else []
            if not rows or not rows[0]["alive"]:
                tx.commit(); return ClaimResult(ClaimStatus.CONFLICT, False, "ABSENT")
            if rows[0].get("effects_schema_version") is not None:
                if rows[0]["effects_schema_version"] != "command-effects/v1":
                    tx.commit(); return ClaimResult(ClaimStatus.CONFLICT, False, "EFFECTS_RECORDED")
                previous = _effects_from_json(rows[0]["effect_checkpoints"])
                if previous == effects:
                    tx.commit(); return ClaimResult(ClaimStatus.EXISTING_SAME, False, "EFFECTS_RECORDED")
                if not _valid_effect_transition(previous, effects):
                    tx.commit(); return ClaimResult(ClaimStatus.CONFLICT, False, "EFFECTS_RECORDED")
                params = {**key, "$payload": payload}
                tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $payload AS Json; UPDATE `{table}` SET effect_checkpoints=$payload,updated_at=CurrentUtcTimestamp() WHERE repository=$repo AND record_key=$key AND effects_schema_version='command-effects/v1';", params)
                actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT effect_checkpoints FROM `{table}` WHERE repository=$repo AND record_key=$key;", key, commit_tx=True)
                won = bool(actual and actual[0].rows and _effects_from_json(actual[0].rows[0]["effect_checkpoints"]) == effects)
                return ClaimResult(ClaimStatus.WON if won else ClaimStatus.INCONCLUSIVE, won, "EFFECTS_RECORDED")
            params = {**key, "$version": "command-effects/v1", "$payload": payload}
            tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $version AS Utf8; DECLARE $payload AS Json; UPDATE `{table}` SET effects_schema_version=$version,effect_checkpoints=$payload,updated_at=CurrentUtcTimestamp() WHERE repository=$repo AND record_key=$key AND effects_schema_version IS NULL;", params)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT effects_schema_version,effect_checkpoints FROM `{table}` WHERE repository=$repo AND record_key=$key;", key, commit_tx=True)
            won = bool(actual and actual[0].rows and actual[0].rows[0]["effects_schema_version"] == "command-effects/v1" and _effects_from_json(actual[0].rows[0]["effect_checkpoints"]) == effects)
            return ClaimResult(ClaimStatus.CREATED if won else ClaimStatus.INCONCLUSIVE, won, "EFFECTS_RECORDED")
        return self._serializable(op)

    def get_effect_checkpoints(self, receipt_identity: str) -> tuple[EffectCheckpoint, ...] | None:
        table, key_value = self._table("command_runs"), f"run:{receipt_identity}"
        def op(tx):
            result = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT effects_schema_version,effect_checkpoints FROM `{table}` WHERE repository=$repo AND record_key=$key AND row_kind='RUN' AND expires_at>CurrentUtcTimestamp();", {"$repo": self.repository.canonical, "$key": key_value}, commit_tx=True)
            return result[0].rows[0] if result and result[0].rows else None
        row = self._serializable(op)
        if not row or row.get("effects_schema_version") is None:
            return None
        if row["effects_schema_version"] != "command-effects/v1":
            raise StateError("stored effect checkpoints are invalid")
        try:
            return _effects_from_json(row["effect_checkpoints"])
        except ValueError:
            raise StateError("stored effect checkpoints are invalid") from None

    def reserve_model_call(self, reservation: ModelCallReservation) -> ClaimResult:
        table, key_value = self._table("model_calls"), f"call:{reservation.identity.idempotency_identity}"
        def op(tx):
            key = {"$repo": self.repository.canonical, "$key": key_value}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT state,reservation_nonce,idempotency_identity,lineage_id,run_receipt_identity,provider,model,role,verification_pass,attempt FROM `{table}` WHERE repository=$repo AND record_key=$key;", key)
            rows = before[0].rows if before else []
            if rows:
                tx.commit()
                stored = ModelCallReservation(ModelCallIdentity(rows[0]["idempotency_identity"]), rows[0]["reservation_nonce"], rows[0]["lineage_id"], rows[0]["run_receipt_identity"], rows[0]["provider"], rows[0]["model"], rows[0]["role"], rows[0]["verification_pass"], rows[0]["attempt"])
                same = _same_model_reservation(stored, reservation)
                return ClaimResult(ClaimStatus.EXISTING_SAME if same else ClaimStatus.CONFLICT, False, rows[0]["state"], mutation_nonce=rows[0]["reservation_nonce"])
            params = {**key, "$identity": reservation.identity.idempotency_identity, "$nonce": reservation.reservation_nonce, "$lineage": reservation.lineage_id, "$run": reservation.run_receipt_identity, "$provider": reservation.provider, "$model": reservation.model, "$role": reservation.role, "$pass": reservation.verification_pass, "$attempt": reservation.attempt}
            # Moscow day is derived from the same server timestamp by YDB DateTime UDF.
            tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $identity AS Utf8; DECLARE $nonce AS Utf8; DECLARE $lineage AS Utf8; DECLARE $run AS Utf8; DECLARE $provider AS Utf8; DECLARE $model AS Utf8; DECLARE $role AS Utf8; DECLARE $pass AS Uint32; DECLARE $attempt AS Uint32; $now=CurrentUtcTimestamp(); $day=DateTime::MakeDate(DateTime::Split(AddTimezone($now,'Europe/Moscow'))); INSERT INTO `{table}` (repository,record_key,row_kind,idempotency_identity,lineage_id,run_receipt_identity,provider,model,role,verification_pass,attempt,state,reserved_at,reservation_moscow_day,reservation_nonce,expires_at) VALUES ($repo,$key,'CALL',$identity,$lineage,$run,$provider,$model,$role,$pass,$attempt,'RESERVED',$now,$day,$nonce,$now+Interval('P14D'));", params)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT reservation_nonce FROM `{table}` WHERE repository=$repo AND record_key=$key;", key, commit_tx=True)
            won = bool(actual and actual[0].rows and actual[0].rows[0]["reservation_nonce"] == reservation.reservation_nonce)
            return ClaimResult(ClaimStatus.CREATED if won else ClaimStatus.INCONCLUSIVE, won, ModelState.RESERVED.value, mutation_nonce=reservation.reservation_nonce if won else None)
        try:
            return self._serializable(op)
        except StateError:
            return self._reconcile_model_reservation_once(reservation)

    def _model_transition(self, identity: ModelCallIdentity, allowed: str, target: ModelState, result: RecordedModelResult | None, reconciliation: ModelReconciliation | None) -> TransitionResult:
        table, key_value = self._table("model_calls"), f"call:{identity.idempotency_identity}"
        def op(tx):
            key = {"$repo": self.repository.canonical, "$key": key_value}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT state FROM `{table}` WHERE repository=$repo AND record_key=$key;", key)
            rows = before[0].rows if before else []
            previous = rows[0]["state"] if rows else "ABSENT"
            if previous != allowed:
                tx.commit(); return TransitionResult(False, previous, previous, "invalid_transition")
            params = {**key, "$outcome": result.provider_outcome if result else "UNKNOWN", "$request": result.provider_request_id if result else None, "$input": result.input_tokens if result else None, "$output": result.output_tokens if result else None, "$total": result.total_tokens if result else None, "$cost": result.actual_cost_rub if result else None, "$rkind": reconciliation.kind if reconciliation else None, "$evidence": reconciliation.evidence_sha256 if reconciliation else None}
            if target == ModelState.RESULT_RECORDED:
                tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $outcome AS Utf8; DECLARE $request AS Utf8?; DECLARE $input AS Uint64?; DECLARE $output AS Uint64?; DECLARE $total AS Uint64?; DECLARE $cost AS Decimal(22,9)?; DECLARE $rkind AS Utf8?; DECLARE $evidence AS Utf8?; $now=CurrentUtcTimestamp(); $day=DateTime::MakeDate(DateTime::Split(AddTimezone($now,'Europe/Moscow'))); UPDATE `{table}` SET state='RESULT_RECORDED',provider_outcome=$outcome,provider_request_id=$request,input_tokens=$input,output_tokens=$output,total_tokens=$total,actual_cost_rub=$cost,finished_at=$now,finished_moscow_day=$day,reconciled_at=IF($rkind IS NULL,NULL,$now),reconciliation_kind=$rkind,reconciliation_evidence_sha256=$evidence WHERE repository=$repo AND record_key=$key AND state='{allowed}';", params)
            else:
                tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $rkind AS Utf8?; DECLARE $evidence AS Utf8?; $now=CurrentUtcTimestamp(); UPDATE `{table}` SET state='{target.value}',provider_outcome='UNKNOWN',input_tokens=NULL,output_tokens=NULL,total_tokens=NULL,actual_cost_rub=NULL,finished_at=NULL,finished_moscow_day=NULL,reconciled_at=IF($rkind IS NULL,NULL,$now),reconciliation_kind=$rkind,reconciliation_evidence_sha256=$evidence WHERE repository=$repo AND record_key=$key AND state='{allowed}';", params)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT state FROM `{table}` WHERE repository=$repo AND record_key=$key;", key, commit_tx=True)
            current = actual[0].rows[0]["state"] if actual and actual[0].rows else "ABSENT"
            return TransitionResult(current == target.value, previous, current, "" if current == target.value else "inconclusive")
        return self._serializable(op)

    def record_model_result(self, identity: ModelCallIdentity, result: RecordedModelResult) -> TransitionResult:
        return self._model_transition(identity, ModelState.RESERVED.value, ModelState.RESULT_RECORDED, result, None)

    def mark_model_unknown(self, identity: ModelCallIdentity, unknown: UnknownModelOutcome) -> TransitionResult:
        return self._model_transition(identity, ModelState.RESERVED.value, ModelState.UNKNOWN_BILLED, None, None)

    def reconcile_model_call(self, identity: ModelCallIdentity, reconciliation: ModelReconciliation) -> TransitionResult:
        target = ModelState.RESULT_RECORDED if reconciliation.kind == "RESULT_RECORDED" else ModelState.RECONCILED_NOT_BILLED
        return self._model_transition(identity, ModelState.UNKNOWN_BILLED.value, target, reconciliation.result, reconciliation)

    def get_model_call(self, identity: ModelCallIdentity) -> ModelCallRecord | None:
        table, key = self._table("model_calls"), f"call:{identity.idempotency_identity}"
        def op(tx):
            result = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT * FROM `{table}` WHERE repository=$repo AND record_key=$key AND expires_at>CurrentUtcTimestamp();", {"$repo": self.repository.canonical, "$key": key}, commit_tx=True)
            return result[0].rows[0] if result and result[0].rows else None
        row = self._serializable(op)
        if not row: return None
        try:
            reservation = ModelCallReservation(ModelCallIdentity(row["idempotency_identity"]), row["reservation_nonce"], row["lineage_id"], row["run_receipt_identity"], row["provider"], row["model"], row["role"], row["verification_pass"], row["attempt"])
            state = ModelState(row["state"])
            result = None
            if state == ModelState.RESULT_RECORDED:
                result = RecordedModelResult(row.get("provider_outcome"), row.get("provider_request_id"), row.get("input_tokens"), row.get("output_tokens"), row.get("total_tokens"), row.get("actual_cost_rub"))
            if state == ModelState.UNKNOWN_BILLED and any(row.get(field) is not None for field in ("input_tokens", "output_tokens", "total_tokens", "actual_cost_rub", "finished_at", "finished_moscow_day")):
                raise ValueError("invalid UNKNOWN row")
            if row.get("reconciliation_evidence_sha256") is not None:
                _sha(row["reconciliation_evidence_sha256"], "reconciliation evidence digest")
            return ModelCallRecord(reservation, state, row["reserved_at"], row["reservation_moscow_day"], row.get("finished_at"), row.get("finished_moscow_day"), result, row.get("reconciled_at"), row.get("reconciliation_kind"), row.get("reconciliation_evidence_sha256"), row.get("expires_at"))
        except (KeyError, TypeError, ValueError):
            raise StateError("stored model-call row is invalid") from None

    def actual_spend_current_moscow_day(self) -> Decimal:
        table = self._table("model_calls")
        def op(tx):
            result = tx.execute(f"$now=CurrentUtcTimestamp(); $day=DateTime::MakeDate(DateTime::Split(AddTimezone($now,'Europe/Moscow'))); SELECT COALESCE(SUM(actual_cost_rub),Decimal('0',22,9)) AS spent FROM `{table}` WHERE repository='{self.repository.canonical}' AND row_kind='CALL' AND state='RESULT_RECORDED' AND finished_moscow_day=$day AND actual_cost_rub IS NOT NULL;", commit_tx=True)
            return result[0].rows[0]["spent"]
        return Decimal(str(self._serializable(op)))

    def has_unknown_for_current_moscow_day(self) -> bool:
        table = self._table("model_calls")
        def op(tx):
            result = tx.execute(f"$now=CurrentUtcTimestamp(); $day=DateTime::MakeDate(DateTime::Split(AddTimezone($now,'Europe/Moscow'))); SELECT COUNT(*) AS n FROM `{table}` WHERE repository='{self.repository.canonical}' AND row_kind='CALL' AND state='UNKNOWN_BILLED' AND reservation_moscow_day=$day;", commit_tx=True)
            return bool(result[0].rows[0]["n"])
        return self._serializable(op)

    def get_rotation(self, role: str) -> RotationRecord | None:
        table, key = self._table("model_calls"), f"rotation:{role}"
        def op(tx):
            result = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT role,rotation_cursor,rotation_nonce,rotation_updated_at FROM `{table}` WHERE repository=$repo AND record_key=$key AND row_kind='ROTATION';", {"$repo": self.repository.canonical, "$key": key}, commit_tx=True)
            return result[0].rows[0] if result and result[0].rows else None
        row = self._serializable(op)
        return RotationRecord(row["role"], row["rotation_cursor"], row["rotation_nonce"], row["rotation_updated_at"]) if row else None

    def advance_rotation(self, claim: RotationClaim) -> ClaimResult:
        table, key_value = self._table("model_calls"), f"rotation:{claim.role}"
        def op(tx):
            key = {"$repo": self.repository.canonical, "$key": key_value}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT rotation_cursor,rotation_nonce FROM `{table}` WHERE repository=$repo AND record_key=$key;", key)
            rows = before[0].rows if before else []
            cursor = rows[0]["rotation_cursor"] if rows else 0
            if rows and cursor == claim.next_cursor and rows[0]["rotation_nonce"] == claim.rotation_nonce:
                tx.commit(); return ClaimResult(ClaimStatus.EXISTING_SAME, True, str(cursor), mutation_nonce=claim.rotation_nonce)
            if cursor != claim.expected_cursor:
                tx.commit(); return ClaimResult(ClaimStatus.LOST, False, str(cursor), mutation_nonce=rows[0]["rotation_nonce"] if rows else None)
            params = {**key, "$role": claim.role, "$cursor": claim.next_cursor, "$nonce": claim.rotation_nonce}
            tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; DECLARE $role AS Utf8; DECLARE $cursor AS Uint64; DECLARE $nonce AS Utf8; UPSERT INTO `{table}` (repository,record_key,row_kind,role,state,rotation_cursor,rotation_nonce,rotation_updated_at) VALUES ($repo,$key,'ROTATION',$role,'ROTATION',$cursor,$nonce,CurrentUtcTimestamp());", params)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT rotation_cursor,rotation_nonce FROM `{table}` WHERE repository=$repo AND record_key=$key;", key, commit_tx=True)
            won = bool(actual and actual[0].rows and actual[0].rows[0]["rotation_nonce"] == claim.rotation_nonce)
            return ClaimResult(ClaimStatus.WON if won else ClaimStatus.INCONCLUSIVE, won, str(actual[0].rows[0]["rotation_cursor"]), mutation_nonce=actual[0].rows[0]["rotation_nonce"])
        try:
            return self._serializable(op)
        except StateError:
            return self._reconcile_rotation_once(claim)

    def _reconcile_rotation_once(self, claim: RotationClaim) -> ClaimResult:
        table, key = self._table("model_calls"), f"rotation:{claim.role}"
        def read(session):
            result = session.transaction().execute(
                f"DECLARE $repo AS Utf8; DECLARE $key AS Utf8; SELECT rotation_cursor,rotation_nonce FROM `{table}` WHERE repository=$repo AND record_key=$key AND row_kind='ROTATION';",
                {"$repo": self.repository.canonical, "$key": key}, commit_tx=True,
            )
            return result[0].rows[0] if result and result[0].rows else None
        try:
            row = self.pool.retry_operation_sync(read)
        except Exception:
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "UNKNOWN")
        if not row:
            return ClaimResult(ClaimStatus.INCONCLUSIVE, False, "ABSENT")
        exact = row.get("rotation_cursor") == claim.next_cursor and row.get("rotation_nonce") == claim.rotation_nonce
        return ClaimResult(ClaimStatus.WON if exact else ClaimStatus.LOST, exact, str(row.get("rotation_cursor")), mutation_nonce=row.get("rotation_nonce"))

    def put_verification_result(self, result: VerificationResult) -> ClaimResult:
        table = self._table("verification_results")
        def op(tx):
            key = {"$repo": self.repository.canonical, "$id": result.verification_id}
            before = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; SELECT * FROM `{table}` WHERE repository=$repo AND verification_id=$id;", key)
            rows = before[0].rows if before else []
            if rows:
                existing = _verification_from_row(rows[0])
                tx.commit(); same = _verification_payload_hash(existing) == _verification_payload_hash(result)
                return ClaimResult(ClaimStatus.EXISTING_SAME if same else ClaimStatus.CONFLICT, False, rows[0]["final_verdict"])
            params = {**key, "$version": result.verification_schema_version, "$lineage": result.lineage_id, "$run": result.run_receipt_identity, "$case": result.case_id, "$case_sha": result.case_sha256, "$main": result.main_commit_sha, "$tree": result.main_tree_sha, "$manifest": result.source_manifest_sha256, "$source": result.source_content_sha256, "$target": result.target_content_sha256, "$candidate": result.candidate_content_sha256, "$original": result.original_verdict, "$critics": json.dumps(result.critic_results, sort_keys=True), "$findings": json.dumps(result.deterministic_findings, sort_keys=True), "$repairs": json.dumps(result.repair_evidence, sort_keys=True), "$issues": json.dumps(result.interpreted_issues, sort_keys=True), "$final": result.final_verdict}
            tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; DECLARE $version AS Utf8; DECLARE $lineage AS Utf8; DECLARE $run AS Utf8; DECLARE $case AS Utf8; DECLARE $case_sha AS Utf8; DECLARE $main AS Utf8; DECLARE $tree AS Utf8; DECLARE $manifest AS Utf8; DECLARE $source AS Utf8; DECLARE $target AS Utf8?; DECLARE $candidate AS Utf8; DECLARE $original AS Utf8; DECLARE $critics AS Json; DECLARE $findings AS Json; DECLARE $repairs AS Json; DECLARE $issues AS Json; DECLARE $final AS Utf8; $now=CurrentUtcTimestamp(); INSERT INTO `{table}` (repository,verification_id,verification_schema_version,lineage_id,run_receipt_identity,case_id,case_sha256,main_commit_sha,main_tree_sha,source_manifest_sha256,source_content_sha256,target_content_sha256,candidate_content_sha256,original_verdict,critic_results,deterministic_findings,repair_evidence,interpreted_issues,final_verdict,created_at,updated_at,expires_at) VALUES ($repo,$id,$version,$lineage,$run,$case,$case_sha,$main,$tree,$manifest,$source,$target,$candidate,$original,$critics,$findings,$repairs,$issues,$final,$now,$now,$now+Interval('P14D'));", params)
            actual = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; SELECT verification_id FROM `{table}` WHERE repository=$repo AND verification_id=$id;", key, commit_tx=True)
            won = bool(actual and actual[0].rows)
            return ClaimResult(ClaimStatus.CREATED if won else ClaimStatus.INCONCLUSIVE, won, result.final_verdict)
        return self._serializable(op)

    def get_verification_result(self, verification_id: str) -> VerificationRecord | None:
        table = self._table("verification_results")
        def op(tx):
            value = tx.execute(f"DECLARE $repo AS Utf8; DECLARE $id AS Utf8; SELECT * FROM `{table}` WHERE repository=$repo AND verification_id=$id AND expires_at>CurrentUtcTimestamp();", {"$repo": self.repository.canonical, "$id": verification_id}, commit_tx=True)
            return value[0].rows[0] if value and value[0].rows else None
        row = self._serializable(op)
        if not row: return None
        value = _verification_from_row(row)
        return VerificationRecord(value, row["created_at"], row["updated_at"], row["expires_at"])


def _verification_payload_hash(value: VerificationResult) -> str:
    payload = json.dumps(value.__dict__, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(payload.encode()).hexdigest()


def _json_value(value):
    return json.loads(value) if isinstance(value, str) else value


def _lineage_from_row(row) -> LineageRecord:
    try:
        manifest = _json_value(row["manifest_payload"])
        decisions_value = _json_value(row["typed_decisions"])
        if not isinstance(decisions_value, list):
            raise ValueError("invalid stored lineage decisions")
        value = NewLineage(
            row["lineage_id"], row["source_pr"], row.get("draft_pr"), row["branch"], row["state"],
            row["merge_commit_sha"], row["base_sha"], row["head_sha"], row["manifest_schema_version"],
            row["manifest_sha256"], manifest, row["main_commit_sha"], row["main_tree_sha"],
            row["decisions_schema_version"], tuple(decisions_value),
        )
        return LineageRecord(value, row["continue_count"], row["created_at"], row["updated_at"], row["expires_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise StateError("stored lineage row is invalid") from None


def _verification_from_row(row) -> VerificationResult:
    try:
        return VerificationResult(
            row["verification_id"], row["case_id"], row["case_sha256"], row["lineage_id"],
            row["run_receipt_identity"], row["main_commit_sha"], row["main_tree_sha"],
            row["source_manifest_sha256"], row["source_content_sha256"], row.get("target_content_sha256"),
            row["candidate_content_sha256"], row["original_verdict"],
            tuple(_json_value(row["critic_results"])), tuple(_json_value(row["deterministic_findings"])),
            tuple(_json_value(row["repair_evidence"])), tuple(_json_value(row["interpreted_issues"])),
            row["final_verdict"], row["verification_schema_version"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise StateError("stored verification row is invalid") from None


def schema_statements(prefix: str = "") -> tuple[str, ...]:
    if prefix and not TABLE_PREFIX_RE.fullmatch(prefix):
        raise ValueError("invalid acceptance table prefix")
    name = lambda table: f"{prefix}_{table}" if prefix else table
    command_fields = "repository Utf8 NOT NULL, record_key Utf8 NOT NULL, row_kind Utf8 NOT NULL, receipt_identity Utf8, github_run_id Utf8, github_run_attempt Uint32, github_event_name Utf8, github_event_action Utf8, label_timeline_event_id Uint64, payload_sha256 Utf8, command Utf8, actor Utf8, source_pr Uint64, translation_pr Uint64, phase Utf8 NOT NULL, lock_owner Utf8, mutation_nonce Utf8, lease_until Timestamp, effects_schema_version Utf8, effect_checkpoints Json, terminal_outcome Utf8, report_marker Utf8, created_at Timestamp NOT NULL, updated_at Timestamp NOT NULL, expires_at Timestamp, PRIMARY KEY(repository,record_key)"
    lineage_fields = "repository Utf8 NOT NULL, lineage_id Utf8 NOT NULL, source_pr Uint64 NOT NULL, draft_pr Uint64, branch Utf8 NOT NULL, state Utf8 NOT NULL, merge_commit_sha Utf8 NOT NULL, base_sha Utf8 NOT NULL, head_sha Utf8 NOT NULL, manifest_schema_version Utf8 NOT NULL, manifest_sha256 Utf8 NOT NULL, manifest_payload Json NOT NULL, main_commit_sha Utf8 NOT NULL, main_tree_sha Utf8 NOT NULL, snapshot_captured_at Timestamp NOT NULL, decisions_schema_version Utf8 NOT NULL, typed_decisions Json NOT NULL, continue_count Uint32 NOT NULL, created_at Timestamp NOT NULL, updated_at Timestamp NOT NULL, expires_at Timestamp NOT NULL, PRIMARY KEY(repository,lineage_id)"
    model_fields = "repository Utf8 NOT NULL, record_key Utf8 NOT NULL, row_kind Utf8 NOT NULL, idempotency_identity Utf8, lineage_id Utf8, run_receipt_identity Utf8, provider Utf8, model Utf8, role Utf8 NOT NULL, verification_pass Uint32, attempt Uint32, state Utf8 NOT NULL, provider_outcome Utf8, provider_request_id Utf8, input_tokens Uint64, output_tokens Uint64, total_tokens Uint64, actual_cost_rub Decimal(22,9), reserved_at Timestamp, reservation_moscow_day Date, finished_at Timestamp, finished_moscow_day Date, reconciled_at Timestamp, reconciliation_kind Utf8, reconciliation_evidence_sha256 Utf8, reservation_nonce Utf8, rotation_cursor Uint64, rotation_nonce Utf8, rotation_updated_at Timestamp, expires_at Timestamp, PRIMARY KEY(repository,record_key)"
    verification_fields = "repository Utf8 NOT NULL, verification_id Utf8 NOT NULL, verification_schema_version Utf8 NOT NULL, lineage_id Utf8 NOT NULL, run_receipt_identity Utf8 NOT NULL, case_id Utf8 NOT NULL, case_sha256 Utf8 NOT NULL, main_commit_sha Utf8 NOT NULL, main_tree_sha Utf8 NOT NULL, source_manifest_sha256 Utf8 NOT NULL, source_content_sha256 Utf8 NOT NULL, target_content_sha256 Utf8, candidate_content_sha256 Utf8 NOT NULL, original_verdict Utf8 NOT NULL, critic_results Json NOT NULL, deterministic_findings Json NOT NULL, repair_evidence Json NOT NULL, interpreted_issues Json NOT NULL, final_verdict Utf8 NOT NULL, created_at Timestamp NOT NULL, updated_at Timestamp NOT NULL, expires_at Timestamp NOT NULL, PRIMARY KEY(repository,verification_id)"
    return (
        f"CREATE TABLE IF NOT EXISTS `{name('command_runs')}` ({command_fields});",
        f"CREATE TABLE IF NOT EXISTS `{name('lineages')}` ({lineage_fields});",
        f"CREATE TABLE IF NOT EXISTS `{name('model_calls')}` ({model_fields});",
        f"CREATE TABLE IF NOT EXISTS `{name('verification_results')}` ({verification_fields});",
    )
