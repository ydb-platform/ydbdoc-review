"""Real document/quality/links/Git/transport; only HTTP and builder are mocked."""
import json
from dataclasses import FrozenInstanceError

import pytest
import requests

from ydbdoc_review.build import BuildResult
from ydbdoc_review.document import DocumentIssue, FileResult, RequestBudget
from ydbdoc_review.links import Candidate
from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient
from ydbdoc_review.quality import Issue, Location
from ydbdoc_review.quality_loop import SelectedFile, repair_document, run_quality_loop

PATH = 'ydb/docs/en/a.md'
SOURCE = 'ydb/docs/ru/a.md'
GOOD = json.dumps(dict(complete=True, verdict='correct', issues=[]))
BUDGET = RequestBudget(100000, 20000, lambda m: len(str(m)))


@pytest.fixture
def setup(git_repo, monkeypatch):
    repo, git = git_repo
    calls, freezes, builds = [], [], []
    handler = [lambda op, data: GOOD if op == 'critic' else data['source']]
    records = []
    client = ModelClient(record_request=records.append, record_attempt=lambda r: None)
    choice = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'main', 'dummy'),
                         Endpoint('eliza', 'https://model.invalid', 'alt', 'dummy'))
    def send(session, request, **kwargs):
        record = records[-1]
        data = json.loads(json.loads(request.body)['messages'][-1]['content'])
        calls.append((record.operation, data))
        output = handler[0](record.operation, data)
        if isinstance(output, Exception):
            raise output
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(dict(choices=[dict(message=dict(content=output),
                                                         finish_reason='stop')])).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    def commit(files):
        for path, data in files.items():
            dest = repo / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data.encode() if isinstance(data, str) else data)
        git('add', '.')
        git('commit', '--allow-empty', '-m', 'fixture')
        return Candidate.open(repo, git('rev-parse', 'HEAD').decode().strip())
    def freeze(previous, updates):
        freezes.append((previous.sha, dict(updates)))
        return commit(updates)
    def build(candidate):
        builds.append(candidate.sha)
        return BuildResult(candidate.sha, 'success', returncode=0)
    def run(source='Hello.', target='Hello.', initial=None, extra=None, **kwargs):
        candidate = commit({SOURCE: source, PATH: target, **(extra or {})})
        files = (SelectedFile(PATH, source, 'en', 'Fix only this article.', initial=initial),)
        result = run_quality_loop(candidate, files, client=client, critic_choice=choice,
                                  repair_choice=choice, budget=BUDGET, freeze=kwargs.pop('freeze', freeze),
                                  build=kwargs.pop('build', build), **kwargs)
        return candidate, result
    return run, calls, freezes, builds, handler, client, choice, commit


def test_early_success_one_check_no_repair_and_immutable_bytes(setup):
    run, calls, freezes, builds, _, _, _, _ = setup
    candidate, result = run()
    assert result.status == 'GREEN'
    assert result.checked_sha == result.candidate.sha == candidate.sha
    assert len(result.rounds) == len(builds) == 1
    assert [op for op, _ in calls] == ['critic']
    assert freezes == []
    with pytest.raises(FrozenInstanceError):
        result.status = 'RED'
    (candidate.repo / PATH).write_text('Dirty worktree')
    assert result.candidate.text(PATH) == 'Hello.'


def test_errors_exact_three_checks_two_repairs_current_target(setup):
    run, calls, freezes, builds, handler, *_ = setup
    n = [0]
    def respond(op, data):
        if op == 'critic':
            return GOOD
        n[0] += 1
        return 'Остаток ' + str(n[0])
    handler[0] = respond
    old, result = run(target='Остаток 0')
    assert result.status == 'RED'
    assert len(result.rounds) == len(builds) == 3
    assert len(freezes) == 2
    assert [op for op, _ in calls] == ['critic', 'repair', 'critic', 'repair', 'critic']
    repair_inputs = [d for op, d in calls if op == 'repair']
    assert [d['current_target'] for d in repair_inputs] == ['Остаток 0', 'Остаток 1']
    assert all(d['instruction'] and d['findings'] for d in repair_inputs)
    assert result.candidate.text(PATH) == 'Остаток 2'
    assert result.checked_sha == builds[-1] == result.candidate.sha
    assert result.candidate.read(SOURCE) == old.read(SOURCE)


