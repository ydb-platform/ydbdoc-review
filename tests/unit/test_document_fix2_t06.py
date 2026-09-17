"""Decoded scalar assembly, raw noop and partial-result regressions."""
from types import SimpleNamespace

import pytest
import yaml

from ydbdoc_review.document import (
    RequestBudget,
    protect,
    restore,
    translate_document,
    translation_messages,
)
from ydbdoc_review.model import Endpoint, ModelChoice


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('translate', [False, True])
def test_split_scalar_roundtrip_and_semantics(newline, translate):
    raw = ('title: "Untouched first\\\n  second" # raw\n'
           'description: >+ # keep\n  '
           + ' '.join(f'Текст item {i}.' for i in range(45))
           + '\n\nconfig: [true, 3] # protected\n')
    source = ('---\n' + raw + '---\nBody.\n').replace('\n', newline)
    seen = []
    snapshots = []
    def count(ms):
        return sum(len(m['content']) + 13 for m in ms) + 7
    overhead = count(translation_messages('', source_lang='ru', target_lang='en', path='p.md'))

    class Fake:
        def chat(self, messages, **kwargs):
            text = messages[-1]['content'].split('\n\n', 1)[1].strip()
            if translate:
                text = text.replace('Текст', 'Text')
            seen.append(text)
            return SimpleNamespace(content=text, finish_reason='stop')

    result = translate_document(
        source, path='p.md', source_lang='ru', target_lang='en', client=Fake(),
        choice=ModelChoice(Endpoint('eliza', 'https://example.test', 'test', 'test')),
        budget=RequestBudget(overhead + 320, 160, count), on_progress=snapshots.append,
    )
    assert len(seen) > 1
    assert not result.issues and not result.unfinished
    assert [c.response for c in result.chunks] == seen
    assert snapshots[-1] == result
    assert all(s.unfinished for s in snapshots[:-1])
    if not translate:
        assert result.text == source
    expected = yaml.safe_load(raw.replace('Текст', 'Text') if translate else raw)
    assert yaml.safe_load(result.text.split('---' + newline)[1]) == expected
    assert 'title: "Untouched first\\' + newline + '  second" # raw' + newline in result.text
    assert 'config: [true, 3] # protected' + newline in result.text


def test_decoded_literal_marker_url_and_code_are_atoms():
    source = ('---\ntitle: "Текст \\u27e6CV1\\u27e7 https://example.test/a '
              '`a. b`"\n---\nBody.\n')
    p = protect(source)
    assert 'https://' not in p.text and '`a. b`' not in p.text
    assert restore(p, p.text) == source
    translated = restore(p, p.text.replace('Текст', 'Text'))
    assert yaml.safe_load(translated.split('---\n')[1])['title'] == (
        'Text ⟦CV1⟧ https://example.test/a `a. b`'
    )


@pytest.mark.parametrize('value', ['', 'true', 'a\x7fb', 'a\x85b', 'a\u2028b'])
def test_scalar_serialization_exact_for_empty_type_like_and_control_values(value):
    source = '---\ntitle: Текст\nconfig: safe\n---\n'
    p = protect(source)
    text = restore(p, p.text.replace('Текст', value))
    assert yaml.safe_load(text.split('---\n')[1]) == {'title': value, 'config': 'safe'}


def test_completed_multichunk_scalar_survives_later_error():
    source = ('---\ndescription: |-\n  '
              + ' '.join(f'Текст item {i}.' for i in range(30))
              + '\nconfig: safe\n---\n\n' + 'Body paragraph.\n\n' * 30)
    seen = []

    def count(ms):
        return sum(len(m['content']) + 13 for m in ms) + 7

    overhead = count(translation_messages('', source_lang='ru', target_lang='en', path='p.md'))

    class Fake:
        def chat(self, messages, **kwargs):
            text = messages[-1]['content'].split('\n\n', 1)[1]
            seen.append(text)
            if text.startswith('Body'):
                raise TimeoutError('late failure')
            return SimpleNamespace(content=text.replace('Текст', 'Text'), finish_reason='stop')

    result = translate_document(
        source, path='p.md', source_lang='ru', target_lang='en', client=Fake(),
        choice=ModelChoice(Endpoint('eliza', 'https://example.test', 'test', 'test')),
        budget=RequestBudget(overhead + 320, 160, count),
    )
    assert result.issues and result.unfinished
    assert len(seen) == len(result.chunks) > 2
    assert yaml.safe_load(result.text.split('---\n')[1]) == {
        'description': ' '.join(f'Text item {i}.' for i in range(30)), 'config': 'safe',
    }
