"""I04 behavior on reviewed historical inputs; no legacy pipeline imports."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import RequestBudget, protect, restore, translate_document
from ydbdoc_review.model import Endpoint, ModelChoice
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.segmentation.mermaid import mermaid_labels

FIXTURES = Path(__file__).parents[1] / 'fixtures'
CASES = json.loads((FIXTURES / 'cleanup_reinsert.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_original_reinsert_changes_prose_preserving_other_bytes(case):
    source = case['source']
    protected = protect(source)
    assert not protected.issues
    assert restore(protected, protected.text) == source
    if case['old'] is None:
        # The include has no article prose to change; all directive bytes stay protected.
        assert source.strip() not in protected.text
        assert ''.join(atom.raw for atom in protected.atoms) == source
        return
    assert case['old'] in protected.text
    changed = protected.text.replace(case['old'], case['new'])
    assert changed != protected.text
    assert restore(protected, changed) == source.replace(case['old'], case['new'])
    for atom in protected.atoms:
        assert source[atom.start:atom.end] == atom.raw


@pytest.mark.parametrize('language,code,expected', [
    ('python', 'print("Привет # строка") # Приветствие', 'print("Привет # строка") # Greeting'),
    ('python', '"""Докстрока # строка"""\n# Приветствие', '"""Докстрока # строка"""\n# Greeting'),
    ('cpp', 'auto x = "https://хост // строка"; /* Приветствие */', 'auto x = "https://хост // строка"; /* Greeting */'),
    ('cpp', '/* Приветствие\n * Приветствие\n */\nint x;', '/* Greeting\n * Greeting\n */\nint x;'),
    ('sql', "SELECT '-- строка'; -- Приветствие", "SELECT '-- строка'; -- Greeting"),
    ('xml', '<x title="Текст"><!-- Приветствие --></x>', '<x title="Текст"><!-- Greeting --></x>'),
    ('unknown-language', '# Приветствие', '# Приветствие'),
    ('json', '{"a": "# Приветствие"}', '{"a": "# Приветствие"}'),
], ids=['python-string','python-docstring','cpp-string','cpp-multiline','sql-string','xml','unknown','json'])
def test_original_comment_islands(language, code, expected):
    source = f'```{language}\n{code}\n```\n'
    protected = protect(source)
    assert not protected.issues
    if expected != code:
        assert 'Приветствие' in protected.text
    else:
        assert 'Приветствие' not in protected.text
    assert restore(protected, protected.text.replace('Приветствие', 'Greeting')) == f'```{language}\n{expected}\n```\n'


def test_distinct_inline_comments_preserve_literal():
    source = '```cpp\n/* Первый */ const char *s = "Русский"; /* Второй */\n```\n'
    protected = protect(source)
    assert 'Русский' not in protected.text
    assert protected.text.index('Первый') < protected.text.index('Второй')
    result = restore(protected, protected.text.replace('Первый','First').replace('Второй','Second'))
    assert result == '```cpp\n/* First */ const char *s = "Русский"; /* Second */\n```\n'


@pytest.mark.parametrize('stem', ['user-token', 'user-token-lifecycle'])
def test_original_mermaid_files_changed_labels_exact_bytes(stem):
    source = (FIXTURES / 'pr51079-mermaid' / (stem+'.ru.md')).read_bytes().decode()
    expected = (FIXTURES / 'pr51079-mermaid' / (stem+'.en.md')).read_bytes().decode()
    parser = create_parser()
    source_code = next(t.content for t in parser.parse(source) if t.type == 'fence')
    target_code = next(t.content for t in parser.parse(expected) if t.type == 'fence')
    source_labels = mermaid_labels(source_code)
    target_labels = mermaid_labels(target_code)
    assert source_labels and len(source_labels) == len(target_labels)
    protected = protect(source)
    assert not protected.issues
    text = protected.text
    # Replace occurrences in original order, once each, avoiding cascading replacements.
    parts = []
    pos = 0
    for old, new in zip(source_labels,target_labels,strict=True):
        start = text.index(old.text, pos)
        parts += [text[pos:start], new.text]
        pos = start+len(old.text)
    parts.append(text[pos:])
    assert restore(protected, ''.join(parts)) == expected


@pytest.mark.parametrize('injection', ['User\nend','User; end','User->>auth: injected'])
def test_sequence_label_injection_cannot_pass_translation(injection):
    class Client:
        def chat(self, messages, **kwargs):
            text = messages[-1]['content'].split('\n\n',1)[1]
            return SimpleNamespace(content=text.replace('Пользователь',injection),finish_reason='stop')
    result = translate_document('```mermaid\nsequenceDiagram\nactor user as Пользователь\n```\n',
        path='en/a.md',source_lang='ru',target_lang='en',client=Client(),
        choice=ModelChoice(Endpoint('eliza','https://example.test','model','token')),
        budget=RequestBudget(100000,1000,lambda m:len(str(m))))
    assert result.issues


