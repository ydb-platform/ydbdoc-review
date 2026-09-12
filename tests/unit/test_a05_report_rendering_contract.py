"""A05 contract for safe, attributable later-RU provenance reports."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

from ydbdoc_review.reporting import provenance_drift
from ydbdoc_review.reporting.provenance_drift import (
    _is_relevant_ru_path,
    _markdown_code_literal,
    _Relation,
    _relations,
    build_later_ru_drift_report,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="")


def _commit(repo: Path, subject: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _literal_data(value: str) -> str:
    """The visible, lossless literal form required for untrusted report data."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _code_literal(value: str) -> str:
    """A Markdown code span whose delimiter cannot be closed by ``value``."""
    literal = _literal_data(value)
    longest_run = max((len(run) for run in literal.split("`")), default=0)
    delimiter = "`" * (longest_run + 1)
    return f"{delimiter}{literal}{delimiter}"


def _all_tokens(tokens: Iterable[Token]) -> Iterable[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _all_tokens(token.children)


def _inline_text(token: Token) -> str:
    """Rendered text, without using the report's Markdown source as an oracle."""
    assert token.type == "inline"
    return "".join(
        "\n" if child.type in {"softbreak", "hardbreak"} else child.content
        for child in token.children or ()
        if child.type in {"text", "code_inline", "softbreak", "hardbreak"}
    )


def _heading_texts(tokens: list[Token]) -> list[tuple[str, str]]:
    headings: list[tuple[str, str]] = []
    for index, token in enumerate(tokens[:-1]):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            assert inline.type == "inline"
            headings.append((token.tag, _inline_text(inline)))
    return headings


def _list_items(tokens: list[Token]) -> list[str]:
    items: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index].type != "list_item_open":
            index += 1
            continue
        depth = 1
        index += 1
        contents: list[str] = []
        while index < len(tokens) and depth:
            token = tokens[index]
            if token.type == "list_item_open":
                depth += 1
            elif token.type == "list_item_close":
                depth -= 1
            elif token.type == "inline":
                contents.append(_inline_text(token))
            index += 1
        items.append("".join(contents))
    return items


def _replace_list_item(report: str, expected: str, replacement: str) -> str:
    """Replace one rendered list item by its parsed meaning, not a source substring."""
    parser = MarkdownIt("commonmark", {"html": True})
    lines = report.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if _list_items(parser.parse(line)) == [expected]:
            lines[index] = replacement
            return "".join(lines)
    raise AssertionError(f"missing rendered list item: {expected!r}")


@dataclass(frozen=True)
class _Fixture:
    report: str
    repo_path: Path
    gh: _VerifiedPull
    source_sha: str
    later_sha: str
    paths: tuple[str, ...]
    relation_path: str
    include_target: str
    href_target: str


@dataclass(frozen=True)
class _ScopeFixture:
    repo_path: Path
    gh: _VerifiedPull
    source_sha: str
    later_sha: str
    source_path: str
    nested_path: str
    outsider_paths: tuple[str, str]


class _VerifiedPull:
    def __init__(self, merge_sha: str) -> None:
        self.merge_sha = merge_sha

    def get_pull(self, _owner: str, _repo: str, number: int) -> dict[str, object]:
        return {
            "number": number,
            "merged": True,
            "merge_commit_sha": self.merge_sha,
        }


