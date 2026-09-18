"""One YDB store and a RunHooks adapter; no legacy checkpoint/backend routing.

Schema provisioning is explicit (create_schema), never performed by the factory.
Money is decimal text in YDB, avoiding loss/rounding to a fixed SQL precision.
Each request has a ledger row even before its outcome is known. Daily accounting
uses request time in Moscow, including requests crossing midnight. Unknown cost
prevents an exact comparison: BudgetUnknown is a technical billing limitation.
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import ydb

from ydbdoc_review.model import (
    OPERATIONS,
    AttemptRecord,
    Endpoint,
    ModelClient,
    RequestRecord,
    Usage,
)
from ydbdoc_review.ops.ydb_driver import make_ydb_driver

TTL_SECONDS = 14 * 24 * 3600
CHUNK_SIZE = 512 * 1024
MOSCOW = ZoneInfo('Europe/Moscow')


class StorageError(RuntimeError):
    """YDB unavailable, incomplete object, or invalid stored data; never expiry."""


class ContextExpired(LookupError):
    def __init__(self):
        super().__init__('Контекст отсутствует или истёк срок хранения 14 дней. '
                         'Запустите doc_translate или doc_verify заново.')


class BudgetUnknown(RuntimeError):
    """Cannot compare unknown billing to a numeric budget."""


class BudgetExceeded(RuntimeError):
    pass


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError('Timezone-aware datetime required')
    return value.astimezone(UTC)


def money(value: Decimal | None) -> Decimal | None:
    if value is not None and (not isinstance(value, Decimal)
                              or not value.is_finite() or value < 0):
        raise ValueError('Cost must be a finite nonnegative Decimal or None')
    return value


def cost_breakdown(attempts) -> dict[str, Decimal | None]:
    result = {}
    for operation in OPERATIONS:
        values = [money(a.usage.cost_rub) for a in attempts if a.request.operation == operation]
        result[operation] = None if None in values else sum(values, Decimal(0))
    result['total'] = (None if None in result.values()
                       else sum(result.values(), Decimal(0)))
    return result


def rub_resolver(*, extract: Callable | None = None,
                 tariffs: Mapping[tuple[str, str], tuple[Decimal, Decimal] | tuple[Decimal, Decimal, Decimal]] | None = None):
    """Explicit trusted RUB extractor, or configured RUB per million input/output tokens.

    An optional third rate applies to cached prompt tokens.
    No provider field is implicitly trusted as currency. No default/legacy tariff.
    Missing token counts produce None; zero counts are valid. Extractor errors propagate.
    """
    tariffs = dict(tariffs or {})
    for rates in tariffs.values():
        if len(rates) not in (2, 3):
            raise ValueError('Expected input/output and optional cached-input tariff')
        for rate in rates:
            if money(rate) is None:
                raise ValueError('Tariff cannot be unknown')

    def resolve(endpoint: Endpoint, response: dict) -> Decimal | None:
        if extract is not None:
            value = money(extract(endpoint, response))
            if value is not None:
                return value
        rates = tariffs.get((endpoint.provider, endpoint.model))
        usage = response.get('usage')
        if rates is None or not isinstance(usage, dict):
            return None
        counts = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if any(type(n) is not int or n < 0 for n in counts):
            return None
        prompt, completion = counts
        cached = 0
        if len(rates) == 3:
            details = usage.get('prompt_tokens_details') or {}
            if not isinstance(details, dict):
                return None
            cached = details.get('cached_tokens', 0)
            if type(cached) is not int or not 0 <= cached <= prompt:
                return None
        total = Decimal(prompt - cached) * rates[0] + Decimal(completion) * rates[1]
        if len(rates) == 3:
            total += Decimal(cached) * rates[2]
        return total / Decimal(1_000_000)
    return resolve


def _plain(value):
    """Data only, no dynamic class loading; Decimal/bytes retain exact representation."""
    if isinstance(value, Decimal):
        return {'$decimal': str(value)}
    if isinstance(value, bytes):
        return {'$bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, datetime):
        return {'$datetime': utc(value).isoformat()}
    if isinstance(value, (Path, Enum)):
        return str(value) if isinstance(value, Path) else value.value
    if is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(v) for v in value]
    return value


def _restore(value):
    if len(value) == 1:
        if '$decimal' in value:
            return Decimal(value['$decimal'])
        if '$bytes' in value:
            return base64.b64decode(value['$bytes'], validate=True)
        if '$datetime' in value:
            return datetime.fromisoformat(value['$datetime'])
    return value


def encode(value) -> bytes:
    return json.dumps(_plain(value), ensure_ascii=False, allow_nan=False).encode('utf-8')


def decode(value: bytes):
    return json.loads(value, object_hook=_restore)


def chunk_payload(data: bytes, size: int = CHUNK_SIZE) -> list[bytes]:
    """Reused byte slicing primitive from ops/transcripts, without legacy imports."""
    if size <= 0:
        raise ValueError('Positive chunk size required')
    return [data[i:i + size] for i in range(0, len(data), size)] or [b'']


def schema() -> dict[str, ydb.TableDescription]:
    def table(columns, keys):
        description = ydb.TableDescription()
        for name, kind in columns:
            description.with_column(ydb.Column(name, ydb.OptionalType(kind)))
        return description.with_primary_keys(*keys)

    runs = table([(n, ydb.PrimitiveType.Utf8) for n in
                  ('run_id', 'entry_id', 'day', 'mode', 'status', 'operation', 'cost_rub')]
                 + [('created_at', ydb.PrimitiveType.Timestamp),
                    ('payload', ydb.PrimitiveType.String)], ('run_id', 'entry_id'))
    objects = table([(n, ydb.PrimitiveType.Utf8) for n in
                     ('run_id', 'object_key', 'generation')]
                    + [('part_no', ydb.PrimitiveType.Uint32),
                       ('created_at', ydb.PrimitiveType.Timestamp),
                       ('payload', ydb.PrimitiveType.String)],
                    ('run_id', 'object_key', 'generation', 'part_no'))
    objects.with_ttl(ydb.TtlSettings().with_date_type_column('created_at', TTL_SECONDS))
    return {'runs': runs, 'run_objects': objects}


class YDBStore:
    def __init__(self, driver, *, clock=lambda: datetime.now(UTC)):
        self.driver = driver
        self.pool = ydb.SessionPool(driver)
        self.clock = clock

    def close(self):
        errors = []
        for name, resource in (('pool', self.pool), ('driver', self.driver)):
            try:
                resource.stop()
            except (Exception, KeyboardInterrupt) as exc:
                errors.append(f'{name}: {type(exc).__name__}: {exc}')
        if errors:
            raise StorageError('YDB close: ' + '; '.join(errors))

    def create_schema(self, database: str):
        """Provision NEW tables explicitly. Existing legacy schema needs T15 deployment work."""
        try:
            for name, description in schema().items():
                self.pool.retry_operation_sync(
                    lambda session, n=name, d=description:
                    session.create_table(database.rstrip('/') + '/' + n, d))
        except Exception as exc:
            raise StorageError(f'YDB schema: {exc}') from exc

    def _execute(self, query, params):
        try:
            result = self.pool.retry_operation_sync(lambda session:
                session.transaction(ydb.SerializableReadWrite()).execute(
                    session.prepare(query), params, commit_tx=True))
            if any(getattr(part, 'truncated', False) for part in result):
                raise StorageError('YDB truncated result')
            return result
        except Exception as exc:
            raise StorageError(f'YDB: {exc}') from exc

    def put(self, run_id: str, key: str, data: bytes):
        # Publish manifest last: failed overwrite leaves previous object readable.
        generation = uuid4().hex
        created = utc(self.clock())
        parts = chunk_payload(data)
        for index, part in enumerate(parts):
            self._part(run_id, key, generation, index, created, part)
        self._part(run_id, key, '', 0, created, encode({
            'generation': generation, 'count': len(parts), 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'created_at': created}))

    def _part(self, run_id, key, generation, index, created, payload):
        self._execute('''
            DECLARE $run_id AS Utf8; DECLARE $object_key AS Utf8;
            DECLARE $generation AS Utf8; DECLARE $part_no AS Uint32;
            DECLARE $created_at AS Timestamp; DECLARE $payload AS String;
            UPSERT INTO run_objects (run_id, object_key, generation, part_no, created_at, payload)
            VALUES ($run_id, $object_key, $generation, $part_no, $created_at, $payload);
        ''', {'$run_id': run_id, '$object_key': key, '$generation': generation,
              '$part_no': index, '$created_at': int(created.timestamp() * 1_000_000),
              '$payload': payload})

    def _parts(self, run_id, key, generation):
        return self._execute('''
            DECLARE $run_id AS Utf8; DECLARE $object_key AS Utf8; DECLARE $generation AS Utf8;
            SELECT part_no, payload FROM run_objects
            WHERE run_id=$run_id AND object_key=$object_key AND generation=$generation
            ORDER BY part_no;
        ''', {'$run_id': run_id, '$object_key': key, '$generation': generation})[0].rows

    def get(self, run_id: str, key: str) -> bytes | None:
        rows = self._parts(run_id, key, '')
        if not rows:
            return None
        try:
            manifest = decode(bytes(rows[0].payload))
            if utc(self.clock()) >= manifest['created_at'] + timedelta(seconds=TTL_SECONDS):
                return None
            parts = self._parts(run_id, key, manifest['generation'])
            if [r.part_no for r in parts] != list(range(manifest['count'])):
                raise ValueError('Incomplete chunks')
            data = b''.join(bytes(r.payload) for r in parts)
            if len(data) != manifest['size'] or hashlib.sha256(data).hexdigest() != manifest['sha256']:
                raise ValueError('Object digest/size mismatch')
            return data
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f'Invalid YDB object {key}: {exc}') from exc

    def context(self, run_id: str) -> dict:
        data = self.get(run_id, 'context')
        if data is None:
            raise ContextExpired()
        try:
            return decode(data)
        except Exception as exc:
            raise StorageError(f'Invalid context: {exc}') from exc

    def ledger(self, run_id, entry_id, *, created, mode, status, operation='', cost=None,
               payload=b''):
        cost = money(cost)
        write = '''
            UPSERT INTO runs (run_id, entry_id, day, mode, status, operation, cost_rub, created_at, payload)
            VALUES ($run_id, $entry_id, $day, $mode, $status, $operation, $cost_rub, $created_at, $payload);
        '''
        if status == 'pending':
            # Request callbacks may replay after the outcome, even on a new adapter.
            # Check and insert in the same SerializableReadWrite transaction: no
            # local cache or separate read can protect against a concurrent outcome.
            write = '''
                UPSERT INTO runs
                SELECT incoming.* FROM (
                    SELECT $run_id AS run_id, $entry_id AS entry_id, $day AS day,
                           $mode AS mode, $status AS status, $operation AS operation,
                           $cost_rub AS cost_rub, $created_at AS created_at, $payload AS payload
                ) AS incoming
                LEFT JOIN runs AS previous
                ON incoming.run_id = previous.run_id AND incoming.entry_id = previous.entry_id
                WHERE previous.run_id IS NULL;
            '''
        self._execute('''
            DECLARE $run_id AS Utf8; DECLARE $entry_id AS Utf8; DECLARE $day AS Utf8;
            DECLARE $mode AS Utf8; DECLARE $status AS Utf8; DECLARE $operation AS Utf8;
            DECLARE $cost_rub AS Utf8?; DECLARE $created_at AS Timestamp; DECLARE $payload AS String;
        ''' + write, {'$run_id': run_id, '$entry_id': entry_id, '$day': utc(created).astimezone(MOSCOW).date().isoformat(),
              '$mode': mode, '$status': status, '$operation': operation,
              '$cost_rub': None if cost is None else str(cost),
              '$created_at': int(utc(created).timestamp() * 1_000_000), '$payload': payload})

    def latest_context(self, pr: str) -> dict:
        """Latest confirmed source/result PR context, independently of object TTL.

        Paginate existing summaries; no new index/schema or legacy context lookup.
        A newer expired context never falls back to an older run.
        """
        after = ''
        summaries = []
        while True:
            rows = self._execute("""
                DECLARE $after AS Utf8;
                SELECT run_id, created_at, payload FROM runs
                WHERE entry_id='summary' AND run_id > $after
                ORDER BY run_id LIMIT 1000;
            """, {'$after': after})[0].rows
            try:
                for row in rows:
                    data = decode(bytes(row.payload))
                    if data.get('result_pr'):
                        summaries.append((row.created_at, row.run_id, data))
            except Exception as exc:
                raise StorageError(f'Invalid YDB run lookup: {exc}') from exc
            if len(rows) < 1000:
                break
            after = rows[-1].run_id
        # A later verify on the result PR cannot reset its original PR quota.
        # Association metadata has no TTL, unlike model/document context.
        originals = {d['source_pr'] for _, _, d in summaries
                     if d['source_pr'] != d['result_pr']
                     and pr in (d['source_pr'], d['result_pr'])}
        if len(originals) > 1:
            raise StorageError('Ambiguous original PR association')
        original = next(iter(originals), pr)
        related = {original, pr} | {d['result_pr'] for _, _, d in summaries
                                    if d['source_pr'] == original}
        matching = [(created, run_id) for created, run_id, d in summaries
                    if (d['source_pr'] in related or d['result_pr'] in related)
                    and (pr == original or pr in (d['source_pr'], d['result_pr']))]
        if not matching:
            raise ContextExpired()
        context = self.context(max(matching)[1])
        return {**context, 'original_pr': original}

    def claim_continuation(self, source_pr: str, run_id: str) -> int:
        """Atomic max-three admission in runs (no TTL), idempotent SDK retry.

        The summary key excludes this metadata from daily financial accounting.
        A failed admitted run consumes its slot, regardless of model/outcome.
        """
        rows = self._execute("""
            DECLARE $key AS Utf8; DECLARE $claim AS String;
            DECLARE $created AS Timestamp;
            $previous = SELECT status, payload FROM runs
                        WHERE run_id=$key AND entry_id='summary';
            $count = COALESCE((SELECT CAST(status AS Uint32) FROM $previous), 0u);
            $same = COALESCE((SELECT payload = $claim FROM $previous), false);
            $next = IF($same, $count, $count + 1u);
            UPSERT INTO runs (run_id, entry_id, status, payload, created_at)
            SELECT $key AS run_id, 'summary' AS entry_id, CAST($next AS Utf8) AS status,
                   $claim AS payload, $created AS created_at WHERE $next <= 3u;
            SELECT $next AS continuation_count;
        """, {'$key': 'continuations/' + source_pr, '$claim': encode({'claim': run_id}),
              '$created': int(utc(self.clock()).timestamp() * 1_000_000)})[0].rows
        count = int(rows[0].continuation_count)
        if count > 3:
            raise ValueError('Достигнут максимум: три продолжения на исходный PR.')
        return count

    def daily_cost(self) -> Decimal | None:
        day = utc(self.clock()).astimezone(MOSCOW).date().isoformat()
        after_run, after_entry = '', ''
        values = []
        while True:
            rows = self._execute('''
                DECLARE $day AS Utf8;
                DECLARE $after_run AS Utf8; DECLARE $after_entry AS Utf8;
                SELECT run_id, entry_id, cost_rub FROM runs
                WHERE day=$day AND entry_id != 'summary'
                AND (run_id, entry_id) > ($after_run, $after_entry)
                ORDER BY run_id, entry_id LIMIT 1000;
            ''', {'$day': day,
                  '$after_run': after_run, '$after_entry': after_entry})[0].rows
            try:
                values.extend(None if row.cost_rub is None else money(Decimal(row.cost_rub))
                              for row in rows)
            except Exception as exc:
                raise StorageError(f'Invalid YDB billing: {exc}') from exc
            if len(rows) < 1000:
                return None if None in values else sum(values, Decimal(0))
            after_run, after_entry = rows[-1].run_id, rows[-1].entry_id



class _RecordedClient(ModelClient):
    """Fill only missing outcomes on interruption/unexpected transport exceptions.

    No retry/fallback policy here. An interrupted request has unknown billing and
    no invented response. Append before persistence so runner retains the attempt
    even when YDB itself fails. Ordinary T05 attempts/callbacks remain unchanged.
    """
    def __init__(self, adapter, **kwargs):
        self.adapter = adapter
        super().__init__(record_request=adapter.record_request,
                         record_attempt=adapter.record_attempt, **kwargs)

    def chat(self, *args, **kwargs):
        previous = set(self.adapter._requests)
        try:
            return super().chat(*args, **kwargs)
        except (Exception, KeyboardInterrupt) as exc:
            recorded = {self.adapter._id(a.request) for a in self.attempts}
            for key, request in self.adapter._requests.items():
                if key not in previous and key not in recorded and key in self.adapter._ready:
                    attempt = AttemptRecord(request, None, None,
                                            f'{type(exc).__name__}: outcome unavailable', Usage())
                    self.attempts.append(attempt)
                    try:
                        self.record_attempt(attempt)
                    except Exception as recording_error:
                        if isinstance(exc, KeyboardInterrupt):
                            raise KeyboardInterrupt(f'Cancelled; storage: {recording_error}') from recording_error
                        raise
            raise


class RunStore:
    """One adapter per invocation; caller retains run_id for continue lookup.

    source_pr and continuation_count are explicit caller metadata, not inferred
    lineage. T13 enforces the continuation count/SHA policy. context() returns
    plain data with bytes and Decimal decoded; no historic schema compatibility.
    """
    def __init__(self, store: YDBStore, *, mode: str, source_pr: str,
                 run_id: str | None = None, continuation_count: int = 0):
        if mode not in ('doc_translate', 'doc_verify', 'doc_continue'):
            raise ValueError('Unsupported mode')
        self.store, self.mode, self.source_pr = store, mode, source_pr
        self.run_id = run_id or uuid4().hex
        self.continuation_count = continuation_count
        self.created = utc(store.clock())
        self._admission = None
        self._requests = {}
        self._ready = set()
        self._attempts = {}

    def admit(self, limit: Decimal):
        if self._admission is not None:
            if isinstance(self._admission, Exception):
                raise self._admission
            return
        money(limit)
        if limit is None:
            raise ValueError('Daily budget is required')
        try:
            spent = self.store.daily_cost()
            if spent is None:
                raise BudgetUnknown('Невозможно проверить дневной бюджет: стоимость части '
                                    'обращений неизвестна; требуется достоверный RUB billing.')
            if spent >= limit:
                raise BudgetExceeded(f'Запуск отложен: сегодня потрачено {spent} ₽, дневной лимит — '
                                     f'{limit} ₽ (YDBDOC_DAILY_BUDGET_RUB). '
                                     'Повторите запуск завтра или увеличьте лимит')
        except Exception as exc:
            self._admission = exc
            raise
        self._admission = True

    @staticmethod
    def _id(request):
        return f'{request.id}/{request.attempt}'

    def record_request(self, request: RequestRecord):
        key = self._id(request)
        self._requests[key] = request
        self.store.put(self.run_id, f'request/{key}', encode(request))
        self.store.ledger(self.run_id, key, created=request.created_at, mode=self.mode,
                          status='pending', operation=request.operation)
        self._ready.add(key)

    def record_attempt(self, attempt: AttemptRecord):
        key = self._id(attempt.request)
        self._attempts[key] = attempt  # retain even if a write fails
        # Ledger first: a context failure must not lose known paid usage.
        self.store.ledger(self.run_id, key, created=attempt.request.created_at, mode=self.mode,
                          status='failed' if attempt.error else 'completed',
                          operation=attempt.request.operation, cost=attempt.usage.cost_rub,
                          payload=encode(attempt.usage))
        self.store.put(self.run_id, f'attempt/{key}', encode(attempt))

    def model_factory(self, **kwargs) -> ModelClient:
        return _RecordedClient(self, **kwargs)

    def file_progress(self, result):
        self.store.put(self.run_id, f'file/{result.path}', encode(result))

    def candidate_progress(self, candidate):
        self.store.put(self.run_id, 'candidate', encode({'sha': candidate.sha}))

    def save(self, result, *, known_files=None):
        # Reconcile callback failures from runner's in-memory transport records.
        for attempt in result.attempts:
            self._attempts[self._id(attempt.request)] = attempt
            self._requests[self._id(attempt.request)] = attempt.request
        for attempt in self._attempts.values():
            self.record_attempt(attempt)
        unresolved = [AttemptRecord(request, None, None, 'Outcome unknown', Usage())
                      for key, request in self._requests.items()
                      if key in self._ready and key not in self._attempts]
        costs = cost_breakdown((*self._attempts.values(), *unresolved))
        self.store.ledger(self.run_id, 'summary', created=self.created, mode=self.mode,
                          status=result.status, cost=costs['total'], payload=encode({
                              'cost_breakdown': costs, 'cancelled': result.cancelled,
                              'errors': result.errors, 'source_pr': self.source_pr,
                              'result_pr': (f'{result.publication.repository}/{result.publication.pr_number}'
                                            if result.result_sha else None)}))
        for key, request in self._requests.items():
            self.store.put(self.run_id, f'request/{key}', encode(request))
        # Final candidate bytes, not initial FileResult text. Only selected/changed
        # paths are context; never copy an entire repository into YDB.
        paths = {f.path for f in result.files} | {f.path for f in result.selected_files}
        if result.plan:
            paths.update(path for op in result.plan.operations
                         for path in (op.target_old_path, op.target_new_path) if path)
        if result.candidate and result.snapshot:
            from ydbdoc_review.links import Candidate
            original = Candidate.open(result.candidate.repo, result.snapshot.source_sha)
            paths.update(path for path in original.entries.keys() | result.candidate.entries.keys()
                         if original.entries.get(path) != result.candidate.entries.get(path))
        final_files = ({path: result.candidate.read(path) for path in sorted(paths)}
                       if result.candidate else {})
        self.store.put(self.run_id, 'context', encode({
            'run_id': self.run_id, 'source_pr': self.source_pr,
            'continuation_count': self.continuation_count,
            'source_sha': (result.result_sha if result.publication and result.result_sha
                           and self.source_pr == f'{result.publication.repository}/{result.publication.pr_number}'
                           else result.snapshot.source_sha if result.snapshot else None),
            'known_files': tuple(known_files) if known_files is not None else result.selected_files,
            'result_sha': result.result_sha, 'candidate_sha': result.candidate_sha,
            'result': result, 'final_files': final_files, 'cost_breakdown': costs,
            'requests': tuple(self._requests.values()),
            'attempts': tuple(self._attempts.values())}))

    def hooks(self, *, report=None, cancelled=None, secrets=()):
        from ydbdoc_review.runner import RunHooks
        return RunHooks(file_progress=self.file_progress,
                        candidate_progress=self.candidate_progress, save=self.save,
                        report=report, cancelled=cancelled, secrets=secrets)


def create_store(**driver_options) -> YDBStore:
    """Production factory: YDB only; missing credentials/SDK is an error."""
    try:
        return YDBStore(make_ydb_driver(**driver_options))
    except Exception as exc:
        raise StorageError(f'YDB connection: {exc}') from exc
