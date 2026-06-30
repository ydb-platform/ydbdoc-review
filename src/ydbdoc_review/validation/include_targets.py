"""Verify locale ``{% include %}`` targets exist on the EN mirror tree (§6.80)."""

from __future__ import annotations

import os
from pathlib import Path

from ydbdoc_review.github.git_ops import read_text
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.heuristics import bump_verdict_for_blocking_heuristics


def _include_target_exists(repo_path: str, rel_path: str) -> bool:
    if read_text(repo_path, rel_path) is not None:
        return True
    return Path(repo_path, rel_path.replace("/", os.sep)).is_file()


def check_missing_locale_include_targets(
    en_md_path: str,
    en_text: str,
    *,
    repo_path: str,
    docs_root: str = "ydb/docs",
) -> list[str]:
    """Blocking messages when a locale-relative include has no EN mirror file."""
    root = docs_root.strip("/")
    if not en_md_path.startswith(f"{root}/en/"):
        return []

    missing: list[str] = []
    seen: set[str] = set()
    for inc in collect_yfm_includes(en_text):
        resolved = resolve_locale_md_path(
            en_md_path, inc.path, docs_root=docs_root
        )
        if resolved is None or not resolved.startswith(f"{root}/en/"):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if not _include_target_exists(repo_path, resolved):
            missing.append(
                "include_target: "
                f"EN include missing `{resolved}` "
                f"(from `{{% include … %}}` → `{inc.path}` in `{en_md_path}`)"
            )
    return missing


def apply_include_target_checks(
    result: PRTranslationResult,
    *,
    repo_path: str,
    docs_root: str = "ydb/docs",
) -> None:
    """Attach blocking include-target findings to verify pair results."""
    for run in result.pair_results:
        fr = run.file_result
        if fr is None or run.skipped or run.deleted or run.error:
            continue
        if run.plan.target_lang != "en" or not run.plan.target_path.endswith(".md"):
            continue
        en_text = fr.final_text or run.target_text or ""
        msgs = check_missing_locale_include_targets(
            run.plan.target_path,
            en_text,
            repo_path=repo_path,
            docs_root=docs_root,
        )
        if not msgs:
            continue
        fr.heuristic_blocking.extend(msgs)
        fr.verdict = bump_verdict_for_blocking_heuristics(fr.verdict, msgs)
