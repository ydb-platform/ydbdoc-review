from unittest.mock import Mock

import pytest

from ydbdoc_review.github.restart import stop_previous_runs


def worker(n, pr=42, status='in_progress'):
    return {'id': n, 'workflow_id': 7, 'status': status, 'pull_requests': [{'number': pr}]}


def test_cancel_wait_and_only_same_pr():
    gh = Mock()
    gh._request.side_effect = [worker(10), {'workflow_runs': [worker(9), worker(8, 43), worker(10)]},
                               None, {'status': 'in_progress'}, {'status': 'completed'}]
    stop_previous_runs(gh, 'o', 'r', 42, 10, poll_interval=0)
    calls = gh._request.call_args_list
    assert [(c.args[0], c.args[1].split('/')[-2:]) for c in calls[2:]] == [
        ('POST', ['9', 'cancel']), ('GET', ['runs', '9']), ('GET', ['runs', '9'])]


def test_unconfirmed_stop_fails_closed():
    gh = Mock()
    gh._request.side_effect = [worker(10), {'workflow_runs': [worker(9)]}, None, {'status': 'in_progress'}]
    with pytest.raises(RuntimeError, match='stop not confirmed'):
        stop_previous_runs(gh, 'o', 'r', 42, 10, timeout=0)


def test_older_worker_does_not_cancel_newer():
    gh = Mock()
    gh._request.side_effect = [worker(10), {'workflow_runs': [worker(11)]}]
    with pytest.raises(RuntimeError, match='superseded'):
        stop_previous_runs(gh, 'o', 'r', 42, 10)
    assert all(c.args[0] == 'GET' for c in gh._request.call_args_list)


def test_restart_removes_owned_branch_left_before_pr_creation():
    from ydbdoc_review.github import workflow
    gh = Mock()
    gh.find_open_pull_by_head.return_value = None
    gh.get_branch_sha.return_value = "a" * 40
    gh._request.return_value = {"commit": {
        "author": {"name": workflow._GITHUB_ACTOR_NAME, "email": workflow._GITHUB_ACTOR_EMAIL},
        "message": "Auto-translate docs from PR #42\n\nTranslated files",
    }}
    assert workflow._restart_owned_translation_pr(
        gh, 'o', 'r', source_pr=42, branch='ydbdoc-review/pr-42', base='main', explicit=True)
    gh.delete_branch.assert_called_once_with('o', 'r', 'ydbdoc-review/pr-42')
    gh.close_pull.assert_not_called()


def test_restart_does_not_delete_unowned_branch_without_pr():
    from ydbdoc_review.github import workflow
    gh = Mock()
    gh.find_open_pull_by_head.return_value = None
    gh.get_branch_sha.return_value = "a" * 40
    gh._request.return_value = {"commit": {"author": {"name": "human"}, "message": "Work"}}
    with pytest.raises(RuntimeError, match="cannot prove ownership"):
        workflow._restart_owned_translation_pr(
            gh, 'o', 'r', source_pr=42, branch='ydbdoc-review/pr-42', base='main', explicit=True)
    gh.delete_branch.assert_not_called()
