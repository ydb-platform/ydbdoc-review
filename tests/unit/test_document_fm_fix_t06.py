"""Scalar boundaries are data boundaries, including on unsuccessful output."""
from types import SimpleNamespace

import pytest
import yaml

from ydbdoc_review.document import (
    RequestBudget,
    chunk_document,
    protect,
    restore,
    translate_document,
)
from ydbdoc_review.model import Endpoint, ModelChoice
from ydbdoc_review.parsing.front_matter import FrontMatterError, apply_front_matter_updates


def run(source, mutation=lambda text: text):
    calls = []
    class Fake:
        def chat(self, messages, **kwargs):
            calls.append(messages)
            text = messages[-1]["content"].split("\n\n", 1)[1]
            return SimpleNamespace(content=mutation(text), finish_reason="stop")
    result = translate_document(
        source, path="fm.md", source_lang="ru", target_lang="en", client=Fake(),
        choice=ModelChoice(Endpoint("eliza", "https://example.test", "test", "test")),
        budget=RequestBudget(50000, 10000, lambda ms: sum(len(m["content"]) for m in ms)),
    )
    assert len(calls) == 1
    return result


@pytest.mark.parametrize('key', ['title', 'description'])
@pytest.mark.parametrize('style,value', [("'Текст'", "User's guide"), ('"Текст"', 'The "best" guide'), ('Текст', "User's guide"), ('|-\n  Текст', 'First\nconfig: hacked')])
def test_safe_encoding_preserves_value_and_protected_bytes(key, style, value):
    source = f'---\nconfig: safe # untouched\n{key}: {style}\nother: [a, b]\n---\nBody.\n'
    result = run(source, lambda t: t.replace('Текст', value))
    assert not result.issues and not result.unfinished
    fields = yaml.safe_load(result.text.split('---\n')[1])
    assert fields[key] == value
    assert fields['config'] == 'safe'
    assert fields['other'] == ['a', 'b']
    assert 'config: safe # untouched\n' in result.text
    assert result.text.endswith('other: [a, b]\n---\nBody.\n')


@pytest.mark.parametrize('style', ['Текст', "'Текст'", '>-\n  Текст'])
def test_newline_uses_safe_scalar_style_change(style):
    source = f'---\nconfig: safe\ntitle: {style}\n---\nBody.\n'
    result = run(source, lambda t: t.replace('Текст', 'Text\nconfig: hacked'))
    assert not result.issues and not result.unfinished
    assert yaml.safe_load(result.text.split("---\n")[1]) == {
        "config": "safe", "title": "Text\nconfig: hacked",
    }
    assert result.text.startswith('---\n')
    assert 'config: hacked' in result.chunks[0].response
    assert 'config: hacked' in result.text


@pytest.mark.parametrize('body', [
    'title: &a Текст\nconfig: *a\n',
    'config: &a Текст\ntitle: *a\n',
    'title: Текст\ntitle: Другой\n',
    'title: Текст\ntitle: [Другой]\n',
])
def test_ambiguous_selected_values_are_explicit_and_unchanged(body):
    source = '---\n' + body + '---\nBody.\n'
    result = run(source)
    assert result.text == source and result.issues
    assert restore(protect(source), protect(source).text) == source


@pytest.mark.parametrize('body', [
    'title: "A \\"quote\\" and \\n line"\r\n',
    "title: 'User''s guide'\r\n",
    'title: >-\r\n  First\r\n  second\r\n',
    'title: |+\r\n  First\r\n\r\n',
])
def test_untouched_raw_scalar_roundtrip(body):
    source = '---\r\n' + body + '---\r\nBody.\r\n'
    document = protect(source)
    assert restore(document, document.text) == source


def test_front_matter_not_split_inside_scalar():
    source = '---\ntitle: "One. Two. Three."\n---\n\n' + 'Body paragraph.\n\n' * 8
    document = protect(source)
    chunks = chunk_document(document, lambda t: len(t) < 65)
    assert len(chunks) > 1
    assert ''.join(restore(document, c.text, expected=c.text) for c in chunks) == source
    assert 'title: \"One. Two. Three.\"' in restore(document, chunks[0].text, expected=chunks[0].text)


@pytest.mark.parametrize('value', ['Text\nextra: yes', 'Text\nconfig: hacked', 'Text # comment', '*anchor', 'true', 'Text\n---\nextra: yes'])
def test_plain_helper_rejects_new_fields_and_scalar_type_changes(value):
    with pytest.raises(FrontMatterError):
        apply_front_matter_updates('config: safe\ntitle: Текст\n', {'title': value})


@pytest.mark.parametrize('scalar,expected', [
    ("'Текст User''s'", "Text User's"),
    ('"Текст \\"best\\""', 'Text "best"'),
    ('"Текст \\n line"', 'Text \n line'),
])
def test_existing_source_escapes_are_not_double_encoded(scalar, expected):
    result = run('---\ntitle: ' + scalar + '\n---\nBody.\n', lambda t: t.replace('Текст', 'Text'))
    assert not result.issues
    assert yaml.safe_load(result.text.split('---\n')[1])['title'] == expected


@pytest.mark.parametrize('mutation', [
    lambda t: t.replace('⟦C1⟧', ''),
    lambda t: t.replace('⟦C1⟧', '⟦C1⟧⟦C1⟧'),
    lambda t: t + '⟦broken',
])
def test_front_matter_damaged_markers_retain_response_and_issue(mutation):
    result = run('---\ntitle: "Текст"\nconfig: safe\n---\nBody.\n', mutation)
    assert result.issues
    assert result.text == result.chunks[0].response


def test_double_quoted_newline_is_data_not_a_key_or_delimiter():
    replacement = 'Text"\n---\nconfig: hacked\nextra: "'
    result = run('---\ntitle: "Текст"\nconfig: safe\n---\nBody.\n', lambda t: t.replace('Текст', replacement))
    assert not result.issues
    assert result.text.count('---\n') == 2
    assert yaml.safe_load(result.text.split('---\n')[1]) == {'title': replacement, 'config': 'safe'}
