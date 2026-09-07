"""Regression contract for snapshot-bound docs readers.

These tests deliberately use only the current public/private reader entry
points and real temporary Git histories. They document where a ref reader
must fail closed instead of silently borrowing unrelated checkout bytes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.git_ops import read_text_at_ref
from ydbdoc_review.github.workflow import (
    _collect_fixed_shas,
    _docs_text_reader,
    _final_tree_reader,
    run_doc_translate,
    run_doc_verify,
)
from ydbdoc_review.navigation.scope_planner import make_repo_scope_readers
from ydbdoc_review.ops import lifecycle
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import FileTranslationResult, PairRunResult, PRTranslationResult


@pytest.fixture(autouse=True)
def _isolate_external_ops_factories(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runner probes are offline even if a future default enables ops gates."""
    monkeypatch.setenv("YDBDOC_SKIP_OPS_GATES", "1")

    def _unexpected_external_factory(*_args, **_kwargs):
        raise AssertionError("external ledger/transcript factory must stay isolated")

    monkeypatch.setattr(lifecycle, "create_runs_ledger", _unexpected_external_factory)
    monkeypatch.setattr(lifecycle, "create_transcript_store", _unexpected_external_factory)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "reader@example.test")
    _git(repo, "config", "user.name", "Reader")
    return repo


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _config():
    return load_config(
        env={
            "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
            "YDBDOC_YC_FOLDER_ID": "b1",
            "YDBDOC_YC_API_KEY": "k",
            "GITHUB_TOKEN": "gh",
            "GITHUB_PUSH_TOKEN": "ghp",
            "YDBDOC_SKIP_OPS_GATES": "1",
        }
    )


def _pull(
    head_ref: str = "feature/docs",
    *,
    head_sha: str = "source-head",
    base_sha: str = "",
) -> dict:
    return {
        "title": "docs",
        "body": "",
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": base_sha},
    }


def _result(ru_path: str, en_path: str, text: str = "candidate bytes\n") -> PRTranslationResult:
    pair = DocPair(ru_path=ru_path, en_path=en_path, ru_changed=True)
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=ru_path,
        target_path=en_path,
        source_lang="ru",
        target_lang="en",
    )
    file_result = FileTranslationResult(
        file_path=en_path,
        final_text=text,
        segments_count=1,
        verdict="ok",
        prompt_version="reader-contract",
    )
    return PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text=text, file_result=file_result)]
    )


