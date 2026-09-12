"""F-019: scope comes from the complete PR file list, not a truncated diff."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.pr import (
    build_pairs_from_changes,
    list_pr_file_changes_api,
    load_pair_contents,
)


def test_F019_pagination() -> None:
    client = GitHubClient("test-token")
    batches = [
        [
            {"filename": f"docs/ru/page-{index}.md", "status": "modified"}
            for index in range(100)
        ],
        [
            {"filename": "docs/ru/added.md", "status": "added"},
            {"filename": "docs/ru/deleted.md", "status": "removed"},
            {
                "filename": "docs/ru/renamed.md",
                "previous_filename": "docs/ru/old-name.md",
                "status": "renamed",
            },
            {"filename": "docs/ru/last.md", "status": "modified"},
        ],
    ]
    requested_pages: list[int] = []

    def request(_method: str, _url: str, *, params: dict[str, int]):
        requested_pages.append(params["page"])
        return batches[params["page"] - 1]

    client._request = request  # type: ignore[method-assign]

    changes = list_pr_file_changes_api(client, "o", "r", 19)

    assert requested_pages == [1, 2]
    assert len(changes) == 105
    assert ("docs/ru/added.md", "added") in changes
    assert ("docs/ru/deleted.md", "deleted") in changes
    assert ("docs/ru/old-name.md", "deleted") in changes
    assert ("docs/ru/renamed.md", "modified") in changes


def test_F019_full_source(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "docs" / "ru" / "page.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Heading\n\nNew line\nUnchanged line\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=test", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)

    pairs = build_pairs_from_changes(
        [("docs/ru/page.md", "modified")], docs_root="docs"
    )
    contents = load_pair_contents(str(repo), pairs, merge_base_with="HEAD")

    assert contents[0].ru_text == source.read_text(encoding="utf-8")
    assert "Unchanged line" in contents[0].ru_text
