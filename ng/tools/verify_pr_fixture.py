#!/usr/bin/env python3
"""Strictly verify the closed PR 45949 provenance pack offline."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from derive_pr_fixture import DerivationError, SAFE_RELATIVE, canonical, derive, strict_json_bytes


class FixtureError(ValueError):
    """The evidence pack is incomplete, substituted, or corrupt."""


DOCUMENTS = {
    "requests.json": "requests",
    "inventory.json": "inventory",
    "headers.json": "headers",
    "manifest.json": "manifest",
    "PROVENANCE.json": "provenance",
}


def _load_closed_json(path: Path) -> Any:
    try:
        return strict_json_bytes(path.read_bytes(), path.as_posix())
    except (OSError, DerivationError) as exc:
        raise FixtureError(str(exc)) from exc


def _verify_checksums(root: Path) -> None:
    checksums = _load_closed_json(root / "checksums.json")
    if not isinstance(checksums, dict) or set(checksums) != {"schema_version", "algorithm", "files"}:
        raise FixtureError("checksums.json is not a closed object")
    if checksums["schema_version"] != "fixture-checksums/v2" or checksums["algorithm"] != "sha256":
        raise FixtureError("wrong checksum contract")
    entries = checksums["files"]
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 200:
        raise FixtureError("invalid checksum entries")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "checksums.json"
    }
    if set(entries) != actual:
        raise FixtureError("checksum coverage mismatch")
    for relative, expected in entries.items():
        if not isinstance(relative, str) or not SAFE_RELATIVE.fullmatch(relative):
            raise FixtureError(f"unsafe checksum path: {relative!r}")
        if not isinstance(expected, str) or len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            raise FixtureError(f"invalid checksum digest: {relative}")
        try:
            observed = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError as exc:
            raise FixtureError(f"unreadable checksum target: {relative}") from exc
        if observed != expected:
            raise FixtureError(f"SHA-256 mismatch: {relative}")


def _verify_exact_tree(root: Path, expected: dict[str, Any]) -> None:
    allowed = set(DOCUMENTS) | {"PROVENANCE.md", "checksums.json"}
    allowed |= set(expected["manifest"]["authoritative_raw_artifacts"])
    allowed |= set(expected["manifest"]["authoritative_blob_artifacts"])
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != allowed:
        raise FixtureError("fixture contains missing or unknown files")


def _verify_documents(root: Path, expected: dict[str, Any]) -> None:
    for filename, key in DOCUMENTS.items():
        observed = _load_closed_json(root / filename)
        if canonical(observed) != canonical(expected[key]):
            raise FixtureError(f"derived closed document mismatch: {filename}")
    try:
        display = (root / "PROVENANCE.md").read_bytes()
    except OSError as exc:
        raise FixtureError("missing display provenance") from exc
    if display != expected["display"]:
        raise FixtureError("display provenance was not machine-derived")


def _verify_blobs(root: Path, expected: dict[str, Any]) -> None:
    for sha, data in expected["blobs"].items():
        path = root / "blobs" / f"{sha}.b64"
        try:
            encoded = b"".join(path.read_bytes().split())
            observed = base64.b64decode(encoded, validate=True)
        except (OSError, ValueError) as exc:
            raise FixtureError(f"invalid stored blob {sha}") from exc
        if base64.b64encode(observed) != encoded or observed != data:
            raise FixtureError(f"stored blob substitution: {sha}")


def verify_fixture(root: Path) -> None:
    root = root.resolve()
    _verify_checksums(root)
    try:
        expected = derive(root)
    except DerivationError as exc:
        raise FixtureError(str(exc)) from exc
    _verify_exact_tree(root, expected)
    _verify_documents(root, expected)
    _verify_blobs(root, expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, nargs="?", default=Path("fixtures/pr-45949"))
    args = parser.parse_args()
    try:
        verify_fixture(args.fixture)
    except FixtureError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    provenance = _load_closed_json(args.fixture / "PROVENANCE.json")
    print(f"PASS: PR 45949 provenance root {provenance['provenance_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