def test_final_reader_missing_chosen_commit_never_resurrects_stale_checkout(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/gone.md"
    stale = _write(repo, rel, "historical checkout bytes\n")
    historical = _commit(repo, "historical page")
    stale.unlink()
    chosen = _commit(repo, "delete from chosen snapshot")
    _git(repo, "checkout", "-q", historical)

    assert _final_tree_reader(str(repo), chosen, set())(rel) is None


def test_final_reader_ignores_untracked_bytes_absent_from_the_chosen_snapshot(tmp_path: Path):
    repo = _repo(tmp_path)
    _write(repo, "ydb/docs/en/core/anchor.md", "committed anchor\n")
    chosen = _commit(repo, "chosen snapshot")
    rel = "ydb/docs/en/core/untracked.md"
    _write(repo, rel, "untracked worktree bytes\n")

    assert _final_tree_reader(str(repo), chosen, set())(rel) is None


def test_scope_en_baseline_missing_at_tip_never_uses_historical_merge_base(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/removed.md"
    _write(repo, rel, "historical EN\n")
    historical = _commit(repo, "historical EN")
    (repo / rel).unlink()
    tip = _commit(repo, "tip removes EN")
    _git(repo, "checkout", "-q", historical)

    _read_ru, read_en_base, _read_ru_base = make_repo_scope_readers(str(repo), tip)

    assert read_en_base(rel) is None


def test_explicit_ru_content_snapshot_does_not_fallback_to_another_snapshot(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/ru/core/new.md"
    _write(repo, "ydb/docs/ru/core/anchor.md", "snapshot anchor\n")
    missing_snapshot = _commit(repo, "snapshot without RU page")
    _write(repo, rel, "only another snapshot has this page\n")
    other_snapshot = _commit(repo, "later RU page")

    read_ru, _read_en_base, _read_ru_base = make_repo_scope_readers(
        str(repo),
        other_snapshot,
        ru_content_ref=missing_snapshot,
        ru_base_ref=missing_snapshot,
    )

    assert read_ru(rel) is None


def test_explicit_ru_baseline_snapshot_does_not_fallback_to_another_snapshot(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/ru/core/new.md"
    _write(repo, "ydb/docs/ru/core/anchor.md", "snapshot anchor\n")
    missing_snapshot = _commit(repo, "snapshot without RU page")
    _write(repo, rel, "only another snapshot has this page\n")
    other_snapshot = _commit(repo, "later RU page")

    _read_ru, _read_en_base, read_ru_base = make_repo_scope_readers(
        str(repo),
        other_snapshot,
        ru_content_ref=other_snapshot,
        ru_base_ref=missing_snapshot,
    )

    assert read_ru_base(rel) is None


def test_reader_freezes_branch_bytes_when_branch_moves_after_creation(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/page.md"
    _write(repo, rel, "first bytes\n")
    _commit(repo, "first")
    read = _final_tree_reader(str(repo), "main", set())
    _write(repo, rel, "moved branch bytes\n")
    _commit(repo, "main moved")

    assert read(rel) == "first bytes\n"


def test_declared_overlay_missing_from_disk_is_an_integrity_error(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/overlay.md"
    page = _write(repo, rel, "base bytes\n")
    tip = _commit(repo, "base")
    page.unlink()

    read = _final_tree_reader(str(repo), tip, {rel})

    with pytest.raises(RuntimeError, match="overlay"):
        read(rel)


def test_committed_reader_controls_preserve_snapshot_identity_and_candidate_overlay(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/page.md"
    _write(repo, rel, "base bytes\n")
    base = _commit(repo, "base")
    _write(repo, rel, "candidate bytes\n")
    candidate = _commit(repo, "candidate")

    # Distinct immutable refs remain distinct even when checkout bytes are dirty.
    _write(repo, rel, "dirty worktree bytes\n")
    assert read_text_at_ref(str(repo), base, rel) == "base bytes\n"
    assert read_text_at_ref(str(repo), candidate, rel) == "candidate bytes\n"
    assert _final_tree_reader(str(repo), candidate, set())(rel) == "candidate bytes\n"

    # An explicit write is the candidate, not its base snapshot.
    _write(repo, rel, "explicit candidate bytes\n")
    assert _final_tree_reader(str(repo), base, {rel})(rel) == "explicit candidate bytes\n"


def test_tombstone_and_frozen_sha_controls_do_not_read_worktree_or_moved_ref(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/page.md"
    _write(repo, rel, "frozen bytes\n")
    _commit(repo, "initial")
    frozen = _collect_fixed_shas(
        str(repo), merge_base_with="main", ru_ref=None, head_sha=None
    )["merge_base"]
    read = _final_tree_reader(str(repo), frozen, set(), deleted_paths={rel})
    _write(repo, rel, "dirty and moved bytes\n")
    _commit(repo, "main moved")

    assert read(rel) is None
    assert _final_tree_reader(str(repo), frozen, set())(rel) == "frozen bytes\n"


def test_legacy_git_reader_invalid_ref_returns_none_compatibility_control(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/page.md"
    _write(repo, rel, "committed bytes\n")
    _commit(repo, "initial")

    assert read_text_at_ref(str(repo), "missing-ref", rel) is None


def test_docs_reader_preserves_exact_crlf_bytes(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/page.md"
    page = repo / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"one\r\ntwo\r\n")
    tip = _commit(repo, "CRLF")

    assert _docs_text_reader(str(repo), tip)(rel) == "one\r\ntwo\r\n"


def test_final_reader_non_overlay_preserves_exact_crlf_bytes(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/page.md"
    page = repo / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"one\r\ntwo\r\n")
    tip = _commit(repo, "CRLF")

    assert _final_tree_reader(str(repo), tip, set())(rel) == "one\r\ntwo\r\n"


def test_final_reader_preserves_empty_explicit_overlay_bytes(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/page.md"
    page = _write(repo, rel, "base bytes\n")
    tip = _commit(repo, "base")
    page.write_text("", encoding="utf-8", newline="")
    assert _final_tree_reader(str(repo), tip, {rel})(rel) == ""


def test_committed_empty_blob_is_not_missing(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/empty.md"
    _write(repo, rel, "")
    tip = _commit(repo, "empty")

    assert _final_tree_reader(str(repo), tip, set())(rel) == ""


def test_tombstone_precedes_declared_overlay(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/deleted.md"
    _write(repo, rel, "base bytes\n")
    tip = _commit(repo, "base")
    _write(repo, rel, "candidate bytes\n")

    assert _final_tree_reader(str(repo), tip, {rel}, deleted_paths={rel})(rel) is None


def test_default_ru_reader_uses_head_and_explicit_snapshots_remain_distinct(tmp_path: Path):
    repo = _repo(tmp_path)
    ru = "ydb/docs/ru/core/page.md"
    en = "ydb/docs/en/core/page.md"
    _write(repo, ru, "RU base\n")
    _write(repo, en, "EN base\n")
    base = _commit(repo, "base")
    _write(repo, ru, "RU content\n")
    _write(repo, en, "EN tip\n")
    content = _commit(repo, "content")

    default_ru, _default_en, _default_ru_base = make_repo_scope_readers(str(repo), content)
    explicit_ru, explicit_en, explicit_ru_base = make_repo_scope_readers(
        str(repo), content, ru_content_ref=content, ru_base_ref=base
    )

    assert default_ru(ru) == "RU content\n"
    assert explicit_ru(ru) == "RU content\n"
    assert explicit_ru_base(ru) == "RU base\n"
    assert explicit_en(en) == "EN tip\n"


def test_scope_factory_rejects_invalid_ref_instead_of_trying_other_ref_spellings(tmp_path: Path):
    repo = _repo(tmp_path)
    _write(repo, "ydb/docs/ru/core/page.md", "HEAD bytes\n")
    _commit(repo, "HEAD")

    with pytest.raises(RuntimeError, match="missing-ref"):
        make_repo_scope_readers(str(repo), "missing-ref")


def test_final_reader_git_error_does_not_fallback_to_worktree(tmp_path: Path):
    repo = _repo(tmp_path)
    rel = "ydb/docs/en/core/page.md"
    _write(repo, rel, "committed bytes\n")
    tip = _commit(repo, "committed blob")
    _write(repo, rel, "worktree bytes\n")
    read = _final_tree_reader(str(repo), tip, set())

    git_error = MagicMock(returncode=128, stdout="", stderr="injected git failure")
    with patch("ydbdoc_review.github.git_ops.subprocess.run", return_value=git_error):
        with pytest.raises(RuntimeError, match="git"):
            read(rel)


def test_factories_retain_snapshots_after_ref_moves_and_worktree_drift(tmp_path: Path):
    repo = _repo(tmp_path)
    ru = "ydb/docs/ru/core/page.md"
    en = "ydb/docs/en/core/page.md"
    _write(repo, ru, "RU B0\n")
    _write(repo, en, "EN B0\n")
    base = _commit(repo, "B0")
    _write(repo, ru, "RU C\n")
    _write(repo, en, "EN C\n")
    content = _commit(repo, "C")

    docs = _docs_text_reader(str(repo), "main")
    default_ru, _default_en, _default_ru_base = make_repo_scope_readers(str(repo), "main")
    explicit_ru, explicit_en, explicit_ru_base = make_repo_scope_readers(
        str(repo), "main", ru_content_ref=content, ru_base_ref=base
    )

    _write(repo, ru, "RU B1\n")
    _write(repo, en, "EN B1\n")
    _commit(repo, "B1")
    _write(repo, ru, "RU W dirty\n")
    _write(repo, en, "EN W dirty\n")

    assert {
        "docs": docs(en),
        "default_ru": default_ru(ru),
        "explicit_ru": explicit_ru(ru),
        "explicit_ru_base": explicit_ru_base(ru),
        "explicit_en": explicit_en(en),
    } == {
        "docs": "EN C\n",
        "default_ru": "RU C\n",
        "explicit_ru": "RU C\n",
        "explicit_ru_base": "RU B0\n",
        "explicit_en": "EN C\n",
    }


def test_translate_freezes_base_before_model_and_final_gate_when_main_moves(tmp_path: Path):
    """A moving top-level base ref must not alter post-model final-link inputs."""
    repo = _repo(tmp_path)
    ru = "ydb/docs/ru/a.md"
    en = "ydb/docs/en/a.md"
    _write(repo, ru, "RU B0\n")
    _write(repo, en, "EN B0\n")
    _commit(repo, "B0")
    captured: dict[str, str | None] = {}

    def _move_main_then_return_result(*_args, **_kwargs):
        _write(repo, en, "EN B1\n")
        _commit(repo, "B1")
        return _result(ru, en)

    def _capture_final_gate(_result, **kwargs):
        captured["baseline"] = kwargs["baseline_read"](en)
        captured["final"] = kwargs["docs_read"](en)
        return []

    with patch("ydbdoc_review.github.workflow.GitHubClient") as gh_cls, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[(ru, "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        return_value=[(ru, "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.create_llm_client",
        return_value=MagicMock(),
    ), patch("ydbdoc_review.github.workflow.load_glossary", return_value=MagicMock()), patch(
        "ydbdoc_review.github.workflow.run_pr_translation",
        side_effect=_move_main_then_return_result,
    ), patch(
        "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks", return_value=[]
    ), patch(
        "ydbdoc_review.github.workflow.apply_en_link_target_checks",
        side_effect=_capture_final_gate,
    ), patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prepare, patch(
        "ydbdoc_review.github.workflow.push_branch"
    ) as push:
        frozen = _git(repo, "rev-parse", "HEAD")
        gh_cls.return_value.get_pull.return_value = _pull(
            head_sha=frozen,
            base_sha=frozen,
        )
        run_doc_translate(
            repo_path=str(repo),
            github_repo="o/r",
            pr_number=7,
            merge_base_with="main",
            dry_run=True,
            config=_config(),
        )

    assert captured == {"baseline": "EN B0\n", "final": "EN B0\n"}
    prepare.assert_not_called()
    push.assert_not_called()


def test_verify_uses_captured_head_not_dirty_worktree_for_pair_and_final_gate(tmp_path: Path):
    """Verify B base / C captured / W dirty must feed C to both readers."""
    repo = _repo(tmp_path)
    ru = "ydb/docs/ru/a.md"
    en = "ydb/docs/en/a.md"
    _write(repo, ru, "RU B\n")
    _write(repo, en, "EN B\n")
    base = _commit(repo, "B")
    _write(repo, en, "EN C\n")
    candidate = _commit(repo, "C")
    captured: dict[str, str | None] = {}

    def _dirty_after_head_capture(*_args, **_kwargs):
        _write(repo, en, "EN W dirty\n")
        return [(en, "modified")]

    def _capture_verify_pairs(*_args, **kwargs):
        captured["pair"] = kwargs["docs_text_reader"](en)
        return PRTranslationResult()

    def _capture_final_gate(_result, **kwargs):
        captured["final"] = kwargs["docs_read"](en)
        return []

    def _capture_orphan_gate(_result, **kwargs):
        captured["orphan_ref"] = kwargs.get("baseline_ref")
        return []

    with patch("ydbdoc_review.github.workflow.GitHubClient") as gh_cls, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        side_effect=_dirty_after_head_capture,
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[]
    ), patch(
        "ydbdoc_review.github.workflow.create_llm_client",
        return_value=MagicMock(),
    ), patch("ydbdoc_review.github.workflow.load_glossary", return_value=MagicMock()), patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        side_effect=_capture_verify_pairs,
    ), patch(
        "ydbdoc_review.github.workflow.apply_en_link_target_checks",
        side_effect=_capture_final_gate,
    ), patch(
        "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
        side_effect=_capture_orphan_gate,
    ), patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prepare, patch(
        "ydbdoc_review.github.workflow.push_branch"
    ) as push:
        gh_cls.return_value.get_pull.return_value = _pull()
        run_doc_verify(
            repo_path=str(repo),
            github_repo="o/r",
            pr_number=9,
            merge_base_with=base,
            dry_run=True,
            config=_config(),
        )

    assert captured == {
        "pair": "EN C\n",
        "final": "EN C\n",
        "orphan_ref": candidate,
    }
    prepare.assert_not_called()
    push.assert_not_called()


def test_translate_invalid_snapshot_stops_before_scope_model_or_mutation(tmp_path: Path):
    repo = _repo(tmp_path)
    ru = "ydb/docs/ru/a.md"
    _write(repo, ru, "RU bytes\n")
    _commit(repo, "HEAD")
    scope = MagicMock(side_effect=AssertionError("scope invoked after invalid snapshot"))
    model = MagicMock(side_effect=AssertionError("model invoked after invalid snapshot"))

    with patch("ydbdoc_review.github.workflow.GitHubClient") as gh_cls, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[(ru, "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        return_value=[(ru, "modified")],
    ), patch("ydbdoc_review.github.workflow.plan_translation_scope", scope), patch(
        "ydbdoc_review.github.workflow.create_llm_client", model
    ), patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prepare, patch(
        "ydbdoc_review.github.workflow.push_branch"
    ) as push:
        gh_cls.return_value.get_pull.return_value = _pull()
        with pytest.raises(RuntimeError, match="missing-ref"):
            run_doc_translate(
                repo_path=str(repo),
                github_repo="o/r",
                pr_number=7,
                merge_base_with="missing-ref",
                dry_run=False,
                config=_config(),
            )

    scope.assert_not_called()
    model.assert_not_called()
    prepare.assert_not_called()
    push.assert_not_called()


def test_verify_invalid_snapshot_stops_before_scope_model_or_mutation(tmp_path: Path):
    repo = _repo(tmp_path)
    ru = "ydb/docs/ru/a.md"
    en = "ydb/docs/en/a.md"
    _write(repo, ru, "RU bytes\n")
    _write(repo, en, "EN bytes\n")
    _commit(repo, "HEAD")
    scope = MagicMock(side_effect=AssertionError("scope invoked after invalid snapshot"))
    model = MagicMock(side_effect=AssertionError("model invoked after invalid snapshot"))

    with patch("ydbdoc_review.github.workflow.GitHubClient") as gh_cls, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[(en, "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        return_value=[(ru, "modified")],
    ), patch("ydbdoc_review.github.workflow.plan_translation_scope", scope), patch(
        "ydbdoc_review.github.workflow.create_llm_client", model
    ), patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prepare, patch(
        "ydbdoc_review.github.workflow.push_branch"
    ) as push:
        gh_cls.return_value.get_pull.return_value = _pull("ydbdoc-review/pr-7")
        with pytest.raises(RuntimeError, match="missing-ref"):
            run_doc_verify(
                repo_path=str(repo),
                github_repo="o/r",
                pr_number=9,
                merge_base_with="missing-ref",
                dry_run=False,
                config=_config(),
            )

    scope.assert_not_called()
    model.assert_not_called()
    prepare.assert_not_called()
    push.assert_not_called()


def test_verify_unborn_head_with_valid_base_stops_before_scope_model_or_mutation(
    tmp_path: Path,
):
    repo = _repo(tmp_path)
    ru = "ydb/docs/ru/a.md"
    en = "ydb/docs/en/a.md"
    _write(repo, ru, "RU base\n")
    _write(repo, en, "EN base\n")
    _commit(repo, "valid base")
    _git(repo, "checkout", "--orphan", "unborn")
    scope = MagicMock(side_effect=AssertionError("scope invoked after unborn HEAD"))
    model = MagicMock(side_effect=AssertionError("model invoked after unborn HEAD"))
    cleanup = MagicMock(side_effect=AssertionError("mutation before unborn HEAD barrier"))

    with patch("ydbdoc_review.github.workflow.GitHubClient") as gh_cls, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[(en, "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[]
    ), patch("ydbdoc_review.github.workflow.plan_translation_scope", scope), patch(
        "ydbdoc_review.github.workflow.create_llm_client", model
    ), patch("ydbdoc_review.github.workflow._delete_stale_verify_fixup", cleanup), patch(
        "ydbdoc_review.github.workflow.prepare_translation_branch_on_base"
    ) as prepare, patch("ydbdoc_review.github.workflow.push_branch") as push:
        gh_cls.return_value.get_pull.return_value = _pull()
        with pytest.raises(RuntimeError, match="HEAD"):
            run_doc_verify(
                repo_path=str(repo),
                github_repo="o/r",
                pr_number=9,
                merge_base_with="main",
                dry_run=False,
                config=_config(),
            )

    cleanup.assert_not_called()
    scope.assert_not_called()
    model.assert_not_called()
    prepare.assert_not_called()
    push.assert_not_called()


def test_verify_invalid_base_on_ordinary_branch_does_not_delete_stale_fixup(
    tmp_path: Path,
):
    repo = _repo(tmp_path)
    en = "ydb/docs/en/a.md"
    _write(repo, en, "EN bytes\n")
    _commit(repo, "HEAD")
    cleanup = MagicMock(side_effect=AssertionError("mutation before invalid base barrier"))

    with patch("ydbdoc_review.github.workflow.GitHubClient") as gh_cls, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[(en, "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[]
    ), patch("ydbdoc_review.github.workflow._delete_stale_verify_fixup", cleanup), patch(
        "ydbdoc_review.github.workflow.create_llm_client",
        side_effect=AssertionError("model invoked after invalid base"),
    ) as model, patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prepare, patch(
        "ydbdoc_review.github.workflow.push_branch"
    ) as push:
        gh_cls.return_value.get_pull.return_value = _pull()
        with pytest.raises(RuntimeError, match="missing-ref"):
            run_doc_verify(
                repo_path=str(repo),
                github_repo="o/r",
                pr_number=9,
                merge_base_with="missing-ref",
                dry_run=False,
                config=_config(),
            )

    cleanup.assert_not_called()
    model.assert_not_called()
    prepare.assert_not_called()
    push.assert_not_called()
