"""Independent repeat acceptance: exact repair and whole-tree language gate.

Offline HTTP/Git/real-YFM fixture adapted from initial acceptance; new assertions.
"""
import json
from types import SimpleNamespace

import pytest
import requests
import yaml

from ydbdoc_review.build import build_candidate
from ydbdoc_review.document import RequestBudget
from ydbdoc_review.links import Candidate, check_links, references
from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient
from ydbdoc_review.quality_loop import SelectedFile, repair_document, run_quality_loop

pytestmark = pytest.mark.timeout(120)

P = 'ydb/docs/en/a.md'
RU = 'ydb/docs/ru/a.md'
GOOD = json.dumps({'complete': True, 'verdict': 'correct', 'issues': []})
BUDGET = RequestBudget(100000, 20000, lambda m: len(str(m)))


@pytest.fixture
def rig(git_repo, monkeypatch):
    repo, git = git_repo
    r = SimpleNamespace(calls=[], events=[], records=[], repairs=0)
    r.answer = lambda op, data: GOOD if op == 'critic' else data['source']
    r.client = ModelClient(record_request=r.records.append, record_attempt=lambda a: None)
    r.choice = ModelChoice(Endpoint('eliza', 'https://offline.invalid', 'test', 'dummy'))

    def send(session, request, **kwargs):
        op = r.records[-1].operation
        data = json.loads(json.loads(request.body)['messages'][-1]['content'])
        r.calls.append((op, data))
        r.events.append(op)
        answer = r.answer(op, data)
        if isinstance(answer, Exception):
            raise answer
        content, finish = answer if isinstance(answer, tuple) else (answer, 'stop')
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'choices': [{'message': {'content': content},
                                                    'finish_reason': finish}]}).encode()
        return response

    monkeypatch.setattr(requests.Session, 'send', send)

    def commit(files):
        for path, text in files.items():
            out = repo / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(text.encode() if isinstance(text, str) else text)
        git('add', '.')
        git('commit', '--allow-empty', '-m', 'independent fixture')
        return Candidate.open(repo, git('rev-parse', 'HEAD').decode().strip())

    def freeze(previous, updates):
        r.events.append('freeze')
        return commit(updates)

    def build(candidate):
        r.events.append(('build', candidate.sha))
        return build_candidate(candidate, timeout=30)

    def run(source='Hello.', target='Hello.', extra=None, initial=None, budget=BUDGET,
            builder=None, freezer=None, requested=()):
        tree = commit({RU: source, P: target,
                       'ydb/docs/toc.yaml': 'title: Test\nitems:\n  - name: Article\n    href: en/a.md\n',
                       **(extra or {})})
        result = run_quality_loop(tree, (SelectedFile(P, source, 'en', 'Keep approved wording.',
                                  initial=initial, requested_findings=requested),),
                                  client=r.client, critic_choice=r.choice, repair_choice=r.choice,
                                  budget=budget, freeze=freezer or freeze, build=builder or build)
        return tree, result

    r.run, r.commit, r.build, r.freeze = run, commit, build, freeze
    yield r
    r.client.close()



def repair(rig, source, mapping):
    result = repair_document(SelectedFile(P, source, 'en'), source, (),
                             replacements=mapping, client=rig.client,
                             choice=rig.choice, budget=BUDGET)
    assert result.complete, result.issues
    assert [op for op, _ in rig.calls] == ['repair']
    assert rig.calls[0][1]['current_target'] == source
    return result.text


@pytest.mark.parametrize('eol', ['\n', '\r\n', '\r'])
def test_same_url_many_owners_and_protected_bytes(rig, eol):
    source = eol.join([
        '[one](/ru/b.md) [two](/en/b.md) [prefix](/ru/b.md-extra)',
        '', '[ref][id]', '', '[id]: /ru/b.md "Keep /ru/b.md"', '',
        '<a href="/ru/b.md" data-value="/ru/b.md">Link</a>', '',
        '`/ru/b.md`', '', '```sh', 'curl /ru/b.md', '```', '',
    ])
    expected = eol.join([
        '[one](/en/b.md) [two](/final/b.md) [prefix](/ru/b.md-extra)',
        '', '[ref][id]', '', '[id]: /en/b.md "Keep /ru/b.md"', '',
        '<a href="/en/b.md" data-value="/ru/b.md">Link</a>', '',
        '`/ru/b.md`', '', '```sh', 'curl /ru/b.md', '```', '',
    ])
    assert repair(rig, source, {'/ru/b.md': '/en/b.md', '/en/b.md': '/final/b.md'}) == expected


