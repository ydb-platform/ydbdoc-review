"""§5.1: retry quality gates only after a changed candidate."""
import pytest

from tests.contract.test_quality_loop_t09 import GOOD, PATH, setup  # noqa: F401
from ydbdoc_review.build import BuildResult
from ydbdoc_review.quality import Issue


@pytest.mark.parametrize('failure', ['noop', 'missing', 'invalid', 'preparation', 'global_build'])
def test_unchanged_candidate_stops_red_with_original_and_stop_reasons(setup, monkeypatch, failure):
    run, calls, freezes, builds, handler, *_ = setup
    kwargs = {}
    if failure == 'global_build':
        def build(candidate):
            builds.append(candidate.sha)
            return BuildResult(candidate.sha, 'failure', returncode=1)
        kwargs['build'] = build
        target = 'Hello.'
    else:
        target = 'Остаток'
        handler[0] = lambda op, data: GOOD if op == 'critic' else {
            'noop': target, 'missing': '', 'invalid': 'Broken ⟦marker',
            'preparation': target,
        }[failure]
    if failure == 'preparation':
        import ydbdoc_review.quality_loop as loop
        original = loop.repair_document
        def repair(*args, **kwargs):
            if kwargs.get('prepare_only'):
                return original(*args, **kwargs)
            return loop.RepairResult(PATH, None, False,
                (Issue(PATH, 'Repair preparation exceeds capacity', 'Reduce request'),), ())
        monkeypatch.setattr(loop, 'repair_document', repair)
    before, result = run(target=target, **kwargs)
    assert result.status == 'RED'
    assert result.checked_sha == result.candidate.sha == before.sha
    assert len(result.rounds) == len(builds) == 1
    assert [op for op, _ in calls].count('critic') == 1
    assert [op for op, _ in calls].count('repair') <= 1
    assert freezes == []
    assert any('unchanged' in issue.problem.lower() for issue in result.issues)
    assert len(result.issues) > 1
    assert result.files and result.files[0].chunks
    if failure == 'preparation':
        assert any('exceeds capacity' in issue.problem for issue in result.issues)
    elif failure in {'missing', 'invalid'}:
        assert any('Repair part' in issue.problem for issue in result.issues)


def test_change_then_noop_checks_new_sha_once_and_keeps_correspondence(setup):
    run, calls, freezes, builds, handler, *_ = setup
    handler[0] = lambda op, data: GOOD if op == 'critic' else 'Остаток 1'
    before, result = run(target='Остаток 0')
    assert result.status == 'RED'
    assert len(result.rounds) == len(builds) == 2
    assert len(freezes) == 1
    assert builds[0] == before.sha
    assert builds[1] == result.checked_sha == result.candidate.sha != before.sha
    assert [op for op, _ in calls] == ['critic', 'repair', 'critic', 'repair']
    assert result.files[0] == result.rounds[-1].repairs[0].file_result
    assert result.files[0].text == result.candidate.text(PATH)


def test_partial_repair_with_fatal_error_still_checks_changed_candidate(setup, monkeypatch):
    """A later failed part must not leave earlier committed repairs unchecked."""
    from dataclasses import replace
    import ydbdoc_review.quality_loop as loop
    original = loop.repair_document
    def repair(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get('prepare_only'):
            return result
        return replace(result, complete=False, fatal=True,
                       issues=(Issue(PATH, 'Later repair failed', 'Complete repair'),))
    monkeypatch.setattr(loop, 'repair_document', repair)
    run, _, freezes, builds, *_ = setup
    before, result = run(target='Остаток')
    assert result.status == 'RED'
    assert len(result.rounds) == len(builds) == 2
    assert len(freezes) == 1
    assert result.checked_sha == result.candidate.sha != before.sha
    assert result.files[0].text == result.candidate.text(PATH)
    assert any('Later repair failed' in issue.problem for issue in result.issues)


def test_completed_repair_record_without_changed_bytes_is_still_noop(setup):
    """§5.1's unchanged-candidate stop is not bypassed by new response metadata."""
    from ydbdoc_review.document import ChunkResult, assemble_file, make_chunk, protect
    source = 'Hello.'
    document = protect(source, path=PATH)
    initial = assemble_file(PATH, document, (
        ChunkResult(make_chunk(document, 0, 0, len(document.text)), document.text,
                    source, unfinished=True, status='truncated'),))
    run, _, freezes, builds, *_ = setup
    before, result = run(source=source, target=source, initial=initial)
    assert result.status == 'RED'
    assert result.checked_sha == result.candidate.sha == before.sha
    assert len(result.rounds) == len(builds) == 1 and not freezes
    repaired = result.rounds[0].repairs[0]
    assert repaired.complete and repaired.file_result != initial
    assert repaired.file_result.text == initial.text
    assert repaired.file_result.chunks[0].status == 'complete'
    assert repaired.file_result.chunks[0].response_ref
    assert result.files == (repaired.file_result,)
    assert any('unchanged' in issue.problem.lower() for issue in result.issues)
