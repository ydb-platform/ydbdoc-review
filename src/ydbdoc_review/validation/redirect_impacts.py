"""Deterministic repair of inbound links affected by new docs redirects."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import yaml

_LINK = re.compile(r"(\[[^\]]*\]\()([^)#]+)(#[^)]*)?(\))")


def added_redirects(base_text: str, current_text: str) -> dict[str, str]:
    """Return redirect mappings introduced by the source change."""

    def _rows(text: str) -> list[dict[str, str]]:
        data = yaml.safe_load(text) or {}
        out: list[dict[str, str]] = []
        for locale in ("ru", "en"):
            for row in data.get(locale, []) or []:
                if isinstance(row, dict) and row.get("from") and row.get("to"):
                    out.append({"from_path": str(row["from"]), "to_path": str(row["to"])})
        return out

    base = {(row["from_path"], row["to_path"]) for row in _rows(base_text)}
    return {
        row["from_path"]: row["to_path"]
        for row in _rows(current_text)
        if (row["from_path"], row["to_path"]) not in base
    }


def retarget_redirect_inbound_links(
    repo_path: str,
    mappings: dict[str, str],
    *,
    docs_root: str = "ydb/docs",
    dry_run: bool = False,
    allowed_paths: frozenset[str] | None = None,
) -> list[str]:
    """Retarget source-scoped links covered by new redirects."""
    root = Path(repo_path) / docs_root
    changed: list[str] = []
    for locale in ("ru", "en"):
        locale_root = root / locale / "core"
        if not locale_root.is_dir():
            continue
        for path in sorted(locale_root.rglob("*.md")):
            rel = path.relative_to(repo_path).as_posix()
            if allowed_paths is not None and rel not in allowed_paths:
                continue
            text = path.read_text(encoding="utf-8")
            rel_dir = PurePosixPath(path.relative_to(locale_root).as_posix()).parent

            def _replace(
                match: re.Match[str],
                *,
                _rel_dir: PurePosixPath = rel_dir,
                _locale: str = locale,
            ) -> str:
                href = match.group(2)
                if href.startswith(("/", "http://", "https://")):
                    return match.group(0)
                resolved = "/" + posixpath.normpath((_rel_dir / href).as_posix())
                target = mappings.get(resolved) or mappings.get(resolved.removesuffix(".md"))
                if target is None:
                    return match.group(0)
                target_md = target if target.endswith(".md") else f"{target}.md"
                new_href = posixpath.relpath(target_md.lstrip("/"), _rel_dir.as_posix())
                fragment = match.group(3) or ""
                if _locale == "en" and fragment:
                    ru_target = root / "ru" / "core" / target_md.lstrip("/")
                    en_target = root / "en" / "core" / target_md.lstrip("/")
                    if ru_target.is_file() and en_target.is_file():
                        from ydbdoc_review.validation.href_parity import (
                            map_ru_fragment_to_declared_en_fragment,
                        )

                        localized = map_ru_fragment_to_declared_en_fragment(
                            unquote(fragment[1:]),
                            ru_target.read_text(encoding="utf-8"),
                            en_target.read_text(encoding="utf-8"),
                        )
                        if localized:
                            fragment = f"#{localized}"
                return f"{match.group(1)}{new_href}{fragment}{match.group(4)}"

            updated = _LINK.sub(_replace, text)
            if updated == text:
                continue
            changed.append(rel)
            if not dry_run:
                path.write_text(updated, encoding="utf-8", newline="")
    return changed


def mirror_redirects_to_en(text: str, mappings: dict[str, str]) -> str:
    """Append missing locale-neutral source redirects to the EN list."""
    data = yaml.safe_load(text) or {}
    en_pairs = {
        (str(row.get("from")), str(row.get("to")))
        for row in data.get("en", []) or []
        if isinstance(row, dict)
    }
    missing = [item for item in mappings.items() if item not in en_pairs]
    if not missing:
        return text
    suffix = "" if text.endswith("\n") else "\n"
    blocks = "".join(f"  - from: {old}\n    to: {new}\n" for old, new in missing)
    return text + suffix + blocks
