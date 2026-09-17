"""T03 snapshot and translation planning contracts, before runner integration."""

from functools import partial
from types import SimpleNamespace

import pytest

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.errors import GitHubAPIError
from ydbdoc_review.plan import (
    ChangedFile,
    PlanError,
    Snapshot,
    build_plan,
    freeze_snapshot,
    list_changes,
    read_at_sha,
)

BASE, HEAD, CURRENT = "a" * 40, "b" * 40, "c" * 40
ROOT = "ydb/docs/"


def snapshot(**kwargs):
    return Snapshot("o", "r", 1, kwargs.get("source_sha", HEAD), HEAD, "feature", "o/r")


def fake_client(merged=False):
    return SimpleNamespace(
        get_pull=lambda *args: dict(
            merged=merged,
            state="closed" if merged else "open",
            head=dict(sha=HEAD, ref="feature", repo=dict(full_name="fork/r")),
            base=dict(sha=BASE, ref="main"),
            merge_commit_sha="d" * 40,
        ),
        get_branch_sha=lambda *args: CURRENT,
    )


@pytest.mark.parametrize("merged", [False, True])
def test_snapshot_chooses_open_head_or_current_base(merged):
    frozen = freeze_snapshot(fake_client(merged), "o", "r", 1)
    assert frozen.source_sha == (CURRENT if merged else HEAD)
    assert frozen.publication_base == ("main" if merged else "feature")
    assert frozen.source_repo == ("o/r" if merged else "fork/r")


def test_exact_snapshot_survives_branch_and_worktree_movement(git_repo):
    repo, git = git_repo
    path = ROOT + "en/new.md"
    file = repo / path
    file.parent.mkdir(parents=True)
    file.write_bytes(b" new\r\n\r\n")
    git("add", ".")
    git("commit", "-m", "source")
    sha = git("rev-parse", "HEAD").decode().strip()
    frozen = snapshot(source_sha=sha)
    file.write_bytes(b"later")
    git("commit", "-am", "later main")
    file.write_bytes(b"dirty")
    plan = build_plan(frozen, [ChangedFile(path, "added")], partial(read_at_sha, str(repo)))
    assert plan.files == {path: " new\r\n\r\n"}
    assert plan.operations[0].target_new_path == ROOT + "ru/new.md"
    assert plan.needs_model


@pytest.mark.parametrize("language", ["ru", "en"])
@pytest.mark.parametrize("kind", ["added", "modified", "deleted", "renamed"])
@pytest.mark.parametrize("rename_edit", [False, True])
def test_mirror_operations_both_directions(language, kind, rename_edit):
    path, old = ROOT + language + "/new.md", ROOT + language + "/old.md"
    calls = []

    def read(sha, name):
        calls.append((sha, name))
        return "old" if sha == BASE else ("changed" if rename_edit else "old")

    plan = build_plan(
        snapshot(), [ChangedFile(path, kind, old if kind == "renamed" else None, rename_edit)], read
    )
    (op,) = plan.operations
    target = "en" if language == "ru" else "ru"
    assert op.source_language == language and op.target_language == target
    assert op.target_new_path == (None if kind == "deleted" else ROOT + target + "/new.md")
    assert op.target_old_path == (
        None if kind == "added" else ROOT + target + ("/old.md" if kind == "renamed" else "/new.md")
    )
    assert plan.needs_model == (kind != "deleted" and (kind != "renamed" or rename_edit))
    assert calls == (
        [] if kind == "deleted" else [(HEAD, path)]
    )


@pytest.mark.parametrize("kind", ["added", "modified", "deleted", "renamed"])
def test_bilingual_early_skip_and_mixed_plan(kind):
    changes = [
        ChangedFile(
            ROOT + lang + "/pair.md", kind, ROOT + lang + "/old.md" if kind == "renamed" else None
        )
        for lang in ["ru", "en"]
    ]
    calls = []

    def read(sha, path):
        calls.append(path)
        return ""

    plan = build_plan(snapshot(), changes, read)
    assert plan.no_work and not plan.needs_model
    assert not calls
    assert "pair.md" in plan.skipped_pairs
    changes.append(ChangedFile(ROOT + "en/other.md", "modified"))
    plan = build_plan(snapshot(), changes, read)
    assert calls == [ROOT + "en/other.md"]
    assert len(plan.operations) == 1
    assert plan.files[ROOT + "en/other.md"] == ""


