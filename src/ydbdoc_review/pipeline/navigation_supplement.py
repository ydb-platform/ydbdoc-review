"""Discover parent toc YAML merges required by newly translated EN pages."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from ydbdoc_review.github.git_ops import merge_base, read_text, read_text_at_ref
from ydbdoc_review.navigation.paths import is_toc_yaml
from ydbdoc_review.navigation.toc import (
    collect_toc_link_targets,
    parse_toc_items,
    resolve_toc_target_path,
)
from ydbdoc_review.pipeline.pairs import NavigationPair, counterpart

_TOC_FILENAMES = ("toc_p.yaml", "toc_i.yaml")


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _toc_hrefs(yaml_text: str) -> set[str]:
    return {it["href"] for it in parse_toc_items(yaml_text) if it.get("href")}


def _nested_toc_pairs_in_dir(
    ru_dir: str,
    *,
    repo_path: str,
    docs_root: str,
) -> list[tuple[str, str]]:
    """``toc-*.yaml`` sidebars in the same directory as a translated page."""
    fs_dir = Path(repo_path) / ru_dir.replace("/", os.sep)
    if not fs_dir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(fs_dir.glob("toc-*.yaml")):
        ru_toc = _norm(f"{ru_dir}/{path.name}")
        en_toc = counterpart(ru_toc, docs_root)
        if en_toc is not None:
            out.append((ru_toc, en_toc))
    return out


def _ancestor_toc_pairs(
    ru_md_path: str,
    *,
    repo_path: str,
    docs_root: str,
) -> list[tuple[str, str]]:
    """``(ru_toc, en_toc)`` for each sidebar toc in ancestors of a RU page."""
    root = docs_root.strip("/")
    ru_root = PurePosixPath(root) / "ru"
    dir_path = PurePosixPath(_norm(ru_md_path)).parent
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    while dir_path >= ru_root:
        for name in _TOC_FILENAMES:
            ru_toc = _norm(str(dir_path / name))
            if not is_toc_yaml(ru_toc):
                continue
            en_toc = counterpart(ru_toc, docs_root)
            if en_toc is not None:
                pair = (ru_toc, en_toc)
                if pair not in seen:
                    out.append(pair)
                    seen.add(pair)
        ru_dir = _norm(str(dir_path))
        for pair in _nested_toc_pairs_in_dir(
            ru_dir, repo_path=repo_path, docs_root=docs_root
        ):
            if pair not in seen:
                out.append(pair)
                seen.add(pair)
        if dir_path == ru_root:
            break
        dir_path = dir_path.parent
    return out


def _included_child_toc_pairs(
    ru_toc: str,
    ru_toc_text: str,
    *,
    docs_root: str,
) -> list[tuple[str, str]]:
    """Child toc YAML files referenced via ``include.path`` from a parent toc."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, rel in collect_toc_link_targets(ru_toc_text):
        if kind != "include" or not rel.endswith((".yaml", ".yml")):
            continue
        ru_child = resolve_toc_target_path(ru_toc, rel)
        en_child = counterpart(ru_child, docs_root)
        if en_child is None:
            continue
        pair = (ru_child, en_child)
        if pair not in seen:
            out.append(pair)
            seen.add(pair)
    return out


def _en_toc_missing_on_main(
    repo_path: str,
    en_toc: str,
    *,
    merge_base_ref: str,
    merge_base_with: str,
) -> bool:
    en_base = read_text_at_ref(repo_path, merge_base_ref, en_toc)
    if en_base is None:
        en_base = read_text_at_ref(repo_path, merge_base_with, en_toc)
    return en_base is None


def _supplement_included_child_tocs(
    pairs: list[NavigationPair],
    translated_en_md_paths: set[str],
    *,
    repo_path: str,
    merge_base_ref: str,
    merge_base_with: str,
    docs_root: str,
) -> list[NavigationPair]:
    """Queue child toc merges when a parent toc ``include.path`` points at RU-only yaml."""
    existing = {(p.ru_path, p.en_path) for p in pairs}
    out = list(pairs)
    scan: list[tuple[str, str]] = []
    scan_seen: set[tuple[str, str]] = set()

    def _queue_scan(ru_toc: str, en_toc: str) -> None:
        key = (ru_toc, en_toc)
        if key not in scan_seen:
            scan_seen.add(key)
            scan.append(key)

    for pair in out:
        _queue_scan(pair.ru_path, pair.en_path)

    for en_md in sorted(translated_en_md_paths):
        en_md = _norm(en_md)
        if "/_includes/" in en_md or not en_md.endswith(".md"):
            continue
        ru_md = counterpart(en_md, docs_root)
        if ru_md is None:
            continue
        for ru_toc, en_toc in _ancestor_toc_pairs(
            ru_md, repo_path=repo_path, docs_root=docs_root
        ):
            _queue_scan(ru_toc, en_toc)

    changed = True
    while changed:
        changed = False
        for ru_toc, en_toc in list(scan):
            ru_toc_text = read_text(repo_path, ru_toc)
            if ru_toc_text is None:
                ru_toc_text = read_text_at_ref(repo_path, "HEAD", ru_toc)
            if not ru_toc_text:
                continue
            for ru_child, en_child in _included_child_toc_pairs(
                ru_toc, ru_toc_text, docs_root=docs_root
            ):
                key = (ru_child, en_child)
                if key in existing:
                    continue
                ru_child_text = read_text(repo_path, ru_child)
                if ru_child_text is None:
                    ru_child_text = read_text_at_ref(repo_path, "HEAD", ru_child)
                if not ru_child_text:
                    continue
                if not _en_toc_missing_on_main(
                    repo_path,
                    en_child,
                    merge_base_ref=merge_base_ref,
                    merge_base_with=merge_base_with,
                ):
                    continue
                out.append(
                    NavigationPair(
                        ru_path=ru_child,
                        en_path=en_child,
                        ru_changed=True,
                        supplement_only=True,
                    )
                )
                existing.add(key)
                _queue_scan(ru_child, en_child)
                changed = True
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

        for ru_toc, en_toc in _ancestor_toc_pairs(
            ru_md, repo_path=repo_path, docs_root=docs_root
        ):
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
                    supplement_only=True,
                )
            )
            existing.add(key)

    return _supplement_included_child_tocs(
        out,
        translated_en_md_paths,
        repo_path=repo_path,
        merge_base_ref=mb,
        merge_base_with=merge_base_with,
        docs_root=docs_root,
    )
