"""Independent acceptance after T06 scalar boundary fix."""
from types import SimpleNamespace

import pytest
import yaml

from ydbdoc_review.document import RequestBudget, protect, restore, translate_document
from ydbdoc_review.model import Endpoint, ModelChoice


def run(source, replacement='Text'):
    seen = []
    class Fake:
        def chat(self, messages, **kwargs):
            response = messages[-1]['content'].split('\n\n', 1)[1].replace('Текст', replacement)
            seen.append(response)
            return SimpleNamespace(content=response, finish_reason='stop')
    result = translate_document(source, path='p.md', source_lang='ru', target_lang='en', client=Fake(),
        choice=ModelChoice(Endpoint('eliza', 'https://example.test', 'test', 'test')),
        budget=RequestBudget(50000, 10000, lambda ms: sum(len(m['content']) for m in ms)))
    assert len(seen) == 1
    assert result.chunks[0].response == seen[0]
    return result


SCALARS = [
    '"Текст \\x41 \\u0042 \\U00000043 \\t"',
    "'Текст it''s fine'",
    '>2- # header\n  Текст',
    '|2- # header\n  Текст',
    '>+\n  Текст\n\n',
    '|+\n  Текст\n\n',
    '>-\n  Текст first\n  second',
    '>\n  Текст first\n\n  second',
    '"Текст first\n  second"',
    "'Текст first\n  second'",
    '"Текст first\\\n  second"',
    'Текст first\n  second',
]

@pytest.mark.parametrize('scalar', SCALARS)
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_ordinary_translation_preserves_scalar_semantics(scalar, newline):
    raw = f'title: {scalar}\nconfig: {{enabled: true, count: 3}} # unchanged\ndescription: "Текст too"\n'
    source = ('---\n' + raw + '---\nBody.\n').replace('\n', newline)
    p = protect(source)
    assert restore(p, p.text) == source
    result = run(source)
    assert not result.issues and not result.unfinished, result.issues
    before = yaml.safe_load(raw)
    after = yaml.safe_load(result.text.split('---' + newline)[1])
    assert after == {k: v.replace('Текст', 'Text') if isinstance(v, str) else v for k, v in before.items()}
    assert 'config: {enabled: true, count: 3} # unchanged' + newline in result.text


@pytest.mark.parametrize('style', ['"Текст"', "'Текст'", 'Текст', '|-\n  Текст', '>-\n  Текст'])
@pytest.mark.parametrize('replacement', ['Tab\tvalue', 'Line\rconfig: hacked', 'Unicode\u0085config: hacked', 'Line\u2028config: hacked', 'Text\n...\nconfig: hacked', 'Name: # quoted & *'])
def test_new_control_and_yaml_sensitive_values_never_clean_corrupt(style, replacement):
    source = f'---\nconfig: safe # bytes\ntitle: {style}\nuntouched: [1, true, "literal"]\n---\nBody.\n'
    result = run(source, replacement)
    assert result.text
    if result.issues:
        return
    parsed = yaml.safe_load(result.text.split('---\n')[1])
    assert parsed == {'config': 'safe', 'title': replacement, 'untouched': [1, True, 'literal']}
    assert 'config: safe # bytes\n' in result.text
    assert result.text.endswith('untouched: [1, true, "literal"]\n---\nBody.\n')


def test_long_front_matter_uses_sentence_chunks_with_real_budget():
    from ydbdoc_review.document import translation_messages
    source = '---\ndescription: |-\n  ' + ' '.join(f'Текст sentence {i}.' for i in range(80)) + '\nconfig: safe\n---\nBody.\n'
    def count(ms):
        return sum(len(m['content']) + 13 for m in ms) + 7
    overhead = count(translation_messages('', source_lang='ru', target_lang='en', path='p.md'))
    capacity = 180
    seen = []
    class Fake:
        def chat(self, messages, **kwargs):
            seen.append(messages)
            return SimpleNamespace(content=messages[-1]['content'].split('\n\n', 1)[1].replace('Текст', 'Text'), finish_reason='stop')
    result = translate_document(source, path='p.md', source_lang='ru', target_lang='en', client=Fake(),
        choice=ModelChoice(Endpoint('eliza', 'https://example.test', 'test', 'test')),
        budget=RequestBudget(overhead + capacity * 2, capacity, count))
    assert not result.issues and not result.unfinished, result.issues
    assert len(seen) > 1
    parsed = yaml.safe_load(result.text.split('---\n')[1])
    assert parsed['description'] == ' '.join(f'Text sentence {i}.' for i in range(80))
    assert parsed['config'] == 'safe'
