"""YDB driver helpers for ops ledger / transcripts."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Mapping

logger = logging.getLogger(__name__)

DEFAULT_YDB_ENDPOINT = "grpcs://ydb.serverless.yandexcloud.net:2135"
DEFAULT_YDB_DATABASE = "/ru-central1/b1g7gqj2vnq67gjseuva/etns0641qf73btm7j21k"


def resolve_sa_key_file(env: Mapping[str, str] | None = None) -> str | None:
    """Return path to SA JSON key file, or write inline JSON secret to a temp file."""
    env = env or os.environ
    path = (env.get("YDBDOC_YDB_SA_KEY_FILE") or env.get("SA_KEY_FILE") or "").strip()
    if path and os.path.isfile(path):
        return path
    raw = (env.get("YDB_SA_KEY") or env.get("YDBDOC_YDB_SA_KEY_JSON") or "").strip()
    if not raw:
        return None
    # Validate JSON early
    json.loads(raw)
    fd, tmp = tempfile.mkstemp(prefix="ydbdoc-sa-", suffix=".json")
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(raw)
    os.chmod(tmp, 0o600)
    return tmp


def make_ydb_driver(
    *,
    endpoint: str | None = None,
    database: str | None = None,
    sa_key_file: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Any:
    """Create and wait for a YDB Driver (requires ``pip install 'ydb[yc]'``)."""
    try:
        import ydb
    except ImportError as exc:
        raise ImportError(
            "YDB SDK not installed. Run: pip install 'ydb[yc]'"
        ) from exc

    env = env or os.environ
    endpoint = endpoint or env.get("YDBDOC_YDB_ENDPOINT") or DEFAULT_YDB_ENDPOINT
    database = database or env.get("YDBDOC_YDB_DATABASE") or DEFAULT_YDB_DATABASE
    key_file = sa_key_file or resolve_sa_key_file(env)
    if not key_file:
        raise RuntimeError(
            "YDB SA key not configured. Set YDBDOC_YDB_SA_KEY_FILE or YDB_SA_KEY."
        )
    credentials = ydb.iam.ServiceAccountCredentials.from_file(key_file)
    driver = ydb.Driver(endpoint=endpoint, database=database, credentials=credentials)
    driver.wait(timeout=15, fail_fast=True)
    return driver
