"""Ensure translation PR covers every RU artifact from the source PR diff."""

from __future__ import annotations

from ydbdoc_review.navigation.paths import is_navigation_yaml
from ydbdoc_review.pipeline.pairs import (
    ChangeKind,
    DocPair,
    NavigationPair,
    counterpart,
    is_docs_markdown,
)
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.href_parity import check_href_parity, is_href_only_change


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def is_misresolved_shared_include_mirror(
    en_path: str,
    *,
    docs_root: str = "ydb/docs",
) -> bool:
    """True when ``en_path`` is a false RU↔EN mirror of ``docs/_includes/…``.

    Recipe pages reference shared snippets as ``../../../_includes/go/…`` which
    resolves to nonexistent ``docs/{ru,en}/_includes/…`` instead of language-
    neutral ``docs/_includes/…`` (PR #43997).
    """
    ru_path = counterpart(en_path, docs_root)
    if ru_path is None:
        return False
    root = docs_root.strip("/")
    ru_norm = _norm(ru_path)
    return ru_norm.startswith(f"{root}/ru/_includes/") or ru_norm.startswith(
        f"{root}/en/_includes/"
    )


def bilingual_en_mirrors(
    changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
) -> set[str]:
    """EN paths where both RU and EN mirrors changed in the source PR (§6.76)."""
    ru_touched: set[str] = set()
    en_touched: set[str] = set()
    root = docs_root.strip("/")

    for raw_path, kind in changes:
        if kind == "deleted":
            continue
        path = _norm(raw_path)
        if path.startswith(f"{root}/ru/"):
            if not is_docs_markdown(path, docs_root) and not is_navigation_yaml(path):
                continue
            en_path = counterpart(path, docs_root)
            if en_path is not None:
                ru_touched.add(en_path)
        elif path.startswith(f"{root}/en/"):
            if not is_docs_markdown(path, docs_root) and not is_navigation_yaml(path):
                continue
            en_touched.add(path)
    return ru_touched & en_touched


def expected_en_mirrors(
    changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
) -> set[str]:
    """EN paths that ``doc_translate`` should produce for this source PR."""
    expected: set[str] = set()
    root = docs_root.strip("/")

    for raw_path, kind in changes:
        if kind == "deleted":
            continue
        path = _norm(raw_path)
        if not path.startswith(f"{root}/ru/"):
            continue
        if not is_docs_markdown(path, docs_root) and not is_navigation_yaml(path):
            continue
        en_path = counterpart(path, docs_root)
        if en_path is not None:
            expected.add(en_path)
    return expected


def committed_en_paths(result: PRTranslationResult) -> set[str]:
    """EN paths written or intentionally satisfied in this run.

    Navigation merge may return ``target_text=None`` with ``verdict=ok`` when the
    merged EN equals the upstream baseline (§6.141 no-op). That still satisfies
    the completeness gate: there is nothing to push for that file (§6.144).
    """
    paths: set[str] = set()
    for run in result.pair_results:
        if run.deleted or run.error:
            continue
        if run.skipped:
            # ``skip`` means the EN side at the selected baseline already
            # satisfies this pair.  This is common when translating an old
            # merged PR against current main (#50741), and must count exactly
            # like a navigation no-op below.
            paths.add(run.plan.target_path)
            continue
        if run.target_text is not None:
            paths.add(run.plan.target_path)
    for nav in result.navigation_results:
        if nav.error:
            continue
        if nav.target_text is not None or nav.verdict == "ok":
            paths.add(nav.en_path)
    return paths


def completeness_gaps(
    changes: list[tuple[str, ChangeKind]],
    result: PRTranslationResult,
    *,
    docs_root: str = "ydb/docs",
) -> list[str]:
    """Sorted EN mirror paths missing from the translation run."""
    expected = expected_en_mirrors(changes, docs_root=docs_root)
    expected -= bilingual_en_mirrors(changes, docs_root=docs_root)
    expected = {
        path
        for path in expected
        if not is_misresolved_shared_include_mirror(path, docs_root=docs_root)
    }
    committed = committed_en_paths(result)
    return sorted(expected - committed)


def translation_pr_scope_gaps(
    expected_pairs: list[DocPair],
    expected_nav_pairs: list[NavigationPair],
    translation_changes: list[tuple[str, ChangeKind]],
    *,
    already_satisfied: frozenset[str] | None = None,
) -> list[str]:
    """Expected source-scope EN paths absent from a translation PR diff.

    This is deliberately independent of the critic result: a critic cannot
    approve a file it was never given.  ``supplement_only`` navigation files are
    context for merging and are not required in the resulting commit.
    """
    changed = {_norm(path) for path, _ in translation_changes}
    expected = {pair.en_path for pair in expected_pairs}
    expected.update(nav.en_path for nav in expected_nav_pairs if not nav.supplement_only)
    expected -= already_satisfied or frozenset()
    return sorted(expected - changed)


