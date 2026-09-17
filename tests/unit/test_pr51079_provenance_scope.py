"""Task 8 regressions for exact later-RU provenance scope."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

import pytest

from ydbdoc_review.reporting import provenance_drift
from ydbdoc_review.reporting.provenance_drift import build_later_ru_drift_report

H = "673b924813e35646535e20d917b014094bf7de14"
B = "7886ff84e31c2f52c99adf8fbeb908fda38e4192"
MERGES = {
    51946: "95171080a6fff88e5ab37c1361b5465e99098eb6",
    39046: "c7f267731bdc243b2c274f0d226c656d1695b899",
    52752: "b807721ebfd5033998cced212d1a37931a45bff7",
    52355: "b13cac5dda249992dd70020f1a7e79f82e4efaf7",
}
SOURCE_PATHS = (
    "ydb/docs/ru/core/reference/configuration/auth_config.md",
    "ydb/docs/ru/core/security/authentication.md",
)
_HISTORICAL_CHANGES = {
    MERGES[51946]: (
        (
            "ydb/docs/ru/core/reference/ydb-cli/export-import/_includes/import-file.md",
            "modified",
        ),
    ),
    MERGES[39046]: (
        ("ydb/docs/ru/core/dev/index.md", "modified"),
        ("ydb/docs/ru/core/dev/tables/index.md", "added"),
        ("ydb/docs/ru/core/dev/tables/partitioning/anti-patterns.md", "added"),
        ("ydb/docs/ru/core/dev/tables/partitioning/auto/index.md", "added"),
        (
            "ydb/docs/ru/core/dev/tables/partitioning/choosing-partition-count.md",
            "added",
        ),
        ("ydb/docs/ru/core/dev/tables/partitioning/column-oriented.md", "added"),
        ("ydb/docs/ru/core/dev/tables/partitioning/index.md", "added"),
        ("ydb/docs/ru/core/dev/tables/partitioning/toc_p.yaml", "added"),
        ("ydb/docs/ru/core/dev/tables/toc_p.yaml", "added"),
        ("ydb/docs/ru/core/dev/toc_p.yaml", "modified"),
        (
            "ydb/docs/ru/core/troubleshooting/performance/schemas/splits-merges.md",
            "modified",
        ),
    ),
    MERGES[52752]: (
        ("ydb/docs/ru/core/contributor/load-actors-nbs-dbg-like.md", "modified"),
    ),
    MERGES[52355]: ((SOURCE_PATHS[0], "modified"),),
}


class _HistoricalReadAdapter:
    def __init__(self) -> None:
        self.requested_prs: list[int] = []
        self.changes = dict(_HISTORICAL_CHANGES)
        self.texts: dict[tuple[str, str], str | None] = {}
        self.pulls: dict[int, dict[str, object]] = {
            number: {
                "number": number,
                "merged": True,
                "merge_commit_sha": sha,
            }
            for number, sha in MERGES.items()
        }
        self.before_scan: Callable[[], None] | None = None

    def get_pull(self, _owner: str, _repo: str, number: int) -> dict[str, object]:
        self.requested_prs.append(number)
        return self.pulls[number]


def _historical_read_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> _HistoricalReadAdapter:
    """Replay the pinned H..B merge/path evidence without repository or network I/O."""
    adapter = _HistoricalReadAdapter()
    number_by_sha = {sha: number for number, sha in MERGES.items()}

    def commits_between(repo_path: str, source_head: str, baseline: str) -> tuple[str, ...]:
        assert repo_path == "unused-repo"
        assert (source_head, baseline) == (H, B)
        if adapter.before_scan is not None:
            adapter.before_scan()
        return tuple(MERGES.values())

    monkeypatch.setattr(provenance_drift, "first_parent_commits_between", commits_between)
    monkeypatch.setattr(
        provenance_drift,
        "first_parent_commit_changes",
        lambda repo_path, sha: adapter.changes[sha]
        if repo_path == "unused-repo"
        else (),
    )
    monkeypatch.setattr(
        provenance_drift,
        "commit_subject",
        lambda repo_path, sha: (
            f"Merge pull request #{number_by_sha[sha]}: historical fixture"
            if repo_path == "unused-repo"
            else ""
        ),
    )
    monkeypatch.setattr(
        provenance_drift,
        "commit_parent_sha",
        lambda repo_path, sha: f"{sha}^" if repo_path == "unused-repo" else "",
    )
    monkeypatch.setattr(
        provenance_drift,
        "read_text_at_commit",
        lambda repo_path, sha, path: adapter.texts.get((sha, path))
        if repo_path == "unused-repo"
        else None,
    )
    return adapter


def _build(
    gh: _HistoricalReadAdapter,
    *,
    source_paths: Iterable[str] = SOURCE_PATHS,
    dependency_paths: Iterable[str] = (),
) -> str:
    return build_later_ru_drift_report(
        "unused-repo",
        gh,
        owner="ydb-platform",
        repo="ydb",
        source_head_sha=H,
        baseline_sha=B,
        source_paths=source_paths,
        docs_root="ydb/docs",
        dependency_paths=dependency_paths,
    )


def test_historical_range_only_reports_admitted_source_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _historical_read_adapter(monkeypatch)

    report = _build(gh)

    assert "Confirmed merge association: PR #52355" in report
    assert all(f"#{number}" not in report for number in (51946, 39046, 52752))
    assert gh.requested_prs == [52355]


def test_mixed_commit_renders_only_admitted_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _historical_read_adapter(monkeypatch)
    sha = MERGES[52355]
    outside_path = "ydb/docs/ru/core/concepts/not-admitted.md"
    gh.changes[sha] = (*gh.changes[sha], (outside_path, "modified"))
    gh.texts[(sha, SOURCE_PATHS[0])] = "[diagnostic](../../concepts/outside-scope.md)\n"

    report = _build(gh)

    assert SOURCE_PATHS[0] in report
    assert outside_path not in report
    assert (
        "added href: `ydb/docs/ru/core/reference/configuration/auth_config.md` -> "
        "`ydb/docs/ru/core/concepts/outside-scope.md`"
    ) in report


def test_proven_dependency_is_included_without_expanding_translation_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _historical_read_adapter(monkeypatch)
    dependency = "ydb/docs/ru/core/contributor/load-actors-nbs-dbg-like.md"

    report = _build(gh, dependency_paths=(dependency,))

    assert f"`{dependency}` — modified" in report
    assert f"`{dependency}` — modified, same-path with source PR" not in report
    assert SOURCE_PATHS == (
        "ydb/docs/ru/core/reference/configuration/auth_config.md",
        "ydb/docs/ru/core/security/authentication.md",
    )


class _OneShotIterable:
    def __init__(self, values: Iterable[str]) -> None:
        self.values = tuple(values)
        self.iterations = 0

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("scope iterable was consumed more than once")
        return iter(self.values)


def test_source_and_dependency_generators_are_consumed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _historical_read_adapter(monkeypatch)
    source_paths = _OneShotIterable(SOURCE_PATHS)
    dependency_paths = _OneShotIterable(())
    gh.before_scan = lambda: (
        pytest.fail("scope was not frozen before commit scanning")
        if (source_paths.iterations, dependency_paths.iterations) != (1, 1)
        else None
    )

    report = _build(
        gh,
        source_paths=source_paths,
        dependency_paths=dependency_paths,
    )

    assert "#52355" in report
    assert (source_paths.iterations, dependency_paths.iterations) == (1, 1)


def test_unconfirmed_pr_association_is_not_labelled_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _historical_read_adapter(monkeypatch)
    gh.pulls[52355] = {
        "number": 52355,
        "merged": False,
        "merge_commit_sha": MERGES[52355],
    }

    report = _build(gh)

    assert "Confirmed merge association" not in report
    assert "unknown PR association; unverified subject candidate #52355" in report
    assert gh.requested_prs == [52355]
