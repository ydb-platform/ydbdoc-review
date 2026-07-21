"""Verify EN toc ``href`` / ``include.path`` targets exist on disk (§6.83).

Also flag translated EN pages that are not reachable from any sidebar toc (§6.117).
"""

from __future__ import annotations

import os
from pathlib import Path

from ydbdoc_review.github.git_ops import read_text
from ydbdoc_review.navigation.paths import navigation_yaml_kind
from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.glossary_toc_links import (
    collect_en_toc_reachable_md,
    normalize_repo_path,
)
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


def _is_toc_orphan_exempt(en_md_path: str, *, docs_root: str) -> bool:
    """Locale includes and non-doc paths are not expected as sidebar ``href``s."""
    normalized = normalize_repo_path(en_md_path)
    root = docs_root.strip("/")
    if not normalized.startswith(f"{root}/en/"):
        return True
    if "/_includes/" in normalized or normalized.endswith("/_includes"):
        return True
    return False


def find_en_pages_missing_from_toc(
    repo_path: str,
    *,
    docs_root: str = "ydb/docs",
    pending_toc_texts: dict[str, str] | None = None,
) -> list[str]:
    """Return EN ``.md`` paths under ``docs_root`` that are off the EN toc graph.

    Skips ``_includes/``. Used by the en-toc-orphans audit skill and ops scripts.
    """
    root = docs_root.strip("/")
    en_root = Path(repo_path) / root.replace("/", os.sep) / "en"
    if not en_root.is_dir():
        return []

    candidates: set[str] = set()
    for path in en_root.rglob("*.md"):
        rel = path.relative_to(repo_path).as_posix()
        if _is_toc_orphan_exempt(rel, docs_root=docs_root):
            continue
        candidates.add(normalize_repo_path(rel))

    orphans = check_orphan_translated_pages(
        candidates,
        repo_path=repo_path,
        docs_root=docs_root,
        pending_toc_texts=pending_toc_texts,
    )
    return sorted(orphans)


def check_orphan_translated_pages(
    en_md_paths: set[str] | frozenset[str],
    *,
    repo_path: str,
    docs_root: str = "ydb/docs",
    pending_toc_texts: dict[str, str] | None = None,
    extra_toc_paths: set[str] | frozenset[str] | None = None,
) -> dict[str, list[str]]:
    """Map EN page path → blocking messages when the page is off the EN toc graph.

    A translated ``.md`` (except ``_includes/``) must appear as a ``href`` reachable
    from ``{docs_root}/en/core/toc_p.yaml`` via ``include.path`` child sidebars.

    ``extra_toc_paths`` — EN toc files from the translation PR — are queued into
    the BFS besides the root walk (§6.133). Prefer ``HEAD`` over a dirty worktree
    so a momentary main-like EN sidebar cannot false-🔴 orphan pages.
    """
    from ydbdoc_review.github.git_ops import read_text_at_ref

    pending_tocs = {
        normalize_repo_path(p): text
        for p, text in (pending_toc_texts or {}).items()
    }
    pending_md = {
        normalize_repo_path(p)
        for p in en_md_paths
        if p.endswith(".md") and not _is_toc_orphan_exempt(p, docs_root=docs_root)
    }
    if not pending_md:
        return {}

    def _read(path: str) -> str | None:
        key = normalize_repo_path(path)
        if key in pending_tocs:
            return pending_tocs[key]
        head = read_text_at_ref(repo_path, "HEAD", key)
        if head is not None:
            return head
        return read_text(repo_path, key)

    root_toc = f"{docs_root.strip('/')}/en/core/toc_p.yaml"
    extra = {
        normalize_repo_path(p)
        for p in (extra_toc_paths or ())
        if str(p).endswith((".yaml", ".yml"))
    }
    reachable = collect_en_toc_reachable_md(
        _read,
        root_toc=root_toc,
        extra_md_paths=pending_md,
        extra_toc_paths=extra,
        seed_extra_md=False,
    )

    out: dict[str, list[str]] = {}
    for path in sorted(pending_md):
        if path in reachable:
            continue
        out[path] = [
            "orphan_toc_page: "
            f"translated EN page `{path}` is not linked from any EN toc "
            f"(reachable from `{root_toc}` via href/include.path)"
        ]
    return out


def apply_toc_target_checks(
    result: PRTranslationResult,
    *,
    repo_path: str,
    pending_paths: set[str] | None = None,
) -> None:
    """Attach blocking toc-target findings to navigation verify results."""
    from ydbdoc_review.github.git_ops import read_text_at_ref

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
            en_text = read_text_at_ref(repo_path, "HEAD", nav.en_path)
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


def apply_orphan_toc_page_checks(
    result: PRTranslationResult,
    *,
    repo_path: str,
    docs_root: str = "ydb/docs",
) -> None:
    """Attach blocking findings for translated EN pages missing from the toc graph."""
    pending_toc_texts: dict[str, str] = {}
    extra_toc_paths: set[str] = set()
    for nav in result.navigation_results:
        if nav.error or nav.kind != "toc":
            continue
        extra_toc_paths.add(normalize_repo_path(nav.en_path))
        if nav.target_text is not None:
            pending_toc_texts[nav.en_path] = nav.target_text

    en_md_paths: set[str] = set()
    runs_by_path: dict[str, list] = {}
    for run in result.pair_results:
        fr = run.file_result
        if fr is None or run.skipped or run.deleted or run.error:
            continue
        if run.plan.target_lang != "en" or not run.plan.target_path.endswith(".md"):
            continue
        path = normalize_repo_path(run.plan.target_path)
        en_md_paths.add(path)
        runs_by_path.setdefault(path, []).append(run)

    orphans = check_orphan_translated_pages(
        en_md_paths,
        repo_path=repo_path,
        docs_root=docs_root,
        pending_toc_texts=pending_toc_texts,
        extra_toc_paths=extra_toc_paths,
    )
    for path, msgs in orphans.items():
        for run in runs_by_path.get(path, []):
            fr = run.file_result
            if fr is None:
                continue
            fr.heuristic_blocking.extend(msgs)
            fr.verdict = bump_verdict_for_blocking_heuristics(fr.verdict, msgs)
