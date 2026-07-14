"""Tests for TLS CA bundle helpers."""

from __future__ import annotations

from pathlib import Path

import certifi
import pytest

from ydbdoc_review.llm.errors import LLMConfigError
from ydbdoc_review.llm.tls import eliza_tls_verify, public_ca_bundle


def test_public_ca_bundle_ignores_requests_env(monkeypatch, tmp_path):
    internal = tmp_path / "internal.pem"
    internal.write_text("internal", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(internal))

    assert public_ca_bundle() == certifi.where()


def test_eliza_tls_verify_merges_internal_with_certifi(tmp_path, monkeypatch):
    internal = tmp_path / "internal.pem"
    internal.write_text(
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YDBDOC_ELIZA_CA_BUNDLE", str(internal))

    verify = eliza_tls_verify()
    assert verify != certifi.where()
    assert verify != str(internal)
    merged = Path(verify).read_text(encoding="utf-8")
    assert "BEGIN CERTIFICATE" in merged
    assert Path(certifi.where()).read_text(encoding="utf-8")[:100] in merged


def test_eliza_tls_verify_missing_bundle_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("YDBDOC_ELIZA_CA_BUNDLE", str(tmp_path / "missing.pem"))
    with pytest.raises(LLMConfigError, match="YDBDOC_ELIZA_CA_BUNDLE"):
        eliza_tls_verify()
