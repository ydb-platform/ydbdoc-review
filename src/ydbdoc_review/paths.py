from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DocPair:
    ru_path: str
    en_path: str


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def is_docs_markdown(path: str, docs_prefix: str) -> bool:
    p = _norm(path)
    if not p.endswith(".md"):
        return False
    dp = docs_prefix.strip("/")
    return p.startswith(f"{dp}/ru/") or p.startswith(f"{dp}/en/")


def locale_of(path: str, docs_prefix: str) -> str | None:
    p = _norm(path)
    dp = docs_prefix.strip("/")
    if p.startswith(f"{dp}/ru/"):
        return "ru"
    if p.startswith(f"{dp}/en/"):
        return "en"
    return None


def counterpart(path: str, docs_prefix: str) -> str | None:
    p = _norm(path)
    dp = docs_prefix.strip("/")
    if p.startswith(f"{dp}/ru/"):
        rest = p[len(f"{dp}/ru/") :]
        return f"{dp}/en/{rest}"
    if p.startswith(f"{dp}/en/"):
        rest = p[len(f"{dp}/en/") :]
        return f"{dp}/ru/{rest}"
    return None


def ru_to_en_path(path: str, docs_prefix: str) -> str | None:
    """Map any `docs/ru/...` path to `docs/en/...` (md, assets, yaml, …)."""
    return counterpart(path, docs_prefix)


_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")


def ru_asset_files_to_mirror(changed: list[str], docs_prefix: str) -> list[tuple[str, str]]:
    """Binary assets under docs/ru/ → same path under docs/en/ (identical files)."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    prefix = f"{docs_prefix.strip('/')}/ru/"
    for raw in changed:
        p = _norm(raw)
        if not p.startswith(prefix):
            continue
        if p.endswith(".md") or p.endswith((".yaml", ".yml")):
            continue
        if not ("/_assets/" in p or p.lower().endswith(_ASSET_SUFFIXES)):
            continue
        en = ru_to_en_path(p, docs_prefix)
        if not en:
            continue
        key = (p, en)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def ru_toc_yaml_paths(changed: list[str], docs_prefix: str) -> list[tuple[str, str]]:
    """toc*.yaml / *.yml navigation files — merged, not copied from RU."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    prefix = f"{docs_prefix.strip('/')}/ru/"
    for raw in changed:
        p = _norm(raw)
        if not p.startswith(prefix):
            continue
        if not (p.endswith((".yaml", ".yml")) and "toc" in p.lower()):
            continue
        en = ru_to_en_path(p, docs_prefix)
        if not en:
            continue
        key = (p, en)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out




def pairs_from_changed_files(changed: list[str], docs_prefix: str) -> list[DocPair]:
    seen: set[tuple[str, str]] = set()
    out: list[DocPair] = []
    for raw in changed:
        p_norm = _norm(raw)
        if not is_docs_markdown(p_norm, docs_prefix):
            continue
        loc = locale_of(p_norm, docs_prefix)
        if loc == "ru":
            ru, en = p_norm, counterpart(p_norm, docs_prefix)
        elif loc == "en":
            en, ru = p_norm, counterpart(p_norm, docs_prefix)
        else:
            continue
        if not en:
            continue
        key = (ru, en)
        if key not in seen:
            seen.add(key)
            out.append(DocPair(ru_path=ru, en_path=en))
    return out


def truncate(text: str | None, limit: int) -> tuple[str, bool]:
    if text is None:
        return "", False
    if len(text) <= limit:
        return text, False
    return (
        text[:limit]
        + "\n\n…(truncated for analysis; full file is used when translating)\n",
        True,
    )


def repo_root_join(repo_root: str, rel_path: str) -> str:
    return os.path.normpath(os.path.join(repo_root, rel_path.replace("/", os.sep)))