@pytest.fixture
def hostile_drift(tmp_path: Path) -> _Fixture:
    """Real Git names plus relation values that reach the report renderer intact."""
    repo = tmp_path / "report-rendering"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a05@example.test")
    _git(repo, "config", "user.name", "A05 Contract")
    _git(repo, "config", "core.quotePath", "false")

    source_path = "ydb/docs/ru/core/security/source.md"
    _write(repo, source_path, "# Source\n")
    source_sha = _commit(repo, "source H")

    hostile_path = "ydb/docs/ru/core/security/x`<script>alert(1)</script>.md"
    newline_path = "ydb/docs/ru/core/security/control\n## injected-heading.md"
    relation_path = "ydb/docs/ru/core/security/связи-安全.md"
    include_target = "partials/`<script>include</script>\x1b[31m-ёж.md"
    href_target = "targets/`<img src=x onerror=alert>\x1b[32m-ёж.md#fragment"
    _write(repo, hostile_path, "# Hostile path\n")
    _write(repo, newline_path, "# Newline path\n")
    relation_text = "\n".join(
        (
            "# Unicode positive",
            "",
            f"{{% include [include]({include_target}) %}}",
            f"[href]({href_target})",
            "",
        )
    )
    _write(repo, relation_path, relation_text)
    expected_relations = frozenset(
        {
            _Relation("include", f"ydb/docs/ru/core/security/{include_target}"),
            _Relation("href", f"ydb/docs/ru/core/security/{href_target}"),
        }
    )
    assert _relations(relation_path, relation_text) == expected_relations
    _write(repo, "ydb/docs/en/core/security/ignored.md", "# Not RU\n")
    later_sha = _commit(repo, "Merge pull request #50704: hostile report fixture")

    gh = _VerifiedPull(later_sha)
    report = build_later_ru_drift_report(
        str(repo),
        gh,
        owner="ydb-platform",
        repo="ydb",
        source_head_sha=source_sha,
        baseline_sha=later_sha,
        source_paths=(source_path,),
        docs_root="ydb/docs",
    )
    return _Fixture(
        report=report,
        repo_path=repo,
        gh=gh,
        source_sha=source_sha,
        later_sha=later_sha,
        paths=(hostile_path, newline_path, relation_path),
        relation_path=relation_path,
        include_target=f"ydb/docs/ru/core/security/{include_target}",
        href_target=f"ydb/docs/ru/core/security/{href_target}",
    )


@pytest.fixture
def exact_path_scope(tmp_path: Path) -> _ScopeFixture:
    """A real Git history with slash and backslash filenames that must not alias."""
    repo = tmp_path / "exact-path-scope"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a05@example.test")
    _git(repo, "config", "user.name", "A05 Contract")
    _git(repo, "config", "core.quotePath", "false")

    source_path = r"ydb/docs/ru/a\b.md"
    nested_path = "ydb/docs/ru/a/b.md"
    outsider_paths = (r"ydb\docs\ru\outside.md", r"ydb/docs/ru\outside.md")
    _write(repo, source_path, "# Source backslash path\n")
    source_sha = _commit(repo, "source H")

    _write(repo, source_path, "# Source backslash path, later\n")
    _write(repo, nested_path, "# Nested slash path\n")
    for path in outsider_paths:
        _write(repo, path, "# Must stay outside exact RU scope\n")
    later_sha = _commit(repo, "Merge pull request #50704: exact path scope")
    return _ScopeFixture(
        repo_path=repo,
        gh=_VerifiedPull(later_sha),
        source_sha=source_sha,
        later_sha=later_sha,
        source_path=source_path,
        nested_path=nested_path,
        outsider_paths=outsider_paths,
    )


