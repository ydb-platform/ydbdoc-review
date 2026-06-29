"""Discover parent toc YAML merges required by newly translated EN pages."""

from __future__ import annotations

from pathlib import PurePosixPath

from ydbdoc_review.github.git_ops import merge_base, read_text, read_text_at_ref
from ydbdoc_review.navigation.paths import is_toc_yaml
from ydbdoc_review.navigation.toc import parse_toc_items
from ydbdoc_review.pipeline.pairs import NavigationPair, counterpart

_TOC_FILENAMES = ("toc_p.yaml", "toc_i.yaml")


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _toc_hrefs(yaml_text: str) -> set[str]:
    return {it["href"] for it in parse_toc_items(yaml_text) if it.get("href")}


def _ancestor_toc_pairs(ru_md_path: str, *, docs_root: str) -> list[tuple[str, str]]:
    """``(ru_toc, en_toc)`` for each ``toc_*.yaml`` in ancestors of a RU page."""
    root = docs_root.strip("/")
    ru_root = PurePosixPath(root) / "ru"
    dir_path = PurePosixPath(_norm(ru_md_path)).parent
    out: list[tuple[str, str]] = []
    while dir_path >= ru_root:
        for name in _TOC_FILENAMES:
            ru_toc = _norm(str(dir_path / name))
            if not is_toc_yaml(ru_toc):
                continue
            en_toc = counterpart(ru_toc, docs_root)
            if en_toc is not None:
                out.append((ru_toc, en_toc))
        if dir_path == ru_root:
            break
        dir_path = dir_path.parent
    return out


def supplement_navigation_pairs(
    pairs: list[NavigationPair],
    translated_en_md_paths: set[str],
    *,
    repo_path: str,
    merge_base_with: str,
    docs_root: str = "ydb/docs",
) -> list[NavigationPair]:
    """Add parent toc pairs when RU sidebar lists a page EN main toc still lacks.

    Covers PRs that add only ``.md`` while the RU ``toc_*.yaml`` entry already
    landed on ``main`` in an earlier merge (e.g. #43672 + ``system_tablet_backup_config``).
    """
    if not translated_en_md_paths:
        return pairs

    existing = {(p.ru_path, p.en_path) for p in pairs}
    out = list(pairs)
    mb = merge_base(repo_path, merge_base_with, "HEAD")

    for en_md in sorted(translated_en_md_paths):
        en_md = _norm(en_md)
        if "/_includes/" in en_md or not en_md.endswith(".md"):
            continue
        ru_md = counterpart(en_md, docs_root)
        if ru_md is None:
            continue
        basename = PurePosixPath(ru_md).name

        for ru_toc, en_toc in _ancestor_toc_pairs(ru_md, docs_root=docs_root):
            key = (ru_toc, en_toc)
            if key in existing:
                continue

            ru_toc_text = read_text(repo_path, ru_toc)
            if ru_toc_text is None:
                ru_toc_text = read_text_at_ref(repo_path, "HEAD", ru_toc)
            if not ru_toc_text or basename not in _toc_hrefs(ru_toc_text):
                continue

            en_base = read_text_at_ref(repo_path, mb, en_toc)
            if en_base is None:
                en_base = read_text_at_ref(repo_path, merge_base_with, en_toc)
            if en_base is not None and basename in _toc_hrefs(en_base):
                continue

            out.append(
                NavigationPair(
                    ru_path=ru_toc,
                    en_path=en_toc,
                    ru_changed=True,
                )
            )
            existing.add(key)

    return out
