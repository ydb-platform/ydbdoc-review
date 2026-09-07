"""Verify EN toc ``href`` / ``include.path`` targets exist on disk (§6.83).

Also flag translated EN pages that are not reachable from any sidebar toc (§6.117).
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from ydbdoc_review.github.git_ops import read_text
from ydbdoc_review.navigation.paths import navigation_yaml_kind
from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.glossary_toc_links import (
    collect_en_toc_reachable_md,
    normalize_repo_path,
)
from ydbdoc_review.validation.heuristics import bump_verdict_for_blocking_heuristics
from ydbdoc_review.validation.href_parity import _mask_link_protected_ranges

ReadText = Callable[[str], str | None]


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


def _is_toc_orphan_exempt(
    md_path: str,
    *,
    docs_root: str,
    locale: str = "en",
) -> bool:
    """Locale includes and non-doc paths are not expected as sidebar ``href``s."""
    normalized = normalize_repo_path(md_path)
    root = docs_root.strip("/")
    loc = locale.strip("/").lower()
    if loc not in {"en", "ru"}:
        raise ValueError(f"locale must be 'en' or 'ru', got {locale!r}")
    if not normalized.startswith(f"{root}/{loc}/"):
        return True
    if "/_includes/" in normalized or normalized.endswith("/_includes"):
        return True
    return False


def _relative_include_stays_in_locale(
    referrer_path: str,
    include_ref: str,
    *,
    docs_root: str,
    locale: str,
) -> bool:
    """Reject include proof that escapes its locale before path normalization."""
    ref = include_ref.strip().split("#", 1)[0].strip()
    if not ref or ref.startswith("/") or "\\" in ref:
        return False

    floor = [*PurePosixPath(docs_root.strip("/")).parts, locale]
    stack = list(PurePosixPath(normalize_repo_path(referrer_path)).parent.parts)
    if stack[: len(floor)] != floor:
        return False

    for part in ref.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if len(stack) == len(floor):
                return False
            stack.pop()
            continue
        stack.append(part)
    return True


def _collect_reachable_include_dependencies(
    read_text: ReadText,
    *,
    toc_reachable: set[str] | frozenset[str],
    docs_root: str,
    locale: str,
) -> frozenset[str]:
    """Close real YFM Markdown includes from TOC-reachable pages only."""
    locale_root = f"{docs_root.strip('/')}/{locale}/"
    queue = deque(
        sorted(
            normalize_repo_path(path)
            for path in toc_reachable
            if normalize_repo_path(path).startswith(locale_root)
        )
    )
    seen: set[str] = set()
    reachable: set[str] = set()

    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        text = read_text(path)
        if text is None:
            continue
        reachable.add(path)
        visible_text = _mask_link_protected_ranges(text)
        for include in collect_yfm_includes(visible_text):
            if not _relative_include_stays_in_locale(
                path,
                include.path,
                docs_root=docs_root,
                locale=locale,
            ):
                continue
            resolved = resolve_locale_md_path(
                path,
                include.path,
                docs_root=docs_root,
            )
            if resolved is None:
                continue
            resolved = normalize_repo_path(resolved)
            if not resolved.startswith(locale_root) or resolved in seen:
                continue
            if read_text(resolved) is None:
                continue
            queue.append(resolved)
    return frozenset(reachable)


def find_locale_pages_missing_from_toc(
    repo_path: str,
    *,
    locale: str,
    docs_root: str = "ydb/docs",
    pending_toc_texts: dict[str, str] | None = None,
) -> list[str]:
    """Return ``.md`` paths for ``locale`` that are off that locale's toc graph.

    Skips ``_includes/``. Root toc: ``{docs_root}/{locale}/core/toc_p.yaml``.
    Used by ops scripts (``scripts/find_toc_orphans.py``).
    """
    loc = locale.strip("/").lower()
    if loc not in {"en", "ru"}:
        raise ValueError(f"locale must be 'en' or 'ru', got {locale!r}")

    root = docs_root.strip("/")
    locale_root = Path(repo_path) / root.replace("/", os.sep) / loc
    if not locale_root.is_dir():
        return []

    candidates: set[str] = set()
    for path in locale_root.rglob("*.md"):
        rel = path.relative_to(repo_path).as_posix()
        if _is_toc_orphan_exempt(rel, docs_root=docs_root, locale=loc):
            continue
        candidates.add(normalize_repo_path(rel))

    orphans = check_orphan_pages_for_locale(
        candidates,
        repo_path=repo_path,
        locale=loc,
        docs_root=docs_root,
        pending_toc_texts=pending_toc_texts,
    )
    return sorted(orphans)


def find_pages_missing_from_toc(
    repo_path: str,
    *,
    locales: tuple[str, ...] | list[str] = ("en", "ru"),
    docs_root: str = "ydb/docs",
    pending_toc_texts: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Return ``{locale: [orphan paths…]}`` for each requested locale."""
    out: dict[str, list[str]] = {}
    for locale in locales:
        out[locale.strip("/").lower()] = find_locale_pages_missing_from_toc(
            repo_path,
            locale=locale,
            docs_root=docs_root,
            pending_toc_texts=pending_toc_texts,
        )
    return out


