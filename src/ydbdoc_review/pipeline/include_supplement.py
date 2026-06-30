"""Add RU/EN pairs for locale ``{% include %}`` dependencies (§6.80)."""

from __future__ import annotations

import logging

from ydbdoc_review.github.git_ops import read_text, read_text_at_ref
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.pairs import ChangeKind, DocPair, counterpart

logger = logging.getLogger(__name__)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _read_ru_markdown(
    repo_path: str, ru_path: str, *, merge_base_with: str
) -> str | None:
    text = read_text(repo_path, ru_path)
    if text is not None:
        return text
    return read_text_at_ref(repo_path, "HEAD", ru_path)


def _ru_include_targets(
    ru_md_path: str,
    ru_text: str,
    *,
    docs_root: str,
) -> set[str]:
    targets: set[str] = set()
    for inc in collect_yfm_includes(ru_text):
        resolved = resolve_locale_md_path(
            ru_md_path, inc.path, docs_root=docs_root
        )
        if resolved is not None and resolved.startswith(
            f"{docs_root.strip('/')}/ru/"
        ):
            targets.add(resolved)
    return targets


def supplement_include_pairs(
    pairs: list[DocPair],
    *,
    repo_path: str,
    merge_base_with: str,
    docs_root: str = "ydb/docs",
) -> tuple[list[DocPair], list[tuple[str, ChangeKind]]]:
    """Close transitive locale-include dependencies for pairs already in scope.

    Returns updated pairs and synthetic RU change entries for ``completeness_gaps``.
    """
    if not pairs:
        return pairs, []

    by_ru = {pair.ru_path: pair for pair in pairs}
    queue = sorted(by_ru)
    extra_changes: list[tuple[str, ChangeKind]] = []

    while queue:
        ru_path = queue.pop(0)
        ru_text = _read_ru_markdown(
            repo_path, ru_path, merge_base_with=merge_base_with
        )
        if not ru_text:
            continue

        for target_ru in sorted(
            _ru_include_targets(ru_path, ru_text, docs_root=docs_root)
        ):
            en_path = counterpart(target_ru, docs_root)
            if en_path is None:
                continue
            if target_ru in by_ru:
                continue

            by_ru[target_ru] = DocPair(
                ru_path=target_ru,
                en_path=en_path,
                ru_changed=True,
            )
            queue.append(target_ru)
            kind: ChangeKind = (
                "added"
                if read_text_at_ref(repo_path, merge_base_with, target_ru) is None
                else "modified"
            )
            extra_changes.append((target_ru, kind))
            logger.info(
                "Supplement include pair from %s → %s (%s)",
                ru_path,
                target_ru,
                kind,
            )

    return sorted(by_ru.values(), key=lambda p: p.ru_path), extra_changes
