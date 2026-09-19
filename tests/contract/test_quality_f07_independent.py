# ruff: noqa: F811 -- imported pytest fixture
"""Independent acceptance of durable local repairs; no external services."""
import json
from types import SimpleNamespace

import pytest

from tests.unit.test_store_t12 import adapter, db  # noqa: F401 -- imported pytest fixture
from ydbdoc_review.continuation import select_files
from ydbdoc_review.document import (
    ChunkResult,
    RequestBudget,
    assemble_file,
    make_chunk,
    protect,
    restore,
)
from ydbdoc_review.model import Endpoint, ModelChoice
from ydbdoc_review.quality import Issue, Location, review_parts
from ydbdoc_review.quality_loop import SelectedFile, repair_document
from ydbdoc_review.runner import RunResult

PATH = 'ydb/docs/en/independent.md'
CHOICE = ModelChoice(Endpoint('eliza', 'https://offline.invalid', 'mock', 'dummy'))
BUDGET = RequestBudget(100000, 10000, lambda m: len(str(m)))

class Client:

    def __init__(self, failure=None):
        self.calls = []
        self.attempts = []
        self.failure = failure

    def chat(self, messages, **kwargs):
        p = json.loads(messages[-1]['content'])
        self.calls.append(p)
        if p['chunk_id'] == self.failure:
            return SimpleNamespace(content='', finish_reason='length')
        return SimpleNamespace(content=p['source'], finish_reason='stop')

def mapping(source, missing=(), scalar=False):
    doc = protect(source, path=PATH)
    ends = list(doc.boundaries) if scalar else [p for p in doc.boundaries if doc.text[:p].endswith('⟧')] + [len(doc.text)]
    ends = sorted(set(ends))
    slots = []
    start = 0
    for end in ends:
        c = make_chunk(doc, len(slots), start, end)
        absent = c.index in missing
        slots.append(ChunkResult(c, None if absent else c.text, None if absent else restore(doc, c.text, expected=c.text), unfinished=absent, status='missing' if absent else 'complete'))
        start = end
    return assemble_file(PATH, doc, tuple(slots))

def repair(initial, client, findings=()):
    return repair_document(SelectedFile(PATH, initial.protected.source, 'en', initial=initial), initial.text or '', findings, replacements={}, client=client, choice=CHOICE, budget=BUDGET)

@pytest.mark.parametrize('missing', [(0,), (1,), (3,), (0, 1, 3)])
def test_f11_store_roundtrip_final_partial_map_and_continue(db, missing):
    source = 'Zero statement.\n\nOne statement.\n\nTwo statement.\n\nThree statement.'
    initial = mapping(source, missing)
    assert len(initial.chunks) == 4
    failed = missing[-1]
    client = Client(initial.chunks[failed].chunk.chunk_id)
    partial = repair(initial, client)
    assert not partial.complete
    assert [p['chunk_id'] for p in client.calls] == [initial.chunks[i].chunk.chunk_id for i in missing]
    store, _boundary, _ = db
    run = adapter(store)
    candidate = SimpleNamespace(sha='a' * 40, read=lambda path: partial.text.encode())
    result = RunResult(status='RED', files=(initial,), selected_files=(SelectedFile(PATH, source, 'en', initial=initial),), quality=SimpleNamespace(files=(partial.file_result,)), candidate=candidate, unfinished_files=(PATH,))
    run.save(result)
    context = store.context(run.run_id)
    selected = select_files(context, 'Complete remaining text')
    assert len(selected) == 1 and selected[0].initial == partial.file_result
    assert context['final_files'][PATH] == partial.text.encode()
    resumed = Client()
    final = repair_document(selected[0], partial.text, selected[0].requested_findings, replacements={}, client=resumed, choice=CHOICE, budget=BUDGET)
    assert final.complete and final.text == source
    assert [p['chunk_id'] for p in resumed.calls] == [initial.chunks[failed].chunk.chunk_id]
    assert [s.chunk for s in final.file_result.chunks] == [s.chunk for s in initial.chunks]
    for index in set(range(4)) - {failed}:
        assert final.file_result.chunks[index] == partial.file_result.chunks[index]
    assert review_parts(source, final.text, fits=lambda p: True, correspondence=final.file_result)

@pytest.mark.parametrize('where', ['target', 'source'])
def test_quote_localization_only_one_paragraph(where):
    initial = mapping('First.\n\nSecond.\n\nThird.\n\nFourth.')
    client = Client()
    issue = Issue(PATH, 'Incorrect meaning', 'Fix Second', **{where: Location(3, 3, 'Second.')})
    result = repair(initial, client, (issue,))
    assert result.complete
    assert [p['chunk_id'] for p in client.calls] == [initial.chunks[1].chunk.chunk_id]

@pytest.mark.parametrize('where', ['target', 'source'])
def test_scalar_quote_localization_does_not_repair_neighbour_fragments(where):
    source = '---\ntitle: "Alpha. Beta. Gamma."\n---\n\nBody.'
    initial = mapping(source, scalar=True)
    bad = [s for s in initial.chunks if 'Beta.' in s.chunk.text]
    assert len(bad) == 1
    client = Client()
    issue = Issue(PATH, 'Incorrect Beta meaning', 'Fix Beta only', **{where: Location(2, 2, 'Beta.')})
    result = repair(initial, client, (issue,))
    assert result.complete
    assert [p['chunk_id'] for p in client.calls] == [bad[0].chunk.chunk_id], [(p['chunk_id'], p['source']) for p in client.calls]

@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('side', ['source', 'target'])
@pytest.mark.parametrize('quote,selected', [('Beta. Gamma.', ('Beta.', 'Gamma.')), ('Alpha. Beta.', ('Alpha.', 'Beta.'))])
def test_quote_crossing_fragment_boundary_selects_only_overlaps(newline, side, quote, selected):
    source = newline.join(['---', 'title: "Alpha. Beta. Gamma."', '---', '', 'Body.'])
    initial = mapping(source, scalar=True)
    client = Client()
    issue = Issue(PATH, 'Incorrect meaning', 'Repair precisely quoted span', **{side: Location(2, 2, quote)})
    result = repair(initial, client, (issue,))
    expected = [s.chunk.chunk_id for s in initial.chunks if any(q in s.chunk.text for q in selected)]
    assert len(expected) == 2
    assert [p['chunk_id'] for p in client.calls] == expected
    assert result.complete and result.text == source
    assert [s.chunk for s in result.file_result.chunks] == [s.chunk for s in initial.chunks]
    for old, new in zip(initial.chunks, result.file_result.chunks, strict=True):
        if old.chunk.chunk_id not in expected:
            assert new == old

@pytest.mark.parametrize('side', ['source', 'target'])
def test_duplicate_quote_across_scalar_group_fails_closed(side):
    initial = mapping('---\ntitle: "Beta. Unique. Beta."\n---\n\nBody.', scalar=True)
    client = Client()
    result = repair(initial, client, (Issue(PATH, 'Bad Beta', 'Locate first', **{side: Location(2, 2, 'Beta.')}),))
    assert not result.complete and (not client.calls)
    assert result.file_result == initial
