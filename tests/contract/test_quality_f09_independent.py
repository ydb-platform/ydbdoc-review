
"""Independent acceptance boundaries, authored from REQUIREMENTS_RU §5.1."""
import pytest

from tests.contract.test_quality_loop_t09 import (
    BUDGET,
    GOOD,
    PATH,
    SOURCE,
    setup,  # noqa: F401 -- imported pytest fixture
)
from ydbdoc_review.build import BuildResult
from ydbdoc_review.quality_loop import SelectedFile, run_quality_loop

B = 'ydb/docs/en/b.md'

@pytest.mark.parametrize('first_changes', [False, True])
def test_fatal_second_file_after_first_file_boundary(setup, first_changes):  # noqa: F811 -- imported pytest fixture
    _, calls, freezes, builds, handler, client, choice, commit = setup
    before = commit({PATH: 'Остаток A', B: 'Остаток B', SOURCE: 'Alpha.',
                     'ydb/docs/ru/b.md': 'Beta.'})
    handler[0] = lambda op, data: GOOD if op == 'critic' else (
        'Alpha.' if first_changes else data['current_target'])
    record = client.record_request
    def fail_second_repair(request):
        # Actual model client's callback failure exercises real fatal classification.
        if request.operation == 'repair' and sum(op == 'repair' for op, _ in calls) >= 1:
            raise RuntimeError('independent B persistence failure')
        record(request)
    client.record_request = fail_second_repair
    def freeze(previous, updates):
        freezes.append(previous.sha)
        return commit(updates)
    def build(candidate):
        builds.append(candidate.sha)
        return BuildResult(candidate.sha, 'success', returncode=0)
    result = run_quality_loop(before, (SelectedFile(PATH, 'Alpha.', 'en'),
                                      SelectedFile(B, 'Beta.', 'en')),
        client=client, critic_choice=choice, repair_choice=choice, budget=BUDGET,
        freeze=freeze, build=build)
    assert result.status == 'RED'
    assert len(result.rounds) == len(builds) == (2 if first_changes else 1)
    assert len(set(builds)) == len(builds)
    assert result.checked_sha == result.candidate.sha == builds[-1]
    assert (result.candidate.sha != before.sha) == first_changes
    assert len(freezes) == int(first_changes)
    assert any('independent B persistence failure' in issue.problem for issue in result.issues)
    assert B in result.unfinished_files
    assert result.candidate.text(B) == 'Остаток B'
    assert {f.path for f in result.files} == {PATH, B}
    assert all(f.text == result.candidate.text(f.path) for f in result.files)
    critics = [data['path'] for op, data in calls if op == 'critic']
    assert critics == [PATH, B] * (2 if first_changes else 1)

