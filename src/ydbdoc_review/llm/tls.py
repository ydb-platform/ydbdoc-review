"""TLS CA bundle helpers for ``requests`` (Eliza vs public APIs)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import certifi

_ELIZA_CA_BUNDLE_ENV = "YDBDOC_ELIZA_CA_BUNDLE"
ELIZA_CA_BUNDLE_ENV = _ELIZA_CA_BUNDLE_ENV
_DEFAULT_INTERNAL_CA = "/etc/ssl/certs/YandexInternalCA.pem"


def public_ca_bundle() -> str:
    """CA bundle for public HTTPS (GitHub, Yandex Cloud, …).

    Always uses certifi — ignores ``REQUESTS_CA_BUNDLE`` so a corp-only bundle
    in the shell does not break ``api.github.com``.
    """
    return certifi.where()


def _internal_ca_path() -> str | None:
    explicit = (os.environ.get(_ELIZA_CA_BUNDLE_ENV) or "").strip()
    if explicit:
        if not os.path.isfile(explicit):
            from ydbdoc_review.llm.errors import LLMConfigError

            raise LLMConfigError(
                f"{_ELIZA_CA_BUNDLE_ENV} points to missing file: {explicit!r}"
            )
        return explicit
    if os.path.isfile(_DEFAULT_INTERNAL_CA):
        return _DEFAULT_INTERNAL_CA
    return None


def _merge_ca_bundles(*paths: str) -> str:
    """Concatenate PEM bundles into a cached file under ``~/.cache/ydbdoc-review/``."""
    existing = [p for p in paths if p and os.path.isfile(p)]
    if not existing:
        return certifi.where()
    if len(existing) == 1:
        return existing[0]

    parts: list[str] = []
    mtimes: list[str] = []
    for path in existing:
        p = Path(path)
        parts.append(p.read_text(encoding="utf-8"))
        if not parts[-1].endswith("\n"):
            parts[-1] += "\n"
        mtimes.append(str(p.stat().st_mtime_ns))

    cache_root = Path(
        os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
    ) / "ydbdoc-review"
    cache_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256("".join(existing + mtimes).encode()).hexdigest()[:16]
    out = cache_root / f"ca-merged-{digest}.pem"
    if not out.is_file():
        out.write_text("".join(parts), encoding="utf-8")
    return str(out)


def eliza_tls_verify() -> bool | str:
    """CA bundle for Eliza: public roots + optional internal CA."""
    internal = _internal_ca_path()
    if internal:
        return _merge_ca_bundles(public_ca_bundle(), internal)
    return True
