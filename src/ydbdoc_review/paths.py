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