def href_only_source_noop_satisfied(
    source_base: str | None,
    source_head: str | None,
    current_ru: str | None,
    current_en: str | None,
) -> bool:
    """Whether a historical RU snapshot is already superseded/satisfied in main.

    A later RU move can supersede the source PR before its translation runs.
    In that case forcing historical content into EN can restore deleted sections
    or create unreachable links (#50976). A superseded snapshot is out of scope;
    a still-current href-only edit is covered only when RU/EN links match.
    """
    if source_head is None or current_ru is None or current_en is None:
        return False
    source_was_href_only = is_href_only_change(source_base, source_head)
    source_was_superseded = source_head != current_ru
    if source_was_superseded:
        # RU moved on main after the source PR landed. Do not replay the
        # historical snapshot into EN, but skip the scope gap only when current
        # EN already matches current RU internal links (#50976).
        return not check_href_parity(current_ru, current_en)
    if not source_was_href_only:
        return False
    return not check_href_parity(current_ru, current_en)


def gap_label(en_path: str, *, docs_root: str = "ydb/docs") -> str:
    """Human-readable reason for a completeness gap."""
    if is_misresolved_shared_include_mirror(en_path, docs_root=docs_root):
        return (
            f"{en_path} — ложное EN-зеркало общего snippet "
            f"`{docs_root}/_includes/…` (не переводится; путь include в recipe)"
        )
    if is_navigation_yaml(en_path):
        return f"{en_path} — navigation merge не выполнен"
    return f"{en_path} — не переведён"


def format_completeness_gap_item(
    en_path: str,
    *,
    docs_root: str = "ydb/docs",
    tip_en_exists: bool | None = None,
) -> str:
    """Reviewer-facing completeness gap block (path + RU twin + what to do)."""
    path = _norm(en_path)
    if is_misresolved_shared_include_mirror(path, docs_root=docs_root):
        return (
            f"**`{path}`** — ложное EN-зеркало общего snippet "
            f"`{docs_root}/_includes/…` (не переводится)."
        )
    if is_navigation_yaml(path):
        return f"**`{path}`** — navigation merge не выполнен."

    ru = counterpart(path, docs_root)
    lines = [f"**EN (ожидался в diff переводного PR):** `{path}`"]
    if ru:
        lines.append(f"**RU-близнец:** `{ru}`")
    if tip_en_exists is True:
        lines.append(
            "**Факт:** файл уже есть на tip/`main`, но **не входит в diff** "
            "этого переводного PR (перевод noop относительно tip или не попал "
            "в commit)."
        )
        lines.append(
            "**Что сделать:** не править руками «с нуля». Нужен повторный "
            "`doc_translate` после фикса пайплайна, либо явный commit EN в "
            "ветку `ydbdoc-review/pr-*`, если tip EN реально устарел относительно RU."
        )
    elif tip_en_exists is False:
        lines.append("**Факт:** EN-файла нет на tip.")
        lines.append(
            "**Что сделать:** добавить EN-зеркало (повторный `doc_translate` "
            "или ручной перевод RU→EN в ту же ветку)."
        )
    else:
        lines.append(
            "**Факт:** путь отсутствует в diff переводного PR "
            "(файл мог существовать на main)."
        )
        lines.append(
            "**Что сделать:** проверить tip EN и diff PR; при необходимости "
            "повторный `doc_translate`."
        )
    return "\n".join(lines)


def tip_en_covers_inbound_fragments_from_changed(
    en_path: str,
    en_text: str,
    *,
    changed_en_pages: dict[str, str],
) -> bool:
    """True when tip ``en_text`` declares every ``#frag`` linked from PR-diff EN.

    Used so a scope dependency that already satisfies inbound exact-ASCII
    fragments on tip does not stay a false completeness gap when translate
    produced no git diff (#52077 ``auth_config``).
    """
    from ydbdoc_review.validation.fragment_repair import (
        _page_declares_fragment,
        _resolve_href_path,
    )
    from ydbdoc_review.validation.href_parity import _MD_LINK

    target = _norm(en_path)
    saw_link = False
    for page_path, page_text in changed_en_pages.items():
        if not page_text:
            continue
        for match in _MD_LINK.finditer(page_text):
            href = match.group(2).strip().split(maxsplit=1)[0]
            if "#" not in href:
                continue
            path_part, frag = href.rsplit("#", 1)
            if not frag or not frag.isascii() or not path_part.endswith(".md"):
                continue
            resolved = _resolve_href_path(_norm(page_path), path_part)
            if resolved != target:
                continue
            saw_link = True
            if not _page_declares_fragment(en_text, frag):
                return False
    return saw_link
