import subprocess
from pathlib import Path

from ydbdoc_review.pipeline.provenance import guard_publication_provenance


RU = "ydb/docs/ru/a.md"
EN = "ydb/docs/en/a.md"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str | None]) -> str:
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    base = _commit(repo, "base", {RU: "base\n", EN: "English\n"})
    return repo, base


def _guard(repo: Path, *, source: str, base: str, publication: str, source_paths=()):
    return guard_publication_provenance(
        repo_path=str(repo),
        merged=True,
        source_tree_sha=source,
        source_base_sha=base,
        publication_tree_sha=publication,
        initial_ru_paths={RU},
        auto_added_ru_paths=set(),
        source_pr_paths=set(source_paths),
        to_en_path=lambda path: path.replace("/ru/", "/en/"),
    )


def test_merged_unchanged_source_and_en_proceeds(tmp_path: Path):
    repo, base = _repo(tmp_path)
    source = _commit(repo, "source", {RU: "source\n"})
    publication = _commit(repo, "unrelated", {"README.md": "x\n"})
    assert _guard(repo, source=source, base=base, publication=publication) == ()


def test_newer_ru_blocks_with_touching_commit(tmp_path: Path):
    repo, base = _repo(tmp_path)
    source = _commit(repo, "source", {RU: "source\n"})
    publication = _commit(repo, "newer ru", {RU: "newer\n"})
    finding = _guard(repo, source=source, base=base, publication=publication)[0]
    assert finding.reason == "newer_ru"
    assert finding.touching_commits


def test_newer_en_blocks(tmp_path: Path):
    repo, base = _repo(tmp_path)
    source = _commit(repo, "source", {RU: "source\n"})
    publication = _commit(repo, "newer en", {EN: "New English\n"})
    assert _guard(repo, source=source, base=base, publication=publication)[0].reason == "newer_en"


def test_source_pr_en_conflict_blocks(tmp_path: Path):
    repo, base = _repo(tmp_path)
    source = _commit(repo, "source", {RU: "source\n"})
    finding = _guard(
        repo,
        source=source,
        base=base,
        publication=source,
        source_paths={RU, EN},
    )[0]
    assert finding.reason == "source_pr_en_conflict"


def test_diverged_history_blocks(tmp_path: Path):
    repo, base = _repo(tmp_path)
    _git(repo, "checkout", "-b", "source")
    source = _commit(repo, "source", {RU: "source\n"})
    _git(repo, "checkout", "main")
    publication = _commit(repo, "other", {"README.md": "other\n"})
    finding = _guard(repo, source=source, base=base, publication=publication)[0]
    assert finding.reason == "history_diverged"