def test_initial_malformed_markers_repairable_without_nested_loop(setup):
    run, calls, freezes, _, _, *_ = setup
    bad = 'Hello ⟦broken'
    initial = FileResult(PATH, bad, (DocumentIssue('Broken marker'),), True)
    _, result = run(source='Hello `code`.', target=bad, initial=initial)
    assert result.status == 'GREEN'
    assert result.candidate.text(PATH) == 'Hello `code`.'
    assert len(result.rounds) == 2 and len(freezes) == 1
    assert [op for op, _ in calls] == ['critic', 'repair', 'critic']
    assert not result.unfinished_files


def test_safe_url_replacement_and_unselected_bytes(setup):
    run, calls, freezes, _, _, *_ = setup
    source = '[Link](/ru/b.md)'
    extra = {'ydb/docs/ru/b.md': 'B.', 'ydb/docs/en/b.md': 'B.',
             'ydb/docs/en/untouched.md': 'Successful untouched.'}
    before, result = run(source=source, target=source, extra=extra)
    assert result.status == 'GREEN'
    assert result.candidate.text(PATH) == '[Link](/en/b.md)'
    assert len(freezes) == 1
    for path in (SOURCE, *extra):
        assert result.candidate.read(path) == before.read(path)
    assert all(d['path'] == PATH for _, d in calls)


@pytest.mark.parametrize('kind', ['invalid_marker', 'missing_url', 'incomplete_critic'])
def test_invalid_outputs_no_extra_loops(setup, kind):
    run, calls, freezes, _, handler, *_ = setup
    if kind == 'invalid_marker':
        handler[0] = lambda op, d: GOOD if op == 'critic' else 'Broken ⟦marker'
        source, target = 'Hello `code`.', 'Broken ⟦marker'
    elif kind == 'missing_url':
        source = target = '[Link](/ru/missing.md)'
    else:
        handler[0] = lambda op, d: 'invalid JSON' if op == 'critic' else d['source']
        source = target = 'Hello.'
    _, result = run(source=source, target=target)
    assert result.status == 'RED'
    # §5.1: malformed/unchanged repairs do not justify another check.
    assert len(result.rounds) == 1
    assert [op for op, _ in calls].count('critic') == 1
    assert [op for op, _ in calls].count('repair') == 1
    assert len(freezes) == 0


@pytest.mark.parametrize('kind', ['pending', 'wrong_sha', 'exception'])
def test_build_cannot_be_missing_or_unchecked_green(setup, kind):
    run, *_ = setup
    def build(candidate):
        if kind == 'exception':
            raise RuntimeError('Builder unavailable')
        return BuildResult('f' * 40 if kind == 'wrong_sha' else candidate.sha,
                           'pending' if kind == 'pending' else 'success', returncode=0)
    _, result = run(build=build)
    assert result.status == 'RED'
    assert any(i.code == 'loop_incomplete' for i in result.issues)


def test_global_tree_links_checked_even_unselected(setup):
    run, calls, freezes, *_ = setup
    _, result = run(extra={'ydb/docs/en/other.md': '[gone](gone.md)'})
    assert result.status == 'RED'
    assert any(i.path.endswith('other.md') for i in result.issues)
    assert all(op == 'critic' and d['path'] == PATH for op, d in calls)
    assert freezes == []


def test_freeze_rejects_unselected_mutation(setup):
    run, _, _, _, _, _, _, commit = setup
    def freeze(previous, updates):
        return commit({**updates, SOURCE: 'Changed source'})
    _, result = run(target='Остаток', freeze=freeze)
    assert result.status == 'RED'
    assert any('unselected/source' in i.problem for i in result.issues)