def _assert_safe_attributable_report(fixture: _Fixture, report: str) -> None:
    """Reject rendering injection and any loss or reassignment of drift evidence."""
    tokens = MarkdownIt("commonmark", {"html": True}).parse(report)
    all_tokens = tuple(_all_tokens(tokens))

    # The HTML-enabled parser is the sink. No untrusted field may become an HTML,
    # image, link, or additional heading node after Markdown is rendered.
    assert not {
        token.type
        for token in all_tokens
        if token.type in {"html_block", "html_inline", "image", "link_open"}
    }
    assert _heading_texts(tokens) == [
        ("h2", "Later RU provenance drift"),
        ("h3", f"Verified PR #50704 at {fixture.later_sha}"),
    ]

    expected_items = Counter(
        [
            *(f"{_literal_data(path)} — added" for path in fixture.paths),
            f"added include: {_literal_data(fixture.relation_path)} -> "
            f"{_literal_data(fixture.include_target)}",
            f"added href: {_literal_data(fixture.relation_path)} -> "
            f"{_literal_data(fixture.href_target)}",
        ]
    )
    actual_items = _list_items(tokens)
    assert len(actual_items) == sum(expected_items.values())
    assert Counter(actual_items) == expected_items

    visible = "\n".join(_inline_text(token) for token in all_tokens if token.type == "inline")
    assert f"Frozen first-parent range {fixture.source_sha}..{fixture.later_sha}:" in visible
    assert "связи-安全" in visible  # Safe Unicode stays human-readable, not ASCII-escaped.
    assert "\\n## injected-heading" in visible
    assert "\\u001b[31m" in visible
    assert "\\u001b[32m" in visible


def _exact_scope_report(fixture: _ScopeFixture) -> str:
    return build_later_ru_drift_report(
        str(fixture.repo_path),
        fixture.gh,
        owner="ydb-platform",
        repo="ydb",
        source_head_sha=fixture.source_sha,
        baseline_sha=fixture.later_sha,
        source_paths=(fixture.source_path,),
        docs_root="ydb/docs",
    )


def _safe_exact_scope_report(fixture: _ScopeFixture) -> str:
    """Hand-derived report representation for oracle mutation controls."""
    return "\n".join(
        (
            "## Later RU provenance drift",
            "",
            "Frozen first-parent range "
            f"{_code_literal(fixture.source_sha)}..{_code_literal(fixture.later_sha)}:",
            "",
            f"### Verified PR #50704 at {_code_literal(fixture.later_sha)}",
            "",
            f"- {_code_literal(fixture.source_path)} — modified, same-path with source PR",
            f"- {_code_literal(fixture.nested_path)} — added",
            "",
        )
    )


def _assert_exact_path_scope(fixture: _ScopeFixture, report: str) -> None:
    """Exact Git path identity governs both inclusion and same-path attribution."""
    tokens = MarkdownIt("commonmark", {"html": True}).parse(report)
    assert _heading_texts(tokens) == [
        ("h2", "Later RU provenance drift"),
        ("h3", f"Verified PR #50704 at {fixture.later_sha}"),
    ]
    assert Counter(_list_items(tokens)) == Counter(
        {
            f"{_literal_data(fixture.source_path)} — modified, same-path with source PR": 1,
            f"{_literal_data(fixture.nested_path)} — added": 1,
        }
    )
    visible = "\n".join(_inline_text(token) for token in tokens if token.type == "inline")
    assert f"Frozen first-parent range {fixture.source_sha}..{fixture.later_sha}:" in visible
    assert _literal_data(fixture.source_path) in visible
    assert _literal_data(fixture.nested_path) in visible
    for outsider in fixture.outsider_paths:
        assert _literal_data(outsider) not in visible


def _safe_report(fixture: _Fixture) -> str:
    """Independent safe representation used only to prove the oracle's mutations."""
    return "\n".join(
        (
            "## Later RU provenance drift",
            "",
            "Frozen first-parent range "
            f"{_code_literal(fixture.source_sha)}..{_code_literal(fixture.later_sha)}:",
            "",
            f"### Verified PR #50704 at {_code_literal(fixture.later_sha)}",
            "",
            *(f"- {_code_literal(path)} — added" for path in fixture.paths),
            "",
            "Relationship diffs (href/include/anchor only):",
            "",
            "- added include: "
            f"{_code_literal(fixture.relation_path)} -> {_code_literal(fixture.include_target)}",
            "- added href: "
            f"{_code_literal(fixture.relation_path)} -> {_code_literal(fixture.href_target)}",
            "",
        )
    )


