"""YDB service-account authentication from the existing inline secret."""
from __future__ import annotations

import os
from collections.abc import Mapping

import ydb

DEFAULT_YDB_ENDPOINT = "grpcs://ydb.serverless.yandexcloud.net:2135"
DEFAULT_YDB_DATABASE = "/ru-central1/b1g7gqj2vnq67gjseuva/etns0641qf73btm7j21k"


def make_ydb_driver(*, endpoint: str | None = None, database: str | None = None,
                    env: Mapping[str, str] | None = None):
    """Create one authenticated driver; no token files, backend or credential aliases."""
    env = os.environ if env is None else env
    raw = env.get('YDB_SA_KEY', '').strip()
    if not raw:
        raise RuntimeError('YDB_SA_KEY is required')
    credentials = ydb.iam.ServiceAccountCredentials.from_content(raw)
    driver = ydb.Driver(endpoint=endpoint or env.get('YDBDOC_YDB_ENDPOINT') or DEFAULT_YDB_ENDPOINT,
                        database=database or env.get('YDBDOC_YDB_DATABASE') or DEFAULT_YDB_DATABASE,
                        credentials=credentials)
    try:
        driver.wait(timeout=15, fail_fast=True)
    except BaseException:
        driver.stop()
        raise
    return driver