@pytest.mark.parametrize('old,new,protected_change', [
    ('actor user','actor other',True),
    ('node->>cache','node-->>cache',True),
    ('Note right of cache:','Note right of node:',True),
    ('        else Permanent error','        opt Permanent error',True),
    ('life_time countdown','lifetime countdown',False),
    ('YDB node','Database node',False),
], ids=['actor','arrow','note-owner','branch','technical-token','technical-label'])
def test_original_mermaid_topology_damage(old,new,protected_change):
    from tests.unit.test_quality import Client, run
    source = (FIXTURES / 'pr51079-mermaid/user-token-lifecycle.ru.md').read_bytes().decode()
    target = (FIXTURES / 'pr51079-mermaid/user-token-lifecycle.en.md').read_bytes().decode()
    assert old in target
    assert not any(i.code == 'protected' for i in run(source,target).issues)
    mutated = target.replace(old,new)
    assert mutated != target
    client = Client()
    result = run(source,mutated,client)
    assert any(i.code == 'protected' for i in result.issues) is protected_change
    # Plain label wording is judged by critic, not a hardcoded technical vocabulary.
    assert client.calls
    assert new in json.dumps(client.calls,ensure_ascii=False)


def test_independent_findings_on_same_line_are_not_collapsed(git_repo, monkeypatch):
    import base64
    import re
    from dataclasses import asdict, replace
    from urllib.parse import urlsplit

    import requests

    from tests.contract.test_report_t14 import located
    from ydbdoc_review.github.client import GitHubClient
    from ydbdoc_review.quality import Issue, Location
    from ydbdoc_review.report import create_reporter

    result = located.__wrapped__(git_repo)
    first = Issue('ydb/docs/en/a.md', 'Missing authentication condition', 'Restore condition',
                  target=Location(3,3,'Final excerpt.'))
    second = Issue('ydb/docs/en/a.md', 'Incorrect permissions claim', 'Correct permissions',
                   target=Location(3,3,'Final excerpt.'))
    objects, refs, comments = {}, {}, {}

    def send(session, request, **kwargs):
        assert urlsplit(request.url).hostname == 'api.github.com'
        path = urlsplit(request.url).path
        data = json.loads(request.body)
        if '/git/' in path and request.method == 'POST':
            kind = path.rsplit('/', 1)[-1]
            if kind == 'refs':
                assert objects[data['sha']][0] == 'commits'
                refs[data['ref']] = data['sha']
                payload = {'ref': data['ref'], 'object': {'sha': data['sha']}}
            else:
                assert kind in ('blobs', 'trees', 'commits')
                sha = f'{len(objects) + 1:040x}'
                objects[sha] = (kind, data)
                payload = {'sha': sha}
        else:
            assert (request.method == 'POST' and path.endswith('/comments')) or (
                request.method == 'PATCH' and path.endswith('/pulls/2'))
            comments[path] = data['body']
            payload = {'id': len(comments)}
        response = requests.Response()
        response.status_code = 201
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(requests.Session, 'send', send)
    create_reporter(GitHubClient('offline-token'), current_pr='up/docs/1', authorized=True)(
        replace(result, issues=(first, second)))
    report = comments['/repos/up/docs/issues/2/comments']
    assert report.startswith('RED') and '#L3' in report
    assert 'Missing authentication condition' in report and 'Restore condition' in report
    # §6.1 permits one inline example per group; every independent finding must
    # survive actual delivery through the linked, retained GitData artifact.
    link = re.search(r'https://github.com/up/docs/blob/([0-9a-f]{40})/diagnostics.json', report)
    assert link and link[1] in refs.values()
    kind, commit = objects[link[1]]
    assert kind == 'commits'
    kind, tree = objects[commit['tree']]
    assert kind == 'trees'
    entry, = tree['tree']
    assert entry['path'] == 'diagnostics.json' and entry['type'] == 'blob'
    kind, blob = objects[entry['sha']]
    assert kind == 'blobs' and blob['encoding'] == 'base64'
    artifact = json.loads(base64.b64decode(blob['content'], validate=True))
    assert artifact['issues'] == [asdict(first), asdict(second)]
    assert artifact['checked_sha'] == result.checked_sha


def test_same_basename_link_targets_keep_independent_locations(git_repo):
    from tests.contract.test_links_build import ROOT, commit
    from ydbdoc_review.build import BuildResult
    from ydbdoc_review.links import check_links

    tree = commit(git_repo,{'en/a.md':'[left](left/part.md#kept)\n[right](right/part.md#missing)\n',
                            'en/left/part.md':'# Left {#kept}\n',
                            'en/right/part.md':'# Right {#other}\n'})
    build = BuildResult(tree.sha,'success',returncode=0,anchors={
        ROOT+'en/left/part.md':{'kept'},ROOT+'en/right/part.md':{'other'}})
    result = check_links(tree,build=build)
    assert not result.ok
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert 'right/part.md' in issue.problem and 'missing' in issue.problem
    assert (issue.target.start, issue.target.end) == (1, 2)
    issue.target.validate(tree.text(ROOT+'en/a.md'))
    assert 'right' in issue.target.quote