def test_later_ru_drift_report_renders_hostile_git_data_as_literal_attributed_evidence(
    hostile_drift: _Fixture,
) -> None:
    """Raw interpolation must fail this test; literal rendering preserves evidence."""
    _assert_safe_attributable_report(hostile_drift, hostile_drift.report)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "   ",
        "`",
        "`leading",
        "trailing`",
        "``both```",
        " leading",
        "trailing ",
        " both ",
        "carriage\rline\n tab\t",
        "\u0441\u0432\u044f\u0437\u0438-\u5b89\u5168",
    ],
)
def test_markdown_code_literal_round_trips_hostile_values(value: str) -> None:
    tokens = MarkdownIt("commonmark", {"html": True}).parseInline(_markdown_code_literal(value))
    all_tokens = tuple(_all_tokens(tokens))

    assert not {
        token.type
        for token in all_tokens
        if token.type in {"html_block", "html_inline", "image", "link_open"}
    }
    assert "".join(
        _inline_text(token) for token in all_tokens if token.type == "inline"
    ) == _literal_data(value)


def test_later_ru_drift_scope_treats_git_path_bytes_as_verbatim() -> None:
    """A backslash in a Git filename is not a directory separator for report scope."""
    raw_git_path = r"ydb\docs\ru\outside.md"

    assert not _is_relevant_ru_path(raw_git_path, docs_root="ydb/docs")


def test_later_ru_drift_scope_and_attribution_use_exact_git_path_identity(
    exact_path_scope: _ScopeFixture,
) -> None:
    """Outsiders stay out; slash/backslash siblings stay distinct and attributable."""
    _assert_exact_path_scope(exact_path_scope, _exact_scope_report(exact_path_scope))


@pytest.mark.parametrize(
    "name",
    [
        "normalizes inclusion",
        "normalizes source attribution",
        "bans all backslashes",
    ],
)
def test_exact_path_scope_oracle_kills_normalization_and_backslash_mutants(
    exact_path_scope: _ScopeFixture, name: str
) -> None:
    """Each scope or attribution mutation fails against a hand-derived healthy report."""
    safe_report = _safe_exact_scope_report(exact_path_scope)
    _assert_exact_path_scope(exact_path_scope, safe_report)

    if name == "normalizes inclusion":
        mutant = safe_report.replace(
            f"- {_code_literal(exact_path_scope.nested_path)} — added\n",
            f"- {_code_literal(exact_path_scope.nested_path)} — added\n"
            f"- {_code_literal(exact_path_scope.outsider_paths[0])} — added\n",
        )
    elif name == "normalizes source attribution":
        mutant = safe_report.replace(
            f"- {_code_literal(exact_path_scope.source_path)} — modified, same-path with source PR",
            f"- {_code_literal(exact_path_scope.source_path)} — modified",
        ).replace(
            f"- {_code_literal(exact_path_scope.nested_path)} — added",
            f"- {_code_literal(exact_path_scope.nested_path)} — added, same-path with source PR",
        )
    else:
        mutant = _replace_list_item(
            safe_report,
            f"{_literal_data(exact_path_scope.source_path)} — modified, same-path with source PR",
            "",
        )

    with pytest.raises(AssertionError):
        _assert_exact_path_scope(exact_path_scope, mutant)
    _assert_exact_path_scope(exact_path_scope, safe_report)


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "raw hostile path interpolation",
            lambda fixture, report: _replace_list_item(
                report,
                f"{_literal_data(fixture.paths[0])} — added",
                f"- `{fixture.paths[0]}` — added\n",
            ),
        ),
        (
            "dropped hostile path row",
            lambda fixture, report: _replace_list_item(
                report, f"{_literal_data(fixture.paths[1])} — added", ""
            ),
        ),
        (
            "unescaped href relation target",
            lambda fixture, report: _replace_list_item(
                report,
                f"added href: {_literal_data(fixture.relation_path)} -> "
                f"{_literal_data(fixture.href_target)}",
                f"- added href: `{fixture.relation_path}` -> `{fixture.href_target}`\n",
            ),
        ),
        (
            "wrong verified PR",
            lambda fixture, report: report.replace("Verified PR #50704", "Verified PR #50705", 1),
        ),
        (
            "wrong verified SHA",
            lambda fixture, report: report.replace(fixture.later_sha, "0" * 40, 1),
        ),
        (
            "wrong path attribution",
            lambda fixture, report: report.replace(
                _literal_data(fixture.relation_path),
                _literal_data("ydb/docs/ru/core/security/other.md"),
                1,
            ),
        ),
    ],
)
def test_later_ru_drift_rendering_oracle_kills_safe_synthetic_mutations_and_recovers(
    hostile_drift: _Fixture,
    name: str,
    mutate: Callable[[_Fixture, str], str],
) -> None:
    """Each mutation is killed against an independently safe representation."""
    safe_report = _safe_report(hostile_drift)
    _assert_safe_attributable_report(hostile_drift, safe_report)
    mutant = mutate(hostile_drift, safe_report)
    assert mutant != safe_report
    with pytest.raises(AssertionError):
        _assert_safe_attributable_report(hostile_drift, mutant)
    _assert_safe_attributable_report(hostile_drift, safe_report)


