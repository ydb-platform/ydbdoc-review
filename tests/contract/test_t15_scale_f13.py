"""Cross-mode CLI scale regression; no provider, GitHub, YDB or YFM live calls.

Real Git/CLI/transport/quality/links/report/store with explicit external adapters.
Measurements are fixture measurements, never production latency or price claims.
"""
# ruff: noqa: F811 -- imported pytest fixture
import json
import os
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

from tests.contract.test_t15_cli import assert_saved, process  # noqa: F401
from ydbdoc_review.store import encode

ROOT = Path(__file__).resolve().parents[2]
MODES = ('doc_translate', 'doc_verify', 'doc_continue')
TREE_FILES = 12000
ANCHORS = 8195


def prepare(p, mode):
    for lang in ('ru', 'en'):
        (p.repo / f'ydb/docs/{lang}/b.md').write_text('# Hello\n\nSecond document.\n')
    if mode != 'doc_verify':
        for name in ('a', 'b'):
            (p.repo / f'ydb/docs/en/{name}.md').unlink()
    # Thousands of anchors in an unselected document exercise whole-tree checks.
    (p.repo / 'ydb/docs/index.md').write_text('\n\n'.join(
        f'[Anchor {i}](en/a.md#hello)' for i in range(ANCHORS)) + '\n')
    p.git('add', '.')
    blob = p.git('rev-parse', ':ydb/docs/ru/a.md').decode().strip()
    entries = ''.join(f'100644 {blob}\tunrelated/{i:06}.txt\n' for i in range(TREE_FILES))
    subprocess.run(['git', 'update-index', '--index-info'], cwd=p.repo,
                   input=entries, text=True, check=True, capture_output=True)
    p.git('commit', '-m', 'two selected documents in a large tree')
    p.git('push', str(p.remote), 'HEAD:refs/heads/topic')
    p.update(changes=[dict(filename=f'ydb/docs/ru/{name}.md', status='modified')
                      for name in ('a', 'b')])


def invoke(p, mode, pr=1):
    start = time.monotonic()
    result = subprocess.run([sys.executable, '-m', 'tests.contract.f13_scale_boundary',
                             mode, '--repo', 'up/docs', '--pr', str(pr), '--config',
                             str(p.root / 'models.json')], env=p.env, cwd=ROOT,
                            text=True, capture_output=True, timeout=110)
    return result, time.monotonic() - start


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('failed_build', [False, True], ids=['green', 'failed-build'])
def test_two_documents_large_tree_thousands_anchors(process, mode, failed_build, record_property):
    p = process
    prepare(p, mode)
    if mode == 'doc_continue':
        seed, _ = invoke(p, 'doc_translate')
        assert seed.returncode == 0, seed.stdout + seed.stderr
        p.update(model=[], comments=[], builds=[], http=[], tree_sizes=[])
    p.update(scale_build_failure=failed_build)
    outcome, elapsed = invoke(p, mode, 2 if mode == 'doc_continue' else 1)
    status = 'RED' if failed_build else 'GREEN'
    assert outcome.returncode == int(failed_build), outcome.stdout + outcome.stderr
    context, attempts = assert_saved(p, mode, status)
    state = p.read()
    assert min(state['tree_sizes']) >= TREE_FILES + 4
    assert len(state['builds']) == len(set(state['builds']))
    assert len(context['known_files']) == 2
    assert set(context['final_files']) == {'ydb/docs/en/a.md', 'ydb/docs/en/b.md'}
    assert all(context['final_files'].values())
    serialized = encode(context)
    assert b'unrelated/' not in serialized
    assert b'fixture warning 8893' not in serialized  # full logs belong in diagnostics
    assert len(attempts) == len(state['model'])
    assert context['result']['cost_breakdown']['total'] == Decimal('.25') * len(attempts)
    if mode != 'doc_translate':
        assert all(role != 'translation' for role, _ in state['model'])
    assert state['comments']
    for _, body in state['comments']:
        assert len(body.encode('utf-8')) < 65536
        assert status in body and 'Итого:' in body
    assert context['result']['checked_sha'] == state['builds'][-1]
    if failed_build:
        assert state['artifact_uploaded']
        artifact = json.loads((p.root / state['artifact_paths'][-1]).read_text())
        last = artifact['rounds'][-1]
        assert len(last['links']['unchecked_anchors']) == ANCHORS
        assert 'fixture warning 8893' in last['build']['log']
        assert 'simulated documentation build failure' in last['build']['log']
        assert len(artifact['issues']) < 20  # dependent anchors are grouped
        assert any('diagnostics.json' in body for _, body in state['comments'])
    with sqlite3.connect(p.root / 'database.sqlite') as database:
        stored_bytes = database.execute(
            'SELECT SUM(length(payload)) FROM run_objects WHERE run_id=?',
            (context['run_id'],)).fetchone()[0]
    metrics = dict(mode=mode, failed_build=failed_build,
                   tested_revision=subprocess.check_output(
                       ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), mock_boundaries=['model', 'GitHub', 'YDB', 'YFM'],
                   tree_entries=max(state['tree_sizes']), selected_documents=2,
                   anchor_references=ANCHORS, git_subprocess_calls=state['git_subprocess_calls'],
                   git_subprocess_seconds=state['git_subprocess_seconds'], hydrated_context_bytes=len(serialized), stored_run_object_bytes=stored_bytes,
                   model_calls=len(state['model']), cost_rub=str(context['result']['cost_breakdown']['total']),
                   elapsed_seconds=round(elapsed, 3))
    measured_dir = Path(os.environ.get('F13_MEASUREMENTS_DIR', p.root))
    measured_dir.mkdir(parents=True, exist_ok=True)
    (measured_dir / f'f13-{mode}-{status}.json').write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + '\n')
    record_property('f13_measurements', json.dumps(metrics))
    print('F13_MEASUREMENTS ' + json.dumps(metrics, sort_keys=True))


