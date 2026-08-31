"""Deterministic EN internal-link gate against the final docs tree (§6.226).

Pair-level ``outbound_fragment`` can miss href-only deterministic preserves
(no heuristics) and runs before sibling EN targets exist on disk. After apply /
on verify tip, scan changed EN pages: resolve relative ``.md`` hrefs, require
the target file, and require ``#fragment`` to be declared on that page.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import _render_inline
from ydbdoc_review.validation.fragment_repair import (
    fragment_declared_in_markdown,
)
from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href
from ydbdoc_review.validation.href_parity import _MD_LINK, _is_internal_href
from ydbdoc_review.validation.yfm_anchor import (
    _iter_headings,
    diplodoc_auto_slug,
    split_heading_anchor_suffix,
)

DocsTextReader = Callable[[str], str | None]


def list_declared_fragments(md: str) -> list[str]:
    """Stable list of explicit ``{#id}`` and Diplodoc auto-slugs on ``md``."""
    if not md:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for heading in _iter_headings(parse_markdown(md).children):
        plain = _render_inline(heading.children).strip()
        title, explicit = split_heading_anchor_suffix(plain)
        frag = explicit or diplodoc_auto_slug(title)
        if not frag or frag in seen:
            continue
        seen.add(frag)
        out.append(frag)
    return out


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _short_target(path: str) -> str:
    """``ydb/docs/en/core/devops/concepts/foo.md`` → ``devops/concepts/foo.md``."""
    norm = path.replace("\\", "/")
    marker = "/core/"
    if marker in norm:
        return norm.split(marker, 1)[1]
    return PurePosixPath(norm).name


def _link_target_issue_key(message: str) -> str | None:
    """Stable key: ``target`` path + missing file/fragment (ignore line numbers)."""
    target = None
    detail = None
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith("target:"):
            target = stripped.removeprefix("target:").strip()
        elif stripped.startswith("missing file"):
            detail = "missing file"
        elif stripped.startswith("missing fragment:"):
            detail = stripped
    if not target or not detail:
        return None
    return f"{target}::{detail}"


def check_en_page_link_targets(
    en_page_path: str,
    en_text: str,
    *,
    read_text: DocsTextReader,
    baseline_text: str | None = None,
) -> list[str]:
    """Blocking messages for broken relative EN ``.md`` links / fragments.

    When ``baseline_text`` is set (pre-translate tip EN), suppress findings that
    already exist on that baseline (§6.228 ambient tip debt). New broken hrefs
    introduced by this translation still block.
    """
    if not en_page_path or not en_text:
        return []
    issues: list[str] = []
    page = en_page_path.replace("\\", "/")
    for match in _MD_LINK.finditer(en_text):
        label, href = match.group(1), match.group(2).strip()
        if label.strip() == "{#T}":
            # Autotitle: still validate path/fragment.
            pass
        if not _is_internal_href(href):
            continue
        path_part, _, fragment = href.partition("#")
        if path_part and not path_part.endswith(".md"):
            continue
        target_path = page if not path_part else resolve_internal_md_href(page, href)
        if target_path is None:
            continue
        target_md = en_text if target_path == page else read_text(target_path)
        line = _line_number(en_text, match.start())
        page_name = PurePosixPath(page).name
        if not target_md:
            issues.append(
                "en_link_target: "
                f"{page_name}:{line}\n"
                f"  target: {_short_target(target_path)}\n"
                f"  missing file"
            )
            continue
        if not fragment:
            continue
        if fragment_declared_in_markdown(
            target_md,
            fragment,
            page_path=target_path,
            read_text=read_text,
        ):
            continue
        available = list_declared_fragments(target_md)
        avail_txt = ", ".join(available[:12]) if available else "(none)"
        if len(available) > 12:
            avail_txt += ", …"
        issues.append(
            "en_link_target: "
            f"{page_name}:{line}\n"
            f"  target: {_short_target(target_path)}\n"
            f"  missing fragment: {fragment}\n"
            f"  available: {avail_txt}"
        )
    if not issues or not baseline_text:
        return issues
    ambient = {
        key
        for msg in check_en_page_link_targets(
            en_page_path, baseline_text, read_text=read_text
        )
        if (key := _link_target_issue_key(msg))
    }
    if not ambient:
        return issues
    return [msg for msg in issues if _link_target_issue_key(msg) not in ambient]


def apply_en_link_target_checks(
    result,
    *,
    repo_path: str,
    en_md_paths: set[str] | frozenset[str] | None = None,
    baseline_read: DocsTextReader | None = None,
    docs_read: DocsTextReader | None = None,
) -> list[str]:
    """Attach ``en_link_target`` findings to pair results; return broken paths.

    Prefers final worktree bytes over ``pair_results`` so post-apply late
    repairs are authoritative. Falls back to in-memory text before apply.
    Independent of critic LLM (§6.226).

    ``baseline_read`` supplies pre-translate tip EN for ambient-debt filtering
    (§6.228). ``docs_read`` resolves link targets (default: worktree); pass the
    §6.229 tip+overlay reader on merged-PR checkouts.
    """
    from ydbdoc_review.github.git_ops import read_text
    from ydbdoc_review.pipeline.types import FileTranslationResult
    from ydbdoc_review.validation.heuristics import bump_verdict_for_blocking_heuristics

    def _read(path: str) -> str | None:
        if docs_read is not None:
            return docs_read(path)
        return read_text(repo_path, path)

    runs_by_path: dict[str, list] = {}
    texts: dict[str, str] = {}
    for run in result.pair_results:
        if run.skipped or run.deleted or run.error:
            continue
        if run.plan.target_lang != "en" or not run.plan.target_path.endswith(".md"):
            continue
        path = run.plan.target_path.replace("\\", "/")
        runs_by_path.setdefault(path, []).append(run)
        if run.target_text is not None:
            texts[path] = run.target_text
        elif run.file_result is not None and run.file_result.final_text:
            texts[path] = run.file_result.final_text

    paths = {p.replace("\\", "/") for p in (en_md_paths or ())}
    paths |= set(runs_by_path)
    paths |= set(texts)
    broken: list[str] = []
    for path in sorted(paths):
        text = _read(path) or texts.get(path)
        if not text:
            continue
        baseline = baseline_read(path) if baseline_read is not None else None
        msgs = check_en_page_link_targets(
            path, text, read_text=_read, baseline_text=baseline
        )
        if not msgs:
            continue
        broken.append(path)
        runs = runs_by_path.get(path, [])
        if not runs:
            continue
        for run in runs:
            fr = run.file_result
            if fr is None:
                fr = FileTranslationResult(
                    file_path=path,
                    final_text=text,
                    segments_count=0,
                    verdict="ok",
                    prompt_version="",
                )
                run.file_result = fr
            fr.heuristic_blocking.extend(msgs)
            fr.verdict = bump_verdict_for_blocking_heuristics(fr.verdict, msgs)
    return broken
