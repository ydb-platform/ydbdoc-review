"""Verify EN toc ``href`` / ``include.path`` targets exist on disk (§6.83)."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from ydbdoc_review.github.git_ops import read_text
from ydbdoc_review.navigation.paths import navigation_yaml_kind
from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.heuristics import bump_verdict_for_blocking_heuristics


def _target_exists(repo_path: str, rel_path: str) -> bool:
    if read_text(repo_path, rel_path) is not None:
        return True
    return Path(repo_path, rel_path.replace("/", os.sep)).is_file()


def check_missing_toc_targets(
    en_toc_path: str,
    en_toc_yaml: str,
    *,
    repo_path: str,
    pending_paths: set[str] | None = None,
) -> list[str]:
    """Blocking messages when a toc link points at a missing EN file."""
    if navigation_yaml_kind(en_toc_path) != "toc":
        return []

    pending = pending_paths or set()
    missing: list[str] = []
    seen: set[str] = set()
    for kind, rel in collect_toc_link_targets(en_toc_yaml):
        resolved = resolve_toc_target_path(en_toc_path, rel)
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved in pending or _target_exists(repo_path, resolved):
            continue
        missing.append(
            "missing_toc_target: "
            f"EN toc `{en_toc_path}` {kind} `{rel}` → missing file `{resolved}`"
        )
    return missing


def apply_toc_target_checks(
    result: PRTranslationResult,
    *,
    repo_path: str,
    pending_paths: set[str] | None = None,
) -> None:
    """Attach blocking toc-target findings to navigation verify results."""
    extra_pending = set(pending_paths or ())
    for run in result.pair_results:
        if run.plan.target_lang == "en" and run.plan.target_path.endswith(
            ("toc.yaml", "toc_i.yaml", "toc_p.yaml")
        ):
            extra_pending.add(run.plan.target_path)

    for nav in result.navigation_results:
        if nav.error or nav.kind != "toc":
            continue
        en_text = nav.target_text
        if en_text is None:
            en_text = read_text(repo_path, nav.en_path)
        if en_text is None:
            continue
        msgs = check_missing_toc_targets(
            nav.en_path,
            en_text,
            repo_path=repo_path,
            pending_paths=extra_pending,
        )
        if not msgs:
            continue
        nav.warnings.extend(msgs)
        nav.verdict = bump_verdict_for_blocking_heuristics(nav.verdict, msgs)
