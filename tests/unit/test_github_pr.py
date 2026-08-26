"""Tests for PR helpers."""

# ruff: noqa: RUF001

from __future__ import annotations

import subprocess
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from ydbdoc_review.github.pr import (
    PullRequestContext,
    build_pairs_from_changes,
    infer_doc_pair_moves,
    is_fork_head,
    is_translation_pr_branch,
    is_verify_fixup_branch,
    list_pr_file_changes_api,
    list_pr_renames_api,
    load_pair_contents,
    parse_repo,
    parse_source_pr_from_text,
    pull_request_context,
    reconcile_doc_pair_renames,
    repo_https_clone_url,
    source_pr_number_from_branch,
    translation_branch_base,
    translation_pr_base,
    verify_fixup_pr_base,
)


def test_parse_repo():
    assert parse_repo("ydb-platform/ydb") == ("ydb-platform", "ydb")


def test_repo_https_clone_url():
    assert repo_https_clone_url("ydb-platform", "ydb") == (
        "https://github.com/ydb-platform/ydb.git"
    )


def test_is_fork_head():
    same = PullRequestContext(
        number=1,
        title="t",
        owner="o",
        repo="r",
        head_ref="feat",
        head_sha="abc",
        head_repo_https_url="https://github.com/o/r.git",
        head_repo_full_name="o/r",
        base_ref="main",
    )
    fork = PullRequestContext(
        number=2,
        title="t",
        owner="o",
        repo="r",
        head_ref="feat",
        head_sha="abc",
        head_repo_https_url="https://github.com/contrib/r.git",
        head_repo_full_name="contrib/r",
        base_ref="main",
    )
    assert is_fork_head(same) is False
    assert is_fork_head(fork) is True


def test_translation_branch_base():
    same = PullRequestContext(
        number=1,
        title="t",
        owner="o",
        repo="r",
        head_ref="feature/docs",
        head_sha="abc",
        head_repo_https_url="https://github.com/o/r.git",
        head_repo_full_name="o/r",
        base_ref="main",
    )
    fork = PullRequestContext(
        number=2,
        title="t",
        owner="o",
        repo="r",
        head_ref="contrib-feature",
        head_sha="abc",
        head_repo_https_url="https://github.com/contrib/r.git",
        head_repo_full_name="contrib/r",
        base_ref="main",
    )
    assert translation_branch_base(same) == (
        "https://github.com/o/r.git",
        "feature/docs",
    )
    assert translation_branch_base(fork) == (
        "https://github.com/o/r.git",
        "main",
    )
    assert translation_pr_base(same) == "feature/docs"
    assert translation_pr_base(fork) == "main"


def test_translation_branch_base_merged_same_repo():
    """Merged PR: head branch may be deleted; base translation branch on main."""
    merged = PullRequestContext(
        number=40070,
        title="t",
        owner="ydb-platform",
        repo="ydb",
        head_ref="alexnick88-patch-1",
        head_sha="b2a17bd",
        head_repo_https_url="https://github.com/ydb-platform/ydb.git",
        head_repo_full_name="ydb-platform/ydb",
        base_ref="main",
        merged=True,
        state="closed",
    )
    assert translation_branch_base(merged) == (
        "https://github.com/ydb-platform/ydb.git",
        "main",
    )
    assert translation_pr_base(merged) == "main"


def test_verify_fixup_pr_base():
    translation = PullRequestContext(
        number=11,
        title="t",
        owner="o",
        repo="r",
        head_ref="ydbdoc-review/pr-3",
        head_sha="abc",
        head_repo_https_url="https://github.com/o/r.git",
        head_repo_full_name="o/r",
        base_ref="feature/docs",
    )
    author = PullRequestContext(
        number=7,
        title="t",
        owner="o",
        repo="r",
        head_ref="feature/docs",
        head_sha="abc",
        head_repo_https_url="https://github.com/o/r.git",
        head_repo_full_name="o/r",
        base_ref="main",
    )
    prefix = "ydbdoc-review/pr-"
    assert verify_fixup_pr_base(translation, translation_branch_prefix=prefix) == (
        "ydbdoc-review/pr-3"
    )
    assert verify_fixup_pr_base(author, translation_branch_prefix=prefix) == "main"


