"""Deterministic report of RU drift after a frozen source authority commit."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ydbdoc_review.github.git_ops import (
    commit_parent_sha,
    commit_subject,
    first_parent_commit_changes,
    first_parent_commits_between,
    read_text_at_commit,
)
from ydbdoc_review.pipeline.pairs import ChangeKind

_PR_SUBJECT_PATTERNS = (
    re.compile(r"^Merge pull request #(\d+)(?::|\s|$)"),
    re.compile(r"\(#(\d+)\)\s*$"),
)
_INCLUDE_RE = re.compile(r"{%\s*include\s+\[[^\]\n]*]\(([^)\n]+)\)\s*%}")
_MARKDOWN_HREF_RE = re.compile(r"(?<!!)\[[^\]\n]*]\(([^)\n]+)\)")
_YAML_HREF_RE = re.compile(r"^\s*href:\s*([^#\n]+?)\s*$", re.MULTILINE)
_ANCHOR_RE = re.compile(r"{#([A-Za-z0-9_.:-]+)}")


@dataclass(frozen=True, order=True)
class _Relation:
    kind: str
    target: str


@dataclass(frozen=True)
class _RelevantCommit:
    sha: str
    verified_pr: int | None
    candidate_pr: int | None
    changes: tuple[tuple[str, ChangeKind], ...]


def _markdown_code_literal(value: str) -> str:
    """Render one dynamic value as lossless literal CommonMark text."""
    literal = json.dumps(value, ensure_ascii=False)[1:-1]
    if not literal:
        return ""

    longest_backtick_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", literal)),
        default=0,
    )
    delimiter = "`" * (longest_backtick_run + 1)
    contains_non_space = bool(literal.strip(" "))
    needs_padding = (
        literal.startswith("`")
        or literal.endswith("`")
        or (contains_non_space and (literal.startswith(" ") or literal.endswith(" ")))
    )
    content = f" {literal} " if needs_padding else literal
    return f"{delimiter}{content}{delimiter}"


def _candidate_pr_number(subject: str) -> int | None:
    for pattern in _PR_SUBJECT_PATTERNS:
        match = pattern.search(subject)
        if match is not None:
            return int(match.group(1))
    return None


def _verified_pr_number(
    gh: Any,
    *,
    owner: str,
    repo: str,
    candidate: int | None,
    commit_sha: str,
) -> int | None:
    """Treat a subject regex as discovery only; the API association is proof."""
    if candidate is None:
        return None
    try:
        pull = gh.get_pull(owner, repo, candidate)
    except Exception:
        return None
    if not isinstance(pull, dict):
        return None
    if int(pull.get("number") or 0) != candidate:
        return None
    if pull.get("merged") is not True:
        return None
    if str(pull.get("merge_commit_sha") or "") != commit_sha:
        return None
    return candidate


def _is_relevant_ru_path(path: str, *, docs_root: str) -> bool:
    prefix = f"{docs_root.rstrip('/')}/ru/"
    return path.startswith(prefix) and path.endswith((".md", ".yaml", ".yml"))


def _normalize_target(source_path: str, target: str) -> str:
    raw = target.strip().strip("\"'")
    if not raw or raw.startswith("#") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw):
        return raw
    path, separator, fragment = raw.partition("#")
    if path.startswith("/"):
        normalized = posixpath.normpath(path).lstrip("/")
    else:
        normalized = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), path))
    return normalized + (f"#{fragment}" if separator else "")


def _relations(path: str, text: str | None) -> frozenset[_Relation]:
    if not text:
        return frozenset()
    includes = tuple(_INCLUDE_RE.finditer(text))
    masked = list(text)
    for match in includes:
        masked[match.start() : match.end()] = " " * (match.end() - match.start())
    without_includes = "".join(masked)
    relations = {
        _Relation("include", _normalize_target(path, match.group(1))) for match in includes
    }
    relations.update(
        _Relation("href", _normalize_target(path, match.group(1)))
        for match in _MARKDOWN_HREF_RE.finditer(without_includes)
    )
    relations.update(
        _Relation("href", _normalize_target(path, match.group(1)))
        for match in _YAML_HREF_RE.finditer(without_includes)
    )
    relations.update(_Relation("anchor", f"#{anchor}") for anchor in _ANCHOR_RE.findall(text))
    return frozenset(relations)


def _render_change(
    repo_path: str,
    *,
    commit_sha: str,
    path: str,
    kind: ChangeKind,
    source_paths: frozenset[str],
) -> tuple[str, tuple[str, ...]]:
    qualifiers = [kind]
    if path in source_paths:
        qualifiers.append("same-path with source PR")
    rendered_path = _markdown_code_literal(path)
    row = f"- {rendered_path} — {', '.join(qualifiers)}"
    relations: list[str] = []
    parent_sha = commit_parent_sha(repo_path, commit_sha)
    before = _relations(path, read_text_at_commit(repo_path, parent_sha, path))
    after = _relations(path, read_text_at_commit(repo_path, commit_sha, path))
    for relation in sorted(after - before):
        relations.append(
            f"- added {relation.kind}: {rendered_path} -> {_markdown_code_literal(relation.target)}"
        )
    for relation in sorted(before - after):
        relations.append(
            f"- removed {relation.kind}: {rendered_path} -> "
            f"{_markdown_code_literal(relation.target)}"
        )
    return row, tuple(relations)


def build_later_ru_drift_report(
    repo_path: str,
    gh: Any,
    *,
    owner: str,
    repo: str,
    source_head_sha: str,
    baseline_sha: str,
    source_paths: Iterable[str],
    docs_root: str,
    dependency_paths: Iterable[str] = (),
) -> str:
    """Render relevant first-parent RU drift in ``H..B`` with proven PR identity."""
    frozen_source_paths = frozenset(source_paths)
    allowed_paths = frozen_source_paths | frozenset(dependency_paths)
    relevant: list[_RelevantCommit] = []
    for sha in first_parent_commits_between(repo_path, source_head_sha, baseline_sha):
        changes = tuple(
            (path, kind)
            for path, kind in first_parent_commit_changes(repo_path, sha)
            if path in allowed_paths and _is_relevant_ru_path(path, docs_root=docs_root)
        )
        if not changes:
            continue
        candidate = _candidate_pr_number(commit_subject(repo_path, sha))
        relevant.append(
            _RelevantCommit(
                sha=sha,
                verified_pr=_verified_pr_number(
                    gh,
                    owner=owner,
                    repo=repo,
                    candidate=candidate,
                    commit_sha=sha,
                ),
                candidate_pr=candidate,
                changes=changes,
            )
        )
    if not relevant:
        return ""

    lines = [
        "## Later RU provenance drift",
        "",
        "Frozen first-parent range "
        f"{_markdown_code_literal(source_head_sha)}.."
        f"{_markdown_code_literal(baseline_sha)}:",
    ]
    for commit in relevant:
        lines.append("")
        if commit.verified_pr is not None:
            lines.append(
                "### Confirmed merge association: "
                f"PR #{commit.verified_pr} at {_markdown_code_literal(commit.sha)}"
            )
        else:
            candidate_note = (
                f"; unverified subject candidate #{commit.candidate_pr}"
                if commit.candidate_pr is not None
                else ""
            )
            lines.append(
                f"### Commit {_markdown_code_literal(commit.sha)} "
                f"— unknown PR association{candidate_note}"
            )
        lines.append("")
        rendered = [
            _render_change(
                repo_path,
                commit_sha=commit.sha,
                path=path,
                kind=kind,
                source_paths=frozen_source_paths,
            )
            for path, kind in commit.changes
        ]
        lines.extend(row for row, _relations_for_path in rendered)
        relation_lines = [
            line for _row, relations_for_path in rendered for line in relations_for_path
        ]
        if relation_lines:
            lines.extend(("", "Relationship diffs (href/include/anchor only):", ""))
            lines.extend(relation_lines)
    return "\n".join(lines).rstrip() + "\n"