def find_en_pages_missing_from_toc(
    repo_path: str,
    *,
    docs_root: str = "ydb/docs",
    pending_toc_texts: dict[str, str] | None = None,
) -> list[str]:
    """Return EN ``.md`` paths under ``docs_root`` that are off the EN toc graph.

    Skips ``_includes/``. Alias of ``find_locale_pages_missing_from_toc(…, locale="en")``.
    """
    return find_locale_pages_missing_from_toc(
        repo_path,
        locale="en",
        docs_root=docs_root,
        pending_toc_texts=pending_toc_texts,
    )


def check_orphan_pages_for_locale(
    md_paths: set[str] | frozenset[str],
    *,
    repo_path: str,
    locale: str = "en",
    docs_root: str = "ydb/docs",
    pending_toc_texts: dict[str, str] | None = None,
    pending_md_texts: dict[str, str] | None = None,
    unavailable_md_paths: set[str] | frozenset[str] | None = None,
    extra_toc_paths: set[str] | frozenset[str] | None = None,
    baseline_ref: str | None = None,
) -> dict[str, list[str]]:
    """Map page path → messages when the page is off that locale's toc graph.

    A ``.md`` (except ``_includes/``) must be reachable from
    ``{docs_root}/{locale}/core/toc_p.yaml`` through toc ``href`` entries,
    ``include.path`` child sidebars, or visible YFM includes from those pages.

    When supplied, pending texts override the immutable ``baseline_ref``;
    unavailable pending paths cannot fall back to it. Otherwise prefer ``HEAD``
    over a dirty worktree (§6.133).
    """
    from ydbdoc_review.github.git_ops import (
        read_text_at_ref,
        read_text_at_upstream_tip,
    )

    loc = locale.strip("/").lower()
    if loc not in {"en", "ru"}:
        raise ValueError(f"locale must be 'en' or 'ru', got {locale!r}")

    pending_tocs = {
        normalize_repo_path(p): text
        for p, text in (pending_toc_texts or {}).items()
    }
    pending_texts_supplied = pending_md_texts is not None
    pending_texts = {
        normalize_repo_path(p): text
        for p, text in (pending_md_texts or {}).items()
    }
    unavailable = {
        normalize_repo_path(p) for p in (unavailable_md_paths or ())
    }
    pending_md = {
        normalize_repo_path(p)
        for p in md_paths
        if p.endswith(".md")
        and not _is_toc_orphan_exempt(p, docs_root=docs_root, locale=loc)
    }
    if not pending_md:
        return {}

    def _read(path: str) -> str | None:
        key = normalize_repo_path(path)
        if key in pending_texts:
            return pending_texts[key]
        if key in pending_tocs:
            return pending_tocs[key]
        if key in unavailable:
            return None
        if baseline_ref:
            return read_text_at_upstream_tip(repo_path, baseline_ref, key)
        head = read_text_at_ref(repo_path, "HEAD", key)
        if head is not None:
            return head
        return read_text(repo_path, key)

    root_toc = f"{docs_root.strip('/')}/{loc}/core/toc_p.yaml"
    extra = {
        normalize_repo_path(p)
        for p in (extra_toc_paths or ())
        if str(p).endswith((".yaml", ".yml"))
    }
    # Name is historical (EN QA); BFS is locale-agnostic given ``root_toc``.
    toc_reachable = collect_en_toc_reachable_md(
        _read,
        root_toc=root_toc,
        extra_md_paths=(
            frozenset(pending_texts)
            if pending_texts_supplied
            else pending_md
        ),
        extra_toc_paths=extra,
        seed_extra_md=False,
    )
    include_reachable = _collect_reachable_include_dependencies(
        _read,
        toc_reachable=toc_reachable,
        docs_root=docs_root,
        locale=loc,
    )
    reachable = toc_reachable | include_reachable

    out: dict[str, list[str]] = {}
    for path in sorted(pending_md):
        if path in reachable:
            continue
        out[path] = [
            "orphan_toc_page: "
            f"{'translated EN' if loc == 'en' else 'RU'} page `{path}` is not "
            f"linked from any {'EN' if loc == 'en' else 'RU'} toc "
            f"(reachable from `{root_toc}` via href/include.path)"
        ]
    return out