def test_parse_repo_invalid():
    with pytest.raises(ValueError):
        parse_repo("bad")


def test_source_pr_from_branch():
    assert source_pr_number_from_branch("ydbdoc-review/pr-42", prefix="ydbdoc-review/pr-") == 42
    assert source_pr_number_from_branch("feature/x", prefix="ydbdoc-review/pr-") is None
    assert is_translation_pr_branch("ydbdoc-review/pr-42", translation_branch_prefix="ydbdoc-review/pr-")
    assert not is_translation_pr_branch("ydbdoc-review/verify-42", translation_branch_prefix="ydbdoc-review/pr-")
    assert is_verify_fixup_branch(
        "ydbdoc-review/verify-42", verify_fixup_branch_prefix="ydbdoc-review/verify-"
    )
    assert not is_verify_fixup_branch(
        "ydbdoc-review/pr-42", verify_fixup_branch_prefix="ydbdoc-review/verify-"
    )
    assert source_pr_number_from_branch(
        "ydbdoc-review/verify-46742", prefix="ydbdoc-review/verify-"
    ) == 46742


def test_parse_source_pr_from_text():
    assert parse_source_pr_from_text("Auto-translate docs from PR #17") == 17
    assert parse_source_pr_from_text("Branch ydbdoc-review/pr-9") == 9


def test_build_pairs_from_changes():
    changes = [
        ("ydb/docs/ru/foo.md", "modified"),
        ("ydb/docs/en/bar.md", "added"),
    ]
    pairs = build_pairs_from_changes(changes, docs_root="ydb/docs")
    assert len(pairs) == 2
    paths = {(p.ru_path, p.en_path) for p in pairs}
    assert ("ydb/docs/ru/foo.md", "ydb/docs/en/foo.md") in paths


