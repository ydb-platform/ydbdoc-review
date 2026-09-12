"""Regression coverage for ambient EN-link debt across distinct docs trees."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.github.git_ops import read_text_at_ref
from ydbdoc_review.github.workflow import _proven_outbound_fragment_occurrences
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.en_link_targets import (
    apply_en_link_target_checks,
    check_en_page_link_targets,
)


def _put(repo: Path, path: str, text: str) -> None:
    destination = repo / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _baseline_repo(tmp_path: Path, pages: dict[str, str]) -> tuple[Path, str]:
    """Commit the baseline tree, leaving callers free to make a final overlay."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    for path, text in pages.items():
        _put(repo, path, text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline EN docs")
    return repo, _git(repo, "rev-parse", "HEAD")


def _run_final_gate(repo: Path, baseline_ref: str, page: str) -> list[str]:
    """Exercise the public wrapper with real final disk and git baseline readers."""
    result = PRTranslationResult()
    return apply_en_link_target_checks(
        result,
        repo_path=str(repo),
        en_md_paths={page},
        baseline_read=lambda path: read_text_at_ref(str(repo), baseline_ref, path),
    )


def test_ambient_filter_does_not_hide_unchanged_href_when_final_target_loses_fragment(
    tmp_path: Path,
):
    """A baseline-valid target regression must block even if its referrer is unchanged."""
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    referrer = "See [target](target.md#kept-anchor).\n"
    repo, baseline_ref = _baseline_repo(
        tmp_path,
        {page: referrer, target: "## Kept {#kept-anchor}\n"},
    )
    _put(repo, target, "## Replacement heading\n")

    broken = _run_final_gate(repo, baseline_ref, page)

    assert broken == [page]


def test_ambient_filter_does_not_hide_unchanged_href_when_final_target_is_deleted(
    tmp_path: Path,
):
    """A target present at tip and deleted in the final overlay is a new failure."""
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    repo, baseline_ref = _baseline_repo(
        tmp_path,
        {
            page: "See [target](target.md#kept-anchor).\n",
            target: "## Kept {#kept-anchor}\n",
        },
    )
    (repo / target).unlink()

    broken = _run_final_gate(repo, baseline_ref, page)

    assert broken == [page]


def test_ambient_filter_uses_baseline_include_tree_for_unchanged_wrapper_target(
    tmp_path: Path,
):
    """Removing a final include cannot turn tip-valid wrapper debt into ambient debt."""
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    include = "ydb/docs/en/core/guide/_includes/target-anchor.md"
    repo, baseline_ref = _baseline_repo(
        tmp_path,
        {
            page: "See [target](target.md#included-anchor).\n",
            target: "{% include [target anchor](_includes/target-anchor.md) %}\n",
            include: "## Included {#included-anchor}\n",
        },
    )
    _put(repo, target, "Target wrapper without the former include.\n")
    (repo / include).unlink()

    broken = _run_final_gate(repo, baseline_ref, page)

    assert broken == [page]


def test_pr_52287_base951_candidate_d3e_unchanged_client_certificate_href_blocks(
    tmp_path: Path,
):
    """#52287 excerpts, base951 → d3e: a retained node-auth href loses its target."""
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    target = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    href = (
        "../../devops/concepts/node-authorization.md"
        "#enabling-the-node-authentication-and-authorization-mode"
    )
    referrer = (
        "# Client certificate authorization\n\n"
        "Configure certificate rules for database nodes.\n\n"
        "For the node-authentication mode, see "
        f"[node authorization]({href}).\n"
    )
    repo, baseline_ref = _baseline_repo(
        tmp_path,
        {
            page: referrer,
            target: "## Enabling the node authentication and authorization mode\n",
        },
    )
    _put(repo, target, "## Enabling node authentication and authorization mode\n")

    broken = _run_final_gate(repo, baseline_ref, page)

    assert broken == [page]


def test_ambient_broken_href_in_both_trees_stays_suppressed(tmp_path: Path):
    """Ambient debt is still suppressed when the same target is broken in both trees."""
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    repo, baseline_ref = _baseline_repo(
        tmp_path,
        {page: "See [target](target.md#missing-anchor).\n", target: "# Target\n"},
    )

    assert _run_final_gate(repo, baseline_ref, page) == []


def test_new_href_absent_from_baseline_referrer_is_never_ambient(tmp_path: Path):
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    repo, baseline_ref = _baseline_repo(tmp_path, {page: "# Referrer\n", target: "# Target\n"})
    _put(repo, page, "See [new target](target.md#missing-anchor).\n")

    assert _run_final_gate(repo, baseline_ref, page) == [page]


def test_baseline_broken_href_repaired_in_final_tree_has_no_finding(tmp_path: Path):
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    repo, baseline_ref = _baseline_repo(
        tmp_path,
        {page: "See [target](target.md#fixed-anchor).\n", target: "# Target\n"},
    )
    _put(repo, target, "## Fixed {#fixed-anchor}\n")

    assert _run_final_gate(repo, baseline_ref, page) == []


def test_unchanged_valid_href_has_no_finding_in_either_tree(tmp_path: Path):
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    repo, baseline_ref = _baseline_repo(
        tmp_path,
        {page: "See [target](target.md#valid-anchor).\n", target: "# Target {#valid-anchor}\n"},
    )

    assert _run_final_gate(repo, baseline_ref, page) == []


def test_empty_existing_target_is_not_missing_in_either_tree(tmp_path: Path):
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    repo, baseline_ref = _baseline_repo(
        tmp_path,
        {page: "See [target](target.md).\n", target: ""},
    )

    assert _run_final_gate(repo, baseline_ref, page) == []


def test_same_page_fragment_uses_the_matching_referrer_tree(tmp_path: Path):
    page = "ydb/docs/en/core/guide/referrer.md"
    baseline = "See [here](#same-page).\n\n## Present {#same-page}\n"
    repo, baseline_ref = _baseline_repo(tmp_path, {page: baseline})
    _put(repo, page, "See [here](#same-page).\n\n## Removed heading\n")

    assert _run_final_gate(repo, baseline_ref, page) == [page]


def test_no_baseline_referrer_does_not_fall_back_to_final_tree(tmp_path: Path):
    """A missing baseline page is not permission to read the final page as baseline."""
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    repo, _baseline_ref = _baseline_repo(
        tmp_path,
        {page: "See [target](target.md#missing-anchor).\n", target: "# Target\n"},
    )
    result = PRTranslationResult()

    broken = apply_en_link_target_checks(
        result,
        repo_path=str(repo),
        en_md_paths={page},
        baseline_read=lambda _path: None,
    )

    assert broken == [page]


def test_missing_baseline_target_present_in_final_tree_missing_fragment_blocks(
    tmp_path: Path,
):
    """A target added after baseline cannot make final missing-fragment debt ambient."""
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    baseline_page = "See [target](target.md#missing-anchor).\n"
    repo, _baseline_ref = _baseline_repo(
        tmp_path,
        {page: baseline_page},
    )
    _put(repo, target, "# Target\n")
    result = PRTranslationResult()

    broken = apply_en_link_target_checks(
        result,
        repo_path=str(repo),
        en_md_paths={page},
        baseline_read=lambda path: baseline_page if path == page else None,
    )

    assert broken == [page]

    # Suppression requires an explicit baseline target reader. Its absence leaves
    # the current final-tree blocker intact even when the referrer text is known.
    assert check_en_page_link_targets(
        page,
        baseline_page,
        read_text=lambda path: "# Target\n" if path == target else None,
        baseline_text=baseline_page,
    ) != []


def test_missing_target_in_both_trees_stays_ambient_with_explicit_baseline_reader():
    page = "ydb/docs/en/core/guide/referrer.md"
    baseline_page = "See [target](target.md#missing-anchor).\n"

    assert check_en_page_link_targets(
        page,
        baseline_page,
        read_text=lambda _path: None,
        baseline_text=baseline_page,
        baseline_read_text=lambda _path: None,
    ) == []


def test_baseline_reader_without_referrer_cannot_suppress_final_target_failure(
    tmp_path: Path,
):
    page = "ydb/docs/en/core/guide/referrer.md"
    target = "ydb/docs/en/core/guide/target.md"
    repo, _baseline_ref = _baseline_repo(
        tmp_path,
        {page: "See [target](target.md#missing-anchor).\n", target: "# Target\n"},
    )
    result = PRTranslationResult()

    broken = apply_en_link_target_checks(
        result,
        repo_path=str(repo),
        en_md_paths={page},
        baseline_read=lambda path: "# Target\n" if path == target else None,
    )

    assert broken == [page]


def test_deferred_full_page_and_isolated_probe_keep_final_tree_evidence_separate():
    """Both broken links remain proven when their baseline targets were valid."""
    page = "ydb/docs/en/core/guide/referrer.md"
    old_target = "ydb/docs/en/core/guide/old.md"
    new_target = "ydb/docs/en/core/guide/new.md"
    baseline = "See [old](./old.md#old-anchor).\n"
    final = (
        "See [old](old.md#old-anchor).\n"
        "See [new](new.md#new-anchor).\n"
    )
    baseline_tree = {
        page: baseline,
        old_target: "## Old {#old-anchor}\n",
    }
    final_tree = {
        page: final,
        old_target: "# Old without anchor\n",
        new_target: "# New without anchor\n",
    }

    occurrences = _proven_outbound_fragment_occurrences(
        page,
        final,
        read_docs=final_tree.get,
        baseline_text=baseline,
        baseline_read_text=baseline_tree.get,
    )

    assert [occurrence.href for occurrence in occurrences] == [
        "old.md#old-anchor",
        "new.md#new-anchor",
    ]
    assert [occurrence.expected_link_message for occurrence in occurrences] == [
        "en_link_target: referrer.md:1\n"
        "  target: guide/old.md\n"
        "  missing fragment: old-anchor\n"
        "  available: old-without-anchor",
        "en_link_target: referrer.md:2\n"
        "  target: guide/new.md\n"
        "  missing fragment: new-anchor\n"
        "  available: new-without-anchor",
    ]
