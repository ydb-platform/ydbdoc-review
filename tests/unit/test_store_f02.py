"""Canonical calls and metadata-only finalization use real store SQL boundaries."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.unit.test_store_t12 import adapter, db, paid  # noqa: F401
from ydbdoc_review.document import ChunkResult, assemble_file, chunk_document, protect
from ydbdoc_review.quality_loop import SelectedFile
from ydbdoc_review.runner import RunResult
from ydbdoc_review.store import ContextExpired, StorageError, decode


def writes(boundary):
    return [p for q, p in boundary.calls if 'UPSERT INTO run_objects' in q]


def test_repeated_finalization_and_status_do_not_rewrite_content(db):  # noqa: F811
    store, boundary, _ = db
    run = adapter(store)
    attempt = paid()
    run.record_request(attempt.request)
    run.record_attempt(attempt)
    result = RunResult(attempts=(attempt,), status='GREEN')
    run.save(result)
    previous = deepcopy(boundary.objects)
    boundary.calls.clear()
    run.save(result)
    assert writes(boundary) == []
    assert boundary.objects == previous
    run.save_status(replace(result, status='RED', errors=('Report rejected',)))
    assert {p['object_key'] for p in writes(boundary)} == {'context'}
    assert store.context(run.run_id)['result']['errors'] == ['Report rejected']
    assert store.daily_cost() == attempt.usage.cost_rub
    assert len([p for p in boundary.runs.values() if p['entry_id'] != 'summary']) == 1


def test_canonical_failed_paid_call_survives_replayed_request_and_new_adapter(db):  # noqa: F811
    store, boundary, _ = db
    run = adapter(store)
    attempt = paid()
    run.record_request(attempt.request)
    run.record_attempt(attempt)
    before = deepcopy(boundary.objects)
    replay = adapter(store, run_id=run.run_id)
    boundary.calls.clear()
    replay.record_request(attempt.request)
    replay.record_attempt(attempt)
    assert writes(boundary) == []
    assert boundary.objects == before
    assert store.daily_cost() == attempt.usage.cost_rub
    saved = decode(store.get(run.run_id, 'attempt/req/0'))
    assert 'request' not in saved
    assert saved['request_ref'] == 'request/req/0'
    assert saved['response_text'] == attempt.response_text
    assert decode(store.get(run.run_id, saved['request_ref']))['payload'] == attempt.request.payload


def test_failed_manifest_retry_has_one_generation_and_retains_paid_ledger(db):  # noqa: F811
    store, boundary, _ = db
    run = adapter(store)
    attempt = paid()
    run.record_request(attempt.request)
    boundary.fail = lambda q, p: ('UPSERT INTO run_objects' in q
                                 and p['object_key'].startswith('attempt/') and not p['generation'])
    with pytest.raises(StorageError):
        run.record_attempt(attempt)
    assert store.daily_cost() == attempt.usage.cost_rub
    boundary.fail = lambda q, p: False
    run.save(RunResult(attempts=(attempt,)))
    generations = {k[2] for k in boundary.objects if k[1] == 'attempt/req/0' and k[2]}
    assert len(generations) == 1
    assert store.context(run.run_id)['cost_breakdown']['total'] == attempt.usage.cost_rub


def test_status_does_not_extend_document_ttl_or_read_documents(db):  # noqa: F811
    store, boundary, now = db
    run = adapter(store)
    run.save(RunResult())
    now[0] += timedelta(days=13)
    boundary.calls.clear()
    run.save_status(RunResult(status='RED', errors=('late metadata',)))
    assert all(p.get('object_key', 'context') == 'context' for _, p in boundary.calls)
    now[0] += timedelta(days=1)
    with pytest.raises(ContextExpired):
        store.context(run.run_id)


def test_compact_sources_roundtrip_preserves_partial_chunk_map(db):  # noqa: F811
    store, _, _ = db
    run = adapter(store)
    source = '# Title\n\nFirst sentence.\n\nSecond sentence.\n'
    document = protect(source, path='a.md')
    chunks = chunk_document(document, lambda text: len(text) < 30)
    parts = tuple(ChunkResult(chunk, None, None, unfinished=True, status='missing') for chunk in chunks)
    initial = assemble_file('a.md', document, parts)
    run.save(RunResult(selected_files=(SelectedFile('a.md', source, 'en', initial=initial),)))
    manifest = decode(store.get(run.run_id, 'context'))
    raw = decode(store.get(run.run_id, manifest['known_files_ref']))
    assert 'source' not in raw[0]['initial']['protected']
    assert all('text' not in part['chunk'] for part in raw[0]['initial']['chunks'])
    from ydbdoc_review.document import file_result_from_dict
    restored = store.context(run.run_id)['known_files'][0]['initial']
    assert file_result_from_dict(restored) == initial


def test_pending_completed_and_metadata_retry_keep_cost(db):  # noqa: F811
    store, boundary, _ = db
    run = adapter(store)
    attempt = replace(paid(), error=None, status_code=200)
    run.record_request(attempt.request)
    assert boundary.runs[run.run_id, 'req/0']['status'] == 'pending'
    run.record_attempt(attempt)
    assert boundary.runs[run.run_id, 'req/0']['status'] == 'completed'
    run.save(RunResult(attempts=(attempt,)))
    boundary.fail = lambda q, p: ('UPSERT INTO run_objects' in q
                                 and p['object_key'] == 'context' and not p['generation'])
    with pytest.raises(StorageError):
        run.save_status(RunResult(status='RED', errors=('report',)))
    boundary.fail = lambda q, p: False
    boundary.calls.clear()
    run.save_status(RunResult(status='RED', errors=('report',)))
    assert {p['object_key'] for p in writes(boundary)} == {'context'}
    assert store.daily_cost() == attempt.usage.cost_rub


def test_progress_and_context_reference_only_canonical_model_answer(db):  # noqa: F811
    import json

    from tests.unit.test_document_f06 import SOURCE, translate
    from ydbdoc_review.document import file_result_from_dict

    store, boundary, _ = db
    run = adapter(store)
    original, _ = translate((0, 1, 3))
    response = original.chunks[2].response
    attempt = replace(paid(), response_text=json.dumps({'choices': [{'message': {'content': response}}]}),
                      error=None, status_code=200)
    run.record_attempt(attempt)
    run.file_progress(original)
    run.save(RunResult(attempts=(attempt,), files=(original,),
                       selected_files=(SelectedFile(original.path, SOURCE, 'en', initial=original),)))
    context = decode(store.get(run.run_id, 'context'))
    progress = decode(store.get(run.run_id, 'file/' + original.path))
    known = decode(store.get(run.run_id, context['known_files_ref']))
    canonical = decode(store.get(run.run_id, 'attempt/req/0'))
    assert json.loads(canonical['response_text'])['choices'][0]['message']['content'] == response
    for data in (progress, known[0]['initial']):
        chunk = data['chunks'][2]
        assert 'response' not in chunk
        assert chunk['response_content_ref']['key'] == 'attempt/req/0'
    assert file_result_from_dict(store.hydrate_file(run.run_id, progress)) == original
    restored = file_result_from_dict(store.context(run.run_id)['known_files'][0]['initial'])
    assert restored == original
    # Continuing in another run retains canonical locations and missing slots.
    continuation = adapter(store)
    continuation.save(RunResult(selected_files=(SelectedFile(original.path, SOURCE, 'en', initial=restored),)))
    assert file_result_from_dict(store.context(continuation.run_id)['known_files'][0]['initial']) == original
    assert not [key for rid, key, _, _ in boundary.objects
                if rid == continuation.run_id and key.startswith('response/')]


def test_explicit_attempt_reference_selects_actual_fallback(db):  # noqa: F811
    import json

    from tests.unit.test_document_f06 import translate

    store, _, _ = db
    run = adapter(store)
    original, _ = translate((0, 1, 3))
    chunk = original.chunks[2]
    for index in (0, 1):
        attempt = replace(paid(attempt=index),
                          response_text=json.dumps({'choices': [{'message': {'content': chunk.response}}]}))
        run.record_attempt(attempt)
    chunks = list(original.chunks)
    chunks[2] = replace(chunk, response_ref='attempt/req/1')
    original = replace(original, chunks=tuple(chunks))
    run.file_progress(original)
    saved = decode(store.get(run.run_id, 'file/' + original.path))
    assert saved['chunks'][2]['response_content_ref']['key'] == 'attempt/req/1'
    assert saved['chunks'][2]['response_ref'] == 'attempt/req/1'