def check_orphan_translated_pages(
    en_md_paths: set[str] | frozenset[str],
    *,
    repo_path: str,
    docs_root: str = "ydb/docs",
    pending_toc_texts: dict[str, str] | None = None,
    pending_md_texts: dict[str, str] | None = None,
    unavailable_md_paths: set[str] | frozenset[str] | None = None,
    extra_toc_paths: set[str] | frozenset[str] | None = None,
    baseline_ref: str | None = None,
) -> dict[str, list[str]]:
    """Map EN page path → blocking messages when the page is off the EN toc graph.

    Pipeline QA wrapper around ``check_orphan_pages_for_locale(…, locale="en")``.
    """
    return check_orphan_pages_for_locale(
        en_md_paths,
        repo_path=repo_path,
        locale="en",
        docs_root=docs_root,
        pending_toc_texts=pending_toc_texts,
        pending_md_texts=pending_md_texts,
        unavailable_md_paths=unavailable_md_paths,
        extra_toc_paths=extra_toc_paths,
        baseline_ref=baseline_ref,
    )


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
    baseline_ref: str | None = None,
    exempt_en_paths: frozenset[str] | set[str] | None = None,
) -> list[str]:
    """Attach blocking findings for translated EN pages missing from the toc graph.

    Returns orphan EN ``.md`` paths (for translate completeness gating, §6.140).
    ``exempt_en_paths`` skips redirect tombstones (``redirects.yaml`` ``from``),
    which must not be mirrored as EN pages (§6.224).
    """
    pending_toc_texts: dict[str, str] = {}
    extra_toc_paths: set[str] = set()
    for nav in result.navigation_results:
        if nav.error or nav.kind != "toc" or nav.target_text is None:
            continue
        extra_toc_paths.add(normalize_repo_path(nav.en_path))
        pending_toc_texts[nav.en_path] = nav.target_text

    exempt = {normalize_repo_path(p) for p in (exempt_en_paths or ())}
    en_md_paths: set[str] = set()
    pending_md_texts: dict[str, str] = {}
    unavailable_md_paths: set[str] = set()
    runs_by_path: dict[str, list] = {}
    for run in result.pair_results:
        if run.plan.target_lang != "en" or not run.plan.target_path.endswith(".md"):
            continue
        path = normalize_repo_path(run.plan.target_path)
        if run.deleted or run.error:
            unavailable_md_paths.add(path)
            continue
        if run.skipped:
            continue
        fr = run.file_result
        if fr is None:
            unavailable_md_paths.add(path)
            continue
        pending_md_texts[path] = fr.final_text
        if path in exempt:
            continue
        en_md_paths.add(path)
        runs_by_path.setdefault(path, []).append(run)

    orphans = check_orphan_translated_pages(
        en_md_paths,
        repo_path=repo_path,
        docs_root=docs_root,
        pending_toc_texts=pending_toc_texts,
        pending_md_texts=pending_md_texts,
        unavailable_md_paths=unavailable_md_paths,
        extra_toc_paths=extra_toc_paths,
        baseline_ref=baseline_ref,
    )
    for path, msgs in orphans.items():
        for run in runs_by_path.get(path, []):
            fr = run.file_result
            if fr is None:
                continue
            fr.heuristic_blocking.extend(msgs)
            fr.verdict = bump_verdict_for_blocking_heuristics(fr.verdict, msgs)
    return sorted(orphans.keys())
