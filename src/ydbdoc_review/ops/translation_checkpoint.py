"""Immutable retention records for validated translation output."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.ops.transcripts import TranscriptStore
from ydbdoc_review.segmentation.types import Segment

_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
logger = logging.getLogger(__name__)


class TranslationCheckpointError(RuntimeError):
    """A checkpoint could not be retained and verified durably."""


@dataclass(frozen=True)
class CheckpointIdentity:
    """Frozen source authority and translation implementation identity."""

    authority: RuAuthority
    translation_fingerprint: str


@dataclass(frozen=True)
class UnitReceipt:
    """Content-addressed receipt for one completed translation unit."""

    identity: CheckpointIdentity
    unit_key: str
    source_hash: str
    target_hash: str
    object_key: str
    validated: bool


@dataclass(frozen=True)
class VerifiedUnit:
    """A completed unit whose receipt and content were verified durably."""

    receipt: UnitReceipt
    source: bytes
    target: bytes


def load_verified_unit(
    store: TranscriptStore,
    parent_run_id: str,
    identity: CheckpointIdentity,
    unit_key: str,
    source: bytes,
) -> VerifiedUnit | None:
    """Load one exact completed unit conservatively."""
    receipt = _load_unit_receipt(store, parent_run_id, unit_key)
    if receipt is None:
        return None
    if receipt.identity != identity:
        _resume_miss(parent_run_id, unit_key, "checkpoint identity mismatch")
        return None
    if receipt.validated is not True:
        _resume_miss(parent_run_id, unit_key, "receipt is not validated")
        return None
    if hashlib.sha256(source).hexdigest() != receipt.source_hash:
        _resume_miss(parent_run_id, unit_key, "source hash mismatch")
        return None
    try:
        target = store.get(parent_run_id, receipt.object_key)
    except Exception as exc:
        _resume_miss(parent_run_id, unit_key, f"target load failed: {exc}")
        return None
    if target is None:
        _resume_miss(parent_run_id, unit_key, "target object missing")
        return None
    if hashlib.sha256(target).hexdigest() != receipt.target_hash:
        _resume_miss(parent_run_id, unit_key, "target hash mismatch")
        return None
    return VerifiedUnit(receipt=receipt, source=source, target=target)


def run_has_usable_verified_units(
    store: TranscriptStore,
    parent_run_id: str,
    identity: CheckpointIdentity,
) -> bool:
    """Return whether a completed run has any reusable unit evidence."""
    try:
        keys = store.list_keys(parent_run_id)
    except Exception as exc:
        logger.warning(
            "Translation resume parent=%s key listing failed: %s",
            parent_run_id,
            exc,
        )
        return False
    prefix = "translation/v1/units/"
    suffix = ".json"
    for receipt_key in keys:
        if not receipt_key.startswith(prefix) or not receipt_key.endswith(suffix):
            continue
        unit_key = receipt_key[len(prefix) : -len(suffix)]
        receipt = _load_unit_receipt(store, parent_run_id, unit_key)
        if (
            receipt is None
            or receipt.identity != identity
            or receipt.validated is not True
        ):
            continue
        try:
            target = store.get(parent_run_id, receipt.object_key)
        except Exception as exc:
            _resume_miss(parent_run_id, unit_key, f"target load failed: {exc}")
            continue
        if target is None:
            _resume_miss(parent_run_id, unit_key, "target object missing")
            continue
        if hashlib.sha256(target).hexdigest() != receipt.target_hash:
            _resume_miss(parent_run_id, unit_key, "target hash mismatch")
            continue
        return True
    logger.warning(
        "Translation resume parent=%s has no usable units for current identity",
        parent_run_id,
    )
    return False


def _resume_miss(parent_run_id: str, unit_key: str, reason: str) -> None:
    logger.warning(
        "Translation resume miss parent=%s unit=%s: %s",
        parent_run_id,
        unit_key,
        reason,
    )


def _strict_object(
    value: object,
    *,
    field: str,
    keys: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"malformed translation checkpoint {field}")
    return value


def _strict_string(value: object, *, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"translation checkpoint {field} must be a string")
    return value


def _strict_hash(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    text = _strict_string(value, field=field)
    if pattern.fullmatch(text) is None:
        raise ValueError(f"translation checkpoint {field} has invalid hash")
    return text


def _decode_identity(value: object) -> CheckpointIdentity:
    payload = _strict_object(
        value,
        field="identity",
        keys={"authority", "translation_fingerprint"},
    )
    authority_payload = _strict_object(
        payload["authority"],
        field="authority",
        keys={
            "baseline_sha",
            "mode",
            "ru_sha",
            "source_base_sha",
            "source_head_sha",
            "source_pr",
            "source_repo",
        },
    )
    source_pr = authority_payload["source_pr"]
    if type(source_pr) is not int or source_pr <= 0:
        raise ValueError("translation checkpoint source_pr must be a positive integer")
    try:
        mode = RuAuthorityMode(
            _strict_string(authority_payload["mode"], field="authority.mode")
        )
    except ValueError as exc:
        raise ValueError("translation checkpoint authority.mode is invalid") from exc
    authority = RuAuthority(
        source_repo=_strict_string(
            authority_payload["source_repo"], field="authority.source_repo"
        ),
        source_pr=source_pr,
        source_base_sha=_strict_hash(
            authority_payload["source_base_sha"],
            field="authority.source_base_sha",
            pattern=_COMMIT_SHA_RE,
        ),
        source_head_sha=_strict_hash(
            authority_payload["source_head_sha"],
            field="authority.source_head_sha",
            pattern=_COMMIT_SHA_RE,
        ),
        baseline_sha=_strict_hash(
            authority_payload["baseline_sha"],
            field="authority.baseline_sha",
            pattern=_COMMIT_SHA_RE,
        ),
        ru_sha=_strict_hash(
            authority_payload["ru_sha"],
            field="authority.ru_sha",
            pattern=_COMMIT_SHA_RE,
        ),
        mode=mode,
    )
    return CheckpointIdentity(
        authority=authority,
        translation_fingerprint=_strict_hash(
            payload["translation_fingerprint"],
            field="translation_fingerprint",
            pattern=_SHA256_RE,
        ),
    )


def _decode_unit_receipt(data: bytes, *, requested_unit_key: str) -> UnitReceipt:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed translation checkpoint receipt JSON") from exc
    root = _strict_object(
        payload,
        field="receipt",
        keys={
            "schema",
            "identity",
            "object_key",
            "source_hash",
            "target_hash",
            "unit_key",
            "validated",
        },
    )
    if type(root["schema"]) is not int or root["schema"] != _SCHEMA_VERSION:
        raise ValueError("unsupported translation checkpoint receipt schema")
    unit_key = _strict_hash(root["unit_key"], field="unit_key", pattern=_SHA256_RE)
    if unit_key != requested_unit_key:
        raise ValueError("translation checkpoint requested unit key mismatch")
    source_hash = _strict_hash(
        root["source_hash"], field="source_hash", pattern=_SHA256_RE
    )
    target_hash = _strict_hash(
        root["target_hash"], field="target_hash", pattern=_SHA256_RE
    )
    object_key = _strict_string(root["object_key"], field="object_key")
    if object_key != f"translation/v1/objects/{target_hash}":
        raise ValueError("translation checkpoint object key is not content-addressed")
    if type(root["validated"]) is not bool:
        raise ValueError("translation checkpoint validated must be boolean")
    return UnitReceipt(
        identity=_decode_identity(root["identity"]),
        unit_key=unit_key,
        source_hash=source_hash,
        target_hash=target_hash,
        object_key=object_key,
        validated=root["validated"],
    )


def _load_unit_receipt(
    store: TranscriptStore,
    parent_run_id: str,
    unit_key: str,
) -> UnitReceipt | None:
    if _SHA256_RE.fullmatch(unit_key) is None:
        _resume_miss(parent_run_id, unit_key, "requested unit key is invalid")
        return None
    receipt_key = f"translation/v1/units/{unit_key}.json"
    try:
        raw = store.get(parent_run_id, receipt_key)
    except Exception as exc:
        _resume_miss(parent_run_id, unit_key, f"receipt load failed: {exc}")
        return None
    if raw is None:
        _resume_miss(parent_run_id, unit_key, "completion receipt missing")
        return None
    try:
        return _decode_unit_receipt(raw, requested_unit_key=unit_key)
    except (TypeError, ValueError) as exc:
        _resume_miss(parent_run_id, unit_key, str(exc))
        return None


def translation_unit_key(
    *,
    source: bytes,
    source_path: str,
    target_locale: str,
    atom_signature: tuple[tuple[str, str], ...],
    parent_context: str,
) -> str:
    """Return a stable key for the complete source-side unit identity."""
    payload = {
        "atom_signature": [list(atom) for atom in atom_signature],
        "parent_context": parent_context,
        "source_hash": hashlib.sha256(source).hexdigest(),
        "source_path": source_path.replace("\\", "/"),
        "target_locale": target_locale,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def translation_unit_key_for_segment(
    segment: Segment,
    *,
    source_path: str,
    target_locale: str,
) -> str:
    """Build the one canonical save/load key for a current Segment."""
    atoms: list[tuple[str, str]] = []
    for protected in segment.placeholders:
        node = protected.node
        payload = (
            node.model_dump(mode="json")
            if hasattr(node, "model_dump")
            else str(node)
        )
        kind = (
            str(payload.get("kind", type(node).__name__))
            if isinstance(payload, dict)
            else type(node).__name__
        )
        atoms.append(
            (
                kind,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    parent_context = json.dumps(
        {
            "ast_path": segment.ast_path,
            "heading_anchor": segment.heading_anchor,
            "kind": segment.kind.value,
            "path": segment.path,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return translation_unit_key(
        source=segment.text.encode("utf-8"),
        source_path=source_path,
        target_locale=target_locale,
        atom_signature=tuple(atoms),
        parent_context=parent_context,
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _authority_payload(authority: RuAuthority) -> dict[str, object]:
    return {
        "baseline_sha": authority.baseline_sha,
        "mode": authority.mode.value,
        "ru_sha": authority.ru_sha,
        "source_base_sha": authority.source_base_sha,
        "source_head_sha": authority.source_head_sha,
        "source_pr": authority.source_pr,
        "source_repo": authority.source_repo,
    }


def _identity_payload(identity: CheckpointIdentity) -> dict[str, object]:
    return {
        "authority": _authority_payload(identity.authority),
        "translation_fingerprint": identity.translation_fingerprint,
    }


def _receipt_payload(receipt: UnitReceipt) -> dict[str, object]:
    return {
        "schema": _SCHEMA_VERSION,
        "identity": _identity_payload(receipt.identity),
        "object_key": receipt.object_key,
        "source_hash": receipt.source_hash,
        "target_hash": receipt.target_hash,
        "unit_key": receipt.unit_key,
        "validated": receipt.validated,
    }


def _path_record_key(kind: str, path: str) -> str:
    path_hash = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return f"translation/v1/{kind}/{path_hash}.json"


class CheckpointWriter:
    """Collect retention artifacts for one translation run."""

    def __init__(
        self,
        store: TranscriptStore,
        run_id: str,
        identity: CheckpointIdentity,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.identity = identity
        self._unit_receipts: dict[str, tuple[UnitReceipt, str]] = {}
        self._files: dict[str, dict[str, object]] = {}
        self._navigation: dict[str, dict[str, object]] = {}

    def _put_immutable(self, object_key: str, data: bytes) -> None:
        try:
            existing = self.store.get(self.run_id, object_key)
            if existing is not None and existing != data:
                raise TranslationCheckpointError(
                    f"translation checkpoint immutable conflict for {object_key}"
                )
            if existing is None:
                self.store.put(self.run_id, object_key, data)
            if self.store.get(self.run_id, object_key) != data:
                raise TranslationCheckpointError(
                    "translation checkpoint write verification failed "
                    f"for {object_key}"
                )
        except TranslationCheckpointError:
            raise
        except Exception as exc:
            raise TranslationCheckpointError(
                f"translation checkpoint store failure for {object_key}: {exc}"
            ) from exc

    def save_unit(
        self,
        unit_key: str,
        source: bytes,
        target: bytes,
        *,
        validated: bool,
    ) -> UnitReceipt:
        source_hash = hashlib.sha256(source).hexdigest()
        target_hash = hashlib.sha256(target).hexdigest()
        object_key = f"translation/v1/objects/{target_hash}"
        receipt = UnitReceipt(
            identity=self.identity,
            unit_key=unit_key,
            source_hash=source_hash,
            target_hash=target_hash,
            object_key=object_key,
            validated=validated,
        )
        self._put_immutable(object_key, target)
        receipt_bytes = _canonical_json(_receipt_payload(receipt))
        receipt_key = f"translation/v1/units/{unit_key}.json"
        self._put_immutable(receipt_key, receipt_bytes)
        self._unit_receipts[unit_key] = (
            receipt,
            hashlib.sha256(receipt_bytes).hexdigest(),
        )
        return receipt

    def save_file(
        self,
        path: str,
        source: bytes,
        target: bytes,
        *,
        blockers: tuple[str, ...],
    ) -> None:
        normalized_path = path.replace("\\", "/")
        target_hash = hashlib.sha256(target).hexdigest()
        object_key = f"translation/v1/objects/{target_hash}"
        self._put_immutable(object_key, target)
        record: dict[str, object] = {
            "blockers": list(blockers),
            "path": normalized_path,
            "source_hash": hashlib.sha256(source).hexdigest(),
            "target_hash": target_hash,
            "target_object_key": object_key,
        }
        existing = self._files.get(normalized_path)
        if existing is not None and existing != record:
            raise TranslationCheckpointError(
                f"translation checkpoint immutable file conflict for {normalized_path}"
            )
        self._put_immutable(
            _path_record_key("files", normalized_path),
            _canonical_json(
                {
                    "identity": _identity_payload(self.identity),
                    "record": record,
                    "schema": _SCHEMA_VERSION,
                }
            ),
        )
        self._files[normalized_path] = record

    def save_navigation(
        self,
        path: str,
        target: bytes | None,
        *,
        blockers: tuple[str, ...],
    ) -> None:
        normalized_path = path.replace("\\", "/")
        if target is None:
            target_hash = None
            object_key = None
        else:
            target_hash = hashlib.sha256(target).hexdigest()
            object_key = f"translation/v1/objects/{target_hash}"
            self._put_immutable(object_key, target)
        record: dict[str, object] = {
            "baseline_noop": target is None,
            "blockers": list(blockers),
            "path": normalized_path,
            "target_hash": target_hash,
            "target_object_key": object_key,
        }
        existing = self._navigation.get(normalized_path)
        if existing is not None and existing != record:
            raise TranslationCheckpointError(
                f"translation checkpoint immutable navigation conflict for {normalized_path}"
            )
        self._put_immutable(
            _path_record_key("navigation", normalized_path),
            _canonical_json(
                {
                    "identity": _identity_payload(self.identity),
                    "record": record,
                    "schema": _SCHEMA_VERSION,
                }
            ),
        )
        self._navigation[normalized_path] = record

    def finish(
        self,
        *,
        status: str,
        blockers: tuple[str, ...],
        scope: dict[str, tuple[str, ...]],
    ) -> None:
        units = [
            {
                "receipt_hash": receipt_hash,
                "unit_key": receipt.unit_key,
                "validated": receipt.validated,
            }
            for receipt, receipt_hash in (
                self._unit_receipts[key] for key in sorted(self._unit_receipts)
            )
        ]
        manifest = {
            "blockers": list(blockers),
            "files": [self._files[path] for path in sorted(self._files)],
            "identity": _identity_payload(self.identity),
            "navigation": [
                self._navigation[path] for path in sorted(self._navigation)
            ],
            "schema": _SCHEMA_VERSION,
            "scope": {
                path.replace("\\", "/"): list(scope[path])
                for path in sorted(scope)
            },
            "stage": "retained",
            "status": status,
            "units": units,
        }
        self._put_immutable(
            "translation/v1/manifest.json",
            _canonical_json(manifest),
        )
