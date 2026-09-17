"""Independent final acceptance: semantic edits, exact complements, partial failure."""
from types import SimpleNamespace

import pytest
import yaml

from ydbdoc_review.document import RequestBudget, protect, restore, translate_document, translation_messages
from ydbdoc_review.model import Endpoint, ModelChoice


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('value', ['null', '2026-01-01', 'a\n\n b\n', 'a\x00b', 'x\u2029y', 'Text "quote": # data'])
def test_decoded_edit_preserves_every_unselected_byte(newline, value):
    raw = ('# prefix\nconfig: {nested: [true, 003, "keep\\t"], url: "https://x/a"} # bytes\n'
           'title: >2+ # scalar comment\n  Текст first\n  second\n\n'
           'description: "Other first\\\n  second" # untouched\n'
           'extra: |+\n  literal\n\n').replace('\n', newline)
    source = '---' + newline + raw + '---' + newline + 'Body.' + newline
    doc = protect(source)
    assert restore(doc, doc.text).encode() == source.encode()
    assert 'Текст first second' in doc.text
    scalar = next(s for s in doc.scalars if s.record.key == 'title')
    start, end = doc.text.index(scalar.opening) + len(scalar.opening), doc.text.index(scalar.closing)
    # Keep original separator markers and replace only visible semantic text.
    translated = restore(doc, doc.text[:start] + doc.text[start:end].replace('Текст first second', value) + doc.text[end:])
    parsed = yaml.safe_load(translated.split('---' + newline)[1])
    expected = yaml.safe_load(raw)
    expected['title'] = value + '\n\n'
    assert parsed == expected
    prefix = raw[:scalar.record.start]
    suffix = raw[scalar.record.end:]
    body = translated.split('---' + newline)[1]
    assert body.startswith(prefix) and body.endswith(suffix)
    assert '# scalar comment' in body


@pytest.mark.parametrize('failure', ['timeout', 'empty', 'length'])
def test_failed_middle_scalar_chunk_preserves_raw_and_stays_incomplete(failure):
    source = ('---\ndescription: |-\n  ' + ' '.join(f'Текст number {i}.' for i in range(60))
              + '\nconfig: safe\n---\nBody.\n')
    count = lambda ms: sum(len(m['content']) + 11 for m in ms) + 3
    overhead = count(translation_messages('', source_lang='ru', target_lang='en', path='p.md'))
    responses, snapshots, inputs = [], [], []

    class Fake:
        def chat(self, messages, **kwargs):
            text = messages[-1]['content'].split('\n\n', 1)[1]
            inputs.append(text)
            response = text.replace('Текст', 'Text')
            reason = 'stop'
            if len(inputs) == 2:
                if failure == 'timeout':
                    responses.append(None)
                    raise TimeoutError('middle scalar')
                if failure == 'empty':
                    response = ''
                else:
                    reason = 'length'
            responses.append(response)
            return SimpleNamespace(content=response, finish_reason=reason)

    result = translate_document(source, path='p.md', source_lang='ru', target_lang='en', client=Fake(),
        choice=ModelChoice(Endpoint('eliza', 'https://example.test', 'test', 'test')),
        budget=RequestBudget(overhead + 360, 180, count), on_progress=snapshots.append)
    assert len(inputs) > 3
    assert result.unfinished and result.issues
    assert all(s.unfinished for s in snapshots)
    assert [c.response for c in result.chunks] == responses
    assert result.text and 'Text number 0.' in result.text
    assert result.text != source and 'Текст' not in result.text
    assert result.chunks[1].unfinished and result.chunks[1].issues
    if failure != 'length':
        assert result.chunks[1].text is None
