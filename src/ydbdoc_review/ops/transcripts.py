"""LLM transcript storage: YDB (default) or S3 (§20.11)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger(__name__)

CHUNK_SIZE = 512 * 1024


def new_run_id() -> str:
    return str(uuid.uuid4())


def chunk_payload(data: bytes, size: int = CHUNK_SIZE) -> list[bytes]:
    if not data:
        return [b""]
    return [data[i : i + size] for i in range(0, len(data), size)]


def join_chunks(parts: list[bytes]) -> bytes:
    return b"".join(parts)


class TranscriptStore(Protocol):
    def put(self, run_id: str, object_key: str, data: bytes | str) -> None: ...

    def get(self, run_id: str, object_key: str) -> bytes | None: ...

    def exists_run(self, run_id: str) -> bool: ...

    def list_keys(self, run_id: str) -> list[str]: ...


class NullTranscriptStore:
    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        return None

    def get(self, run_id: str, object_key: str) -> bytes | None:
        return None

    def exists_run(self, run_id: str) -> bool:
        return False

    def list_keys(self, run_id: str) -> list[str]:
        return []


class InMemoryTranscriptStore:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], bytes] = {}

    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        self._data[(run_id, object_key)] = raw

    def get(self, run_id: str, object_key: str) -> bytes | None:
        return self._data.get((run_id, object_key))

    def exists_run(self, run_id: str) -> bool:
        return any(rid == run_id for rid, _ in self._data)

    def list_keys(self, run_id: str) -> list[str]:
        return sorted(k for rid, k in self._data if rid == run_id)


class YdbTranscriptStore:
    """Chunked objects in table ``run_objects``."""

    def __init__(self, driver: object) -> None:
        import ydb

        self._ydb = ydb
        self._driver = driver
        self._pool = ydb.SessionPool(driver)

    def close(self) -> None:
        try:
            self._pool.stop()
        except Exception:
            pass

    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        parts = chunk_payload(raw)
        now_us = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
        # delete existing parts then insert
        self._delete_object(run_id, object_key)
        query = """
        DECLARE $run_id AS Utf8;
        DECLARE $object_key AS Utf8;
        DECLARE $part_no AS Uint32;
        DECLARE $created_at AS Timestamp;
        DECLARE $payload AS String;

        UPSERT INTO run_objects (run_id, object_key, part_no, created_at, payload)
        VALUES ($run_id, $object_key, $part_no, $created_at, $payload);
        """

        def _cal(session: object) -> None:
            prep = session.prepare(query)  # type: ignore[attr-defined]
            tx = session.transaction()  # type: ignore[attr-defined]
            for i, part in enumerate(parts):
                tx.execute(
                    prep,
                    {
                        "$run_id": run_id,
                        "$object_key": object_key,
                        "$part_no": i,
                        "$created_at": now_us,
                        "$payload": part,
                    },
                )
            tx.commit()

        self._pool.retry_operation_sync(_cal)

    def get(self, run_id: str, object_key: str) -> bytes | None:
        query = """
        DECLARE $run_id AS Utf8;
        DECLARE $object_key AS Utf8;
        SELECT part_no, payload FROM run_objects
        WHERE run_id = $run_id AND object_key = $object_key
        ORDER BY part_no;
        """

        def _cal(session: object) -> bytes | None:
            prep = session.prepare(query)  # type: ignore[attr-defined]
            result = session.transaction().execute(  # type: ignore[attr-defined]
                prep,
                {"$run_id": run_id, "$object_key": object_key},
                commit_tx=True,
            )
            rows = list(result[0].rows)
            if not rows:
                return None
            parts = [bytes(row.payload) for row in rows]
            return join_chunks(parts)

        return self._pool.retry_operation_sync(_cal)

    def exists_run(self, run_id: str) -> bool:
        return bool(self.list_keys(run_id))

    def list_keys(self, run_id: str) -> list[str]:
        query = """
        DECLARE $run_id AS Utf8;
        SELECT DISTINCT object_key FROM run_objects WHERE run_id = $run_id;
        """

        def _cal(session: object) -> list[str]:
            prep = session.prepare(query)  # type: ignore[attr-defined]
            result = session.transaction().execute(  # type: ignore[attr-defined]
                prep,
                {"$run_id": run_id},
                commit_tx=True,
            )
            return sorted(str(row.object_key) for row in result[0].rows)

        try:
            return list(self._pool.retry_operation_sync(_cal))
        except Exception as exc:
            logger.warning("list_keys failed: %s", exc)
            return []

    def _delete_object(self, run_id: str, object_key: str) -> None:
        query = """
        DECLARE $run_id AS Utf8;
        DECLARE $object_key AS Utf8;
        DELETE FROM run_objects WHERE run_id = $run_id AND object_key = $object_key;
        """

        def _cal(session: object) -> None:
            prep = session.prepare(query)  # type: ignore[attr-defined]
            session.transaction().execute(  # type: ignore[attr-defined]
                prep,
                {"$run_id": run_id, "$object_key": object_key},
                commit_tx=True,
            )

        try:
            self._pool.retry_operation_sync(_cal)
        except Exception as exc:
            logger.debug("delete before put skipped: %s", exc)


class S3TranscriptStore:
    """Object Storage backend (used after quota is raised)."""

    def __init__(
        self,
        *,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint: str = "https://storage.yandexcloud.net",
        region: str = "ru-central1",
    ) -> None:
        import boto3

        self._bucket = bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )

    def _key(self, run_id: str, object_key: str) -> str:
        return f"runs/{run_id}/{object_key}"

    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        self._s3.put_object(Bucket=self._bucket, Key=self._key(run_id, object_key), Body=raw)

    def get(self, run_id: str, object_key: str) -> bytes | None:
        try:
            resp = self._s3.get_object(
                Bucket=self._bucket, Key=self._key(run_id, object_key)
            )
            return resp["Body"].read()
        except Exception:
            return None

    def exists_run(self, run_id: str) -> bool:
        return bool(self.list_keys(run_id))

    def list_keys(self, run_id: str) -> list[str]:
        prefix = f"runs/{run_id}/"
        resp = self._s3.list_objects_v2(Bucket=self._bucket, Prefix=prefix)
        keys: list[str] = []
        for obj in resp.get("Contents") or []:
            full = str(obj["Key"])
            if full.startswith(prefix):
                keys.append(full[len(prefix) :])
        return sorted(keys)


def create_transcript_store(
    backend: str = "ydb",
    *,
    env: dict[str, str] | None = None,
    driver: object | None = None,
) -> TranscriptStore:
    import os

    env_map = env or dict(os.environ)
    choice = (backend or "ydb").strip().lower()
    if choice in ("off", "null", "none"):
        return NullTranscriptStore()
    if choice == "memory":
        return InMemoryTranscriptStore()
    if choice == "s3":
        return S3TranscriptStore(
            bucket=env_map.get("YDBDOC_S3_BUCKET", "ydb-prs-translations-context"),
            access_key_id=env_map.get("YDBDOC_S3_ACCESS_KEY_ID", ""),
            secret_access_key=env_map.get("YDBDOC_S3_SECRET_ACCESS_KEY", ""),
            endpoint=env_map.get(
                "YDBDOC_S3_ENDPOINT", "https://storage.yandexcloud.net"
            ),
            region=env_map.get("YDBDOC_S3_REGION", "ru-central1"),
        )
    # ydb default
    if driver is None:
        from ydbdoc_review.ops.ydb_driver import make_ydb_driver

        driver = make_ydb_driver(env=env_map)
    return YdbTranscriptStore(driver)


def dump_llm_exchange(
    store: TranscriptStore,
    run_id: str,
    seq: int,
    role: str,
    request_obj: object,
    response_obj: object,
) -> None:
    store.put(
        run_id,
        f"llm/{seq:03d}-{role}-req.json",
        json.dumps(request_obj, ensure_ascii=False, indent=2),
    )
    store.put(
        run_id,
        f"llm/{seq:03d}-{role}-resp.json",
        json.dumps(response_obj, ensure_ascii=False, indent=2),
    )
