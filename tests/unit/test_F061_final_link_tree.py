"""F-061: validate links and reachability against the final tree."""

from pathlib import Path

from ydbdoc_review.validation.en_link_targets import check_en_page_link_targets
from ydbdoc_review.validation.toc_targets import (
    check_missing_toc_targets,
    check_orphan_pages_for_locale,
)


def test_F061_actual_tree(tmp_path: Path):
    repo = tmp_path / "repo"
    foreign = repo / "ydb/docs/en/core/other/ghost.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("# Foreign section\n", encoding="utf-8")
    toc_path = "ydb/docs/en/core/toc_p.yaml"
    toc = "items:\n- name: Ghost\n  href: ghost.md\n"

    missing = check_missing_toc_targets(toc_path, toc, repo_path=str(repo))
    assert missing and "ydb/docs/en/core/ghost.md" in missing[0]

    page = "ydb/docs/en/core/index.md"
    messages = check_en_page_link_targets(
        page,
        "See [ghost](ghost.md).\n",
        read_text=lambda path: (
            foreign.read_text(encoding="utf-8")
            if path == "ydb/docs/en/core/other/ghost.md"
            else None
        ),
    )
    assert messages and "missing file" in messages[0]


def test_F061_old_links():
    page = "ydb/docs/en/core/index.md"
    target = "ydb/docs/en/core/current.md"
    redirects = "common:\n  - from: /old.md\n    to: /current.md\n"
    final_files = {target: "## Current {#kept}\n"}
    baseline_files = {
        "ydb/docs/redirects.yaml": redirects,
        target: final_files[target],
    }

    assert check_en_page_link_targets(
        page,
        "See [old](old.md#kept).\n",
        read_text=final_files.get,
        baseline_read_text=baseline_files.get,
    ) == []

    root_toc = "ydb/docs/en/core/toc_p.yaml"
    pending_toc = {root_toc: "items:\n- name: Current\n  href: current.md\n"}
    pending_pages = {
        "ydb/docs/en/core/current.md": final_files[target],
        "ydb/docs/en/core/orphan.md": "# Orphan\n",
    }
    orphans = check_orphan_pages_for_locale(
        set(pending_pages),
        repo_path=str(Path("/nonexistent")),
        locale="en",
        pending_toc_texts=pending_toc,
        pending_md_texts=pending_pages,
    )
    assert "ydb/docs/en/core/orphan.md" in orphans
    assert "ydb/docs/en/core/current.md" not in orphans