def test_list_pr_file_changes_api():
    class FakeClient:
        def iter_pull_files(self, owner, repo, pr_number):
            yield {"filename": "ydb/docs/ru/x.md", "status": "added"}
            yield {"filename": "ydb/docs/en/y.md", "status": "removed"}

    changes = list_pr_file_changes_api(FakeClient(), "o", "r", 1)  # type: ignore[arg-type]
    assert ("ydb/docs/ru/x.md", "added") in changes
    assert ("ydb/docs/en/y.md", "deleted") in changes


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    ru = repo / "ydb" / "docs" / "ru"
    ru.mkdir(parents=True)
    (ru / "a.md").write_text("# RU\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return str(repo)


def test_load_pair_contents(git_repo: str):
    pairs = build_pairs_from_changes(
        [("ydb/docs/ru/a.md", "modified")],
        docs_root="ydb/docs",
    )
    contents = load_pair_contents(git_repo, pairs, merge_base_with="HEAD")
    assert len(contents) == 1
    assert contents[0].ru_text and "RU" in contents[0].ru_text


def test_pr_45949_rename_metadata_collapses_delete_add_into_one_move():
    old = "ydb/docs/ru/core/devops/deployment-options/manual/node-authorization.md"
    new = "ydb/docs/ru/core/devops/concepts/node-authorization.md"
    old_en = old.replace("/ru/", "/en/")
    new_en = new.replace("/ru/", "/en/")
    pairs = build_pairs_from_changes(
        [(old, "deleted"), (new, "added")], docs_root="ydb/docs"
    )

    moved = reconcile_doc_pair_renames(
        pairs, [(old, new)], docs_root="ydb/docs"
    )

    assert len(moved) == 1
    assert moved[0].ru_path == new
    assert moved[0].en_path == new_en
    assert moved[0].previous_ru_path == old
    assert moved[0].previous_en_path == old_en


def test_pr_45949_added_removed_api_infers_move_from_content_and_redirect_toc():
    old = "ydb/docs/ru/core/devops/deployment-options/manual/node-authorization.md"
    new = "ydb/docs/ru/core/devops/concepts/node-authorization.md"
    redirects = "ydb/docs/ru/redirects.yaml"
    changes = [(old, "deleted"), (new, "added"), (redirects, "modified")]
    pairs = build_pairs_from_changes(changes, docs_root="ydb/docs")
    old_body = (
        "# Authentication and authorization of database nodes\n\n"
        "Node authentication verifies database nodes during service gRPC calls.\n\n"
        "The database node opens a gRPC connection to a storage node.\n"
        "The storage node checks certificate Subject requirements.\n"
    )
    new_body = (
        "# Configuring authentication and authorization of database nodes\n\n"
        "Node authentication verifies database nodes during protected gRPC calls.\n\n"
        "Before a dynamic node joins the cluster, it registers with a storage node.\n"
        "The storage node checks certificate Subject requirements and assigns a SID.\n"
        "The node then joins the cluster using its NodeId.\n"
    )
    yaml = (
        "- from: core/devops/deployment-options/manual/node-authorization.md\n"
        "  to: core/devops/concepts/node-authorization.md\n"
    )

    moved = infer_doc_pair_moves(
        pairs,
        changes,
        docs_root="ydb/docs",
        read_before=lambda path: old_body if path == old else None,
        read_after=lambda path: (
            new_body if path == new else yaml if path == redirects else None
        ),
    )

    ratio = SequenceMatcher(None, old_body, new_body, autojunk=False).ratio()
    assert 0.6 <= ratio < 0.9
    assert len(moved) == 1
    assert moved[0].ru_path == new
    assert moved[0].previous_ru_path == old


def test_inferred_move_rejects_same_basename_with_unrelated_topic():
    old = "ydb/docs/ru/core/old/index.md"
    new = "ydb/docs/ru/core/new/index.md"
    redirects = "ydb/docs/ru/redirects.yaml"
    changes = [(old, "deleted"), (new, "added"), (redirects, "modified")]
    pairs = build_pairs_from_changes(changes, docs_root="ydb/docs")
    route = "from: core/old/index.md\nto: core/new/index.md\n"

    result = infer_doc_pair_moves(
        pairs,
        changes,
        docs_root="ydb/docs",
        read_before=lambda path: "# Storage diagnostics\n" if path == old else None,
        read_after=lambda path: (
            "# Query syntax\n" if path == new else route if path == redirects else None
        ),
    )

    assert len(result) == 2
    assert all(pair.previous_ru_path is None for pair in result)


def test_list_pr_renames_preserves_previous_filename():
    class Client:
        def iter_pull_files(self, owner, repo, pr_number):
            del owner, repo, pr_number
            yield {
                "status": "renamed",
                "previous_filename": "ydb/docs/ru/old.md",
                "filename": "ydb/docs/ru/new.md",
            }

    assert list_pr_renames_api(Client(), "o", "r", 45949) == [  # type: ignore[arg-type]
        ("ydb/docs/ru/old.md", "ydb/docs/ru/new.md")
    ]


def test_load_pair_contents_merged_pr_uses_pre_merge_ru_base(git_repo: str):
    ru = Path(git_repo) / "ydb" / "docs" / "ru" / "a.md"
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    ru.write_text("# RU\n\nНовый раздел.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "merged source"], cwd=git_repo, check=True)
    merge_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    pairs = build_pairs_from_changes(
        [("ydb/docs/ru/a.md", "modified")], docs_root="ydb/docs"
    )

    content = load_pair_contents(
        git_repo,
        pairs,
        merge_base_with=merge_sha,
        ru_content_ref=merge_sha,
        ru_base_ref=base_sha,
    )[0]

    assert content.ru_text == "# RU\n\nНовый раздел.\n"
    assert content.ru_base_text == "# RU\n"


def test_load_pair_contents_merged_pr_uses_current_en_as_target(git_repo: str):
    repo = Path(git_repo)
    ru = repo / "ydb" / "docs" / "ru" / "a.md"
    en = repo / "ydb" / "docs" / "en" / "a.md"
    en.parent.mkdir(parents=True)
    en.write_text("Historical EN.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "historical en"], cwd=git_repo, check=True)
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()

    ru.write_text("# RU\n\nHistorical source change.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "merged source"], cwd=git_repo, check=True)
    merge_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()

    ru.write_text("# RU\n\nCurrent source version.\n", encoding="utf-8")
    en.write_text("Current paired EN.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "advance current pair"], cwd=git_repo, check=True)
    current_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()

    pair = build_pairs_from_changes(
        [("ydb/docs/ru/a.md", "modified")], docs_root="ydb/docs"
    )[0]
    content = load_pair_contents(
        git_repo,
        [pair],
        merge_base_with=current_sha,
        ru_content_ref=merge_sha,
        ru_base_ref=base_sha,
    )[0]

    assert content.ru_text == "# RU\n\nHistorical source change.\n"
    assert content.current_ru_text == "# RU\n\nCurrent source version.\n"
    assert content.historical_en_text == "Historical EN.\n"
    assert content.en_text == "Current paired EN.\n"
    assert content.historical_replay


def test_historical_current_en_absence_never_falls_back_to_checkout(git_repo: str):
    repo = Path(git_repo)
    en = repo / "ydb/docs/en/a.md"
    en.parent.mkdir(parents=True)
    en.write_text("Historical EN.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "historical target"], cwd=repo, check=True)
    historical = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    en.unlink()
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "current tombstone"], cwd=repo, check=True)
    current = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    pair = build_pairs_from_changes(
        [("ydb/docs/ru/a.md", "modified")], docs_root="ydb/docs"
    )[0]

    content = load_pair_contents(
        git_repo,
        [pair],
        merge_base_with=current,
        ru_content_ref=historical,
        ru_base_ref=f"{historical}^",
    )[0]

    assert content.historical_target_text == "Historical EN.\n"
    assert content.current_en_text is None
    assert content.en_text is None


def test_historical_move_uses_live_old_en_as_destination_seed(git_repo: str):
    repo = Path(git_repo)
    old_ru = "ydb/docs/ru/old/a.md"
    new_ru = "ydb/docs/ru/new/a.md"
    old_en = "ydb/docs/en/old/a.md"
    for path, text in ((old_ru, "# RU old\n"), (old_en, "# Current EN old\n")):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "move base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / old_ru).unlink()
    target = repo / new_ru
    target.parent.mkdir(parents=True)
    target.write_text("# RU new\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "historical move"], cwd=repo, check=True)
    merge = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    pair = reconcile_doc_pair_renames(
        build_pairs_from_changes([(old_ru, "deleted"), (new_ru, "added")], docs_root="ydb/docs"),
        [(old_ru, new_ru)],
        docs_root="ydb/docs",
    )[0]

    content = load_pair_contents(
        git_repo, [pair], merge_base_with=merge, ru_content_ref=merge, ru_base_ref=base
    )[0]

    assert content.logical_operation.value == "move"
    assert content.current_en_text is None
    assert content.current_previous_en_text == "# Current EN old\n"
    assert content.en_text == "# Current EN old\n"


def test_load_pair_contents_synthetic_sibling_uses_current_ru(git_repo: str):
    repo = Path(git_repo)
    ru = repo / "ydb/docs/ru/a.md"
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    ru.write_text("Historical source.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "historical"], cwd=git_repo, check=True)
    historical_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    ru.write_text("Current sibling.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "current"], cwd=git_repo, check=True)
    current_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    pair = build_pairs_from_changes(
        [("ydb/docs/ru/a.md", "modified")], docs_root="ydb/docs"
    )[0]

    content = load_pair_contents(
        git_repo,
        [pair],
        merge_base_with=current_sha,
        ru_content_ref=historical_sha,
        ru_base_ref=base_sha,
        historical_ru_paths=frozenset(),
    )[0]

    assert content.ru_text == "Current sibling.\n"
    assert content.ru_base_text == "Current sibling.\n"
    assert not content.historical_replay


def test_pull_request_context():
    class FakeClient:
        def get_pull(self, owner, repo, pr_number):
            return {
                "title": "t",
                "head": {
                    "ref": "feat",
                    "sha": "abc",
                    "repo": {
                        "clone_url": "https://github.com/o/r.git",
                        "full_name": "o/r",
                    },
                },
                "base": {"ref": "main"},
                "merged": False,
                "state": "open",
            }

    ctx = pull_request_context(FakeClient(), "o", "r", 5)  # type: ignore[arg-type]
    assert ctx.number == 5
    assert ctx.head_ref == "feat"
    assert ctx.merged is False