def test_repair_long_document_complete_order_and_transport_fallback(setup):
    _, calls, _, _, handler, client, choice, _ = setup
    source = '\n\n'.join(f'Sentence number {i} is useful.' for i in range(30))
    count = [0]
    def respond(op, data):
        count[0] += 1
        if count[0] == 1:
            return requests.exceptions.Timeout('timeout')
        return data['source']
    handler[0] = respond
    findings = tuple(Issue(PATH, 'Meaning needs repair', 'Correct this sentence',
                           source=Location(2*i + 1, 2*i + 1, f'Sentence number {i} is useful.'))
                     for i in range(30))
    result = repair_document(SelectedFile(PATH, source, 'en'), source, findings, replacements={},
                             client=client, choice=choice,
                             budget=RequestBudget(3000, 350, lambda m: len(str(m))))
    assert result.complete and result.text == source
    assert len(result.parts) > 1
    assert result.parts[0].attempt_end - result.parts[0].attempt_start == 2
    assert len(calls) == len(result.parts) + 1
    requests_seen = [d for _, d in calls][1:]
    assert ''.join(d['current_target'] for d in requests_seen) == source
    assert all(op == 'repair' for op, _ in calls)


def test_repair_callback_failure_is_terminal_red(setup):
    run, calls, _, _, _, client, *_ = setup
    record_original = client.record_request
    def fail(record):
        if record.operation == 'repair':
            raise RuntimeError('storage unavailable')
        record_original(record)
    client.record_request = fail
    _, result = run(target='Остаток')
    assert result.status == 'RED'
    assert len(result.rounds) == 1
    assert result.rounds[0].repairs[0].fatal
    assert any('storage unavailable' in i.problem for i in result.issues)
    assert [op for op, _ in calls] == ['critic']


@pytest.mark.parametrize('source,target', [
    ('---\ntitle: Hello\nconfig: fixed\n---\n\nHello `code`.', 'Damaged'),
    ('A single indivisible sentence ' * 300, 'Damaged'),
])
def test_front_matter_repair_or_indivisible_incomplete(setup, source, target):
    _, _, _, _, _, client, choice, _ = setup
    result = repair_document(SelectedFile(PATH, source, 'en'), target,
                             (Issue(PATH, 'Damaged target', 'Restore protected structure'),), replacements={},
                             client=client, choice=choice,
                             budget=BUDGET if source.startswith('---') else
                             RequestBudget(1600, 350, lambda m: len(str(m))))
    if source.startswith('---'):
        assert result.complete and result.text == source
    else:
        assert not result.complete and not result.parts


def test_success_on_final_check_and_no_late_mutation(setup):
    run, calls, freezes, builds, handler, *_ = setup
    counter = [0]
    def respond(op, data):
        if op == 'critic':
            return GOOD
        counter[0] += 1
        return 'Остаток' if counter[0] == 1 else data['source']
    handler[0] = respond
    _, result = run(target='Ошибка')
    assert result.status == 'GREEN'
    assert len(result.rounds) == len(builds) == 3
    assert len(freezes) == 2
    assert result.rounds[-1].repairs == ()
    assert result.candidate.text(PATH) == 'Hello.'
    assert result.candidate.sha == builds[-1] == result.checked_sha
    assert [op for op, _ in calls][-1] == 'critic'


def test_saved_instruction_findings_trigger_repair_and_noop_no_commit(setup):
    from ydbdoc_review.quality import Issue
    _, calls, _, _, _, client, choice, commit = setup
    candidate = commit({SOURCE: 'Hello.', PATH: 'Hello.'})
    freezes = []
    def freeze(*args):
        freezes.append(args)
        raise AssertionError('Noop must not commit')
    selected = SelectedFile(PATH, 'Hello.', 'en', 'Keep the existing wording.',
                            requested_findings=(Issue(PATH, 'Review requested wording',
                                                      'Keep wording if already correct'),))
    result = run_quality_loop(candidate, (selected,), client=client, critic_choice=choice,
                              repair_choice=choice, budget=BUDGET, freeze=freeze,
                              build=lambda c: BuildResult(c.sha, 'success', returncode=0))
    # A no-op is not a new checked candidate and cannot erase requested errors.
    assert result.status == 'RED' and result.candidate.sha == candidate.sha
    assert len(result.rounds) == 1 and freezes == []
    assert [op for op, _ in calls] == ['critic', 'repair']
    assert calls[1][1]['instruction'] == selected.instruction