@pytest.mark.parametrize('mode', MODES)
def test_reasoning_only_cross_mode_keeps_cost_and_red(process, mode):
    p = process
    prepare(p, mode)
    if mode == 'doc_continue':
        seed, _ = invoke(p, 'doc_translate')
        assert seed.returncode == 0, seed.stdout + seed.stderr
        p.update(model=[], comments=[], builds=[], http=[])
    p.update(reasoning_only=True)
    outcome, _ = invoke(p, mode, 2 if mode == 'doc_continue' else 1)
    assert outcome.returncode == 1, outcome.stdout + outcome.stderr
    context, attempts = assert_saved(p, mode, 'RED')
    assert attempts and len(attempts) == len(p.read()['model'])
    assert context['result']['cost_breakdown']['total'] == Decimal('.25') * len(attempts)
    assert all(b'F13_REASONING_MUST_NOT_BECOME_TRANSLATION' not in value
               for value in context['final_files'].values() if value is not None)
    assert p.read()['comments']
    assert all('RED' in body and 'Итого:' in body for _, body in p.read()['comments'])


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('failure', ['scale_store_failure', 'scale_report_failure'])
def test_finalization_failure_large_tree_no_paid_replay(process, mode, failure):
    p = process
    prepare(p, mode)
    if mode == 'doc_continue':
        seed, _ = invoke(p, 'doc_translate')
        assert seed.returncode == 0, seed.stdout + seed.stderr
        p.update(model=[], comments=[], builds=[], http=[], document_writes=0)
    p.update(**{failure: True})
    outcome, _ = invoke(p, mode, 2 if mode == 'doc_continue' else 1)
    assert outcome.returncode == 1, outcome.stdout + outcome.stderr
    state = p.read()
    expected_calls = {'doc_translate': 4, 'doc_verify': 2, 'doc_continue': 3}[mode]
    assert len(state['model']) == expected_calls
    rows = [row for row in p.rows() if row['mode'] == mode and row['entry_id'] != 'summary']
    assert len(rows) == expected_calls
    assert sum(Decimal(row['cost_rub']) for row in rows) == Decimal('.25') * expected_calls
    if failure == 'scale_store_failure':
        assert state['document_writes'] == 1
        assert state['comments']
        assert all('RED' in body and 'Итого:' in body for _, body in state['comments'])
        assert any('продолжен' in body.lower() or 'doc_continue' in body for _, body in state['comments'])
    else:
        context, attempts = assert_saved(p, mode, 'RED')
        assert len(attempts) == expected_calls
        assert context['result']['errors']
        if mode != 'doc_verify':
            assert any(number == 2 and 'RED' in body and 'Итого:' in body
                       for number, body in state['comments'])
