"""F-101: doc_verify keeps linked scope and standalone PR context."""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.provenance import RuAuthority, TranslationArtifactProvenance
from ydbdoc_review.github.workflow import run_doc_verify
from ydbdoc_review.pipeline.analyze import PairContent, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair


class _ScopeCaptured(Exception):
    pass


def _config():
    return load_config(
        env={
            "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
        }
    )


def _pull(*, head_ref: str, merged: bool = False, fork: bool = False) -> dict:
    return {
        "title": "docs",
        "body": "authority",
        "user": {
            "login": "github-actions[bot]"
            if head_ref.startswith(("ydbdoc-review/pr-", "ydbdoc-review/verify-"))
            else "author"
        },
        "head": {
            "ref": head_ref,
            "sha": "candidate",
            "repo": {
                "clone_url": "https://github.com/contributor/ydb.git",
                "full_name": "contributor/ydb" if fork else "o/r",
            },
        },
        "base": {"ref": "main", "sha": "base"},
        "merged": merged,
        "state": "closed" if merged else "open",
        "merge_commit_sha": "merge" if merged else None,
    }


def _capture_pair_changes(
    *,
    pr_number: int,
    pull: dict,
    git_changes: list[tuple[str, str]],
    api_changes_by_pr: dict[int, list[tuple[str, str]]],
    provenance: TranslationArtifactProvenance | None = None,
) -> tuple[list[tuple[str, str]], MagicMock]:
    captured: list[tuple[str, str]] = []
    commit_changes = MagicMock(return_value=api_changes_by_pr.get(17, []))

    def _capture(changes, *, docs_root):
        del docs_root
        captured.extend(changes)
        raise _ScopeCaptured

    with ExitStack() as stack:
        github_cls = stack.enter_context(patch("ydbdoc_review.github.workflow.GitHubClient"))
        github_cls.return_value.get_pull.return_value = pull
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.resolve_commit_ref",
                side_effect=lambda _repo, ref: "candidate" if ref == "HEAD" else "base",
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow._snapshot_destination_lease",
                return_value=SimpleNamespace(expected_sha="candidate"),
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=git_changes,
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                side_effect=lambda _gh, _owner, _repo, number: api_changes_by_pr[number],
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.build_pairs_from_changes",
                side_effect=_capture,
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.parse_authority_evidence",
                return_value=object(),
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.validate_authority_evidence",
                return_value=provenance,
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.commit_changes_between",
                commit_changes,
            )
        )
        with pytest.raises(_ScopeCaptured):
            run_doc_verify(
                repo_path="/repo",
                github_repo="o/r",
                pr_number=pr_number,
                merge_base_with="origin/main",
                dry_run=True,
                config=_config(),
                skip_ops_gates=True,
            )
    return captured, commit_changes


def test_F101_linked():
    source_scope = [("ydb/docs/ru/source.md", "modified")]
    artifact_scope = [("ydb/docs/en/source.md", "modified")]
    authority = RuAuthority(
        source_repo="o/r",
        source_pr=17,
        source_base_sha="source-base",
        source_head_sha="source-head",
        baseline_sha="base",
        ru_sha="source-head",
        mode=RuAuthorityMode.SOURCE_PRESERVING,
    )
    provenance = TranslationArtifactProvenance(authority, "candidate")

    translation_pairs, commit_changes = _capture_pair_changes(
        pr_number=81,
        pull=_pull(head_ref="ydbdoc-review/pr-17"),
        git_changes=artifact_scope,
        api_changes_by_pr={17: source_scope, 81: artifact_scope},
        provenance=provenance,
    )
    assert translation_pairs == artifact_scope
    commit_changes.assert_called_once_with("/repo", "source-base", "source-head")

    fixup_pairs, _ = _capture_pair_changes(
        pr_number=82,
        pull=_pull(head_ref="ydbdoc-review/verify-17"),
        git_changes=[("ydb/docs/en/fixup-only.md", "modified")],
        api_changes_by_pr={17: source_scope, 82: artifact_scope},
    )
    assert fixup_pairs == source_scope


def test_F101_author_modes():
    ambient_checkout = [("ydb/docs/ru/ambient.md", "modified")]
    ru_only = [("ydb/docs/ru/one.md", "modified")]

    fork_pairs, _ = _capture_pair_changes(
        pr_number=23,
        pull=_pull(head_ref="docs/fork", fork=True),
        git_changes=ambient_checkout,
        api_changes_by_pr={23: ru_only},
    )
    assert fork_pairs == sorted([*ambient_checkout, *ru_only])

    merged_pairs, _ = _capture_pair_changes(
        pr_number=24,
        pull=_pull(head_ref="docs/merged", merged=True, fork=True),
        git_changes=ambient_checkout,
        api_changes_by_pr={24: ru_only},
    )
    assert merged_pairs == ru_only

    en_only = PairContent(
        pair=DocPair(
            ru_path="ydb/docs/ru/one.md",
            en_path="ydb/docs/en/one.md",
            en_changed=True,
        ),
        ru_text="RU",
        en_text="EN",
    )
    bilingual = PairContent(
        pair=DocPair(
            ru_path="ydb/docs/ru/two.md",
            en_path="ydb/docs/en/two.md",
            ru_changed=True,
            en_changed=True,
        ),
        ru_text="RU",
        en_text="EN",
    )
    assert plan_pair_heuristic(en_only).source_lang == "en"
    assert plan_pair_heuristic(bilingual).source_lang == "ru"
