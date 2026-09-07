"""Immutable retention records for validated translation output."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.ops.transcripts import TranscriptStore

_SCHEMA_VERSION = 1


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
