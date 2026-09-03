"""Unit tests for Markdown-link dependency queue (§6 / P2)."""

from __future__ import annotations

from ydbdoc_review.navigation.link_deps import (
    MAX_EXTRA_LINK_DEPS,
    collect_md_link_dependencies,
)
from ydbdoc_review.navigation.scope_planner import plan_translation_scope


def _readers(
    ru: dict[str, str],
    en: dict[str, str],
    *,
    redirects: str = "",
):
    def read_ru(path: str) -> str | None:
        if path.endswith("redirects.yaml") and redirects:
            return redirects
        return ru.get(path)

    def read_en(path: str) -> str | None:
        if path.endswith("redirects.yaml") and redirects:
            return redirects
        return en.get(path)

    return read_ru, read_en


def test_missing_en_link_enqueues_ru_dependency() -> None:
    seed = "ydb/docs/ru/core/a.md"
    dep = "ydb/docs/ru/core/b.md"
    ru = {
        seed: "See [B](b.md).\n",
        dep: "# B\n",
    }
    en: dict[str, str] = {}
    read_ru, read_en = _readers(ru, en)

    result = collect_md_link_dependencies(
        [seed],
        read_ru=read_ru,
        read_en=read_en,
    )
    assert result.queued_ru_paths == frozenset({dep})
    assert result.warnings == ()


def test_existing_stale_en_skips_enqueue() -> None:
    seed = "ydb/docs/ru/core/a.md"
    dep = "ydb/docs/ru/core/b.md"
    ru = {
        seed: "See [B](b.md).\n",
        dep: "# B new\n",
    }
    en = {
        "ydb/docs/en/core/b.md": "# B stale\n",
    }
    read_ru, read_en = _readers(ru, en)

    result = collect_md_link_dependencies(
        [seed],
        read_ru=read_ru,
        read_en=read_en,
    )
    assert result.queued_ru_paths == frozenset()
    assert result.warnings == ()


def test_redirect_to_existing_en_skips_enqueue() -> None:
    seed = "ydb/docs/ru/core/a.md"
    old_ru = "ydb/docs/ru/core/old.md"
    ru = {
        seed: "See [old](old.md).\n",
        old_ru: "# Old RU\n",
        "ydb/docs/ru/core/new.md": "# New RU\n",
    }
    en = {
        "ydb/docs/en/core/new.md": "# New EN\n",
    }
    redirects = (
        "common:\n"
        "  - from: /old.md\n"
        "    to: /new.md\n"
    )
    read_ru, read_en = _readers(ru, en, redirects=redirects)

    result = collect_md_link_dependencies(
        [seed],
        read_ru=read_ru,
        read_en=read_en,
        redirects_yaml=redirects,
    )
    assert result.queued_ru_paths == frozenset()
    assert result.warnings == ()


def test_dedup_repeated_and_cyclic_links() -> None:
    a = "ydb/docs/ru/core/a.md"
    b = "ydb/docs/ru/core/b.md"
    c = "ydb/docs/ru/core/c.md"
    ru = {
        a: "See [B](b.md) and [B again](b.md).\n",
        b: "Back to [A](a.md) and [C](c.md).\n",
        c: "Cycle [B](b.md).\n",
    }
    en: dict[str, str] = {}
    read_ru, read_en = _readers(ru, en)

    result = collect_md_link_dependencies(
        [a],
        read_ru=read_ru,
        read_en=read_en,
    )
    assert result.queued_ru_paths == frozenset({b, c})
    assert result.warnings == ()


def test_limit_20_warns_and_does_not_queue_21st() -> None:
    seed = "ydb/docs/ru/core/seed.md"
    # One seed page linking to 21 missing deps.
    links = " ".join(f"[d{i}](d{i}.md)" for i in range(21))
    ru: dict[str, str] = {seed: f"{links}\n"}
    for i in range(21):
        ru[f"ydb/docs/ru/core/d{i}.md"] = f"# D{i}\n"
    en: dict[str, str] = {}
    read_ru, read_en = _readers(ru, en)

    result = collect_md_link_dependencies(
        [seed],
        read_ru=read_ru,
        read_en=read_en,
        max_extra=MAX_EXTRA_LINK_DEPS,
    )
    assert len(result.queued_ru_paths) == MAX_EXTRA_LINK_DEPS
    assert len(result.warnings) == 1
    assert "d20.md" in result.warnings[0] or "manual action" in result.warnings[0]
    assert "link dependency budget exhausted" in result.warnings[0]
    # 21st (lexicographically last among remaining after 20) not queued
    not_queued = {
        f"ydb/docs/ru/core/d{i}.md" for i in range(21)
    } - set(result.queued_ru_paths)
    assert len(not_queued) == 1
    assert not_queued.pop() not in result.queued_ru_paths


def test_source_pr_files_do_not_consume_budget() -> None:
    """Initial PR files are free; only extras count toward the 20 limit."""
    seeds = [f"ydb/docs/ru/core/s{i}.md" for i in range(5)]
    ru: dict[str, str] = {}
    for i, seed in enumerate(seeds):
        # Each seed links to one unique missing dep.
        ru[seed] = f"[dep](dep{i}.md)\n"
        ru[f"ydb/docs/ru/core/dep{i}.md"] = f"# Dep{i}\n"
    en: dict[str, str] = {}
    read_ru, read_en = _readers(ru, en)

    result = collect_md_link_dependencies(
        seeds,
        read_ru=read_ru,
        read_en=read_en,
        max_extra=MAX_EXTRA_LINK_DEPS,
        already_queued=seeds,
    )
    assert result.queued_ru_paths == frozenset(
        f"ydb/docs/ru/core/dep{i}.md" for i in range(5)
    )
    assert result.warnings == ()
    # Seeds themselves must not appear as extras.
    assert not (result.queued_ru_paths & frozenset(seeds))


def test_plan_translation_scope_hooks_link_deps() -> None:
    """Scope planner enqueues MD-link deps via the same one-pass doc set."""
    seed = "ydb/docs/ru/core/page.md"
    dep = "ydb/docs/ru/core/linked.md"
    ru = {
        seed: "See [{#T}](linked.md).\n",
        dep: "# Linked\n",
    }
    en: dict[str, str] = {}
    read_ru, read_en = _readers(ru, en)

    plan = plan_translation_scope(
        [(seed, "added")],
        read_ru=read_ru,
        read_en_base=read_en,
    )
    assert seed in plan.doc_ru_paths
    assert dep in plan.doc_ru_paths
    assert dep in plan.doc_from_main
    assert seed in plan.doc_from_diff