@pytest.mark.parametrize(
    "name",
    [
        "raw interpolation",
        "fixed delimiter without padding",
        "drop hostile row",
        "unescaped one relation",
        "wrong verified PR",
        "wrong SHA",
        "wrong path attribution",
    ],
)
def test_actual_renderer_mutants_are_killed_and_recover(
    hostile_drift: _Fixture, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Exercise reversible implementation-level mutants, not only report strings."""
    def build() -> str:
        return build_later_ru_drift_report(
            str(hostile_drift.repo_path),
            hostile_drift.gh,
            owner="ydb-platform",
            repo="ydb",
            source_head_sha=hostile_drift.source_sha,
            baseline_sha=hostile_drift.later_sha,
            source_paths=("ydb/docs/ru/core/security/source.md",),
            docs_root="ydb/docs",
        )
    _assert_safe_attributable_report(hostile_drift, build())

    with monkeypatch.context() as altered:
        literal = provenance_drift._markdown_code_literal
        if name == "raw interpolation":
            altered.setattr(provenance_drift, "_markdown_code_literal", lambda value: value)
        elif name == "fixed delimiter without padding":
            altered.setattr(
                provenance_drift,
                "_markdown_code_literal",
                lambda value: f"`{_literal_data(value)}`",
            )
        elif name == "drop hostile row":
            original = provenance_drift._render_change

            def drop_row(*args, **kwargs):
                row, relations = original(*args, **kwargs)
                return ("", relations) if kwargs["path"] == hostile_drift.paths[1] else (row, relations)

            altered.setattr(provenance_drift, "_render_change", drop_row)
        elif name == "unescaped one relation":
            altered.setattr(
                provenance_drift,
                "_markdown_code_literal",
                lambda value: value if value == hostile_drift.href_target else literal(value),
            )
        elif name == "wrong verified PR":
            altered.setattr(
                provenance_drift, "_verified_pr_number", lambda _gh, **_kwargs: 50705
            )
        elif name == "wrong SHA":
            altered.setattr(
                provenance_drift,
                "_markdown_code_literal",
                lambda value: literal("0" * 40) if value == hostile_drift.later_sha else literal(value),
            )
        elif name == "wrong path attribution":
            altered.setattr(
                provenance_drift,
                "_markdown_code_literal",
                lambda value: literal("ydb/docs/ru/core/security/other.md")
                if value == hostile_drift.relation_path
                else literal(value),
            )
        with pytest.raises(AssertionError):
            _assert_safe_attributable_report(hostile_drift, build())

    _assert_safe_attributable_report(hostile_drift, build())
