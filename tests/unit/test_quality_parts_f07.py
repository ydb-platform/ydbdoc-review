"""Saved correspondence is authoritative even when published parts are absent."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import ChunkResult, RequestBudget, assemble_file, make_chunk, protect
from ydbdoc_review.model import Endpoint, ModelChoice
from ydbdoc_review.quality import Issue, Location, review_parts
from ydbdoc_review.quality_loop import SelectedFile, repair_document

PATH = 'docs/en/test.md'
CHOICE = ModelChoice(Endpoint('eliza', 'https://invalid', 'test', 'dummy'))
BUDGET = RequestBudget(100000, 10000, lambda m: len(str(m)))


class Client:
    def __init__(self, fail=None):
        self.attempts = []
        self.calls = []
        self.fail = fail

    def chat(self, messages, **kwargs):
        payload = json.loads(messages[-1]['content'])
        self.calls.append(payload)
        if self.fail == len(self.calls):
            return SimpleNamespace(content='', finish_reason='stop')
        return SimpleNamespace(content=payload['source'], finish_reason='stop')


def mapped(missing=()):
    source = 'Alpha.\n\nBeta.\n\nGamma.\n\nDelta.'
    doc = protect(source, path=PATH)
    # Each protected paragraph includes its separator atom.
    ends = [atom_end for atom_end in doc.boundaries if doc.text[:atom_end].endswith('⟧')] + [len(doc.text)]
    slots = []
    start = 0
    for end in ends:
        chunk = make_chunk(doc, len(slots), start, end)
        from ydbdoc_review.document import restore
        text = restore(doc, chunk.text, expected=chunk.text)
        absent = chunk.index in missing
        slots.append(ChunkResult(chunk, None if absent else chunk.text,
                                 None if absent else text, unfinished=absent,
                                 status='missing' if absent else 'complete'))
        start = end
    return source, assemble_file(PATH, doc, tuple(slots))


@pytest.mark.parametrize('missing', [(0,), (1,), (3,), (0, 1, 3)])
def test_only_absent_slots_are_repaired(missing):
    source, initial = mapped(missing)
    assert len(initial.chunks) == 4
    client = Client()
    result = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text or '',
                             (Issue(PATH, 'Incomplete translation', 'Restore missing parts'),),
                             replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    assert len(client.calls) == len(missing)
    assert [call['source'] for call in client.calls] == [initial.chunks[i].chunk.text for i in missing]
    assert all(call['translation_missing'] for call in client.calls)
    assert result.complete and result.text == source
    assert result.file_result is not None
    assert [p.chunk.chunk_id for p in result.file_result.chunks] == [p.chunk.chunk_id for p in initial.chunks]
    for index in set(range(4)) - set(missing):
        assert result.file_result.chunks[index] == initial.chunks[index]


def test_partial_repair_keeps_successes_and_final_map_for_continue():
    source, initial = mapped((0, 1, 3))
    first = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text or '', (),
                            replacements={}, client=Client(fail=2), choice=CHOICE, budget=BUDGET)
    assert not first.complete
    assert first.file_result.chunks[0].status == 'complete'
    assert first.file_result.chunks[1].status == 'missing'
    second_client = Client()
    second = repair_document(SelectedFile(PATH, source, 'en', initial=first.file_result), first.text or '', (),
                             replacements={}, client=second_client, choice=CHOICE, budget=BUDGET)
    assert second.complete and second.text == source
    assert len(second_client.calls) == 1
    assert second_client.calls[0]['chunk_id'] == initial.chunks[1].chunk.chunk_id


def test_saved_review_windows_keep_zero_length_missing_targets():
    source, initial = mapped((0, 1, 3))
    parts = review_parts(source, initial.text, fits=lambda p: p.source_end - p.source_start < 20,
                         correspondence=initial)
    assert len(parts) == 4
    assert [p.target_end - p.target_start == 0 for p in parts] == [True, True, False, True]


def test_source_only_finding_repairs_one_part():
    source, initial = mapped()
    client = Client()
    issue = Issue(PATH, 'Meaning lost', 'Restore meaning', source=Location(3, 3, 'Beta.'))
    result = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text, (issue,),
                             replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    assert result.complete
    assert len(client.calls) == 1
    assert client.calls[0]['source'] == initial.chunks[1].chunk.text
    assert client.calls[0]['chunk_id'] == initial.chunks[1].chunk.chunk_id


def test_unlocalized_finding_does_not_invent_a_part():
    source, initial = mapped()
    client = Client()
    result = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text,
                             (Issue(PATH, 'Unknown location', 'Locate missing meaning'),),
                             replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    assert not result.complete and not client.calls
    assert 'location unknown' in result.issues[0].problem


def test_final_map_round_trips_into_continue_selection():
    from ydbdoc_review.continuation import select_files
    from ydbdoc_review.document import file_result_to_dict
    source, initial = mapped((0, 1, 3))
    repaired = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text or '', (),
                               replacements={}, client=Client(fail=2), choice=CHOICE, budget=BUDGET)
    context = {'result': {'issues': [], 'unfinished_files': [PATH]}, 'known_files': [
        {'path': PATH, 'source': source, 'target_lang': 'en', 'glossary': [],
         'initial': file_result_to_dict(repaired.file_result)}]}
    selected = select_files(context, 'Finish missing parts')[0]
    assert selected.initial == repaired.file_result
    client = Client()
    final = repair_document(selected, repaired.text, selected.requested_findings,
                            replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    assert final.complete and len(client.calls) == 1


@pytest.mark.parametrize('absent', [True, False])
def test_missing_scalar_fragment_preserves_other_saved_fragments(absent):
    from ydbdoc_review.document import restore
    source = '---\ntitle: "Alpha. Beta. Gamma."\n---\n\nBody.'
    doc = protect(source, path=PATH)
    chunks = []
    start = 0
    for index, end in enumerate(doc.boundaries):
        chunk = make_chunk(doc, index, start, end)
        chunks.append(ChunkResult(chunk, chunk.text, restore(doc, chunk.text, expected=chunk.text)))
        start = end
    assert len(chunks) > 2
    lost = 1
    previous = chunks[lost].text
    chunks[lost] = (replace(chunks[lost], response=None, text=None, status='missing', unfinished=True)
                    if absent else replace(chunks[lost], status='damaged', unfinished=True))
    initial = assemble_file(PATH, doc, tuple(chunks))
    client = Client()
    result = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text or '', (),
                             replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    assert result.complete and result.text == source
    assert len(client.calls) == 1
    assert client.calls[0]['source'] == chunks[lost].chunk.text
    assert client.calls[0]['current_target'] == ('' if absent else previous)
    assert client.calls[0]['translation_missing'] is absent
    assert result.file_result.chunks[0] == chunks[0]
    parts = review_parts(source, result.text, fits=lambda p: True, correspondence=result.file_result)
    assert parts[0].target_start == 0 and parts[-1].target_end == len(source)


def test_url_repair_final_map_does_not_cascade_in_continue():
    source = '[one](/ru/b.md) [two](/en/b.md)'
    urls = {'/ru/b.md': '/en/b.md', '/en/b.md': '/final/b.md'}
    first = repair_document(SelectedFile(PATH, source, 'en'), source, (), replacements=urls,
                            client=Client(), choice=CHOICE, budget=BUDGET)
    assert first.complete and first.text == '[one](/en/b.md) [two](/final/b.md)'
    second = repair_document(SelectedFile(PATH, source, 'en', initial=first.file_result), first.text,
                             (Issue(PATH, 'Repair requested', 'Keep approved links'),), replacements=urls,
                             client=Client(), choice=CHOICE, budget=BUDGET)
    assert second.complete and second.text == first.text
    assert second.file_result.file_id == first.file_result.file_id
    assert second.file_result.chunks[0].chunk == first.file_result.chunks[0].chunk
    review_parts(source, second.text, fits=lambda p: True, correspondence=second.file_result)


def scalar_map(value='Alpha. Beta. Gamma.', *, translated=False, missing=False):
    from ydbdoc_review.document import restore
    source = f'---\ntitle: "{value}"\n---\n\nBody.'
    doc = protect(source, path=PATH)
    slots = []
    start = 0
    for index, end in enumerate(doc.boundaries):
        chunk = make_chunk(doc, index, start, end)
        absent = missing and 'Beta.' in chunk.text
        response = chunk.text.replace('Beta.', 'Different.') if translated else chunk.text
        slots.append(ChunkResult(chunk, None if absent else response,
                                 None if absent else restore(doc, response, expected=chunk.text),
                                 unfinished=absent, status='missing' if absent else 'complete'))
        start = end
    return source, assemble_file(PATH, doc, tuple(slots))


@pytest.mark.parametrize('side', ['source', 'target', 'both'])
def test_scalar_located_quote_repairs_only_its_fragment(side):
    source, initial = scalar_map(translated=True)
    locations = {}
    if side in {'source', 'both'}:
        locations['source'] = Location(2, 2, 'Beta.')
    if side in {'target', 'both'}:
        locations['target'] = Location(2, 2, 'Different.')
    issue = Issue(PATH, 'Incorrect meaning', 'Fix this fragment only', **locations)
    client = Client()
    result = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text, (issue,),
                             replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    expected = next(slot for slot in initial.chunks if 'Beta.' in slot.chunk.text)
    assert result.complete
    assert [call['chunk_id'] for call in client.calls] == [expected.chunk.chunk_id]
    assert client.calls[0]['current_target'] == expected.text
    for old, new in zip(initial.chunks, result.file_result.chunks, strict=True):
        if old.chunk != expected.chunk:
            assert new == old


@pytest.mark.parametrize('side', ['source', 'target'])
def test_scalar_ambiguous_quote_does_not_repair_neighbours(side):
    source, initial = scalar_map('Repeat. Beta. Repeat.')
    issue = Issue(PATH, 'Ambiguous repeated wording', 'Locate the occurrence',
                  **{side: Location(2, 2, 'Repeat.')})
    client = Client()
    result = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text, (issue,),
                             replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    assert not result.complete and not client.calls
    assert result.file_result == initial
    assert any('ambiguous' in issue.problem for issue in result.issues)


@pytest.mark.parametrize('locations', [
    {'target': Location(4, 4, 'Beta.')},
    {'source': Location(2, 2, 'Alpha.'), 'target': Location(2, 2, 'Different.')},
])
def test_scalar_wrong_lines_or_conflicting_sides_do_not_choose_a_fragment(locations):
    source, initial = scalar_map(translated=True)
    client = Client()
    result = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text,
                             (Issue(PATH, 'Uncertain location', 'Locate the fragment', **locations),),
                             replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    assert not result.complete and not client.calls


def test_missing_scalar_source_quote_selects_only_absent_slot():
    source, initial = scalar_map(missing=True)
    client = Client()
    issue = Issue(PATH, 'Missing Beta', 'Restore Beta', source=Location(2, 2, 'Beta.'))
    result = repair_document(SelectedFile(PATH, source, 'en', initial=initial), initial.text, (issue,),
                             replacements={}, client=client, choice=CHOICE, budget=BUDGET)
    expected = next(slot for slot in initial.chunks if slot.text is None)
    assert result.complete and result.text == source
    assert [call['chunk_id'] for call in client.calls] == [expected.chunk.chunk_id]
    assert client.calls[0]['current_target'] == ''
    assert client.calls[0]['translation_missing'] is True