def test_old_target_not_read_or_used_as_source():
    path = ROOT + "en/page.md"

    def read(sha, name):
        assert (sha, name) == (HEAD, path)
        return "Original English"

    plan = build_plan(snapshot(), [ChangedFile(path, "modified")], read)
    assert plan.files == {path: "Original English"}


@pytest.mark.parametrize("failure", [None, OSError("read denied")])
def test_missing_or_unreadable_source_fails_with_path_and_reason(failure):
    def read(*args):
        if isinstance(failure, Exception):
            raise failure
        return failure

    with pytest.raises(PlanError, match=r"en/page.md.*(отсутствует|read denied)"):
        build_plan(snapshot(), [ChangedFile(ROOT + "en/page.md", "modified")], read)


def test_exact_reader_distinguishes_empty_missing_bad_utf8_and_bad_commit(git_repo):
    repo, git = git_repo
    (repo / "empty.md").write_bytes(b"")
    (repo / "binary.md").write_bytes(b"\xff")
    git("add", ".")
    git("commit", "-m", "blobs")
    sha = git("rev-parse", "HEAD").decode().strip()
    assert read_at_sha(str(repo), sha, "empty.md") == ""
    assert read_at_sha(str(repo), sha, "missing.md") is None
    with pytest.raises(PlanError, match=r"binary.md.*UTF-8"):
        read_at_sha(str(repo), sha, "binary.md")
    with pytest.raises(PlanError, match=r"empty.md"):
        read_at_sha(str(repo), "f" * 40, "empty.md")
    with pytest.raises(PlanError, match="SHA"):
        read_at_sha(str(repo), "main", "empty.md")


def test_api_rename_metadata_and_errors(monkeypatch):
    client = GitHubClient("fake")
    row = dict(
        filename=ROOT + "en/new.md",
        previous_filename=ROOT + "en/old.md",
        status="renamed",
        patch="ignored",
        changes=0,
        additions=0,
        deletions=0,
    )
    monkeypatch.setattr(client, "_request", lambda *a, **k: [row])
    assert list_changes(client, "o", "r", 1) == [
        ChangedFile(row["filename"], "renamed", row["previous_filename"], False)
    ]
    monkeypatch.setattr(client, "_request", lambda *a, **k: {"error": "malformed"})
    with pytest.raises(GitHubAPIError, match="Malformed"):
        list_changes(client, "o", "r", 1)

    def fail(*args, **kwargs):
        raise GitHubAPIError("HTTP 503")

    monkeypatch.setattr(client, "_request", fail)
    with pytest.raises(GitHubAPIError, match="503"):
        list_changes(client, "o", "r", 1)


def test_partial_api_failure_does_not_return_partial_plan(monkeypatch):
    client = GitHubClient("fake")

    def rows(*args):
        yield dict(filename=ROOT + "ru/a.md", status="added")
        raise GitHubAPIError("page 2 failed")

    monkeypatch.setattr(client, "iter_pull_files", rows)
    with pytest.raises(GitHubAPIError, match="page 2"):
        list_changes(client, "o", "r", 1)


def test_different_renames_are_not_bilingual_equivalent():
    changes = [
        ChangedFile(ROOT + lang + "/" + new, "renamed", ROOT + lang + "/old.md", False)
        for lang, new in [("ru", "a.md"), ("en", "b.md")]
    ]
    plan = build_plan(snapshot(), changes, lambda *args: "same")
    assert len(plan.operations) == 2
    assert not plan.skipped_pairs


