"""Independent T09 acceptance. Real core, Git and YFM; fake external model HTTP only."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests

from ydbdoc_review.build import build_candidate
from ydbdoc_review.document import DocumentIssue, FileResult, RequestBudget, protect, restore
from ydbdoc_review.links import Candidate
from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient
from ydbdoc_review.quality import Issue, Location
from ydbdoc_review.quality_loop import SelectedFile, repair_document, run_quality_loop

# Up to three real CLI builds; each subprocess has its own 30s bound.
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


@pytest.mark.parametrize('rounds', [1, 2, 3])
def test_bound_early_stop_final_sha_and_current_context(rig, rounds):
    def answer(op, data):
        if op == 'critic':
            return GOOD
        rig.repairs += 1
        return 'Hello.' if rig.repairs == rounds - 1 else f'Остаток {rig.repairs}'
    rig.answer = answer
    original, result = rig.run(target='Hello.' if rounds == 1 else 'Остаток 0',
                              extra={'ydb/docs/en/kept.md': 'Approved.'})
    assert result.status == 'GREEN', result.issues
    assert len(result.rounds) == rounds
    assert [op for op, _ in rig.calls] == ['critic', 'repair'] * (rounds - 1) + ['critic']
    assert rig.events.count('freeze') == rounds - 1
    assert result.checked_sha == result.candidate.sha == result.rounds[-1].candidate_sha
    assert result.rounds[-1].repairs == ()
    assert rig.events[-1] == 'critic'
    for n, data in enumerate(d for op, d in rig.calls if op == 'repair'):
        assert data['source'] == protect('Hello.').text
        assert data['current_target'] == f'Остаток {n}'
        assert data['findings'] and data['instruction'] == 'Keep approved wording.'
    assert result.candidate.read(RU) == original.read(RU)
    assert result.candidate.read('ydb/docs/en/kept.md') == original.read('ydb/docs/en/kept.md')
    (result.candidate.repo / P).write_text('Unverified dirty bytes')
    assert result.candidate.text(P) == 'Hello.'


@pytest.mark.parametrize('failure', ['residual', 'critic_json', 'critic_partial', 'repair_empty',
                                     'repair_length', 'repair_network', 'repair_marker'])
def test_errors_partial_unfinished_are_red_and_bounded(rig, failure):
    def answer(op, data):
        if op == 'critic':
            if failure == 'critic_json':
                return '{broken'
            if failure == 'critic_partial':
                return json.dumps({'complete': False, 'verdict': 'correct', 'issues': []})
            return GOOD
        return {'residual': 'Остаток', 'repair_empty': '',
                'repair_length': ('Hello.', 'length'),
                'repair_network': requests.exceptions.Timeout('offline timeout'),
                'repair_marker': '⟦broken'}.get(failure, data['source'])
    rig.answer = answer
    _, result = rig.run(target='Остаток')
    assert result.status == 'RED'
    # Invalid critic responses allow one changed repair, then stop on no-op.
    expected = 2 if failure in {'critic_json', 'critic_partial'} else 1
    assert len(result.rounds) == expected
    assert len([op for op, _ in rig.calls if op == 'repair']) == expected
    assert result.rounds[-1].repairs
    assert result.issues
    if failure.startswith('repair_'):
        assert result.unfinished_files == (P,)
        assert 'freeze' not in rig.events


def test_initial_damaged_markers_use_same_repair_loop(rig):
    bad = 'Broken ⟦marker'
    initial = FileResult(P, bad, (DocumentIssue('Missing markers'),), True)
    _, result = rig.run(source='Hello `x`.', target=bad, initial=initial)
    assert result.status == 'GREEN', result.issues
    assert len(result.rounds) == 2
    assert result.candidate.text(P) == 'Hello `x`.'
    assert not result.unfinished_files


@pytest.mark.parametrize('gate', ['pending', 'wrong_sha', 'exception', 'missing', 'links'])
def test_mandatory_build_and_whole_tree_links(rig, gate):
    def builder(tree):
        actual = rig.build(tree)
        assert actual.ok_for(tree.sha), actual.log
        if gate == 'exception':
            raise RuntimeError('Unavailable build')
        if gate == 'missing':
            return None
        return replace(actual, status='pending') if gate == 'pending' else replace(actual, candidate_sha='0' * 40)
    _, result = rig.run(builder=None if gate == 'links' else builder,
                        extra={'ydb/docs/en/other.md': '[gone](absent.md)'} if gate == 'links' else None)
    assert result.status == 'RED'
    assert 'freeze' not in rig.events
    assert all(op == 'critic' for op, _ in rig.calls)
    assert result.issues


def test_long_repair_complete_source_current_coverage(rig):
    source = '\n\n'.join(f'Section {i} has exact facts and `code{i}`.' for i in range(60))
    current = '\n\n'.join(f'Section {i} has current wording and `code{i}`.' for i in range(60))
    findings = tuple(Issue(P, 'Incorrect fact', 'Restore source fact',
                           source=Location(2*i+1, 2*i+1, f'Section {i} has exact facts and `code{i}`.'))
                     for i in range(60))
    result = repair_document(SelectedFile(P, source, 'en', 'Keep facts.'), current, findings,
                             replacements={}, client=rig.client, choice=rig.choice,
                             budget=RequestBudget(4000, 650, lambda m: len(str(m))))
    assert result.complete, result.issues
    assert len(result.parts) > 1
    assert result.text == source
    assert ''.join(d['current_target'] for _, d in rig.calls) == current
    assert ''.join(d['source'] for _, d in rig.calls) == protect(source).text
    assert all(op == 'repair' for op, _ in rig.calls)
    assert all(p.attempt_end - p.attempt_start == 1 for p in result.parts)
    assert restore(protect(source), ''.join(p.response for p in result.parts)) == source


@pytest.mark.parametrize('protected', ['`/ru/b.md`', '[suffix](/ru/b.md-extra)',
                                      '```sh\ncurl /ru/b.md\n```',
                                      '---\nconfig: /ru/b.md\n---'])
def test_t09_repair_mutation_replaces_only_exact_link_destination(rig, protected):
    source = protected + '\n\n[allowed](/ru/b.md)'
    result = repair_document(SelectedFile(P, source, 'en'), source, (),
                             replacements={'/ru/b.md': '/en/b.md'}, client=rig.client,
                             choice=rig.choice, budget=BUDGET)
    assert result.complete, result.issues
    assert '[allowed](/en/b.md)' in result.text
    assert protected in result.text, f'T09 itself mutated protected bytes: {result.text!r}'


def test_real_loop_preserves_inline_code_during_allowed_link_repair(rig):
    source = '[allowed](/ru/b.md) and `/ru/b.md`.'
    _, result = rig.run(source=source, target=source,
                        extra={'ydb/docs/ru/b.md': 'B.', 'ydb/docs/en/b.md': 'B.'})
    assert result.rounds[0].repairs[0].complete
    repaired = result.rounds[0].repairs[0].text
    print('T09 MUTATION:', result.status, result.checked_sha, result.candidate.text(P))
    assert repaired == '[allowed](/en/b.md) and `/ru/b.md`.', repaired
    assert result.candidate.text(P) == repaired
    assert result.status == 'GREEN', result.issues


def test_absent_english_destination_is_never_invented(rig):
    source = '[allowed](/ru/b.md)'
    _, result = rig.run(source=source, target=source, extra={'ydb/docs/ru/b.md': 'B.'})
    assert result.status == 'RED'
    assert result.candidate.text(P) == source
    assert '/en/b.md' not in result.candidate.text(P)
    assert 'freeze' not in rig.events


def test_freeze_source_mutation_rejected(rig):
    def bad_freeze(previous, updates):
        return rig.commit({**updates, RU: 'Tampered'})
    before, result = rig.run(target='Ошибка', freezer=bad_freeze)
    assert result.status == 'RED'
    assert result.candidate.sha == before.sha
    assert any('unselected/source' in i.problem for i in result.issues)


def test_saved_finding_triggers_no_initial_translation(rig):
    finding = Issue(P, 'Check requested wording', 'Keep approved wording.')
    _, result = rig.run(requested=(finding,))
    assert result.status == 'RED'  # §5.1: no-op cannot clear the requested error
    assert [op for op, _ in rig.calls] == ['critic', 'repair']
    assert 'freeze' not in rig.events
    assert rig.calls[1][1]['findings'][0]['problem'] == finding.problem


def test_real_loop_prefix_url_is_not_authorized_by_shorter_url(rig):
    source = '[allowed](/ru/b.md) [suffix](/ru/b.md-extra)'
    _, result = rig.run(source=source, target=source,
                        extra={'ydb/docs/ru/b.md': 'B.', 'ydb/docs/en/b.md': 'B.',
                               'ydb/docs/ru/b.md-extra': 'Suffix.'})
    repair = result.rounds[0].repairs[0]
    print('T09 PREFIX MUTATION:', result.status, result.checked_sha, result.candidate.text(P))
    assert repair.complete, repair.issues
    assert repair.text == '[allowed](/en/b.md) [suffix](/ru/b.md-extra)', repair.text
    assert result.status == 'RED'


def test_real_loop_exact_allowed_url_succeeds(rig):
    source = '[allowed](/ru/b.md)'
    _, result = rig.run(source=source, target=source,
                        extra={'ydb/docs/ru/b.md': 'B.', 'ydb/docs/en/b.md': 'B.'})
    assert result.status == 'GREEN', result.issues
    assert result.candidate.text(P) == '[allowed](/en/b.md)'
    assert len(result.rounds) == 2
    assert result.checked_sha == result.candidate.sha


def test_long_document_loop_trace_separates_rounds_and_chunk_calls(rig):
    source = '\n\n'.join(f'Section {i} has exact facts and `code{i}`.' for i in range(60))
    findings = tuple(Issue(P, 'Incorrect fact', 'Restore source fact',
                           source=Location(2*i+1, 2*i+1, f'Section {i} has exact facts and `code{i}`.'))
                     for i in range(60))
    # Keep coverage of multiple review rounds by supplying an actual defect;
    # identical repaired bytes must now stop after one round (§5.1).
    current = source.replace('exact facts', 'wrong facts')
    _, result = rig.run(source=source, target=current, requested=findings,
                        budget=RequestBudget(5000, 800, lambda m: len(str(m))))
    assert result.status == 'GREEN', result.issues
    assert len(result.rounds) == 2
    assert len(result.rounds[0].repairs) == 1
    assert len(result.rounds[0].repairs[0].parts) > 1
    assert result.candidate.text(P) == source
    assert 'freeze' in rig.events
    for trace in result.rounds:
        assert trace.checks[0].ok
        assert ''.join(d['source'] for op, d in rig.calls[
            trace.critic_attempt_start:trace.critic_attempt_end] if op == 'critic') == source
    repairs = [d for op, d in rig.calls if op == 'repair']
    assert ''.join(d['current_target'] for d in repairs) == current
