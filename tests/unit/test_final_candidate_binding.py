"""Exact Git object identity and byte-preserving review receipts."""

import subprocess
from dataclasses import replace

import pytest

from ydbdoc_review.pipeline.final_candidate import (
    bind_final_candidate,
    read_candidate_bytes,
    require_reviewed_candidate,
    review_final_candidate,
)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


@pytest.fixture
def candidate_repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    (tmp_path / "a.md").write_bytes("Привет\r\nlast\n".encode())
    (tmp_path / "empty.md").write_bytes(b"")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "candidate")
    sha = git(tmp_path, "rev-parse", "HEAD").decode().strip()
    return tmp_path, sha


def test_candidate_reader_preserves_raw_bytes(candidate_repo):
    repo, sha = candidate_repo
    candidate = bind_final_candidate(str(repo), candidate_sha=sha,
        en_paths=("empty.md", "./a.md", "a.md"), deleted_paths=("gone.md",))
    assert candidate.en_paths == ("a.md", "empty.md")
    assert candidate.tree_sha == git(repo, "rev-parse", f"{sha}^{{tree}}").decode().strip()
    (repo / "a.md").write_bytes(b"dirty\n")
    (repo / "gone.md").write_bytes(b"not part of candidate")
    assert read_candidate_bytes(str(repo), candidate, "a.md") == "Привет\r\nlast\n".encode()
    assert read_candidate_bytes(str(repo), candidate, "empty.md") == b""
    assert read_candidate_bytes(str(repo), candidate, "gone.md") is None
    assert read_candidate_bytes(str(repo), candidate, "missing.md") is None


@pytest.mark.parametrize("ref", ["HEAD", "main", "abc123"])
def test_candidate_requires_full_sha(candidate_repo, ref):
    repo, _ = candidate_repo
    with pytest.raises(ValueError):
        bind_final_candidate(str(repo), candidate_sha=ref, en_paths=(), deleted_paths=())


def test_receipt_binds_commit_and_tree(candidate_repo):
    repo, sha = candidate_repo
    candidate = bind_final_candidate(str(repo), candidate_sha=sha, en_paths=("a.md",), deleted_paths=())
    seen = []
    receipt = review_final_candidate(candidate, seen.append)
    assert seen == [candidate]
    require_reviewed_candidate(candidate, receipt)
    for wrong in (replace(receipt, candidate_sha="0" * 40), replace(receipt, candidate_tree_sha="0" * 40)):
        with pytest.raises(ValueError, match="candidate_review_mismatch"):
            require_reviewed_candidate(candidate, wrong)


def test_failed_review_does_not_return_receipt(candidate_repo):
    repo, sha = candidate_repo
    candidate = bind_final_candidate(str(repo), candidate_sha=sha, en_paths=(), deleted_paths=())
    def fail(_candidate):
        raise RuntimeError("critic unavailable")
    with pytest.raises(RuntimeError, match="critic unavailable"):
        review_final_candidate(candidate, fail)


def test_missing_authoritative_source_fails_closed(candidate_repo):
    from unittest.mock import MagicMock

    from tests.unit.test_github_workflow import _env, _fake_pr_result
    from ydbdoc_review.config.loader import load_config
    from ydbdoc_review.github.workflow import _review_translation_candidate
    from ydbdoc_review.translation.glossary import load_glossary

    repo, sha = candidate_repo
    result = _fake_pr_result()
    result.pair_results[0].plan = replace(result.pair_results[0].plan, target_path="a.md")
    candidate = bind_final_candidate(str(repo), candidate_sha=sha, en_paths=("a.md",), deleted_paths=())
    _review_translation_candidate(str(repo), candidate, result, MagicMock(), load_glossary(), load_config(env=_env()))
    assert result.pair_results[0].file_result.verdict == "blocked"
    assert result.pair_results[0].file_result.heuristic_blocking == [
        "final_candidate_review_failed: final_candidate_review_missing_text"
    ]
