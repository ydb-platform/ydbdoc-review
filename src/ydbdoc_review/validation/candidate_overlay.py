"""Transactional validation of pending documentation writes and deletes."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.types import PRTranslationResult


def validate_candidate_overlay(
    repo_path: str,
    result: PRTranslationResult,
    *,
    docs_root: str = "ydb/docs",
) -> list[str]:
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
    for nav in result.navigation_results:
        if not nav.error and nav.target_text is not None:
            writes[nav.en_path] = nav.target_text

    def read_overlay(path: str) -> str | None:
        if path in deletes:
            return None
        if path in writes:
            return writes[path]
        disk = Path(repo_path) / path
        if not disk.is_file():
            return None
        return disk.read_text(encoding="utf-8")

    def overlay_exists(path: str) -> bool:
        if path in deletes:
            return False
        return path in writes or (Path(repo_path) / path).exists()

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

    errors: list[str] = []
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
                errors.append(f"candidate_overlay_missing_include: {page} -> {target}")
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
                errors.append(f"candidate_overlay_delete_markdown_reference: {page} -> {target}")
            elif not overlay_exists(target):
                errors.append(f"candidate_overlay_missing_markdown_target: {page} -> {target}")

    if deletes:
        all_md = {
            str(path.relative_to(repo_path)).replace("\\", "/")
            for path in Path(repo_path).joinpath(docs_root).rglob("*.md")
        }
        for page in sorted(all_md - deletes):
            text = read_overlay(page)
            if text is None:
                continue
            includes = collect_yfm_includes(text)
            include_hrefs = {include.path for include in includes}
            for include in includes:
                target = resolve_locale_md_path(page, include.path, docs_root=docs_root)
                if target in deletes:
                    errors.append(f"candidate_overlay_delete_inbound_include: {page} -> {target}")
            for href in re.findall(r"\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)", text):
                if href.strip().strip("<>") in include_hrefs:
                    continue
                target = resolve_local(page, href)
                if target is None or not target.lower().endswith(".md"):
                    continue
                if target in deletes:
                    errors.append(f"candidate_overlay_delete_markdown_reference: {page} -> {target}")

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
                errors.append(f"candidate_overlay_delete_toc_dependency: {toc_path} -> {target}")
            elif not overlay_exists(target):
                errors.append(f"candidate_overlay_missing_toc_target: {toc_path} -> {target}")

    if deletes:
        all_yaml = {
            str(path.relative_to(repo_path)).replace("\\", "/")
            for path in Path(repo_path).joinpath(docs_root).rglob("*.yaml")
        } | {
            str(path.relative_to(repo_path)).replace("\\", "/")
            for path in Path(repo_path).joinpath(docs_root).rglob("*.yml")
        }
        for toc_path in sorted(all_yaml - deletes):
            text = read_overlay(toc_path)
            if text is None:
                continue
            for href in re.findall(r"(?:href|path):\s*['\"]?([^'\"\s]+)", text):
                target = resolve_local(toc_path, href)
                if target is None:
                    continue
                if target in deletes:
                    errors.append(f"candidate_overlay_delete_toc_dependency: {toc_path} -> {target}")
    return sorted(set(errors))
