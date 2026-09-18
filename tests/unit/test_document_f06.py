"""Regression: PR 53522 missing responses must never shift correspondence."""
import json
from types import SimpleNamespace

import pytest

from ydbdoc_review import document as state
from ydbdoc_review.document import (
    ChunkResult,
    chunk_document,
    protect,
    restore,
    translate_document,
)
from ydbdoc_review.model import Endpoint, ModelChoice

CHOICE = ModelChoice(Endpoint('eliza', 'https://example.test', 'test', 'test'))
SOURCE = ''.join(f'Paragraph {i} with original text.\n\n' for i in range(6))


class Budget(SimpleNamespace):
    def for_choice(self, choice):
        return self


class Client:
    def __init__(self, absent=()):
        self.absent = absent
        self.calls = 0

    def chat(self, messages, **kwargs):
        index = self.calls
        self.calls += 1
        if index in self.absent:
            raise TimeoutError(f'Part {index} unavailable')
        text = messages[-1]['content'].split('\n\n', 1)[1]
        return SimpleNamespace(content=text.replace('original', 'translated'), finish_reason='stop')


def translate(absent=(), source=SOURCE):
    snapshots = []
    budget = Budget(max_output_tokens=1000,
                             fits=lambda messages, expected_output='': len(expected_output) <= 42)
    result = translate_document(source, path='en/page.md', source_lang='ru', target_lang='en',
                                client=Client(absent), choice=CHOICE, budget=budget,
                                on_progress=snapshots.append)
    return result, snapshots


@pytest.mark.parametrize('absent', [(0,), (1,), (3,), (0, 1, 3)])
def test_missing_slots_preserve_ids_source_bounds_and_all_later_results(absent):
    result, snapshots = translate(absent)
    complete, _ = translate()
    assert len(result.chunks) == 6
    assert all(len(s.chunks) == 6 for s in snapshots)
    assert result.file_id == complete.file_id
    assert [r.chunk for r in result.chunks] == [r.chunk for r in complete.chunks]
    assert len({r.chunk.chunk_id for r in result.chunks}) == 6
    assert result.unfinished
    for index, item in enumerate(result.chunks):
        chunk = item.chunk
        raw = SOURCE[chunk.source_start:chunk.source_end]
        assert restore(result.protected, chunk.text, expected=chunk.text) == raw
        assert result.protected.text[chunk.start:chunk.end] == chunk.text
        assert chunk.file_id == result.file_id
        if index in absent:
            assert item.status == 'missing' and item.unfinished
            assert item.text is None and item.response is None
        else:
            assert item.status == 'complete' and not item.unfinished
            assert item.text == raw.replace('original', 'translated')
    assert result.text == ''.join(r.text for r in result.chunks if r.text is not None)
    assert all(len(s.chunks) == 6 for s in snapshots)
    assert [r.status for r in snapshots[0].chunks[1:]] == ['pending'] * 5
    assert all(r.text is None for r in snapshots[0].chunks[1:])


def test_json_roundtrip_and_resume_missing_parts_without_touching_successes():
    result, _ = translate((0, 1, 3))
    saved = json.loads(json.dumps(state.file_result_to_dict(result)))
    resumed = state.file_result_from_dict(saved)
    assert resumed == result
    repaired = []
    for item in resumed.chunks:
        if item.status != 'missing':
            repaired.append(item)
            continue
        response = item.chunk.text.replace('original', 'translated')
        repaired.append(ChunkResult(item.chunk, response,
                                    restore(resumed.protected, response, expected=item.chunk.text)))
    final = state.assemble_file(resumed.path, resumed.protected, tuple(repaired))
    assert not final.unfinished and not final.issues
    assert final.text == SOURCE.replace('original', 'translated')
    assert [r.chunk.chunk_id for r in final.chunks] == [r.chunk.chunk_id for r in result.chunks]
    for index in (2, 4, 5):
        assert final.chunks[index] is resumed.chunks[index]


def test_serialized_front_matter_mapping_owns_raw_scalar_and_roundtrips():
    source = '---\ntitle: "First sentence. Second sentence. Third sentence."\n---\n\nBody.\n'
    document = protect(source, path='page.md')
    chunks = chunk_document(document, lambda text: len(text) <= 34)
    slots = tuple(ChunkResult(c, c.text, restore(document, c.text, expected=c.text)) for c in chunks)
    result = state.assemble_file('page.md', document, slots)
    assert result.text == source
    resumed = state.file_result_from_dict(json.loads(json.dumps(state.file_result_to_dict(result))))
    assert resumed == result
    assert state.assemble_file(resumed.path, resumed.protected, resumed.chunks).text == source
    scalar_span = next(s for s in document.source_spans
                       if document.scalars[0].opening in document.text[s.start:s.end])
    inside = [c for c in chunks if c.start < scalar_span.end and c.end > scalar_span.start]
    assert len(inside) > 1
    assert all(c.source_start <= scalar_span.source_start and c.source_end >= scalar_span.source_end
               for c in inside)


def test_source_identity_changes_with_path_or_source_but_not_response():
    first = protect(SOURCE, path='a.md')
    assert first.file_id == protect(SOURCE, path='a.md').file_id
    assert first.file_id != protect(SOURCE, path='b.md').file_id
    assert first.file_id != protect(SOURCE + 'Changed', path='a.md').file_id


def test_assembly_rejects_deleted_duplicate_and_reordered_slots():
    result, _ = translate((1,))
    for bad in (result.chunks[1:], result.chunks + result.chunks[-1:], result.chunks[::-1]):
        with pytest.raises(ValueError):
            state.assemble_file(result.path, result.protected, bad)


def test_marker_boundary_cannot_be_split_and_unknown_schema_is_explicit():
    document = protect('`executable` prose')
    with pytest.raises(ValueError, match='marker'):
        state.make_chunk(document, 0, 0, 2)
    with pytest.raises(ValueError, match='schema'):
        state.file_result_from_dict({'schema_version': 0})


def test_code_comment_translation_and_executable_bytes_survive_serialization():
    source = '```python\nx = "original" # original comment\n```\n'
    budget = Budget(max_output_tokens=1000, fits=lambda *args, **kwargs: True)
    result = translate_document(source, path='page.md', source_lang='ru', target_lang='en',
                                client=Client(), choice=CHOICE, budget=budget)
    resumed = state.file_result_from_dict(json.loads(json.dumps(state.file_result_to_dict(result))))
    assert resumed.text == '```python\nx = "original" # translated comment\n```\n'
    assert not resumed.issues and not resumed.unfinished
    assert resumed.chunks[0].chunk.source_end == len(source)


def test_nonempty_truncation_and_damaged_markers_keep_explicit_status_and_response():
    class Damaged:
        def __init__(self, reason):
            self.reason = reason

        def chat(self, messages, **kwargs):
            return SimpleNamespace(content='Partial ⟦broken', finish_reason=self.reason)
    for reason, status in [('length', 'truncated'), ('stop', 'damaged')]:
        result = translate_document('`code` Original.', path='page.md', source_lang='ru', target_lang='en',
                                    client=Damaged(reason), choice=CHOICE,
                                    budget=Budget(max_output_tokens=1000, fits=lambda *a, **kw: True))
        assert result.unfinished and result.chunks[0].status == status
        assert result.chunks[0].response == result.chunks[0].text == 'Partial ⟦broken'
        assert state.file_result_from_dict(json.loads(json.dumps(state.file_result_to_dict(result)))) == result
