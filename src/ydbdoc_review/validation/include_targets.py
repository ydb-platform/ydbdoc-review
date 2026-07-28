"""Verify locale ``{% include %}`` targets exist on the EN mirror tree (§6.80).

Also enforce RU→EN include **parity** (§6.148): every locale-relative include in
RU must appear in EN; missing calls are auto-inserted when the EN target file
exists so ``doc_verify`` can stay green without silent CTA/footer loss.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from ydbdoc_review.github.git_ops import read_text
from ydbdoc_review.parsing.ast_types import YfmInclude
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.glossary_toc_links import en_mirror_path
from ydbdoc_review.validation.heuristics import bump_verdict_for_blocking_heuristics

DocsTextReader = Callable[[str], str | None]


def _include_target_exists(repo_path: str, rel_path: str) -> bool:
    if read_text(repo_path, rel_path) is not None:
        return True
    return Path(repo_path, rel_path.replace("/", os.sep)).is_file()


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _ru_to_en_resolved(resolved: str, *, docs_root: str) -> str | None:
    root = docs_root.strip("/")
    p = _norm(resolved)
    ru_prefix = f"{root}/ru/"
    en_prefix = f"{root}/en/"
    if p.startswith(ru_prefix):
        return en_prefix + p[len(ru_prefix) :]
    if p.startswith(en_prefix):
        return p
    return None


def _format_include_line(inc: YfmInclude) -> str:
    notitle_part = "notitle " if inc.notitle else ""
    return f"{{% include {notitle_part}[{inc.text}]({inc.path}) %}}"


def missing_ru_includes(
    source_text: str,
    target_text: str,
    *,
    source_file: str,
    docs_root: str = "ydb/docs",
) -> list[tuple[str, YfmInclude]]:
    """Return ``(en_resolved_path, YfmInclude)`` for RU includes absent from EN.

    Matching is by resolved EN mirror path (not display text). Relative ``path``
    on the include node is kept as in RU (usually ``./_includes/…``).
    """
    root = docs_root.strip("/")
    src = _norm(source_file)
    if not src.startswith(f"{root}/ru/"):
        return []

    en_file = en_mirror_path(src, docs_root=docs_root)
    en_present: set[str] = set()
    for inc in collect_yfm_includes(target_text):
        resolved = resolve_locale_md_path(en_file, inc.path, docs_root=docs_root)
        if resolved is None:
            continue
        en_key = _ru_to_en_resolved(resolved, docs_root=docs_root) or resolved
        en_present.add(en_key)

    missing: list[tuple[str, YfmInclude]] = []
    seen: set[str] = set()
    for inc in collect_yfm_includes(source_text):
        resolved_ru = resolve_locale_md_path(src, inc.path, docs_root=docs_root)
        if resolved_ru is None:
            continue
        en_resolved = _ru_to_en_resolved(resolved_ru, docs_root=docs_root)
        if en_resolved is None:
            continue
        if en_resolved in en_present or en_resolved in seen:
            continue
        seen.add(en_resolved)
        missing.append((en_resolved, inc))
    return missing


def check_include_parity(
    source_text: str,
    target_text: str,
    *,
    source_file: str | None,
    docs_root: str = "ydb/docs",
) -> list[str]:
    """Blocking when EN lacks locale-relative ``{% include %}`` present in RU."""
    if not source_file:
        return []
    missing = missing_ru_includes(
        source_text,
        target_text,
        source_file=source_file,
        docs_root=docs_root,
    )
    if not missing:
        return []
    preview = ", ".join(
        f"`{PurePosixPath(path).name}`" for path, _ in missing[:6]
    )
    if len(missing) > 6:
        preview += f", … (+{len(missing) - 6})"
    return [f"include_parity: EN missing RU include(s): {preview}"]


def repair_missing_includes(
    source_text: str,
    target_text: str,
    *,
    source_file: str | None,
    docs_root: str = "ydb/docs",
    docs_text_reader: DocsTextReader | None = None,
    repo_path: str | None = None,
    out_warnings: list[str] | None = None,
) -> str:
    """Append missing RU includes to EN when the EN target file exists (§6.148).

    Skips an include when the EN mirror file is known missing (reader/repo).
    When existence cannot be checked, still inserts (parity > silent drop).
    """
    if not source_file or not target_text:
        return target_text
    missing = missing_ru_includes(
        source_text,
        target_text,
        source_file=source_file,
        docs_root=docs_root,
    )
    if not missing:
        return target_text

    to_add: list[str] = []
    skipped: list[str] = []
    for en_resolved, inc in missing:
        exists: bool | None = None
        if docs_text_reader is not None:
            exists = docs_text_reader(en_resolved) is not None
        elif repo_path is not None:
            exists = _include_target_exists(repo_path, en_resolved)
        if exists is False:
            skipped.append(en_resolved)
            continue
        to_add.append(_format_include_line(inc))

    if skipped and out_warnings is not None:
        names = ", ".join(f"`{PurePosixPath(p).name}`" for p in skipped[:6])
        out_warnings.append(
            f"include_parity: cannot auto-insert — EN include file missing: {names}"
        )
    if not to_add:
        return target_text

    body = target_text.rstrip()
    addition = "\n\n".join(to_add)
    repaired = f"{body}\n\n{addition}\n"
    if out_warnings is not None:
        out_warnings.append(
            "include_parity_repaired: added "
            + ", ".join(f"`{line}`" for line in to_add[:4])
            + (f", … (+{len(to_add) - 4})" if len(to_add) > 4 else "")
        )
    return repaired


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


def apply_include_parity_repair(
    result: PRTranslationResult,
    *,
    repo_path: str,
    docs_root: str = "ydb/docs",
) -> None:
    """PR-level safety net: repair missing includes on disk-bound final text."""
    for run in result.pair_results:
        fr = run.file_result
        if fr is None or run.skipped or run.deleted or run.error:
            continue
        if run.plan.target_lang != "en" or not run.plan.target_path.endswith(".md"):
            continue
        source = read_text(repo_path, run.plan.source_path) or ""
        en_text = fr.final_text or run.target_text or ""
        if not source or not en_text:
            continue
        warnings: list[str] = []
        repaired = repair_missing_includes(
            source,
            en_text,
            source_file=run.plan.source_path,
            docs_root=docs_root,
            repo_path=repo_path,
            out_warnings=warnings,
        )
        if repaired != en_text:
            fr.final_text = repaired
            run.target_text = repaired
            for w in warnings:
                if w.startswith("include_parity_repaired:"):
                    fr.heuristic_info.append(w)
                elif w.startswith("include_parity:"):
                    fr.heuristic_blocking.append(w)
        fr.heuristic_blocking = [
            m
            for m in fr.heuristic_blocking
            if not m.startswith("include_parity:")
        ]
        still = check_include_parity(
            source,
            fr.final_text or "",
            source_file=run.plan.source_path,
            docs_root=docs_root,
        )
        if still:
            fr.heuristic_blocking.extend(still)
            fr.verdict = bump_verdict_for_blocking_heuristics(fr.verdict, still)
