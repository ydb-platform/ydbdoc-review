"""Copy locale binary assets (``_assets/*.svg|png|…``) RU→EN (§6.157).

When a RU page is translated, relative image refs such as
``![…](_assets/foo.svg)`` land in EN markdown, but Diplodoc resolves them under
``docs/en/…``. Until this module runs, binaries that exist only under ``docs/ru``
cause ``ENOENT`` in ``build-docs``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path, PurePosixPath

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.pairs import counterpart
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.link_locale import collect_relative_hrefs

logger = logging.getLogger(__name__)

_ASSET_EXT = re.compile(
    r"\.(?:svg|png|jpe?g|gif|webp|ico)$",
    re.IGNORECASE,
)
# Mirror §6.47 / link_locale: RU diagrams use ``-rub`` before the extension.
_RU_ASSET_SUFFIX_BEFORE_EXT = re.compile(
    r"-rub(?=\.(?:svg|png|jpe?g|gif|webp|ico)$)",
    re.IGNORECASE,
)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def en_asset_rel_path(ru_asset_path: str, *, docs_root: str = "ydb/docs") -> str | None:
    """Map a RU locale asset path to its EN mirror, stripping ``-rub`` before ext."""
    en = counterpart(ru_asset_path, docs_root)
    if en is None:
        return None
    parent = str(PurePosixPath(en).parent)
    name = PurePosixPath(en).name
    name = _RU_ASSET_SUFFIX_BEFORE_EXT.sub("", name)
    return _norm(str(PurePosixPath(parent) / name))


def resolve_relative_asset(
    source_file: str,
    href: str,
    *,
    docs_root: str = "ydb/docs",
) -> str | None:
    """Resolve a relative image href against ``source_file`` to a docs path."""
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "data:")):
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I):
        return None
    path_only = href.split("#", 1)[0].split("?", 1)[0]
    if not path_only or not _ASSET_EXT.search(path_only):
        return None
    base = PurePosixPath(_norm(source_file)).parent
    resolved = _norm(str((base / path_only).as_posix()))
    # Collapse .. segments
    parts: list[str] = []
    for part in PurePosixPath(resolved).parts:
        if part == "..":
            if parts:
                parts.pop()
            continue
        if part in ("", "."):
            continue
        parts.append(part)
    resolved = "/".join(parts)
    root = docs_root.strip("/")
    if not resolved.startswith(f"{root}/ru/") and not resolved.startswith(
        f"{root}/en/"
    ):
        return None
    return resolved


def plan_locale_asset_copies(
    source_file: str,
    source_text: str,
    *,
    docs_root: str = "ydb/docs",
) -> list[tuple[str, str]]:
    """Return ``(ru_or_src_path, en_path)`` pairs for binary assets in ``source_text``.

    Source is normally a RU page; if the resolved path is already under ``en/``,
    it is skipped (nothing to mirror).
    """
    root = docs_root.strip("/")
    doc = parse_markdown(source_text)
    planned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href in collect_relative_hrefs(doc):
        resolved = resolve_relative_asset(source_file, href, docs_root=docs_root)
        if resolved is None:
            continue
        if resolved.startswith(f"{root}/en/"):
            continue
        if not resolved.startswith(f"{root}/ru/"):
            continue
        en_path = en_asset_rel_path(resolved, docs_root=docs_root)
        if en_path is None or en_path in seen:
            continue
        seen.add(en_path)
        planned.append((resolved, en_path))
    return planned


def copy_locale_assets_for_pair(
    repo_path: str,
    *,
    source_file: str,
    source_text: str,
    docs_root: str = "ydb/docs",
    dry_run: bool = False,
) -> list[str]:
    """Copy missing/outdated RU assets to EN; return written EN paths."""
    written: list[str] = []
    for src_rel, en_rel in plan_locale_asset_copies(
        source_file, source_text, docs_root=docs_root
    ):
        src = Path(repo_path) / src_rel.replace("/", os.sep)
        dest = Path(repo_path) / en_rel.replace("/", os.sep)
        if not src.is_file():
            logger.warning(
                "locale asset missing on disk (skip copy): %s → %s", src_rel, en_rel
            )
            continue
        if dest.is_file() and dest.read_bytes() == src.read_bytes():
            continue
        written.append(en_rel)
        if dry_run:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        logger.info("Copied locale asset %s → %s", src_rel, en_rel)
    return written


def apply_locale_asset_copies(
    result: PRTranslationResult,
    *,
    repo_path: str,
    docs_root: str = "ydb/docs",
    dry_run: bool = False,
) -> list[str]:
    """Copy RU→EN assets for every successful markdown pair in ``result``."""
    written: list[str] = []
    for run in result.pair_results:
        if run.skipped or run.error or run.deleted:
            continue
        if run.target_text is None:
            continue
        source_text = run.source_text
        if not source_text:
            # Fall back to EN body image refs only if RU body unavailable —
            # still resolve via counterpart from en→ru for the source file.
            continue
        source_file = run.plan.source_path
        if run.plan.source_lang.lower() not in {"ru", "russian"}:
            # EN→RU translate: mirror the other way if needed later; skip for now.
            continue
        written.extend(
            copy_locale_assets_for_pair(
                repo_path,
                source_file=source_file,
                source_text=source_text,
                docs_root=docs_root,
                dry_run=dry_run,
            )
        )
    return list(dict.fromkeys(written))
