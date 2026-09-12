"""F-023: source and target reads stay on their captured snapshots."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.github.pr import build_pairs_from_changes, load_pair_contents
from ydbdoc_review.navigation.scope_planner import make_repo_scope_readers


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return repo


def test_F023_read_origins(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source_paths = {
        "ydb/docs/ru/page.md": "source document",
        "ydb/docs/ru/includes/part.md": "source include",
        "ydb/docs/ru/anchors.md": "source anchor",
        "ydb/docs/ru/toc.yaml": "source toc",
        "ydb/docs/ru/redirects.yaml": "source redirect",
    }
    for path, text in source_paths.items():
        _write(repo, path, text)
    source = _commit(repo, "source snapshot")
    _write(repo, "ydb/docs/en/page.md", "target base")
    target = _commit(repo, "target snapshot")

    read_ru, read_en_base, _read_ru_base = make_repo_scope_readers(
        str(repo), target, ru_content_ref=source, ru_base_ref=source
    )

    for path, text in source_paths.items():
        assert read_ru(path) == text
    assert read_en_base("ydb/docs/en/page.md") == "target base"


def test_F023_no_history_fill(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "ydb/docs/en/page.md", "target from B")
    source = _commit(repo, "source snapshot")
    _write(repo, "ydb/docs/ru/page.md", "old prose from checkout")
    _write(repo, "ydb/docs/en/page.md", "target from B")
    target = _commit(repo, "target snapshot")

    pair = build_pairs_from_changes(
        [("ydb/docs/ru/page.md", "modified")], docs_root="ydb/docs"
    )[0]
    content = load_pair_contents(
        str(repo), [pair], merge_base_with=target, ru_content_ref=source, ru_base_ref=source
    )[0]

    assert content.ru_text is None
    assert content.en_text == "target from B"

