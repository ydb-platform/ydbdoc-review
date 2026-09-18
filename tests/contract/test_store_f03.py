"""Bounded object reads using real SQL and SDK conversion, offline only.

The execute boundary rejects results over the production 48 MiB cap. SQLite
checks predicates/LIMIT, but does not establish live YQL/server compatibility.
"""
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import ydb

from tests.contract.test_t12_recheck import PersistentSQL
from ydbdoc_review.store import (
    CHUNK_SIZE,
    OBJECT_PAGE_PARTS,
    ContextExpired,
    StorageError,
    YDBStore,
    encode,
)


class CappedSQL(PersistentSQL):
    def __init__(self):
        super().__init__(':memory:')
        self.pages = []
        self.alter = lambda p, rows: rows
        self.truncate_at = None

    def execute(self, sql, params, commit_tx):
        result = super().execute(sql, params, commit_tx)
        if 'SELECT part_no, payload FROM run_objects' in sql:
            rows = result[0].rows
            size = sum(len(row.payload) + 64 for row in rows)
            if size > 48 * 1024 * 1024:
                raise RuntimeError('YDB result exceeds 50331648 bytes')
            self.pages.append((sql, params.copy(), size, len(rows)))
            result[0].rows = self.alter(params, rows)
            result[0].truncated = (self.truncate_at is not None
                                   and params.get('$start') == self.truncate_at)
        return result


@pytest.fixture
def db(monkeypatch):
    sql = CappedSQL()
    monkeypatch.setattr(ydb, 'SessionPool', lambda driver: sql)
    now = [datetime(2026, 9, 18, tzinfo=UTC)]
    store = YDBStore(object(), clock=lambda: now[0])
    yield store, sql, now
    sql.connection.close()


def test_over_50_mib_reconstructs_exact_bytes_under_hard_cap(db):
    store, sql, _ = db
    data = bytes(range(256)) * (51 * 4096) + b'final partial part'
    assert len(data) > 50 * 1024 * 1024
    store.put('r', 'large', data)
    sql.pages.clear()
    assert store.get('r', 'large') == data
    assert len(sql.pages) > 6
    for query, params, size, count in sql.pages:
        assert 'AND part_no >= $start' in query
        assert 'ORDER BY part_no LIMIT $limit' in query
        assert 0 < params['$limit'] <= OBJECT_PAGE_PARTS
        assert count <= params['$limit']
        assert size < 9 * 1024 * 1024
    sql.pages.clear()
    store.put('r', 'large', data)
    assert len(sql.pages) == 1  # unchanged content reads only the tiny manifest
    assert sql.pages[0][1]['$generation'] == ''
    assert sql.pages[0][1]['$limit'] == 2


@pytest.mark.parametrize('parts', [1, 15, 16, 17, 32, 33])
def test_page_boundaries_and_surplus_part_detection(db, parts):
    store, _, now = db
    data = b'x' * (parts * CHUNK_SIZE)
    store.put('r', 'object', data)
    assert store.get('r', 'object') == data
    store._part('r', 'object', hashlib.sha256(data).hexdigest(), parts, now[0], b'extra')
    with pytest.raises(StorageError, match='chunks'):
        store.get('r', 'object')


@pytest.mark.parametrize('fault', ['missing', 'reordered', 'duplicate', 'short', 'truncated', 'offline', 'bytes'])
def test_later_page_failure_never_returns_partial_or_expired(db, fault):
    store, sql, _ = db
    store.put('r', 'context', b'x' * (34 * CHUNK_SIZE))
    if fault == 'truncated':
        sql.truncate_at = 16
    elif fault == 'offline':
        sql.fail = lambda q, p: p.get('start') == 16
    else:
        def alter(p, rows):
            if p.get('$start') != 16:
                return rows
            if fault == 'missing':
                return []
            if fault == 'reordered':
                return rows[::-1]
            if fault == 'duplicate':
                return [rows[0], *rows[:-1]]
            if fault == 'short':
                return rows[:-1]
            return [SimpleNamespace(part_no=r.part_no, payload=b'y' * len(r.payload)) for r in rows]
        sql.alter = alter
    with pytest.raises(StorageError):
        store.context('r')


@pytest.mark.parametrize('version', [None, 0, 2, '1', True])
def test_unsupported_context_schema_is_explicit_and_read_only(db, version):
    store, sql, _ = db
    store.put('r', 'context', encode({} if version is None else {'schema_version': version}))
    before = sql.connection.total_changes
    with pytest.raises(StorageError, match='Unsupported context schema'):
        store.context('r')
    assert sql.connection.total_changes == before


def test_context_hydrates_f02_references_over_multiple_pages(db):
    store, _, now = db
    text = 'translated ' * (CHUNK_SIZE * 2)
    store.put('r', 'answer', encode(text))
    initial = {'chunks': [{'response_content_ref': {'key': 'answer', 'run_id': 'r',
                'sha256': hashlib.sha256(encode(text)).hexdigest()}}]}
    store.put('r', 'known', encode([{'path': 'en.md', 'source': 'source', 'initial': initial}]))
    store.put('r', 'final', encode({'en.md': text.encode()}))
    store.put('r', 'context', encode({'schema_version': 1, 'known_files_ref': 'known',
                                    'final_files_ref': 'final'}))
    context = store.context('r')
    assert context['known_files'][0]['initial']['chunks'][0]['response'] == text
    assert context['final_files']['en.md'] == text.encode()
    now[0] += timedelta(days=14)
    with pytest.raises(ContextExpired, match='14'):
        store.context('r')


@pytest.mark.parametrize('field,value', [('count', 0), ('count', -1), ('count', True),
                                         ('count', 2**32), ('size', -1), ('size', 999),
                                         ('generation', ''), ('sha256', 'bad')])
def test_malformed_manifest_stops_before_data_queries(db, field, value):
    store, sql, now = db
    payload = b'payload'
    digest = hashlib.sha256(payload).hexdigest()
    manifest = dict(generation=digest, count=1, size=len(payload), sha256=digest,
                    created_at=now[0])
    manifest[field] = value
    store._part('r', 'object', '', 0, now[0], encode(manifest))
    with pytest.raises(StorageError):
        store.get('r', 'object')
    # A plausible but wrong size is checked against the first data page.
    assert len(sql.pages) <= 2