@pytest.mark.parametrize('mapping', [{}, {'/absent.md': '/unused.md'}])
def test_noop_preserves_exact_bytes(rig, mapping):
    source = ('---\r\ntitle: "A \\u0026 B"\r\nconfig: /ru/b.md\r\n---\r\n'
              '[link](/ru/b.md "Keep")\r\n\r\n`/ru/b.md`  \r\n')
    assert repair(rig, source, mapping).encode() == source.encode()


def test_decoded_frontmatter_only_real_destinations(rig):
    source = ('---\ntitle: "[one](\\u002fru/b.md) `/ru/b.md` [prefix](/ru/b.md-extra)"\n'
              'description: "[two](/en/b.md)"\nconfig: /ru/b.md # keep\n---\n'
              '[body](/ru/b.md)\n')
    result = repair(rig, source, {'/ru/b.md': '/en/b.md', '/en/b.md': '/final/b.md'})
    metadata = yaml.safe_load(result.split('---\n')[1])
    assert metadata['title'] == '[one](/en/b.md) `/ru/b.md` [prefix](/ru/b.md-extra)'
    assert metadata['description'] == '[two](/final/b.md)'
    assert 'config: /ru/b.md # keep\n' in result
    assert result.endswith('---\n[body](/en/b.md)\n')


@pytest.mark.parametrize('syntax', ['markdown', 'html'])
def test_common_links_rejects_unselected_unusual_extension(rig, syntax):
    link = ('[Russian](/ru/b.md-extra)' if syntax == 'markdown'
            else '<a href="/ru/b.md-extra">Russian</a>')
    tree = rig.commit({P: 'Hello.', RU: 'Hello.', 'ydb/docs/en/other.md': link,
                       'ydb/docs/ru/b.md-extra': 'Russian page.'})
    result = check_links(tree)
    assert result.complete
    assert any(i.path == 'ydb/docs/en/other.md' and '/ru/b.md-extra' in i.problem
               for i in result.issues), result


def test_whole_tree_loop_rejects_unselected_unusual_extension(rig):
    other = '[Russian](/ru/b.md-extra)'
    original, result = rig.run(extra={'ydb/docs/en/other.md': other,
                                     'ydb/docs/ru/b.md-extra': 'Russian page.'})
    assert all(r.build.ok_for(r.candidate_sha) for r in result.rounds)
    assert result.checked_sha == result.candidate.sha == original.sha
    assert result.candidate.text('ydb/docs/en/other.md') == other
    assert result.candidate.read(RU) == original.read(RU)
    assert 'freeze' not in rig.events
    assert all(op == 'critic' for op, _ in rig.calls)
    assert len(result.rounds) <= 3
    assert result.status == 'RED', (result.status, result.issues, result.rounds[-1].links)
    assert any(i.path == 'ydb/docs/en/other.md' and '/ru/b.md-extra' in i.problem
               for i in result.issues)


@pytest.mark.parametrize(('source', 'old', 'new', 'expected'), [
    ('[link](/ru/b.md?a=1&amp;b=2)', '/ru/b.md?a=1&b=2', '/en/b.md?a=1&b=2',
     '[link](/en/b.md?a=1&b=2)'),
    (r'[link](/ru/b\(1\).md)', '/ru/b(1).md', '/en/b(1).md',
     r'[link](/en/b\(1\).md)'),
])
def test_escaped_complete_url_repair_regression(rig, source, old, new, expected):
    # Known adjacent T07 defect stays a strict assertion, no xfail/weakening.
    result = repair(rig, source, {old: new})
    assert [r.href for r in references(result)] == [new], (result, expected)
