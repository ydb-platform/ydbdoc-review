"""Runtime glossary reaches initial translation; HTTP and builder are offline fakes."""
import json

from ydbdoc_review.build import BuildResult

from . import test_t10_independent as fixtures

rig = fixtures.rig


def test_runner_initial_translation_receives_explicit_glossary(rig):
    state, run, commit, *_ = rig
    commit({'ru/unrelated.md': 'UNRELATED_CONTENT_DO_NOT_SEND'})
    result = run(glossary=(('Hello', 'Hello'),),
                 build=lambda candidate: BuildResult(candidate.sha, 'success', returncode=0))
    assert result.status == 'GREEN', (result.errors, result.issues)
    requests = [r for r in state['records'] if r.operation == 'translation']
    assert len(requests) == 1
    system = requests[0].payload['messages'][0]['content']
    assert 'YDB' in system
    assert '"Hello": "Hello"' in system
    assert 'caller-supplied inline glossary mapping' in system
    assert 'UNRELATED_CONTENT_DO_NOT_SEND' not in json.dumps([r.payload for r in state['records']])
