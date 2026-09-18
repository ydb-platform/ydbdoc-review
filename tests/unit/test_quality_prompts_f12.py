"""F12: inspect serialized HTTP requests; no paid model calls."""
import json

import pytest
import requests

from ydbdoc_review.document import RequestBudget, translate_document, translation_messages
from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient
from ydbdoc_review.quality import (
    Issue,
    Location,
    ReviewPart,
    check,
    critic_messages,
    structure_counts,
)
from ydbdoc_review.quality_loop import SelectedFile, repair_document

PATH = 'docs/en/selected.md'
CHOICE = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'test', 'dummy'))
BUDGET = RequestBudget(100000, 10000, lambda m: len(str(m)))
GLOSSARY = {'транзакция': 'transaction'}


@pytest.fixture
def wire(monkeypatch):
    calls = []
    def send(session, request, **kwargs):
        body = json.loads(request.body)
        calls.append(body)
        messages = body['messages']
        try:
            data = json.loads(messages[-1]['content'])
        except ValueError:
            output = messages[-1]['content'].split('\n\n', 1)[1]
        else:
            output = (json.dumps({'complete': True, 'verdict': 'correct', 'issues': []})
                      if 'target' in data else data['source'])
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'choices': [{'message': {'content': output},
                                                     'finish_reason': 'stop'}]}).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    return ModelClient(record_request=lambda r: None, record_attempt=lambda r: None), calls


def assert_policy(messages):
    system = messages[0]['content']
    assert 'YDB' in system
    assert 'technical' in system
    assert 'claims' in system
    assert 'glossary' in system


def test_translation_actual_request_contains_inline_glossary_and_ydb(wire):
    client, calls = wire
    result = translate_document('транзакция `SELECT 1`', path=PATH, source_lang='ru',
                                target_lang='en', client=client, choice=CHOICE,
                                budget=BUDGET, glossary=GLOSSARY)
    assert not result.unfinished
    messages = calls[0]['messages']
    assert_policy(messages)
    system = messages[0]['content']
    assert 'transaction' in system and 'транзакция' in system
    assert 'caller-supplied inline glossary mapping' in system
    assert 'A URL is a reference, not glossary contents' in system
    assert 'exactly once' in system and 'original order' in system
    assert 'SELECT 1' not in messages[-1]['content']  # protected atom


def test_critic_actual_request_rejects_style_blockers_and_carries_rules(wire):
    client, calls = wire
    result = check('транзакция', 'transaction', path=PATH, candidate_sha='abc', target_lang='en',
                   client=client, choice=CHOICE, budget=BUDGET, glossary=GLOSSARY)
    assert result.complete
    messages = calls[0]['messages']
    assert_policy(messages)
    assert 'Pure style preferences' in messages[0]['content']
    assert 'warning, never error' in messages[0]['content']
    data = json.loads(messages[-1]['content'])
    assert data['glossary_context']['terms'] == GLOSSARY
    assert data['glossary_context']['rules']


def test_repair_actual_request_excludes_other_file_findings(wire):
    client, calls = wire
    findings = (Issue(PATH, 'Mistranslated term', 'Use transaction'),
                Issue('docs/en/unrelated.md', 'SECRET_OTHER_FILE', 'SECRET_OTHER_FIX'))
    result = repair_document(SelectedFile(PATH, 'транзакция', 'en', glossary=tuple(GLOSSARY.items())),
                             'transaction', findings, replacements={}, client=client,
                             choice=CHOICE, budget=BUDGET)
    assert result.complete
    messages = calls[0]['messages']
    assert_policy(messages)
    data = json.loads(messages[-1]['content'])
    assert len(data['findings']) == 1
    assert data['glossary_context']['terms'] == GLOSSARY
    assert 'SECRET_OTHER' not in json.dumps(calls)
    assert 'pure style preferences' in messages[0]['content']


def test_critic_sends_only_selected_window_and_no_invented_glossary():
    source, target = 'Selected\nSECRET_SOURCE', 'Translated\nSECRET_TARGET'
    messages = critic_messages(source, target, path=PATH, part=ReviewPart(0, 8, 0, 10),
                               source_counts=structure_counts(source), target_counts=structure_counts(target))
    assert 'SECRET_' not in json.dumps(messages)
    data = json.loads(messages[-1]['content'])
    assert data['glossary_context'] == {'source': None, 'terms': {}, 'rules': []}
    assert 'caller-supplied' not in translation_messages('text', source_lang='en', target_lang='ru', path=PATH)[0]['content']


def test_repair_findings_are_restricted_to_window():
    from ydbdoc_review.quality_loop import _finding_in_part
    current = 'one\r\ntwo\rthree\nfour'
    window = ReviewPart(0, 5, 5, 9)  # second line only
    assert _finding_in_part(Issue(PATH, 'bad', 'fix', target=Location(2, 2, 'two')), PATH, current, window)
    assert not _finding_in_part(Issue(PATH, 'bad', 'fix', target=Location(3, 3, 'three')), PATH, current, window)
