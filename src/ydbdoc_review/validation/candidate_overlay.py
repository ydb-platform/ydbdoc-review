"""Transactional validation of pending documentation writes and deletes."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.types import PRTranslationResult


@dataclass(frozen=True)
class CandidateOverlayIssue:
    code: str
    owner_path: str
    target_path: str
    responsible_path: str

    def format(self) -> str:
        return f"{self.code}: {self.owner_path} -> {self.target_path}"

    def __contains__(self, value: str) -> bool:
        return value in self.format()

    def endswith(self, value: str) -> bool:
        return self.format().endswith(value)


def validate_candidate_overlay(
    repo_path: str,
    result: PRTranslationResult,
    *,
    docs_root: str = "ydb/docs",
    baseline_ref: str | None = None,
) -> list[CandidateOverlayIssue]:
    """Resolve includes against current files plus the complete pending transaction."""
    writes: dict[str, str] = {}
    deletes: set[str] = set()
    for run in result.pair_results:
        if run.skipped or run.error:
            continue
        if run.deleted:
            deletes.add(run.plan.target_path)
        elif run.target_text is not None:
            writes[run.plan.target_path] = run.target_text
        deletes.update(run.additional_delete_paths)
    for nav in result.navigation_results:
        if not nav.error and nav.target_text is not None:
            writes[nav.en_path] = nav.target_text

    baseline_paths: set[str] | None = None
    if baseline_ref is not None:
        proc = subprocess.run(
            ["git", "-C", repo_path, "ls-tree", "-r", "--name-only", baseline_ref, "--", docs_root],
            capture_output=True,
            text=True,
            check=True,
        )
        baseline_paths = {line for line in proc.stdout.splitlines() if line}

    def read_baseline(path: str) -> str | None:
        if baseline_ref is None:
            disk = Path(repo_path) / path
            return disk.read_text(encoding="utf-8") if disk.is_file() else None
        if baseline_paths is not None and path not in baseline_paths:
            return None
        proc = subprocess.run(
            ["git", "-C", repo_path, "show", f"{baseline_ref}:{path}"],
            capture_output=True,
        )
        return proc.stdout.decode("utf-8") if proc.returncode == 0 else None

    def read_overlay(path: str) -> str | None:
        if path in deletes:
            return None
        if path in writes:
            return writes[path]
        return read_baseline(path)

    def overlay_exists(path: str) -> bool:
        if path in deletes:
            return False
        return path in writes or read_baseline(path) is not None

    def resolve_local(owner: str, href: str) -> str | None:
        href = href.strip().strip("<>")
        if not href or href.startswith("#"):
            return None
        href = href.split("#", 1)[0]
        if not href or href.startswith("/") or "://" in href or href.startswith("mailto:"):
            return None
        joined = PurePosixPath(owner).parent.joinpath(href)
        parts: list[str] = []
        for part in joined.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in {"", "."}:
                parts.append(part)
        return "/".join(parts)

    errors: list[CandidateOverlayIssue] = []
    def issue(code: str, owner: str, target: str, responsible: str | None = None) -> None:
        errors.append(CandidateOverlayIssue(code, owner, target, responsible or owner))
    seen: set[str] = set()
    queue = sorted(path for path in writes if path.endswith(".md"))
    while queue:
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        text = read_overlay(page)
        if text is None:
            continue
        for include in collect_yfm_includes(text):
            target = resolve_locale_md_path(page, include.path, docs_root=docs_root)
            if target is None:
                continue
            if read_overlay(target) is None:
                issue("candidate_overlay_missing_include", page, target)
            elif target.endswith(".md") and target not in seen:
                queue.append(target)

    # Outbound markdown links from pending writes only. A full-repo scan
    # false-positives on template placeholders in style guides and ruler files.
    for page in sorted(seen):
        text = read_overlay(page)
        if text is None:
            continue
        includes = collect_yfm_includes(text)
        include_hrefs = {include.path for include in includes}
        for href in re.findall(r"\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)", text):
            if href.strip().strip("<>") in include_hrefs:
                continue
            target = resolve_local(page, href)
            if target is None or not target.lower().endswith(".md"):
                continue
            if target in deletes:
                issue("candidate_overlay_delete_markdown_reference", page, target, target)
            elif not overlay_exists(target):
                issue("candidate_overlay_missing_markdown_target", page, target)

    if deletes:
        all_md = (
            {path for path in baseline_paths or set() if path.endswith(".md")}
            if baseline_ref is not None
            else {
                str(path.relative_to(repo_path)).replace("\\", "/")
                for path in Path(repo_path).joinpath(docs_root).rglob("*.md")
            }
        )
        for page in sorted(all_md - deletes):
            text = read_overlay(page)
            if text is None:
                continue
            includes = collect_yfm_includes(text)
            include_hrefs = {include.path for include in includes}
            for include in includes:
                target = resolve_locale_md_path(page, include.path, docs_root=docs_root)
                if target in deletes:
                    issue("candidate_overlay_delete_inbound_include", page, target, target)
            for href in re.findall(r"\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)", text):
                if href.strip().strip("<>") in include_hrefs:
                    continue
                target = resolve_local(page, href)
                if target is None or not target.lower().endswith(".md"):
                    continue
                if target in deletes:
                    issue("candidate_overlay_delete_markdown_reference", page, target, target)

    yaml_writes = {path for path in writes if path.endswith((".yaml", ".yml"))}
    for toc_path in sorted(yaml_writes - deletes):
        text = read_overlay(toc_path)
        if text is None:
            continue
        for href in re.findall(r"(?:href|path):\s*['\"]?([^'\"\s]+)", text):
            target = resolve_local(toc_path, href)
            if target is None:
                continue
            if target in deletes:
                issue("candidate_overlay_delete_toc_dependency", toc_path, target, target)
            elif not overlay_exists(target):
                issue("candidate_overlay_missing_toc_target", toc_path, target)

    if deletes:
        all_yaml = ({path for path in baseline_paths or set() if path.endswith((".yaml", ".yml"))} if baseline_ref is not None else {
            str(path.relative_to(repo_path)).replace("\\", "/")
            for path in Path(repo_path).joinpath(docs_root).rglob("*.yaml")
        } | {
            str(path.relative_to(repo_path)).replace("\\", "/")
            for path in Path(repo_path).joinpath(docs_root).rglob("*.yml")
        })
        for toc_path in sorted(all_yaml - deletes):
            text = read_overlay(toc_path)
            if text is None:
                continue
            for href in re.findall(r"(?:href|path):\s*['\"]?([^'\"\s]+)", text):
                target = resolve_local(toc_path, href)
                if target is None:
                    continue
                if target in deletes:
                    issue("candidate_overlay_delete_toc_dependency", toc_path, target, target)
    return sorted(set(errors), key=lambda item: (item.code, item.owner_path, item.target_path))
