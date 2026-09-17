"""Repair must preserve bytes outside exact, parser-owned URL destinations."""
import json

import pytest
import requests

from ydbdoc_review.document import RequestBudget
from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient
from ydbdoc_review.quality_loop import SelectedFile, repair_document


@pytest.mark.parametrize(('source', 'expected'), [
    ('[link](/ru/b.md) `/ru/b.md` [prefix](/ru/b.md-more)',
     '[link](/en/b.md) `/ru/b.md` [prefix](/ru/b.md-more)'),
    ('[link](/ru/b.md)\n\n```sh\ncurl /ru/b.md\n```',
     '[link](/en/b.md)\n\n```sh\ncurl /ru/b.md\n```'),
    ('---\nconfig: /ru/b.md\ntitle: "[link](/ru/b.md) `/ru/b.md`"\n---\n',
     '---\nconfig: /ru/b.md\ntitle: "[link](/en/b.md) `/ru/b.md`"\n---\n'),
    ('[link][id]\n\n[id]: /ru/b.md "Keep /ru/b.md"\n',
     '[link][id]\n\n[id]: /en/b.md "Keep /ru/b.md"\n'),
    ('<a href="/ru/b.md" data-value="/ru/b.md">Link</a>',
     '<a href="/en/b.md" data-value="/ru/b.md">Link</a>'),
    ('[link](</ru/b.md>) ![image](/ru/b.md)',
     '[link](</en/b.md>) ![image](/en/b.md)'),
    ('[one](/ru/b.md) [two](/en/b.md)',
     '[one](/en/b.md) [two](/final/b.md)'),
    ('\n\n'.join(f'Section {i}: [one](/ru/b.md) [two](/en/b.md) '
                 '`/ru/b.md` [prefix](/ru/b.md-more).' for i in range(80)),
     '\n\n'.join(f'Section {i}: [one](/en/b.md) [two](/final/b.md) '
                 '`/ru/b.md` [prefix](/ru/b.md-more).' for i in range(80))),
])
def test_exact_repair_destinations_without_cascade_or_collateral_mutation(monkeypatch, source, expected):
    calls = []

    def send(session, request, **kwargs):
        payload = json.loads(json.loads(request.body)['messages'][-1]['content'])
        calls.append(payload)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'choices': [{'message': {'content': payload['source']},
                                                     'finish_reason': 'stop'}]}).encode()
        return response

    monkeypatch.setattr(requests.Session, 'send', send)
    choice = ModelChoice(Endpoint('eliza', 'https://offline.invalid', 'test', 'dummy'))
    client = ModelClient(record_request=lambda r: None, record_attempt=lambda a: None)
    file = SelectedFile('ydb/docs/en/a.md', source, 'en')
    long = len(source) > 5000
    budget = RequestBudget(3000, 800, lambda m: len(str(m))) if long else RequestBudget(
        100000, 20000, lambda m: len(str(m)))
    try:
        result = repair_document(file, source, (),
                                 replacements={'/ru/b.md': '/en/b.md', '/en/b.md': '/final/b.md'},
                                 client=client, choice=choice, budget=budget)
    finally:
        client.close()
    assert result.complete, result.issues
    assert result.text == expected
    assert file.source == source
    assert len(calls) > 1 if long else len(calls) == 1
    assert ''.join(call['current_target'] for call in calls) == source