def test_merged_plan_reads_current_base_not_historical_head(git_repo):
    repo, git = git_repo
    path = ROOT + "ru/page.md"
    file = repo / path
    file.parent.mkdir(parents=True)
    file.write_text("historical head")
    git("add", ".")
    git("commit", "-m", "PR head")
    historical = git("rev-parse", "HEAD").decode().strip()
    file.write_text("current base")
    git("commit", "-am", "post-merge update")
    current = git("rev-parse", "HEAD").decode().strip()
    client = fake_client(True)
    client.get_branch_sha = lambda *args: current
    frozen = freeze_snapshot(client, "o", "r", 1)
    assert frozen.source_sha != historical
    file.write_text("later base")
    git("commit", "-am", "main moved after freeze")
    plan = build_plan(frozen, [ChangedFile(path, "modified")], partial(read_at_sha, str(repo)))
    assert plan.files[path] == "current base"


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("edited", [False, True])
def test_api_rename_counts_drive_plan_using_only_frozen_source(monkeypatch, merged, edited):
    """PR diff metadata survives base movement and fast-forward merges."""
    client = GitHubClient("fake")
    source, old = ROOT + "en/new.md", ROOT + "en/old.md"
    row = dict(filename=source, previous_filename=old, status="renamed",
               changes=2 if edited else 0, additions=1 if edited else 0,
               deletions=1 if edited else 0)
    monkeypatch.setattr(client, "_request", lambda *a, **k: [row])
    api = fake_client(merged)
    # Already merged: old path is absent in both head and base.
    if merged:
        pull = api.get_pull()
        pull["head"]["sha"] = CURRENT
        pull["base"]["sha"] = CURRENT
        api.get_pull = lambda *a: pull
    frozen = freeze_snapshot(api, "o", "r", 1)
    api.get_branch_sha = lambda *a: "f" * 40
    calls = []

    def read(sha, path):
        calls.append((sha, path))
        assert (sha, path) == (frozen.source_sha, source)
        return "source from frozen tree, not current base or old translation"

    plan = build_plan(frozen, list_changes(client, "o", "r", 1), read)
    assert not plan.no_work
    assert plan.needs_model is edited
    assert plan.operations[0].content_changed is edited
    assert plan.operations[0].target_old_path == ROOT + "ru/old.md"
    assert calls == [(frozen.source_sha, source)]
    assert plan.files[source] == "source from frozen tree, not current base or old translation"


@pytest.mark.parametrize("counts", [
    {}, {"changes": 0}, {"additions": 0, "deletions": 0},
    {"changes": 0, "additions": 1, "deletions": 0},
    {"changes": -1, "additions": -1, "deletions": 0},
    {"changes": "0", "additions": 0, "deletions": 0},
    {"changes": False, "additions": 0, "deletions": 0},
    {"changes": 0, "additions": None, "deletions": 0},
    {"changes": 0, "additions": 0.0, "deletions": 0},
])
def test_rename_insufficient_or_invalid_api_metadata_fails(counts):
    row = dict(filename=ROOT + "en/new.md", previous_filename=ROOT + "en/old.md",
               status="renamed", **counts)
    api = SimpleNamespace(iter_pull_files=lambda *a: iter([row]))
    with pytest.raises(PlanError, match=r"changes/additions/deletions.*en/new.md"):
        list_changes(api, "o", "r", 1)


def test_selected_rename_without_content_metadata_fails_before_reads():
    def read(*args):
        pytest.fail("Insufficient rename metadata must not cause historical source reads")

    with pytest.raises(PlanError, match=r"en/new.md.*метаданные"):
        build_plan(snapshot(), [ChangedFile(ROOT + "en/new.md", "renamed", ROOT + "en/old.md")], read)


@pytest.mark.parametrize("value", [0, 1, "false", []])
def test_content_changed_requires_boolean(value):
    with pytest.raises(PlanError, match="content_changed"):
        ChangedFile(ROOT + "en/new.md", "renamed", ROOT + "en/old.md", value)
